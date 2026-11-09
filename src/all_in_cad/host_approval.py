"""Approval gate for mutating / command-executing native host methods.

This is the Python reference implementation of the gate enforced by the AutoCAD 2027 plugin
(``native/shared/HostWriteGuard.cs``). Both implementations follow the same contract and are
checked against the same vectors (``tests/fixtures/host_approval_vectors.json``):

1. **plan** - the client calls ``host.plan_write`` with ``{"method": ..., "params": ...}``.
   The worker validates the operation (including the ``host.send_command`` policy), stores a
   pending plan and returns ``plan_id``, ``approval_token``, ``operation_digest`` and
   ``expires_at``. Nothing is written.
2. **execute** - the client calls the gated method with the *same* params plus
   ``params.approval_token``. The token must be signed by this worker process, bound to the exact
   method and canonical params digest, unexpired, and not used before. It is consumed on use.

Same concept as power-cad-mcp's ``cad_plan_create`` -> ``cad_plan_execute`` (persisted plan,
short expiry, never replayed) and All-In-Cad's own :mod:`all_in_cad.approval` (HMAC-signed,
digest-bound tokens). The gate is ON by default; ``AIC_REQUIRE_APPROVAL=0`` is the documented
opt-out for trusted local automation. The ``host.send_command`` denylist stays active even when
the token gate is switched off.

The gate stops blind single-call writes and binds what is executed to what was planned. It is not
by itself a human in the loop: an MCP client should show the ``host.plan_write`` result to the
user before it sends the execute call.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

PLAN_METHOD = "host.plan_write"
TOKEN_PARAM = "approval_token"

ENV_REQUIRE = "AIC_REQUIRE_APPROVAL"
ENV_TTL = "AIC_APPROVAL_TTL_SECONDS"
ENV_DENY = "AIC_SEND_COMMAND_DENY"
ENV_ALLOW = "AIC_SEND_COMMAND_ALLOW"
ENV_ALLOW_LISP = "AIC_SEND_COMMAND_ALLOW_LISP"

DEFAULT_TTL_SECONDS = 120
MAX_TTL_SECONDS = 600
MAX_PENDING = 64

# Every pipe method that writes to the drawing or executes command-line / LISP text.
GATED_METHODS: frozenset[str] = frozenset(
    {
        "host.send_command",
        "host.draw_line",
        "xicad.ensure_loaded",
        "xicad.draw_wall",
        "xicad.draw_door",
        "xicad.draw_window",
        "xicad.smoke_wall_door_window",
        "xicad.run",
        "xicad.rollback",
    }
)
# Gated only for some params: xicad.probe loads a LISP file when force_load is true.
CONDITIONAL_METHODS: frozenset[str] = frozenset({"xicad.probe"})
# Methods whose command-line text goes through SendCommandPolicy.
COMMAND_TEXT_METHODS: frozenset[str] = frozenset({"host.send_command", "xicad.run"})

# Commands refused by host.send_command / xicad.run regardless of approval. Categories:
# process / code execution, loading code, file write / document lifecycle, security settings,
# and undo (the C-2 hazard: a queued U can silently undo the user's own work).
# fmt: off
DEFAULT_DENYLIST: frozenset[str] = frozenset(
    {
        # process and code execution
        "SHELL", "SH", "START", "SCRIPT", "SCRIPTCALL", "RSCRIPT", "ACTPLAY",
        "VBARUN", "VBALOAD", "VBAIDE", "VBAMAN", "VBANEW", "VBASTMT", "VBAUNLOAD",
        "NETLOAD", "APPLOAD", "ARX", "LOADJSP", "WEBLOAD", "PYLOAD", "RUNPY",
        # menus / customisation that can carry macros
        "CUI", "CUILOAD", "CUIUNLOAD", "MENU", "MENULOAD", "MENUUNLOAD",
        # file writes and document lifecycle
        "SAVE", "QSAVE", "SAVEAS", "SAVEALL", "WBLOCK", "EXPORT", "DXFOUT", "EXPORTPDF",
        "PUBLISH", "PLOT", "ETRANSMIT", "ARCHIVE", "OPEN", "NEW", "QNEW", "RECOVER",
        "RECOVERALL", "CLOSE", "CLOSEALL", "QUIT", "EXIT", "PURGE",
        # security settings
        "SECURELOAD", "TRUSTEDPATHS", "TRUSTEDDOMAINS", "LEGACYCODESEARCH", "OPTIONS",
        # undo
        "U", "UNDO",
    }
)
# fmt: on

_PREFIX_CHARS = "._-'+"
_WHITESPACE = re.compile(r"\s+")


class HostApprovalError(RuntimeError):
    """Gate refusal. ``code`` is the wire error code returned by the worker."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------------- canonical


