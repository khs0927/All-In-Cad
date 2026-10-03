from __future__ import annotations

from enum import StrEnum
import re
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HostKind(StrEnum):
    AUTOCAD = "autocad"
    ZWCAD = "zwcad"
    HEADLESS = "headless"


class AdapterChannel(StrEnum):
    AUTODESK_OFFICIAL_MCP = "autodesk_official_mcp"
    AUTOCAD_NATIVE = "autocad_native"
    ZWCAD_NATIVE = "zwcad_native"
    ZWCAD_LISP_FALLBACK = "zwcad_lisp_fallback"
    EZDXF = "ezdxf"


class SourceBindingRef(BaseModel):
    """Cross-repository provenance from Ontology SOURCE_BOUND to a native CAD executor."""

    model_config = ConfigDict(frozen=True, extra="allow")

    schema: Literal["aec-executor-handoff/1"] = "aec-executor-handoff/1"
    binding_state: Literal["SOURCE_BOUND"] = "SOURCE_BOUND"
    review_status: Literal["VERIFIED_FOR_REVIEW"] = "VERIFIED_FOR_REVIEW"
    document_id: str = Field(min_length=1)
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_byte_revision_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_revision_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    handoff_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolver_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_authorized: Literal[False] = False
    may_execute_mutation: Literal[False] = False
    requires_executor_authorization: Literal[True] = True


class DrawingGrammarRef(BaseModel):
    """Read-only local drafting grammar sampled from an existing CAD drawing."""

    model_config = ConfigDict(frozen=True, extra="allow")

    schema: Literal["cad-drawing-grammar/1"] = "cad-drawing-grammar/1"
    document: dict[str, Any] = Field(default_factory=dict)
    anchor: dict[str, Any] = Field(default_factory=dict)
    recommended_generation_style: dict[str, Any] = Field(default_factory=dict)
    distributions: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    contract_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_authorized: Literal[False] = False
    may_execute_mutation: Literal[False] = False

    @model_validator(mode="after")
    def validate_evidence(self) -> DrawingGrammarRef:
        sample_digest = self.evidence.get("sample_digest")
        nearby_count = self.evidence.get("nearby_entity_count")
        if not isinstance(sample_digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", sample_digest
        ):
            raise ValueError("drawing grammar evidence.sample_digest must be SHA-256")
        if not isinstance(nearby_count, int) or nearby_count < 0:
            raise ValueError("drawing grammar evidence.nearby_entity_count must be >= 0")
        return self


class DocumentRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: HostKind
    host_version: str
    document_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    path: str | None = None
    source_binding: SourceBindingRef | None = None

    @model_validator(mode="after")
    def validate_source_binding(self) -> DocumentRef:
        if self.source_binding is not None and self.source_binding.document_id != self.document_id:
            raise ValueError("source_binding.document_id must match document_id")
        return self


class EntityRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str = Field(min_length=1)
    handle: str = Field(pattern=r"^[0-9A-F]+$")
    entity_type: str | None = None
    layer: str | None = None

    @field_validator("handle", mode="before")
    @classmethod
    def normalize_handle(cls, value: Any) -> str:
        return str(value).strip().upper()


class ChangeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    targets: list[EntityRef] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    writes: bool = True


class ChangePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID = Field(default_factory=uuid4)
    document: DocumentRef
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=200)
    operations: list[ChangeOperation] = Field(min_length=1)
    approval_required: bool = True
    evidence_requirements: list[str] = Field(
        default_factory=lambda: ["native_readback", "independent_cross_check"]
    )
    drawing_grammar: DrawingGrammarRef | None = None

    @model_validator(mode="after")
    def validate_plan_fences(self) -> ChangePlan:
        if self.expected_revision != self.document.revision:
            raise ValueError("expected_revision must match document.revision at plan creation")
        op_ids = [op.op_id for op in self.operations]
        if len(op_ids) != len(set(op_ids)):
            raise ValueError("operation op_id values must be unique")
        if any(op.writes for op in self.operations) and not self.approval_required:
            raise ValueError("write plans must require approval")
        for op in self.operations:
            for target in op.targets:
                if target.document_id != self.document.document_id:
                    raise ValueError("all operation targets must belong to the planned document")
        return self


class AdapterDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    adapter_id: str = Field(min_length=1)
    host: HostKind
    host_version: str | None = None
    channel: AdapterChannel
    capabilities: frozenset[str] = Field(default_factory=frozenset)
    online: bool = True
    preferred_for_writes: bool = False
    authoritative: bool = False

    def supports(self, capability: str) -> bool:
        return self.online and capability in self.capabilities


class OperationResult(BaseModel):
    op_id: str
    status: Literal["ok", "failed", "skipped"]
    affected_handles: list[str] = Field(default_factory=list)
    message: str | None = None


class ExecutionReceipt(BaseModel):
    plan_id: UUID
    adapter_id: str
    document_id: str
    revision_before: int = Field(ge=0)
    revision_after: int = Field(ge=0)
    idempotency_key: str
    committed: bool
    results: list[OperationResult] = Field(default_factory=list)
    source_binding_handoff_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_byte_revision_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    parser_revision_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_revision_progress(self) -> ExecutionReceipt:
        if self.committed and self.revision_after < self.revision_before:
            raise ValueError("committed execution cannot move revision backwards")
        return self


class VerificationFinding(BaseModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    source: str
    handles: list[str] = Field(default_factory=list)


class VerificationReport(BaseModel):
    document_id: str
    revision: int = Field(ge=0)
    passed: bool
    sources: list[str] = Field(min_length=1)
    findings: list[VerificationFinding] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_error_consistency(self) -> VerificationReport:
        if any(f.severity == "error" for f in self.findings) and self.passed:
            raise ValueError("verification cannot pass while error findings exist")
        return self
