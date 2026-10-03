"""Headless FreeCAD execution wrapper with ARTIFACT-FIRST verdict adjudication.

WHY THIS FILE CHANGED (observed fact, not inference)
---------------------------------------------------
The previous version ranked stdout markers as "the strongest signal" and
treated their absence as 'unknown'. That ranking was falsified by a direct
measurement on this host:

  * D:\\CAD\\FreeCAD\\FreeCAD_1.1.3-Windows-x86_64-py311\\bin\\freecadcmd.exe
    was launched on a real DXF smoke script.
  * The script RAN: output files were produced, and FreeCAD built 19 objects
    (11 Part::Feature shapes + named layers CEN1/WAL1/WAL2/DOOR/DOOR_ELE + the
    layer container).
  * stdout produced NOTHING. No [AIC-BEGIN], no [AIC-OK], no [AIC-FAIL].
  * Process exit code was 0.

=> the stdout marker channel did not work in that measurement.

HONESTY NOTE ON THE CAUSE: the observed fact is "no output on stdout in the
parent's run". WHY it is missing there is UNDETERMINED. This file makes no
claim about the cause. It is also NOT reproduced in every run: live runs of
this wrapper on this same host DID see [AIC-BEGIN]/[AIC-OK] on stdout, and
the template's own prints appeared before FreeCAD's banner. The difference
between the two invocations is not established.

The fix is therefore structural rather than diagnostic: the verdict no longer
depends on that channel, so the wrapper is correct whether or not the channel
happens to be alive today. A channel that is intermittently dead is still
not something a drawing pipeline may depend on.

VERDICT PRIORITY (artifact-first; this is the reversed priority)
---------------------------------------------------------------
  1. timeout                        -> failed
  2. exit code != 0                 -> failed
  3. expected artifact missing/0 B  -> failed  (UNCONDITIONAL: no artifact =
                                        failure; "success without output"
                                        means a silently lost drawing)
  4. verdict JSON record            -> authoritative
        present & status == "ok"    -> ok      (evidence: artifact-verdict-json)
        present & status != "ok"    -> failed  (reason taken from the record)
        required but absent         -> failed  (verdict-record-missing)
  5. no verdict record configured (legacy/marker-only mode), stdout marker:
        fail  -> failed
        ok    -> ok      (evidence: stdout-marker)
        none  -> unknown (evidence: none) - never silently promoted

The third state is preserved. "No marker" is NOT promoted to success and NOT
promoted to failure. What replaces it is a positive, artifact-based proof:
status "ok" with evidence "artifact-verdict-json" means the run wrote a UTF-8
JSON verdict record naming its own success, object count and layer list, and
the wrapper parsed that file. A missing marker merely means stdout carried
nothing this time.

LIVE PROOF (freecadcmd 1.1.3, this host, fixture wall_door_e2e.dxf):
  * a script that raised wrote status="failed" and the wrapper reported
    'failed' with reason "RuntimeError: shape is invalid" DESPITE exit
    code 0 - the case the old marker-first logic adjudicated as 'unknown'.
  * the shipped template reported ok / evidence=artifact-verdict-json.
  * the DXF opened to 19 objects: 11 Part::Feature (Shape..Shape010) plus
    LayerContainer and 7 App::FeaturePython layers labelled 0, Defpoints,
    CEN1, WAL1, WAL2, DOOR, DOOR_ELE.
  * markers were observed on stdout in these runs (see the note above).

FORCED MECHANISMS (markers are abandoned, not just demoted)
-----------------------------------------------------------
  (a) PRIMARY - verdict record artifact. The script writes UTF-8 JSON to
      VERDICT_NAME. The wrapper parses that file. No stdout involved.
  (b) SECONDARY - file-handle / timing detection. Before launch the wrapper
      snapshots the verdict file's existence and mtime; afterwards it requires
      the file to be NEW or REWRITTEN during the run, so a stale record from an
      earlier run can never prove a fresh success.
  (c) TERTIARY - --log-file token scan. A unique per-run token is injected into
      the prepared copy and echoed through FreeCAD.Console (which reaches the
      log file even when stdout is dead), and the wrapper greps the log for it.

ENTRYPOINT-GUARD DEFENCE (unchanged, still valid)
-------------------------------------------------
FreeCAD imports the .py argument rather than running it as __main__, so a
conventional `if __name__ == "__main__":` body is neutralised by an AST
rewrite into a temp copy. The original file on disk is never modified. The
prepared copy is where the (b)/(c) header and footer are injected.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import signal
import subprocess
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- markers ---
# Kept for backwards compatibility and as an AUXILIARY signal only.
MARKER_BEGIN = "[AIC-BEGIN]"
MARKER_OK = "[AIC-OK]"
MARKER_FAIL = "[AIC-FAIL:"
MARKER_FAIL_RE = re.compile(r"\[AIC-FAIL:([^\]\r\n]*)\]")

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"

# ------------------------------------------------------- verdict contract ---
VERDICT_SCHEMA = "aic.verdict/1"
VERDICT_NAME = "aic_verdict.json"
VERDICT_OK = "ok"
VERDICT_FAILED = "failed"

# evidence labels recorded in the result
EV_VERDICT = "artifact-verdict-json"     # proven by the JSON verdict artifact
EV_MARKER = "stdout-marker"              # auxiliary only
EV_NONE = "none"

DEFAULT_TIMEOUT = 300.0


# ------------------------------------------------------------------ result --
@dataclass
class ArtifactReport:
    path: str
    exists: bool = False
    size: int = -1
    sha256: str | None = None
    size_ok: bool | None = None
    hash_ok: bool | None = None
    note: str = ""


@dataclass
class FreeRunResult:
    status: str                     # ok | failed | unknown
    evidence: str                   # artifact-verdict-json | stdout-marker | none
    exit_code: int | None
    marker: str                     # ok | fail | unknown  (AUXILIARY)
    marker_reason: str | None
    has_markers: bool
    verdict: dict | None = None      # parsed JSON verdict record
    verdict_path: str | None = None
    verdict_fresh: bool | None = None   # mechanism (b): written during this run
    log_token_seen: bool = False       # mechanism (c)
    artifacts: list[ArtifactReport] = field(default_factory=list)
    artifacts_ok: bool = False
    stdout: str = ""
    stderr: str = ""
    log_file: str | None = None
    timed_out: bool = False
    duration_s: float = 0.0
    command: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    run_token: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "evidence": self.evidence,
            "ok": self.ok,
            "run_token": self.run_token,
            "exit_code": self.exit_code,
            "marker": self.marker,
            "marker_reason": self.marker_reason,
            "has_markers": self.has_markers,
            "verdict_path": self.verdict_path,
            "verdict_fresh": self.verdict_fresh,
            "log_token_seen": self.log_token_seen,
            "verdict": self.verdict,
            "artifacts_ok": self.artifacts_ok,
            "artifacts": [
                {
                    "path": a.path,
                    "exists": a.exists,
                    "size": a.size,
                    "size_ok": a.size_ok,
                    "hash_ok": a.hash_ok,
                    "note": a.note,
                }
                for a in self.artifacts
            ],
            "timed_out": self.timed_out,
            "duration_s": round(self.duration_s, 3),
            "log_file": self.log_file,
            "command": self.command,
            "reasons": self.reasons,
        }


# ------------------------------------------------------------ verdict I/O ---
def write_verdict(
    path: str | os.PathLike,
    status: str,
    reason: str = "",
    run_token: str = "",
    script: str = "",
    objects: int | None = None,
    layers: Sequence[str] | None = None,
    artifacts: Sequence[dict] | None = None,
    extra: dict | None = None,
) -> Path:
    """Write the authoritative UTF-8 JSON verdict record.

    Called by the headless script on BOTH the success and the failure path.
    Atomic-ish: write to a temp sibling then replace, so a reader never sees a
    half-written record.
    """
    if status not in (VERDICT_OK, VERDICT_FAILED):
        raise ValueError(
            f"verdict status must be {VERDICT_OK!r}/{VERDICT_FAILED!r}, got {status!r}"
        )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "schema": VERDICT_SCHEMA,
        "status": status,
        "reason": reason,
        "run_token": run_token,
        "script": script,
        "objects": objects,
        "layers": list(layers or []),
        "artifacts": list(artifacts or []),
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if extra:
        rec.update(extra)
    tmp = p.with_suffix(p.suffix + ".part")
    tmp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return p


def read_verdict(path: str | os.PathLike) -> tuple[dict | None, str]:
    """Return (record, error). record is None when the file is missing or is
    not parseable JSON; error describes which."""
    p = Path(path)
    if not p.is_file():
        return None, "verdict-record-missing"
    text = p.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return None, "verdict-record-empty"
    try:
        rec = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"verdict-record-unparseable: {exc}"
    if not isinstance(rec, dict):
        return None, "verdict-record-not-an-object"
    return rec, ""


# ------------------------------------------------------- entrypoint guard ---
def neutralize_main_guard(source: str, marker: str) -> tuple[str, str]:
    """Return (rewritten_source, note) where the __main__ guard is neutralised."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # pragma: no cover - defensive
        return source, f"ast-parse-failed: {exc}"

    if not any(
        isinstance(n, ast.If) and _is_main_test(n.test) for n in tree.body
    ):
        return source, "no-main-guard-found"

    lines = source.splitlines()
    drop: set[int] = set()
    out: list[str] = []

    for node in tree.body:
        if not (isinstance(node, ast.If) and _is_main_test(node.test)):
            continue
        drop.update(range(node.lineno - 1, node.end_lineno))
        body = [n for n in node.body if not isinstance(n, ast.Pass)]
        if not body:
            continue
        base = min((n.col_offset for n in body), default=0)
        out.append(f"# {marker}: __main__ guard neutralised (was lines "
                   f"{node.lineno}-{node.end_lineno})")
        for n in body:
            for i in range(n.lineno - 1, n.end_lineno):
                text = lines[i]
                out.append(text[base:] if text[:base].strip() == "" else text.lstrip())

    for i, text in enumerate(lines):
        if i not in drop:
            out.append(text)

    return "\n".join(out) + "\n", "guard-neutralised"


