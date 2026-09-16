from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from .models import ChangePlan
from .protocol import NativeMethod, RpcRequest, RpcResponse


class WorkerBackend(Protocol):
    def ping(self) -> dict[str, Any]: ...

    def context(self) -> dict[str, Any]: ...

    def capabilities(self) -> dict[str, Any]: ...

    def snapshot(self, params: dict[str, Any]) -> dict[str, Any]: ...

    def read_entity(self, params: dict[str, Any]) -> dict[str, Any]: ...

    def execute_plan(
        self,
        plan: ChangePlan,
        approval_token: str | None,
    ) -> dict[str, Any]: ...

    def capture_verification(self, params: dict[str, Any]) -> dict[str, Any]: ...


class RpcDispatcher:
    """Host-neutral request dispatcher used by both AutoCAD and ZWCAD workers."""

    def __init__(
        self,
        backend: WorkerBackend,
        *,
        required_session_token: str | None = None,
    ) -> None:
        self.backend = backend
        self.required_session_token = required_session_token

    def dispatch(self, request: RpcRequest) -> RpcResponse:
        if self.required_session_token is not None:
            if request.session_token != self.required_session_token:
                return RpcResponse.failure(
                    request.request_id,
                    "E_SESSION_AUTH",
                    "invalid or missing session token",
                )

        try:
            result = self._dispatch(request)
        except ValidationError as exc:
            return RpcResponse.failure(
                request.request_id,
                "E_VALIDATION",
                "request validation failed",
                details={"errors": exc.errors(include_url=False)},
            )
        except ValueError as exc:
            return RpcResponse.failure(
                request.request_id,
                "E_BAD_REQUEST",
                str(exc),
            )
        except Exception as exc:
            return RpcResponse.failure(
                request.request_id,
                "E_INTERNAL",
                str(exc),
                details={"exception_type": type(exc).__name__},
            )
        return RpcResponse.success(request.request_id, result)

    def _dispatch(self, request: RpcRequest) -> dict[str, Any]:
        method = request.method
        if method == NativeMethod.SYSTEM_PING:
            return self.backend.ping()
        if method == NativeMethod.HOST_CONTEXT:
            return self.backend.context()
        if method == NativeMethod.HOST_CAPABILITIES:
            return self.backend.capabilities()
        if method == NativeMethod.DOCUMENT_SNAPSHOT:
            return self.backend.snapshot(request.params)
        if method == NativeMethod.ENTITY_READ:
            return self.backend.read_entity(request.params)
        if method == NativeMethod.PLAN_EXECUTE:
            raw_plan = request.params.get("plan")
            if not isinstance(raw_plan, dict):
                raise ValueError("plan.execute requires params.plan object")
            plan = ChangePlan.model_validate(raw_plan)
            approval_token = request.params.get("approval_token")
            if approval_token is not None and not isinstance(approval_token, str):
                raise ValueError("approval_token must be a string")
            return self.backend.execute_plan(plan, approval_token)
        if method == NativeMethod.VERIFICATION_CAPTURE:
            return self.backend.capture_verification(request.params)
        raise ValueError(f"unsupported method: {method}")


class InMemoryWorkerBackend:
    """Deterministic backend for protocol conformance tests."""

    def __init__(self) -> None:
        self.executed_plan_ids: list[str] = []

    def ping(self) -> dict[str, Any]:
        return {"pong": True}

    def context(self) -> dict[str, Any]:
        return {"host": "headless", "document_id": "fixture", "revision": 0}

    def capabilities(self) -> dict[str, Any]:
        return {
            "read": ["document.snapshot", "entity.read", "verification.capture"],
            "write": ["plan.execute"],
        }

    def snapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"params": params, "entities": []}

    def read_entity(self, params: dict[str, Any]) -> dict[str, Any]:
        handle = params.get("handle")
        if not isinstance(handle, str) or not handle:
            raise ValueError("entity.read requires params.handle")
        return {"handle": handle.upper(), "found": False}

    def execute_plan(
        self,
        plan: ChangePlan,
        approval_token: str | None,
    ) -> dict[str, Any]:
        if not approval_token:
            raise ValueError("approval token is required")
        self.executed_plan_ids.append(str(plan.plan_id))
        return {
            "plan_id": str(plan.plan_id),
            "accepted": True,
            "operation_count": len(plan.operations),
        }

    def capture_verification(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"source": "fixture", "params": params}
