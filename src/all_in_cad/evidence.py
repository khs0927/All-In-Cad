from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from .extraction_runtime import ExtractionRun
from .models import VerificationReport
from .readback import EntitySnapshot
from .verification import DrawingEvidence, cross_check_evidence


class EvidenceSourceSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    lane: str
    entity_count: int
    layer_counts: dict[str, int]
    geometry_digest: str
    warnings: tuple[str, ...] = ()


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema: str = "all-in-cad/evidence-manifest/v1"
    document_id: str
    captured_at: str
    sources: tuple[EvidenceSourceSummary, ...]
    verification: VerificationReport


def evidence_from_snapshots(
    source: str,
    snapshots: Iterable[EntitySnapshot],
    *,
    document_id: str | None = None,
    revision: int = 0,
) -> DrawingEvidence:
    items = list(snapshots)
    resolved_document = _document_id(items, document_id)
    layers = Counter(item.layer for item in items)
    return DrawingEvidence(
        source=source,
        document_id=resolved_document,
        revision=revision,
        entity_count=len(items),
        layer_counts=dict(sorted(layers.items())),
        geometry_digest=handle_independent_geometry_digest(items),
    )


def evidence_from_run(run: ExtractionRun, *, revision: int = 0) -> DrawingEvidence:
    return evidence_from_snapshots(
        run.lane.value,
        run.snapshots,
        revision=revision,
    )


def compare_extraction_runs(
    runs: Iterable[ExtractionRun],
    *,
    require_digest_match: bool = True,
) -> EvidenceManifest:
    items = list(runs)
    if len(items) < 2:
        raise ValueError("at least two extraction runs are required")

    evidences = [evidence_from_run(run) for run in items]
    verification = cross_check_evidence(
        evidences,
        require_digest_match=require_digest_match,
    )
    summaries = tuple(
        EvidenceSourceSummary(
            source=run.source_path,
            lane=run.lane.value,
            entity_count=evidence.entity_count,
            layer_counts=dict(evidence.layer_counts),
            geometry_digest=str(evidence.geometry_digest),
            warnings=run.warnings,
        )
        for run, evidence in zip(items, evidences, strict=True)
    )
    return EvidenceManifest(
        document_id=evidences[0].document_id,
        captured_at=datetime.now(UTC).isoformat(),
        sources=summaries,
        verification=verification,
    )


def handle_independent_geometry_digest(snapshots: Iterable[EntitySnapshot]) -> str:
    normalized = [
        {
            "entity_type": item.entity_type.upper(),
            "layer": item.layer,
            "geometry": item.geometry,
        }
        for item in snapshots
    ]
    normalized.sort(key=_canonical_json)
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _document_id(items: list[EntitySnapshot], explicit: str | None) -> str:
    if explicit:
        return explicit
    ids = {item.document_id for item in items}
    if len(ids) == 1:
        return next(iter(ids))
    if not ids:
        return "unknown"
    raise ValueError("evidence snapshots must belong to exactly one document")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