def _is_main_test(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare):
        return False
    left = test.left
    if not (isinstance(left, ast.Name) and left.id == "__name__"):
        return False
    return any(
        isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators
    )


# -------------------------------------------- injected header / footer code --
# (b) start-of-run detection + (c) log-file token emission.
_HEADER_TPL = '''# --- AIC-RUN-HEADER (injected by freecad_runner) ---
_AIC_TOKEN = {token!r}
try:
    import FreeCAD as _aic_FreeCAD          # noqa: F401
    _aic_FreeCAD.Console.PrintMessage(
        "[AIC-BEGIN] " + _AIC_TOKEN + "\\n")
except Exception:
    print("[AIC-BEGIN] " + _AIC_TOKEN, flush=True)
'''

# End-of-run detection only. Reaching this line proves the body did not exit
# early; it does NOT prove success - only the JSON verdict record does that.
_FOOTER_TPL = '''
# --- AIC-RUN-FOOTER (injected by freecad_runner) ---
try:
    import FreeCAD as _aic_FreeCAD
    _aic_FreeCAD.Console.PrintMessage(
        "[AIC-END] " + _AIC_TOKEN + "\\n")
except Exception:
    print("[AIC-END] " + _AIC_TOKEN, flush=True)
'''


def _header_insert_lineno(source: str) -> int:
    """0-based line index at which the injected header may be spliced in.

    It must go AFTER the module docstring and AFTER any `from __future__`
    import, otherwise the prepared copy is a SyntaxError. Observed on this
    host: FreeCAD swallowed that SyntaxError, exited 0, produced no artifact
    and printed nothing - exactly the silent-drawing-loss the wrapper exists
    to catch, so the fix is asserted by a test.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    idx = 0
    body = list(tree.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        idx = body[0].end_lineno or body[0].lineno
        body = body[1:]
    for node in body:
        if (isinstance(node, ast.ImportFrom)
                and node.module == "__future__"):
            idx = max(idx, node.end_lineno or node.lineno)
    return idx


def prepare_script(
    script_path: str | os.PathLike,
    workdir: str | os.PathLike | None = None,
    run_token: str = "",
) -> tuple[Path, str]:
    """Write a guarded, runnable copy of the script. Original is never modified.

    The copy also carries the injected header/footer used for start/end and
    log-token detection.
    """
    src = Path(script_path)
    text = src.read_text(encoding="utf-8", errors="replace")
    rewritten, note = neutralize_main_guard(text, "AIC-GUARD")
    lines = rewritten.splitlines()
    at = _header_insert_lineno(rewritten)
    header = _HEADER_TPL.format(token=run_token).splitlines()
    body = "\n".join(lines[:at] + header + lines[at:])
    out_dir = Path(workdir) if workdir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{src.stem}__aic_flat{src.suffix or '.py'}"
    dst.write_text(body + _FOOTER_TPL, encoding="utf-8")
    # A prepared copy that cannot be parsed is a guaranteed silent no-op, so
    # refuse to hand one to FreeCAD.
    ast.parse(dst.read_text(encoding="utf-8"))
    return dst, note


# ----------------------------------------------------------------- markers --
def parse_markers(stdout: str) -> tuple[str, bool, str | None]:
    """AUXILIARY channel only. See module docstring.

    kind: 'ok' | 'fail' | 'unknown'.
    """
    if MARKER_BEGIN not in stdout:
        return "unknown", False, None
    m = MARKER_FAIL_RE.search(stdout)
    if m:
        return "fail", True, (m.group(1).strip() or "unspecified")
    if MARKER_OK in stdout:
        return "ok", True, None
    return "unknown", True, None


# --------------------------------------------------------------- artifacts --
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_artifacts(
    expect_artifacts: Sequence[str | os.PathLike] | None,
    min_size: int = 1,
    hashes: dict[str, str] | None = None,
    base: str | os.PathLike | None = None,
) -> tuple[list[ArtifactReport], bool]:
    if not expect_artifacts:
        return [], True
    hashes = hashes or {}
    base_dir = Path(base) if base else None
    reports: list[ArtifactReport] = []
    all_ok = True
    for item in expect_artifacts:
        p = Path(item)
        if base_dir is not None and not p.is_absolute():
            p = base_dir / p
        rep = ArtifactReport(path=str(p))
        if not p.is_file():
            rep.note = "missing"
            all_ok = False
        else:
            rep.exists = True
            rep.size = p.stat().st_size
            rep.size_ok = rep.size >= min_size
            if not rep.size_ok:
                rep.note = f"below-min-size({min_size})"
                all_ok = False
            want = hashes.get(str(item)) or hashes.get(str(p))
            if want:
                rep.sha256 = _sha256(p)
                rep.hash_ok = rep.sha256.lower() == want.lower()
                if not rep.hash_ok:
                    rep.note = "sha256-mismatch"
                    all_ok = False
        reports.append(rep)
    return reports, all_ok


# ------------------------------------------------------------------- runner --
PopenFactory = object  # typing alias placeholder: Callable[..., subprocess.Popen]


def run_freecad_script(
    script_path: str | os.PathLike,
    freecad_exe: str | os.PathLike | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    markers: Iterable[str] | None = None,
    expect_artifacts: Sequence[str | os.PathLike] | None = None,
    min_size: int = 1,
    artifact_hashes: dict[str, str] | None = None,
    workdir: str | os.PathLike | None = None,
    extra_args: Sequence[str] = (),
    env: dict | None = None,
    keep_prepared: bool = True,
    popen: PopenFactory | None = None,
    verdict_path: str | os.PathLike | None = None,
    run_token: str = "",
) -> FreeRunResult:
    """Run a .py through headless FreeCAD and apply the artifact-first verdict.

    verdict_path: the JSON verdict record the script is REQUIRED to write. When
                  given, the record is authoritative: absent or unparseable
                  means 'failed' even if the exit code is 0 and no marker
                  objected. When None, the wrapper falls back to the auxiliary
                  stdout-marker channel and may report 'unknown'.
    """
    started = time.monotonic()
    exe = Path(freecad_exe or os.environ.get("FREECAD_EXE", "FreeCADCmd.exe"))
    work = Path(workdir) if workdir else Path(script_path).resolve().parent
    work.mkdir(parents=True, exist_ok=True)
    token = run_token or f"AIC-RUN-{uuid.uuid4().hex[:12]}"
    try:
        prepared, guard_note = prepare_script(script_path, work, run_token=token)
    except SyntaxError as exc:
        # prepare_script refuses to emit an unparseable copy: FreeCAD would
        # swallow the SyntaxError, exit 0 and leave no artifact.
        return FreeRunResult(
            status=STATUS_FAILED, evidence=EV_NONE, exit_code=None, marker="unknown",
            marker_reason=None, has_markers=False, duration_s=time.monotonic() - started,
            command=[str(exe), str(script_path)], run_token=token,
            reasons=[f"prepared-script-not-parseable: {exc}"],
        )

    verdict_file = Path(verdict_path) if verdict_path else None
    if verdict_file is not None and not verdict_file.is_absolute():
        verdict_file = work / verdict_file

    # (b) timing baseline: a stale record from a previous run must not count.
    pre_exists = bool(verdict_file and verdict_file.is_file())
    pre_mtime = verdict_file.stat().st_mtime_ns if pre_exists else None

    log_file = work / f"{prepared.stem}.FreeCAD.log"
    cmd = [str(exe), "--log-file", str(log_file), str(prepared), *extra_args]

    child_env = dict(os.environ)
    if env:
        child_env.update(env)

    timed_out = False
    stdout = stderr = ""
    code: int | None = None
    try:
        proc = (popen or subprocess.Popen)(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=child_env,
            cwd=str(work),
        )
    except FileNotFoundError as exc:
        return FreeRunResult(
            status=STATUS_FAILED, evidence=EV_NONE, exit_code=None, marker="unknown",
            marker_reason=None, has_markers=False, duration_s=time.monotonic() - started,
            command=cmd, run_token=token,
            reasons=[f"freecad-executable-not-found: {exc}"],
        )

    try:
        out, err = proc.communicate(timeout=timeout)
        stdout, stderr, code = out or "", err or "", proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)                       # no orphan children
        try:
            out, err = proc.communicate(timeout=15)
        except Exception:                       # pragma: no cover
            out, err = "", ""
        stdout, stderr, code = out or "", err or "", proc.returncode

    duration = time.monotonic() - started

    # ---- mechanism (b): did the verdict file appear / change during the run? --
    verdict_fresh: bool | None = None
    verdict: dict | None = None
    verdict_err = ""
    if verdict_file is not None:
        exists_now = verdict_file.is_file()
        verdict_fresh = exists_now and (
            not pre_exists or verdict_file.stat().st_mtime_ns != pre_mtime
        )
        if not exists_now:
            verdict_err = "verdict-record-missing"
        elif not verdict_fresh:
            verdict_err = "verdict-record-stale (not rewritten by this run)"
        else:
            verdict, verdict_err = read_verdict(verdict_file)

    # ---- auxiliary channels -------------------------------------------------
    marker_kind, has_markers, reason = parse_markers(stdout)
    missing_required = [m for m in (markers or []) if m not in stdout]
    if missing_required:
        marker_kind, has_markers = "fail", True
        reason = "missing-required-marker: " + ", ".join(missing_required)

    # (c) log-file token scan
    log_token_seen = False
    if log_file.is_file():
        try:
            log_token_seen = token in log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover
            log_token_seen = False

    arts, arts_ok = check_artifacts(expect_artifacts, min_size, artifact_hashes, base=work)

    reasons: list[str] = [f"entrypoint: {guard_note}", f"run-token: {token}"]
    if timed_out:
        reasons.append(f"timeout after {timeout}s (process tree killed)")
    if not has_markers:
        reasons.append(
            "stdout carried no protocol marker (expected on this host: the "
            "marker channel is known-dead; verdict is taken from artifacts)"
        )
    if log_token_seen:
        reasons.append("run token found in FreeCAD log file (start/end observed)")

    # ---------------- artifact-first verdict, explicit ----------------------
    evidence = EV_NONE
    if timed_out:
        status = STATUS_FAILED
        reasons.append("timed out")
    elif code not in (0, None):
        status = STATUS_FAILED
        reasons.append(f"non-zero exit code {code}")
    elif not arts_ok:
        # UNCONDITIONAL: no usable artifact is always a failure.
        status = STATUS_FAILED
        bad = [a.path for a in arts if not (a.exists and a.size_ok)]
        reasons.append("artifact check failed: " + ", ".join(bad))
    elif verdict_file is not None:
        if verdict_err:
            status = STATUS_FAILED
            reasons.append(f"verdict record unusable: {verdict_err}")
        else:
            vstatus = str(verdict.get("status", "")).strip().lower()
            if vstatus == VERDICT_OK:
                status = STATUS_OK
                evidence = EV_VERDICT
                reasons.append(
                    f"proven by artifact: verdict record status=ok "
                    f"(objects={verdict.get('objects')}, layers={verdict.get('layers')})"
                )
            else:
                status = STATUS_FAILED
                vr = verdict.get("reason") or "unspecified"
                reasons.append(f"verdict record reported failure: {vr}")
                reason = str(vr)
    elif marker_kind == "fail":
        status = STATUS_FAILED
        reasons.append(f"script reported failure: {reason}")
    elif marker_kind == "ok":
        status = STATUS_OK
        evidence = EV_MARKER
    else:
        # No verdict record was configured and stdout is dead: evidence is
        # incomplete -> 'unknown'. Deliberately neither ok nor failed.
        status = STATUS_UNKNOWN
        reasons.append(
            "no verdict record configured, no marker on stdout, artifacts present: "
            "verdict is missing evidence. Pass verdict_path=... and have the script "
            "write the JSON record; do not treat as success."
        )

    if not keep_prepared and prepared.exists() and status == STATUS_OK:
        prepared.unlink(missing_ok=True)

    return FreeRunResult(
        status=status, evidence=evidence, exit_code=code, marker=marker_kind,
        marker_reason=reason, has_markers=has_markers, verdict=verdict,
        verdict_path=str(verdict_file) if verdict_file else None,
        verdict_fresh=verdict_fresh, log_token_seen=log_token_seen,
        artifacts=arts, artifacts_ok=arts_ok, stdout=stdout, stderr=stderr,
        log_file=str(log_file) if log_file.exists() else None, timed_out=timed_out,
        duration_s=duration, command=cmd, reasons=reasons, run_token=token,
    )


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate the child and its descendants (Windows: taskkill /T /F)."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, AttributeError):
            pass
    try:
        proc.kill()
    except Exception:  # pragma: no cover
        pass


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    ap = argparse.ArgumentParser(description="Headless FreeCAD wrapper (artifact-first)")
    ap.add_argument("script")
    ap.add_argument("--freecad", default=None)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--expect-artifact", action="append", default=[])
    ap.add_argument("--expect-sha256", action="append", default=[])
    ap.add_argument("--verdict", default=None,
                    help="JSON verdict record the script MUST write")
    ap.add_argument("--workdir", default=None)
    a = ap.parse_args(argv)
    hashes = {}
    for item in a.expect_sha256:
        k, _, v = item.partition("=")
        hashes[k] = v
    res = run_freecad_script(
        a.script, freecad_exe=a.freecad, timeout=a.timeout,
        expect_artifacts=a.expect_artifact or None, artifact_hashes=hashes or None,
        workdir=a.workdir, verdict_path=a.verdict,
    )
    print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
    if res.status == STATUS_OK:
        return 0
    if res.status == STATUS_FAILED:
        return 1
    return 2  # unknown: distinct, never silently zero


if __name__ == "__main__":
    raise SystemExit(main())
