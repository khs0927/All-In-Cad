from __future__ import annotations

import json
import struct
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROTOCOL_VERSION = "aic.native/1"
MAX_FRAME_BYTES = 8 * 1024 * 1024
_HEADER = struct.Struct("<I")


class ProtocolError(ValueError):
    """Raised when native transport or envelope validation fails."""


class NativeMethod(StrEnum):
    SYSTEM_PING = "system.ping"
    HOST_CONTEXT = "host.context"
    HOST_CAPABILITIES = "host.capabilities"
    DOCUMENT_SNAPSHOT = "document.snapshot"
    ENTITY_READ = "entity.read"
    PLAN_EXECUTE = "plan.execute"
    VERIFICATION_CAPTURE = "verification.capture"


class RpcError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class RpcRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: str = PROTOCOL_VERSION
    request_id: UUID = Field(default_factory=uuid4)
    method: NativeMethod
    params: dict[str, Any] = Field(default_factory=dict)
    session_token: str | None = None

    @model_validator(mode="after")
    def validate_protocol(self) -> "RpcRequest":
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol: {self.protocol}")
        return self


class RpcResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: str = PROTOCOL_VERSION
    request_id: UUID
    ok: bool
    result: dict[str, Any] | None = None
    error: RpcError | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "RpcResponse":
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol: {self.protocol}")
        if self.ok and self.error is not None:
            raise ValueError("successful response cannot contain error")
        if not self.ok and self.error is None:
            raise ValueError("failed response requires error")
        return self

    @classmethod
    def success(
        cls,
        request_id: UUID,
        result: dict[str, Any] | None = None,
    ) -> "RpcResponse":
        return cls(request_id=request_id, ok=True, result=result or {})

    @classmethod
    def failure(
        cls,
        request_id: UUID,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> "RpcResponse":
        return cls(
            request_id=request_id,
            ok=False,
            error=RpcError(code=code, message=message, details=details or {}),
        )


def encode_payload(payload: BaseModel | dict[str, Any]) -> bytes:
    if isinstance(payload, BaseModel):
        data = payload.model_dump(mode="json", exclude_none=True)
    else:
        data = payload
    body = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if not body:
        raise ProtocolError("frame body must not be empty")
    if len(body) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame body exceeds {MAX_FRAME_BYTES} bytes")
    return body


def encode_frame(payload: BaseModel | dict[str, Any]) -> bytes:
    body = encode_payload(payload)
    return _HEADER.pack(len(body)) + body


def decode_body(body: bytes) -> dict[str, Any]:
    if not body:
        raise ProtocolError("frame body must not be empty")
    if len(body) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame body exceeds {MAX_FRAME_BYTES} bytes")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("frame body is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("frame JSON root must be an object")
    return value


class FrameDecoder:
    """Incremental decoder for 4-byte little-endian length-prefixed JSON frames."""

    def __init__(self, *, maximum_message_bytes: int = MAX_FRAME_BYTES) -> None:
        if maximum_message_bytes <= 0:
            raise ValueError("maximum_message_bytes must be positive")
        self.maximum_message_bytes = maximum_message_bytes
        self._buffer = bytearray()
        self._expected: int | None = None

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        messages: list[bytes] = []
        while True:
            if self._expected is None:
                if len(self._buffer) < _HEADER.size:
                    break
                length = _HEADER.unpack(self._buffer[: _HEADER.size])[0]
                del self._buffer[: _HEADER.size]
                if length <= 0 or length > self.maximum_message_bytes:
                    self.reset()
                    raise ProtocolError(f"invalid message length: {length}")
                self._expected = length

            if len(self._buffer) < self._expected:
                break

            expected = self._expected
            messages.append(bytes(self._buffer[:expected]))
            del self._buffer[:expected]
            self._expected = None
        return messages

    def reset(self) -> None:
        self._buffer.clear()
        self._expected = None

    @property
    def pending_bytes(self) -> int:
        return len(self._buffer)
