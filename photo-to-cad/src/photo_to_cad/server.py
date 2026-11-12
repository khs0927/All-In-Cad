"""Offline-first Oracle job receiver for authorized small GIS exports.

No external downloads, no arbitrary command execution and no GPU. Bind localhost
behind a separately configured TLS proxy only after deployment approval.
"""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
from typing import Any

from .core import load_metric_building
from .export import write_dxf, write_obj

JOB_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
REF_RE = re.compile(r"^[A-Za-z0-9_./-]{1,512}$")
MAX_BYTES = 16384
_JOB_LOCK = threading.RLock()


def _under(base: Path, relative: str, *, suffix: str) -> Path:
    if (not REF_RE.fullmatch(relative) or ".." in relative or relative.startswith("/")):
        raise ValueError("Invalid relative manifest reference")
    path = (base / relative).resolve(strict=False)
    if not path.is_relative_to(base.resolve()) or path.suffix.lower() != suffix:
        raise ValueError("Reference outside designated directory or wrong file extension")
    return path


def run_job(*, data_root: Path, job_id: str, manifest_ref: str) -> dict[str, Any]:
    """Authorize job-scoped source and write only new files under data_root/jobs/."""
    if not JOB_RE.fullmatch(job_id):
        raise ValueError("Invalid job_id")
    root = data_root.resolve()
    output = _under(root / "jobs", job_id + ".json", suffix=".json")
    with _JOB_LOCK:
        if output.is_file():
            old = json.loads(output.read_text(encoding="utf-8"))
            if old.get("input_manifest_ref") != manifest_ref:
                raise ValueError("Idempotency collision: same job ID for different manifest")
            return old
    manifest = _under(root / "inbox", manifest_ref, suffix=".json")
    if not manifest.is_file() or manifest.stat().st_size > 32768:
        raise ValueError("Manifest unavailable or oversized")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid manifest")
    geojson_name = data.get("geojson")
    if not isinstance(geojson_name, str):
        raise ValueError("Manifest requires geojson filename")
    geojson_file = _under(manifest.parent, geojson_name, suffix=".geojson")
    if not geojson_file.is_file() or geojson_file.stat().st_size > 5_000_000:
        raise ValueError("GeoJSON unavailable or oversized")
    height = data.get("height_m")
    height_source = data.get("height_source")
    feature_id = data.get("feature_id")
    if feature_id is not None and not isinstance(feature_id, str):
        raise ValueError("feature_id must be a string")
    if height is not None and (isinstance(height, bool) or not isinstance(height, (float, int))):
        raise ValueError("height_m must be a number")
    model = load_metric_building(json.loads(geojson_file.read_text(encoding="utf-8")),
                                  height_m=height, height_source=height_source,
                                  feature_id=feature_id,
                                  source="manifest:" + manifest_ref)
    # Atomic idempotent response file and directory that cannot follow escaping symlinks.
    workdir = root / "jobs" / job_id
    if not workdir.resolve(strict=False).is_relative_to(root / "jobs"):
        raise ValueError("Job output directory escapes data root")
    with _JOB_LOCK:
        if output.is_file():
            old = json.loads(output.read_text(encoding="utf-8"))
            if old.get("input_manifest_ref") != manifest_ref:
                raise ValueError("Idempotency collision: same job ID for different manifest")
            return old
        workdir.mkdir(parents=True, exist_ok=True)
        dxf = write_dxf(model, workdir / "footprint.dxf")
        files = [dxf.name]
        if model.height_m is not None:
            files.append(write_obj(model, workdir / "mass.obj").name)
        result = {"job_id":job_id,"input_manifest_ref":manifest_ref,
                  "quality":model.report()["quality"],"geometry":model.report()["geometry"],"files":files}
        tmp = output.with_suffix(".tmp")
        tmp.write_text(json.dumps(result), encoding="utf-8")
        tmp.replace(output)
        return result


class Handler(BaseHTTPRequestHandler):
    server: "JobServer"
    def log_message(self, format, *args):
        # Do not print user payload, bearer tokens, paths or file contents.
        return

    def _json(self, code, obj):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"status":"ok","type":"cpu-only"})
        return self._json(404, {"error":"not_found"})

    def do_POST(self):
        if self.path != "/jobs":
            return self._json(404, {"error":"not_found"})
        supplied = self.headers.get("authorization", "")
        if not self.server.token or not hmac.compare_digest(supplied, "Bearer " + self.server.token):
            return self._json(401, {"error":"unauthorized"})
        try:
            length = int(self.headers.get("content-length", "0"))
            if length < 1 or length > MAX_BYTES:
                return self._json(413, {"error":"bad_request_size"})
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or set(body) != {"job_id", "input_manifest_ref"}:
                return self._json(422, {"error":"invalid_job"})
            result = run_job(data_root=self.server.data_root,
                             job_id=body["job_id"], manifest_ref=body["input_manifest_ref"])
        except (ValueError, json.JSONDecodeError, TypeError) as exc:
            return self._json(422, {"error":"invalid_job","detail":str(exc)[:180]})
        except Exception:
            return self._json(500, {"error":"internal_error"})
        return self._json(201, {"accepted":True,"result":result})


class JobServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, addr, *, data_root: Path, token: str):
        self.data_root = data_root.resolve()
        self.token = token
        super().__init__(addr, Handler)


def main():
    token = os.environ.get("PHOTO_CAD_BACKEND_TOKEN", "")
    if len(token) < 20:
        raise SystemExit("PHOTO_CAD_BACKEND_TOKEN (at least 20 characters) is required")
    root = Path(os.environ.get("PHOTO_CAD_DATA_ROOT", "./photo-cad-data"))
    (root / "inbox").mkdir(parents=True, exist_ok=True)
    (root / "jobs").mkdir(parents=True, exist_ok=True)
    server = JobServer(("127.0.0.1", int(os.environ.get("PHOTO_CAD_PORT", "8788"))),
                       data_root=root, token=token)
    server.serve_forever()


if __name__ == "__main__":
    main()
