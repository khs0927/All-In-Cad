from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .architecture import (
    infer_annotation_bindings,
    infer_opening_hosts,
    infer_room_adjacency,
    infer_rooms,
)
from .extraction import ExtractionLane, ToolProbe
from .extraction_runtime import extract_dwg, normalize_dxf
from .inventory import FileKind, FileRecord, InventoryManifest
from .project_index import IndexResult, ProjectIndex, SemanticIndexResult
from .readback import EntitySnapshot
from .semantic_graph import build_semantic_graph


@dataclass(frozen=True, slots=True)
class PipelineItemResult:
    path: str
    lane: ExtractionLane
    snapshot_digest: str
    index: IndexResult
    semantics: SemanticIndexResult
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PipelineFailure:
    path: str
    error: str


@dataclass(frozen=True, slots=True)
class PipelineBatchResult:
    processed: tuple[PipelineItemResult, ...]
    skipped: tuple[str, ...]
    ignored: tuple[str, ...]
    failures: tuple[PipelineFailure, ...]


def process_record(
    record: FileRecord,
    *,
    probes: dict[ExtractionLane, ToolProbe],
    workdir: str | Path,
    index: ProjectIndex,
) -> PipelineItemResult:
    if record.kind == FileKind.PDF:
        raise ValueError("PDF records are evidence sidecars, not CAD semantic inputs")

    workspace = _record_workspace(workdir, record)
    if record.kind == FileKind.DWG:
        extraction = extract_dwg(
            record.path,
            probes,
            workspace,
            document_id=record.relative_path,
        )
    elif record.kind == FileKind.DXF:
        extraction = normalize_dxf(record.path, document_id=record.relative_path)
    else:  # pragma: no cover - FileKind is currently exhaustive
        raise ValueError(f"unsupported CAD input kind: {record.kind}")

    snapshots = list(extraction.snapshots)
    graph = build_semantic_graph(snapshots)
    rooms = infer_rooms(snapshots)
    opening_hosts = infer_opening_hosts(snapshots)
    room_adjacencies = infer_room_adjacency(rooms)
    annotation_bindings = infer_annotation_bindings(snapshots)
    digest = snapshot_digest(snapshots)

    index_result = index.index_drawing(record, snapshots)
    semantic_result = index.replace_semantics(
        index_result.drawing_id,
        graph,
        rooms=rooms,
        opening_hosts=opening_hosts,
        room_adjacencies=room_adjacencies,
        annotation_bindings=annotation_bindings,
        extraction_lane=extraction.lane.value,
        snapshot_digest=digest,
        warnings=extraction.warnings,
        intermediate_path=extraction.intermediate_path,
    )

    return PipelineItemResult(
        path=record.path,
        lane=extraction.lane,
        snapshot_digest=digest,
        index=index_result,
        semantics=semantic_result,
        warnings=extraction.warnings,
    )


def process_manifest(
    manifest: InventoryManifest,
    *,
    probes: dict[ExtractionLane, ToolProbe],
    workdir: str | Path,
    index: ProjectIndex,
    continue_on_error: bool = True,
) -> PipelineBatchResult:
    changed_paths = {record.path for record in index.changed_records(manifest)}
    processed: list[PipelineItemResult] = []
    skipped: list[str] = []
    ignored: list[str] = []
    failures: list[PipelineFailure] = []

    for record in manifest.records:
        if record.kind == FileKind.PDF:
            ignored.append(record.path)
            continue
        if record.path not in changed_paths:
            skipped.append(record.path)
            continue
        try:
            processed.append(
                process_record(
                    record,
                    probes=probes,
                    workdir=workdir,
                    index=index,
                )
            )
        except Exception as exc:
            if not continue_on_error:
                raise
            failures.append(PipelineFailure(path=record.path, error=str(exc)))

    return PipelineBatchResult(
        processed=tuple(processed),
        skipped=tuple(skipped),
        ignored=tuple(ignored),
        failures=tuple(failures),
    )


def snapshot_digest(snapshots: list[EntitySnapshot]) -> str:
    payload = [
        {"handle": item.handle.upper(), "digest": item.digest()}
        for item in sorted(snapshots, key=lambda entity: entity.handle.upper())
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_workspace(workdir: str | Path, record: FileRecord) -> Path:
    root = Path(workdir).resolve()
    key = hashlib.sha256(record.path.lower().encode("utf-8")).hexdigest()[:16]
    workspace = root / key
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace
