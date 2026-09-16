from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .extraction import ExtractionLane, ToolProbe
from .readback import EntitySnapshot


@dataclass(frozen=True, slots=True)
class ExtractionRun:
    source_path: str
    lane: ExtractionLane
    snapshots: tuple[EntitySnapshot, ...]
    warnings: tuple[str, ...] = ()
    intermediate_path: str | None = None


def normalize_dxf(path: str | Path, *, document_id: str | None = None) -> ExtractionRun:
    """Recover/audit a DXF and convert supported modelspace entities to stable snapshots."""
    try:
        from ezdxf import recover
    except ImportError as exc:  # pragma: no cover - packaging/configuration error
        raise RuntimeError("install All-In-Cad with the 'headless' extra") from exc

    source = Path(path).resolve()
    doc, auditor = recover.readfile(source)
    doc_id = document_id or source.name
    snapshots: list[EntitySnapshot] = []

    for index, entity in enumerate(doc.modelspace(), start=1):
        handle_value = getattr(entity.dxf, "handle", None)
        handle = str(handle_value or f"SYNTH-{index}").upper()
        layer = str(getattr(entity.dxf, "layer", "0"))
        geometry, properties = _normalize_ezdxf_entity(entity)
        snapshots.append(
            EntitySnapshot(
                document_id=doc_id,
                handle=handle,
                entity_type=entity.dxftype(),
                layer=layer,
                geometry=geometry,
                properties=properties,
            )
        )

    warnings = ("ezdxf auditor reported errors",) if auditor.has_errors else ()
    return ExtractionRun(
        source_path=str(source),
        lane=ExtractionLane.EZDXF,
        snapshots=tuple(snapshots),
        warnings=warnings,
    )


def convert_with_oda(
    source: str | Path,
    destination: str | Path,
    *,
    version: str = "R2018",
) -> Path:
    """Convert DWG to DXF through ezdxf's documented ODA File Converter adapter."""
    try:
        from ezdxf.addons import odafc
    except ImportError as exc:  # pragma: no cover - packaging/configuration error
        raise RuntimeError("install All-In-Cad with the 'headless' extra") from exc

    src = Path(source).resolve()
    dest = Path(destination).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    odafc.convert(src, dest, version=version, audit=True, replace=True)
    if not dest.exists():
        raise RuntimeError(f"ODA conversion did not create expected output: {dest}")
    return dest


def convert_with_libredwg(
    executable: str,
    source: str | Path,
    destination: str | Path,
    *,
    timeout_seconds: float = 120.0,
) -> Path:
    """Run GPL LibreDWG as a separate executable; no GPL code is imported or linked."""
    src = Path(source).resolve()
    dest = Path(destination).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [executable, "-y", "-o", str(dest), str(src)],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0 or not dest.exists():
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"LibreDWG conversion failed: {detail}")
    return dest


def read_with_acadsharp(
    probe: ToolProbe,
    source: str | Path,
    *,
    document_id: str | None = None,
    timeout_seconds: float = 120.0,
) -> ExtractionRun:
    """Invoke the separate AllInCad.ACadSharpProbe process and ingest its JSON census."""
    if probe.lane != ExtractionLane.ACADSHARP or not probe.available or not probe.executable:
        raise ValueError("an available ACadSharp ToolProbe with executable is required")

    src = Path(source).resolve()
    command = _dotnet_command(probe.executable, src)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"ACadSharp probe failed: {detail}")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ACadSharp probe returned invalid JSON") from exc

    doc_id = document_id or src.name
    snapshots = tuple(_snapshot_from_acadsharp(item, doc_id) for item in payload["entities"])
    return ExtractionRun(
        source_path=str(src),
        lane=ExtractionLane.ACADSHARP,
        snapshots=snapshots,
    )


