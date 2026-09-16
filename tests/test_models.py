import pytest
from pydantic import ValidationError

from all_in_cad.models import ChangeOperation, ChangePlan, DocumentRef, EntityRef, HostKind


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
