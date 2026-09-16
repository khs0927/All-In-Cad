from __future__ import annotations

from copy import deepcopy
from typing import Protocol

from .approval import ApprovalSigner, plan_digest
from .journal import IdempotencyJournal
from .lease import DocumentLeaseManager
from .models import AdapterDescriptor, ChangePlan, DocumentRef, ExecutionReceipt, OperationResult


class ExecutionError(RuntimeError):
    pass


class NativeAdapter(Protocol):
    descriptor: AdapterDescriptor
    def current_document(self) -> DocumentRef: ...
    def execute_atomic(self, plan: ChangePlan) -> ExecutionReceipt: ...


class GuardedExecutor:
    def __init__(self, *, signer: ApprovalSigner, leases: DocumentLeaseManager, journal: IdempotencyJournal) -> None:
        self._signer = signer
        self._leases = leases
        self._journal = journal

    def execute(self, plan: ChangePlan, *, adapter: NativeAdapter, approval_token: str) -> ExecutionReceipt:
        if not adapter.descriptor.supports("entity.write"):
            raise ExecutionError(f"adapter is not write-capable: {adapter.descriptor.adapter_id}")
        self._signer.verify(approval_token, plan)

        digest = plan_digest(plan)
        entry = self._journal.reserve(plan.idempotency_key, digest)
        if entry.status == "committed" and entry.response:
            return ExecutionReceipt.model_validate(entry.response)
        if entry.status == "failed":
            raise ExecutionError("previous attempt with this idempotency key failed")

        document_key = plan.document.path or plan.document.document_id
        owner = adapter.descriptor.adapter_id
        try:
            current = adapter.current_document()
            if current.document_id != plan.document.document_id:
                raise ExecutionError("active document id does not match plan")
            if current.revision != plan.expected_revision:
                raise ExecutionError("active document revision is stale relative to plan")
            with self._leases.acquire(document_key, owner):
                receipt = adapter.execute_atomic(plan)
                if not receipt.committed:
                    raise ExecutionError("native adapter did not commit the transaction")
                if receipt.document_id != plan.document.document_id:
                    raise ExecutionError("execution receipt document mismatch")
                self._journal.commit(plan.idempotency_key, receipt.model_dump(mode="json"))
                return receipt
        except Exception as exc:
            self._journal.fail(plan.idempotency_key, str(exc))
            raise


class InMemoryNativeAdapter:
    """Host-independent transactional adapter used to prove control-plane invariants."""

    def __init__(self, descriptor: AdapterDescriptor, document: DocumentRef) -> None:
        self.descriptor = descriptor
        self._document = document
        self.applied_operations: list[str] = []
        self.execution_count = 0

    def current_document(self) -> DocumentRef:
        return self._document

    def execute_atomic(self, plan: ChangePlan) -> ExecutionReceipt:
        before_operations = deepcopy(self.applied_operations)
        before_document = self._document
        self.execution_count += 1
        results: list[OperationResult] = []
        try:
            for operation in plan.operations:
                if operation.kind == "test.fail":
                    raise ExecutionError(f"forced failure at {operation.op_id}")
                self.applied_operations.append(operation.op_id)
                results.append(OperationResult(op_id=operation.op_id, status="ok", affected_handles=[target.handle for target in operation.targets]))
            self._document = self._document.model_copy(update={"revision": before_document.revision + 1})
            return ExecutionReceipt(plan_id=plan.plan_id, adapter_id=self.descriptor.adapter_id, document_id=plan.document.document_id, revision_before=before_document.revision, revision_after=self._document.revision, idempotency_key=plan.idempotency_key, committed=True, results=results)
        except Exception:
            self.applied_operations = before_operations
            self._document = before_document
            raise
