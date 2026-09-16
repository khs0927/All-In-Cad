from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .evidence import EvidenceManifest, compare_extraction_runs
from .extraction import ExtractionLane, ToolProbe
from .extraction_runtime import (
    ExtractionRun,
    convert_with_libredwg,
    convert_with_oda,
    normalize_dxf,
    read_with_acadsharp,
)

DWG_VERIFICATION_LANES = (
    ExtractionLane.ACADSHARP,
    ExtractionLane.ODA,
    ExtractionLane.LIBREDWG,
)


def available_verification_lanes(
    probes: dict[ExtractionLane, ToolProbe],
) -> tuple[ExtractionLane, ...]:
    available: list[ExtractionLane] = []
    for lane in DWG_VERIFICATION_LANES:
        probe = probes.get(lane)
        if probe is None or not probe.available:
            continue
        if lane in {ExtractionLane.ACADSHARP, ExtractionLane.LIBREDWG} and not probe.executable:
            continue
        available.append(lane)
    return tuple(available)


def extract_dwg_with_lane(
    source: str | Path,
    lane: ExtractionLane,
    probes: dict[ExtractionLane, ToolProbe],
    workdir: str | Path,
    *,
    document_id: str | None = None,
) -> ExtractionRun:
    if lane not in DWG_VERIFICATION_LANES:
        raise ValueError(f"unsupported forced DWG lane: {lane.value}")

    probe = probes.get(lane)
    if probe is None or not probe.available:
        raise RuntimeError(f"requested extraction lane is unavailable: {lane.value}")

    source_path = Path(source).resolve()
    workspace = Path(workdir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    doc_id = document_id or source_path.name

    if lane == ExtractionLane.ACADSHARP:
        return read_with_acadsharp(probe, source_path, document_id=doc_id)

    intermediate = workspace / f"{source_path.stem}.{lane.value}.dxf"
    if lane == ExtractionLane.ODA:
        convert_with_oda(source_path, intermediate)
    else:
        if not probe.executable:
            raise RuntimeError("LibreDWG executable is required")
        convert_with_libredwg(probe.executable, source_path, intermediate)

    normalized = normalize_dxf(intermediate, document_id=doc_id)
    return ExtractionRun(
        source_path=str(source_path),
        lane=lane,
        snapshots=normalized.snapshots,
        warnings=normalized.warnings,
        intermediate_path=str(intermediate),
    )


def verify_dwg_across_lanes(
    source: str | Path,
    probes: dict[ExtractionLane, ToolProbe],
    workdir: str | Path,
    *,
    lanes: Iterable[ExtractionLane] | None = None,
    require_digest_match: bool = True,
) -> EvidenceManifest:
    selected = tuple(lanes) if lanes is not None else available_verification_lanes(probes)
    if len(selected) < 2:
        raise RuntimeError("cross-lane verification requires at least two available DWG lanes")
    if len(set(selected)) != len(selected):
        raise ValueError("cross-lane verification lanes must be unique")

    source_path = Path(source).resolve()
    runs = [
        extract_dwg_with_lane(
            source_path,
            lane,
            probes,
            workdir,
            document_id=source_path.name,
        )
        for lane in selected
    ]
    return compare_extraction_runs(runs, require_digest_match=require_digest_match)
