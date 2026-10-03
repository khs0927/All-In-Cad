import pytest
from pydantic import ValidationError

from all_in_cad.models import (
    ChangeOperation,
    ChangePlan,
    DocumentRef,
    EntityRef,
    ExecutionReceipt,
    HostKind,
    SourceBindingRef,
)


def _source_binding(document_id: str = "doc-1") -> SourceBindingRef:
    return SourceBindingRef(
        document_id=document_id,
        source_id="a" * 64,
        source_byte_revision_id="b" * 64,
        parser_revision_id="c" * 64,
        handoff_digest="d" * 64,
        resolver_receipt_sha256="e" * 64,
    )

def _doc() -> DocumentRef:
    return DocumentRef(
        host=HostKind.ZWCAD,
        host_version="2026",
        document_id="doc-1",
        revision=7,
        path=r"C:\\CAD\\project.dwg",
    )


def test_entity_handle_is_normalized() -> None:
    entity = EntityRef(document_id="doc-1", handle="7f5ad")
    assert entity.handle == "7F5AD"


def test_write_plan_requires_revision_fence_and_approval() -> None:
    operation = ChangeOperation(op_id="move-wall", kind="entity.move", writes=True)
    with pytest.raises(ValidationError):
        ChangePlan(
            document=_doc(),
            expected_revision=6,
            idempotency_key="project-0001",
            operations=[operation],
        )
    with pytest.raises(ValidationError):
        ChangePlan(
            document=_doc(),
            expected_revision=7,
            idempotency_key="project-0002",
            operations=[operation],
            approval_required=False,
        )


def test_targets_must_belong_to_document() -> None:
    operation = ChangeOperation(
        op_id="move-wall",
        kind="entity.move",
        targets=[EntityRef(document_id="other", handle="AA")],
    )
    with pytest.raises(ValidationError):
        ChangePlan(
            document=_doc(),
            expected_revision=7,
            idempotency_key="project-0003",
            operations=[operation],
        )


def test_source_binding_must_match_document_and_remains_non_authorizing() -> None:
    binding = _source_binding()
    doc = DocumentRef(
        host=HostKind.AUTOCAD,
        host_version="2027",
        document_id="doc-1",
        revision=4,
        path=r"C:\\CAD\\a.dwg",
        source_binding=binding,
    )
    assert doc.source_binding is not None
    assert doc.source_binding.binding_state == "SOURCE_BOUND"
    assert doc.source_binding.execution_authorized is False

    with pytest.raises(ValidationError):
        DocumentRef(
            host=HostKind.AUTOCAD,
            host_version="2027",
            document_id="other-doc",
            revision=4,
            source_binding=binding,
        )


def test_execution_receipt_can_reference_ontology_handoff() -> None:
    receipt = ExecutionReceipt(
        plan_id="11111111-1111-1111-1111-111111111111",
        adapter_id="power-cad-2027-native",
        document_id="doc-1",
        revision_before=4,
        revision_after=5,
        idempotency_key="idem-source-bound",
        committed=True,
        source_binding_handoff_digest="d" * 64,
        source_id="a" * 64,
        source_byte_revision_id="b" * 64,
        parser_revision_id="c" * 64,
    )
    assert receipt.source_binding_handoff_digest == "d" * 64
    assert receipt.source_id == "a" * 64