def extract_dwg(
    source: str | Path,
    probes: dict[ExtractionLane, ToolProbe],
    workdir: str | Path,
    *,
    document_id: str | None = None,
) -> ExtractionRun:
    """Execute the deterministic DWG fallback order: ACadSharp -> ODA -> LibreDWG."""
    src = Path(source).resolve()
    workspace = Path(workdir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    acadsharp = probes.get(ExtractionLane.ACADSHARP)
    if acadsharp and acadsharp.available and acadsharp.executable:
        return read_with_acadsharp(acadsharp, src, document_id=document_id)

    intermediate = workspace / f"{src.stem}.normalized.dxf"
    oda = probes.get(ExtractionLane.ODA)
    if oda and oda.available:
        convert_with_oda(src, intermediate)
        result = normalize_dxf(intermediate, document_id=document_id or src.name)
        return ExtractionRun(
            source_path=str(src),
            lane=ExtractionLane.ODA,
            snapshots=result.snapshots,
            warnings=result.warnings,
            intermediate_path=str(intermediate),
        )

    libredwg = probes.get(ExtractionLane.LIBREDWG)
    if libredwg and libredwg.available and libredwg.executable:
        convert_with_libredwg(libredwg.executable, src, intermediate)
        result = normalize_dxf(intermediate, document_id=document_id or src.name)
        return ExtractionRun(
            source_path=str(src),
            lane=ExtractionLane.LIBREDWG,
            snapshots=result.snapshots,
            warnings=result.warnings,
            intermediate_path=str(intermediate),
        )

    raise RuntimeError("no executable DWG extraction lane is available")


def _dotnet_command(executable: str, source: Path) -> list[str]:
    path = Path(executable)
    if path.suffix.lower() == ".dll":
        return ["dotnet", str(path), str(source)]
    return [executable, str(source)]


def _snapshot_from_acadsharp(item: dict[str, Any], document_id: str) -> EntitySnapshot:
    return EntitySnapshot(
        document_id=document_id,
        handle=str(item["handle"]).upper(),
        entity_type=str(item["entity_type"]),
        layer=str(item.get("layer") or "0"),
        geometry=dict(item.get("geometry") or {}),
        properties=dict(item.get("properties") or {}),
    )


def _normalize_ezdxf_entity(entity: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    kind = entity.dxftype().upper()
    geometry: dict[str, Any] = {}
    properties: dict[str, Any] = {}

    if kind == "LINE":
        geometry = {
            "start": _point(entity.dxf.start),
            "end": _point(entity.dxf.end),
        }
    elif kind == "LWPOLYLINE":
        geometry = {
            "points": [
                [float(x), float(y), float(start_width), float(end_width), float(bulge)]
                for x, y, start_width, end_width, bulge in entity.get_points("xyseb")
            ],
            "closed": bool(entity.closed),
        }
    elif kind == "CIRCLE":
        geometry = {"center": _point(entity.dxf.center), "radius": float(entity.dxf.radius)}
    elif kind == "ARC":
        geometry = {
            "center": _point(entity.dxf.center),
            "radius": float(entity.dxf.radius),
            "start_angle": float(entity.dxf.start_angle),
            "end_angle": float(entity.dxf.end_angle),
        }
    elif kind in {"TEXT", "MTEXT"}:
        insert = getattr(entity.dxf, "insert", None)
        if insert is not None:
            geometry["insert"] = _point(insert)
        properties["text"] = entity.plain_text() if kind == "MTEXT" else str(entity.dxf.text)
    elif kind == "INSERT":
        geometry = {"insert": _point(entity.dxf.insert)}
        properties["block_name"] = str(entity.dxf.name)
    elif kind == "DIMENSION":
        properties["dimension_type"] = int(entity.dxf.dimtype)
        properties["text"] = str(getattr(entity.dxf, "text", ""))

    return geometry, properties


def _point(value: Any) -> list[float]:
    return [float(value[0]), float(value[1]), float(value[2] if len(value) > 2 else 0.0)]
