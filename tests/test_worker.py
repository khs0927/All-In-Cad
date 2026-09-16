from all_in_cad.models import ChangeOperation, ChangePlan, DocumentRef, HostKind
from all_in_cad.protocol import NativeMethod, RpcRequest
from all_in_cad.worker import InMemoryWorkerBackend, RpcDispatcher


def make_plan() -> ChangePlan:
    document = DocumentRef(
        host=HostKind.HEADLESS,
        host_version="fixture",
        document_id="fixture",
        revision=0,
    )
    return ChangePlan(
        document=document,
        expected_revision=0,
        idempotency_key="fixture-key-001",
        operations=[ChangeOperation(op_id="op-1", kind="entity.move")],
    )


def test_dispatcher_ping_and_session_auth() -> None:
    backend = InMemoryWorkerBackend()
    dispatcher = RpcDispatcher(backend, required_session_token="secret")
    denied = dispatcher.dispatch(RpcRequest(method=NativeMethod.SYSTEM_PING))
    assert denied.ok is False
    assert denied.error is not None
    assert denied.error.code == "E_SESSION_AUTH"
    allowed = dispatcher.dispatch(
        RpcRequest(method=NativeMethod.SYSTEM_PING, session_token="secret")
    )
    assert allowed.ok is True
    assert allowed.result == {"pong": True}


def test_dispatcher_validates_plan_execute() -> None:
    backend = InMemoryWorkerBackend()
    dispatcher = RpcDispatcher(backend)
    invalid = dispatcher.dispatch(
        RpcRequest(method=NativeMethod.PLAN_EXECUTE, params={"plan": {}})
    )
    assert invalid.ok is False
    assert invalid.error is not None
    assert invalid.error.code == "E_VALIDATION"
    plan = make_plan()
    ok = dispatcher.dispatch(
        RpcRequest(
            method=NativeMethod.PLAN_EXECUTE,
            params={
                "plan": plan.model_dump(mode="json"),
                "approval_token": "token",
            },
        )
    )
    assert ok.ok is True
    assert ok.result is not None
    assert ok.result["operation_count"] == 1
