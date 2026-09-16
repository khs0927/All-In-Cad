from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PYRX_READ_BASELINE = frozenset(
    {
        "document.database.read",
        "document.modelspace.read",
        "entity.line.type",
        "entity.polyline.type",
        "entity.block_reference.type",
        "entity.text.type",
        "entity.mtext.type",
        "entity.dimension.type",
        "table.layer.type",
    }
)


class CapabilityState(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    ERROR = "error"


class CapabilityEvidence(StrEnum):
    OBSERVED = "observed"
    DECLARED = "declared"
    INFERRED = "inferred"


class CapabilityEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    capability: str
    state: CapabilityState
    evidence: CapabilityEvidence = CapabilityEvidence.OBSERVED
    detail: str | None = None


class CapabilityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    adapter: str
    host: str
    host_version: str | None = None
    schema: str = "all-in-cad/capability-report/v1"
    source_schema: str | None = None
    entries: tuple[CapabilityEntry, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    def state(self, capability: str) -> CapabilityState:
        for entry in self.entries:
            if entry.capability == capability:
                return entry.state
        return CapabilityState.UNKNOWN


class CapabilityMatrixRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    capability: str
    states: dict[str, CapabilityState]
    common_supported: bool


class CapabilityMatrix(BaseModel):
    model_config = ConfigDict(frozen=True)

    columns: tuple[str, ...]
    rows: tuple[CapabilityMatrixRow, ...]


def normalize_probe_payload(payload: dict[str, Any]) -> CapabilityReport:
    schema = str(payload.get("schema") or "")
    if schema == "all-in-cad/pyrx-probe/v1" or "db_types" in payload:
        return normalize_pyrx_report(payload)
    if payload.get("adapter") == "acadsharp" or "reader" in payload:
        return normalize_acadsharp_capabilities(payload)
    raise ValueError("unrecognized capability probe payload")


def normalize_pyrx_report(payload: dict[str, Any]) -> CapabilityReport:
    schema = str(payload.get("schema") or "unknown")
    host_label = str(payload.get("host_label") or "unknown")
    entries: list[CapabilityEntry] = []

    _append_observation(
        entries,
        "document.database.read",
        _ok_state(payload.get("current_database")),
    )
    _append_observation(
        entries,
        "document.modelspace.read",
        _ok_state(payload.get("model_space")),
    )
    _append_observation(
        entries,
        "entity.block_reference.scan",
        _ok_state(payload.get("block_reference_scan")),
    )

    type_capabilities = {
        "Line": "entity.line.type",
        "Circle": "entity.circle.type",
        "Arc": "entity.arc.type",
        "Polyline": "entity.polyline.type",
        "BlockReference": "entity.block_reference.type",
        "DBText": "entity.text.type",
        "MText": "entity.mtext.type",
        "Dimension": "entity.dimension.type",
        "Hatch": "entity.hatch.type",
        "LayerTable": "table.layer.type",
    }
    db_types = payload.get("db_types")
    if isinstance(db_types, dict):
        for type_name, capability in type_capabilities.items():
            present = db_types.get(type_name)
            state = (
                CapabilityState.SUPPORTED
                if present is True
                else CapabilityState.UNSUPPORTED
                if present is False
                else CapabilityState.UNKNOWN
            )
            _append_observation(entries, capability, state)

    return CapabilityReport(
        adapter="pyrx",
        host=host_label,
        source_schema=schema,
        entries=tuple(sorted(entries, key=lambda item: item.capability)),
        metadata={
            "python": payload.get("python"),
            "platform": payload.get("platform"),
        },
    )


def normalize_acadsharp_capabilities(payload: dict[str, Any]) -> CapabilityReport:
    has_reader = bool(payload.get("reader"))
    entries = (
        CapabilityEntry(
            capability="file.dwg.read",
            state=CapabilityState.SUPPORTED if has_reader else CapabilityState.ERROR,
            evidence=CapabilityEvidence.DECLARED,
            detail=str(payload.get("reader")) if payload.get("reader") else None,
        ),
        CapabilityEntry(
            capability="entity.census.read",
            state=CapabilityState.SUPPORTED if has_reader else CapabilityState.ERROR,
            evidence=CapabilityEvidence.DECLARED,
            detail=str(payload.get("output")) if payload.get("output") else None,
        ),
        CapabilityEntry(
            capability="entity.geometry.normalized",
            state=CapabilityState.UNKNOWN,
            evidence=CapabilityEvidence.DECLARED,
            detail="capability probe proves reader surface, not geometry fidelity",
        ),
    )
    return CapabilityReport(
        adapter=str(payload.get("adapter") or "acadsharp"),
        host="headless",
        source_schema=str(payload.get("output") or "unknown"),
        entries=entries,
        metadata={"assembly": payload.get("assembly")},
    )


def build_capability_matrix(reports: list[CapabilityReport]) -> CapabilityMatrix:
    columns = tuple(_report_key(report) for report in reports)
    if len(set(columns)) != len(columns):
        raise ValueError("capability reports must have unique adapter/host keys")

    capabilities = sorted(
        {
            entry.capability
            for report in reports
            for entry in report.entries
        }
    )
    rows: list[CapabilityMatrixRow] = []
    for capability in capabilities:
        states = {
            key: report.state(capability)
            for key, report in zip(columns, reports, strict=True)
        }
        rows.append(
            CapabilityMatrixRow(
                capability=capability,
                states=states,
                common_supported=bool(states)
                and all(state == CapabilityState.SUPPORTED for state in states.values()),
            )
        )
    return CapabilityMatrix(columns=columns, rows=tuple(rows))


def capability_gaps(
    report: CapabilityReport,
    required: set[str] | frozenset[str],
) -> dict[str, CapabilityState]:
    return {
        capability: report.state(capability)
        for capability in sorted(required)
        if report.state(capability) != CapabilityState.SUPPORTED
    }


def _append_observation(
    entries: list[CapabilityEntry],
    capability: str,
    state: CapabilityState,
) -> None:
    entries.append(
        CapabilityEntry(
            capability=capability,
            state=state,
            evidence=CapabilityEvidence.OBSERVED,
        )
    )


def _ok_state(value: object) -> CapabilityState:
    if not isinstance(value, dict):
        return CapabilityState.UNKNOWN
    ok = value.get("ok")
    if ok is True:
        return CapabilityState.SUPPORTED
    if ok is False:
        return CapabilityState.ERROR
    return CapabilityState.UNKNOWN


def _report_key(report: CapabilityReport) -> str:
    return f"{report.adapter}@{report.host}"
