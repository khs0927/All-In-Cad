from uuid import uuid4

import pytest

from all_in_cad.approval import ApprovalError, ApprovalSigner
from all_in_cad.executor import ExecutionError, GuardedExecutor, InMemoryNativeAdapter
from all_in_cad.journal import IdempotencyJournal, JournalConflict
from all_in_cad.lease import DocumentLeaseManager, LeaseConflict
from all_in_cad.models import AdapterChannel, AdapterDescriptor, ChangeOperation, ChangePlan, DocumentRef, HostKind


def descriptor() -> AdapterDescriptor:
    return AdapterDescriptor(adapter_id="zwcad-2026-native", host=HostKind.ZWCAD, host_version="2026", channel=AdapterChannel.ZWCAD_NATIVE, capabilities=frozenset({"entity.write", "entity.read"}), preferred_for_writes=True)


def document(revision: int = 3) -> DocumentRef:
    return DocumentRef(host=HostKind.ZWCAD, host_version="2026", document_id="doc-a", revision=revision, path=r"C:\CAD\a.dwg")


def plan(*kinds: str, revision: int = 3, key: str = "idem-key-001") -> ChangePlan:
    return ChangePlan(plan_id=uuid4(), document=document(revision), expected_revision=revision, idempotency_key=key, operations=[ChangeOperation(op_id=f"op-{i}", kind=kind) for i, kind in enumerate(kinds, 1)])


def executor() -> tuple[GuardedExecutor, ApprovalSigner]:
    signer = ApprovalSigner(b"x" * 32)
    return GuardedExecutor(signer=signer, leases=DocumentLeaseManager(), journal=IdempotencyJournal()), signer


def test_approval_is_bound_to_exact_plan_and_expiry() -> None:
    signer = ApprovalSigner(b"s" * 32)
    original = plan("entity.move")
    token = signer.issue(original, ttl_seconds=10, now=100)
    signer.verify(token, original, now=109)
    with pytest.raises(ApprovalError): signer.verify(token, original, now=111)
    modified = original.model_copy(update={"idempotency_key": "idem-key-999"})
    with pytest.raises(ApprovalError): signer.verify(token, modified, now=105)


def test_document_lease_rejects_second_writer() -> None:
    leases = DocumentLeaseManager()
    with leases.acquire(r"C:\CAD\same.dwg", "zwcad"):
        with pytest.raises(LeaseConflict):
            with leases.acquire(r"c:\cad\SAME.dwg", "autocad"): pass


def test_stale_revision_fails_closed() -> None:
    guarded, signer = executor(); requested = plan("entity.move", revision=2)
    adapter = InMemoryNativeAdapter(descriptor(), document(revision=3)); token = signer.issue(requested)
    with pytest.raises(ExecutionError, match="revision"): guarded.execute(requested, adapter=adapter, approval_token=token)
    assert adapter.execution_count == 0


def test_atomic_failure_rolls_back() -> None:
    guarded, signer = executor(); requested = plan("entity.move", "test.fail")
    adapter = InMemoryNativeAdapter(descriptor(), document()); token = signer.issue(requested)
    with pytest.raises(ExecutionError, match="forced failure"): guarded.execute(requested, adapter=adapter, approval_token=token)
    assert adapter.applied_operations == [] and adapter.current_document().revision == 3


def test_committed_idempotent_request_replays_without_second_execution() -> None:
    guarded, signer = executor(); requested = plan("entity.move")
    adapter = InMemoryNativeAdapter(descriptor(), document()); token = signer.issue(requested)
    first = guarded.execute(requested, adapter=adapter, approval_token=token)
    second = guarded.execute(requested, adapter=adapter, approval_token=token)
    assert first == second and adapter.execution_count == 1


def test_idempotency_key_cannot_be_reused_for_different_request() -> None:
    guarded, signer = executor(); first_plan = plan("entity.move", key="shared-key-01")
    adapter = InMemoryNativeAdapter(descriptor(), document()); guarded.execute(first_plan, adapter=adapter, approval_token=signer.issue(first_plan))
    second_plan = plan("entity.copy", revision=4, key="shared-key-01")
    with pytest.raises(JournalConflict): guarded.execute(second_plan, adapter=adapter, approval_token=signer.issue(second_plan))