def _canonical_string(value: str) -> str:
    out = ['"']
    for ch in value:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif 0x20 <= code < 0x7F:
            out.append(ch)
        elif code > 0xFFFF:
            code -= 0x10000
            out.append(f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}")
        else:
            out.append(f"\\u{code:04x}")
    out.append('"')
    return "".join(out)


def _canonical_number(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise ValueError("non-finite numbers are not valid JSON")
    if value.is_integer() and abs(value) < 2**53:
        return str(int(value))
    return repr(value).lower()


def canonical_json(value: Any) -> str:
    """Language-neutral canonical JSON used for operation digests.

    Object keys sorted ordinally, no whitespace, every non-ASCII / control character escaped as
    lowercase ``\\uXXXX``, integral numbers below 2**53 written without a fraction (so ``1000`` and
    ``1000.0`` digest the same), other numbers as their shortest round-trip form.
    """

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _canonical_number(value)
    if isinstance(value, str):
        return _canonical_string(value)
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda kv: kv[0])
        return (
            "{"
            + ",".join(f"{_canonical_string(str(k))}:{canonical_json(v)}" for k, v in items)
            + "}"
        )
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    raise TypeError(f"unsupported type for canonical JSON: {type(value).__name__}")


def operation_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Params that define the operation: everything except the approval token itself."""

    return {k: v for k, v in (params or {}).items() if k != TOKEN_PARAM}


def operation_digest(method: str, params: Mapping[str, Any] | None) -> str:
    body = canonical_json({"method": method, "params": operation_params(params)})
    return hashlib.sha256(body.encode("ascii")).hexdigest()


def requires_approval(method: str, params: Mapping[str, Any] | None) -> bool:
    if method in GATED_METHODS:
        return True
    if method in CONDITIONAL_METHODS:
        # Fail closed: anything but an absent / null / false force_load counts as a load.
        force_load = (params or {}).get("force_load")
        return not (force_load is None or force_load is False)
    return False


# ---------------------------------------------------------------------------- command policy


def _parse_names(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    return frozenset(
        part.strip().lstrip(_PREFIX_CHARS).upper()
        for part in re.split(r"[,;\s]+", raw)
        if part.strip().lstrip(_PREFIX_CHARS)
    )


def _command_name(token: str) -> str:
    return token.lstrip(_PREFIX_CHARS).upper()


@dataclass(frozen=True)
class SendCommandPolicy:
    """Denylist (always) plus optional first-command allowlist for command-line text.

    Every whitespace-separated token is checked against the denylist, because a space or newline
    is Enter on the AutoCAD command line and any later token can start a new command. A LISP form
    (any ``(``) is refused unless ``allow_lisp`` is set, since AutoLISP can do anything the
    denylist forbids. When ``allowlist`` is non-empty the first command must be on it.
    """

    denylist: frozenset[str] = DEFAULT_DENYLIST
    allowlist: frozenset[str] = frozenset()
    allow_lisp: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> SendCommandPolicy:
        return cls(
            denylist=DEFAULT_DENYLIST | _parse_names(environ.get(ENV_DENY)),
            allowlist=_parse_names(environ.get(ENV_ALLOW)),
            allow_lisp=environ.get(ENV_ALLOW_LISP, "").strip().lower()
            in {"1", "true", "yes", "on"},
        )

    def check_text(self, text: str, *, first_is_command: bool = True) -> None:
        if not self.allow_lisp and "(" in text:
            raise HostApprovalError(
                "E_COMMAND_DENIED",
                "LISP expressions are refused by host command policy "
                f"(set {ENV_ALLOW_LISP}=1 to allow)",
            )
        tokens = [t for t in _WHITESPACE.split(text) if t]
        for token in tokens:
            name = _command_name(token)
            if name in self.denylist:
                raise HostApprovalError(
                    "E_COMMAND_DENIED", f"command '{name}' is on the host command denylist"
                )
        if first_is_command and self.allowlist and tokens:
            first = _command_name(tokens[0])
            if first not in self.allowlist:
                raise HostApprovalError(
                    "E_COMMAND_DENIED",
                    f"command '{first}' is not on the {ENV_ALLOW} allowlist",
                )

    def check(self, method: str, params: Mapping[str, Any] | None) -> None:
        if method not in COMMAND_TEXT_METHODS:
            return
        params = params or {}
        if method == "host.send_command":
            command = params.get("command", params.get("cmd"))
            self.check_text("" if command is None else str(command))
            return
        # xicad.run: the command and every prompt answer are typed into the command line, and a
        # stray answer becomes a new command once the XiCAD command has ended.
        self.check_text(str(params.get("command") or ""))
        args = params.get("args")
        if isinstance(args, list):
            for arg in args:
                self.check_text(str(arg), first_is_command=False)


# ------------------------------------------------------------------------------------ guard


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _env_flag_off(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"0", "false", "off", "no"}


def _ttl_from_env(value: str | None) -> int:
    if not value:
        return DEFAULT_TTL_SECONDS
    try:
        ttl = int(value)
    except ValueError:
        return DEFAULT_TTL_SECONDS
    return min(max(ttl, 1), MAX_TTL_SECONDS)


@dataclass
class _Pending:
    method: str
    digest: str
    expires_at: int


@dataclass
class HostWriteGuard:
    """Per-worker-process approval state. The secret never leaves the process."""

    required: bool = True
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    policy: SendCommandPolicy = field(default_factory=SendCommandPolicy)
    secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    _pending: dict[str, _Pending] = field(default_factory=dict, init=False, repr=False)
    _consumed: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.secret) < 32:
            raise ValueError("approval secret must be at least 32 bytes")
        if not 1 <= self.ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError(f"ttl_seconds must be between 1 and {MAX_TTL_SECONDS}")

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> HostWriteGuard:
        return cls(
            required=not _env_flag_off(environ.get(ENV_REQUIRE)),
            ttl_seconds=_ttl_from_env(environ.get(ENV_TTL)),
            policy=SendCommandPolicy.from_env(environ),
        )

    def describe(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "plan_method": PLAN_METHOD,
            "token_param": TOKEN_PARAM,
            "ttl_seconds": self.ttl_seconds,
            "gated_methods": sorted(GATED_METHODS),
            "conditional_methods": sorted(CONDITIONAL_METHODS),
            "send_command": {
                "denylist_size": len(self.policy.denylist),
                "allowlist_active": bool(self.policy.allowlist),
                "lisp_allowed": self.policy.allow_lisp,
            },
        }

    def _sign(self, body: bytes) -> bytes:
        return hmac.new(self.secret, body, hashlib.sha256).digest()

    def _prune(self, now: int) -> None:
        for plan_id in [k for k, v in self._pending.items() if v.expires_at < now]:
            del self._pending[plan_id]
        for plan_id in [k for k, exp in self._consumed.items() if exp < now]:
            del self._consumed[plan_id]

    def plan(
        self, method: str, params: Mapping[str, Any] | None, *, now: int | None = None
    ) -> dict[str, Any]:
        """``host.plan_write``: validate the operation and issue a single-use token."""

        if not isinstance(method, str) or not requires_approval(method, params):
            raise HostApprovalError(
                "E_BAD_PARAMS", f"method '{method}' with these params does not need approval"
            )
        if params is not None and not isinstance(params, Mapping):
            raise HostApprovalError("E_BAD_PARAMS", "params must be an object")
        if params and TOKEN_PARAM in params:
            raise HostApprovalError("E_BAD_PARAMS", f"params must not contain {TOKEN_PARAM}")
        self.policy.check(method, params)
        issued = int(time.time() if now is None else now)
        op_params = operation_params(params)
        digest = operation_digest(method, op_params)
        plan_id = uuid.uuid4().hex
        expires_at = issued + self.ttl_seconds
        body = canonical_json(
            {
                "digest": digest,
                "exp": expires_at,
                "method": method,
                "nonce": secrets.token_hex(8),
                "plan_id": plan_id,
                "v": 1,
            }
        ).encode("ascii")
        token = f"{_b64url(body)}.{_b64url(self._sign(body))}"
        with self._lock:
            self._prune(issued)
            if len(self._pending) >= MAX_PENDING:
                raise HostApprovalError("E_APPROVAL_BUSY", "too many pending plans; retry later")
            self._pending[plan_id] = _Pending(method, digest, expires_at)
        return {
            "plan_id": plan_id,
            "approval_token": token,
            "method": method,
            "operation_digest": digest,
            "operation": {"method": method, "params": op_params},
            "expires_at": expires_at,
            "ttl_seconds": self.ttl_seconds,
            "approval_required": self.required,
            "note": f"Nothing was written. Re-send {method} with these params plus "
            f"params.{TOKEN_PARAM} before expires_at; the token works once.",
        }

    def authorize(
        self, method: str, params: Mapping[str, Any] | None, *, now: int | None = None
    ) -> dict[str, Any] | None:
        """Gate a call. Returns the consumed plan record, ``None`` when nothing was needed.

        The send_command policy is always checked; the token only when ``required`` is set.
        """

        if not requires_approval(method, params):
            return None
        self.policy.check(method, params)
        if not self.required:
            return None
        token = (params or {}).get(TOKEN_PARAM)
        if not token:
            raise HostApprovalError(
                "E_APPROVAL_REQUIRED",
                f"{method} needs an approval token: call {PLAN_METHOD} first "
                f"(opt out with {ENV_REQUIRE}=0 for trusted local automation)",
            )
        if not isinstance(token, str):
            raise HostApprovalError("E_APPROVAL_INVALID", "approval_token must be a string")
        try:
            body_part, sig_part = token.split(".", 1)
            body = _b64url_decode(body_part)
            signature = _b64url_decode(sig_part)
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HostApprovalError("E_APPROVAL_INVALID", "malformed approval token") from exc
        if not hmac.compare_digest(signature, self._sign(body)):
            raise HostApprovalError("E_APPROVAL_INVALID", "approval token signature is invalid")
        if payload.get("method") != method or payload.get("digest") != operation_digest(
            method, params
        ):
            raise HostApprovalError(
                "E_APPROVAL_MISMATCH",
                "approval token was issued for a different method or params",
            )
        plan_id = str(payload.get("plan_id"))
        timestamp = int(time.time() if now is None else now)
        with self._lock:
            if plan_id in self._consumed:
                raise HostApprovalError("E_APPROVAL_REUSED", "approval token was already used")
            if int(payload.get("exp", 0)) < timestamp:
                self._pending.pop(plan_id, None)
                raise HostApprovalError("E_APPROVAL_EXPIRED", "approval token expired; plan again")
            pending = self._pending.pop(plan_id, None)
            if pending is None or pending.digest != payload["digest"]:
                raise HostApprovalError("E_APPROVAL_INVALID", "approval token is not pending")
            self._consumed[plan_id] = pending.expires_at
            self._prune(timestamp)
        return {"plan_id": plan_id, "method": method, "operation_digest": pending.digest}


def gated_method_names() -> Iterable[str]:
    return sorted(GATED_METHODS | CONDITIONAL_METHODS)
