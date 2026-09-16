from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from .models import ChangePlan


class ApprovalError(RuntimeError):
    pass


def plan_digest(plan: ChangePlan) -> str:
    payload = plan.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@dataclass(frozen=True)
class VerifiedApproval:
    plan_id: str
    document_id: str
    expected_revision: int
    digest: str
    expires_at: int
    nonce: str


class ApprovalSigner:
    """Plan-scoped HMAC approval tokens bound to the exact ChangePlan digest."""

    def __init__(self, secret: bytes):
        if len(secret) < 32:
            raise ValueError("approval secret must be at least 32 bytes")
        self._secret = secret

    def issue(self, plan: ChangePlan, *, ttl_seconds: int = 300, now: int | None = None) -> str:
        if ttl_seconds <= 0 or ttl_seconds > 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600")
        issued_at = int(time.time() if now is None else now)
        payload = {
            "v": 1,
            "plan_id": str(plan.plan_id),
            "document_id": plan.document.document_id,
            "expected_revision": plan.expected_revision,
            "digest": plan_digest(plan),
            "exp": issued_at + ttl_seconds,
            "nonce": secrets.token_hex(8),
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._secret, body, hashlib.sha256).digest()
        return f"{_b64url(body)}.{_b64url(signature)}"

    def verify(self, token: str, plan: ChangePlan, *, now: int | None = None) -> VerifiedApproval:
        try:
            body_part, signature_part = token.split(".", 1)
            body = _b64url_decode(body_part)
            signature = _b64url_decode(signature_part)
            payload = json.loads(body)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ApprovalError("malformed approval token") from exc

        expected_signature = hmac.new(self._secret, body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ApprovalError("invalid approval signature")

        timestamp = int(time.time() if now is None else now)
        if int(payload.get("exp", 0)) < timestamp:
            raise ApprovalError("approval token expired")

        expected = {
            "plan_id": str(plan.plan_id),
            "document_id": plan.document.document_id,
            "expected_revision": plan.expected_revision,
            "digest": plan_digest(plan),
        }
        for key, value in expected.items():
            if payload.get(key) != value:
                raise ApprovalError(f"approval token does not match plan field: {key}")

        return VerifiedApproval(
            plan_id=payload["plan_id"], document_id=payload["document_id"],
            expected_revision=int(payload["expected_revision"]), digest=payload["digest"],
            expires_at=int(payload["exp"]), nonce=payload["nonce"],
        )
