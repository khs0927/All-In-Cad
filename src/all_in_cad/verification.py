from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .models import VerificationFinding, VerificationReport


@dataclass(frozen=True)
class DrawingEvidence:
    source: str
    document_id: str
    revision: int
    entity_count: int
    layer_counts: Mapping[str, int]
    geometry_digest: str | None = None


def cross_check_evidence(
    evidences: list[DrawingEvidence],
    *,
    require_digest_match: bool = True,
) -> VerificationReport:
    if len(evidences) < 2:
        raise ValueError("at least two evidence sources are required")

    first = evidences[0]
    findings: list[VerificationFinding] = []
    for evidence in evidences[1:]:
        if evidence.document_id != first.document_id:
            findings.append(VerificationFinding(code="DOCUMENT_ID_MISMATCH", severity="error", source=evidence.source, message=f"{evidence.document_id} != {first.document_id}"))
        if evidence.entity_count != first.entity_count:
            findings.append(VerificationFinding(code="ENTITY_COUNT_MISMATCH", severity="error", source=evidence.source, message=f"{evidence.entity_count} != {first.entity_count}"))
        if dict(evidence.layer_counts) != dict(first.layer_counts):
            findings.append(VerificationFinding(code="LAYER_CENSUS_MISMATCH", severity="error", source=evidence.source, message="layer entity census differs from baseline"))
        if require_digest_match and first.geometry_digest and evidence.geometry_digest and first.geometry_digest != evidence.geometry_digest:
            findings.append(VerificationFinding(code="GEOMETRY_DIGEST_MISMATCH", severity="error", source=evidence.source, message="handle-independent geometry digest differs from baseline"))

    return VerificationReport(
        document_id=first.document_id,
        revision=max(item.revision for item in evidences),
        passed=not any(f.severity == "error" for f in findings),
        sources=[item.source for item in evidences],
        findings=findings,
        evidence={"entity_count": first.entity_count, "layer_counts": dict(first.layer_counts), "geometry_digest": first.geometry_digest},
    )
