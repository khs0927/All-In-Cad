"""Headless tests for the native write approval gate (plan -> token -> execute).

The C# gate in native/shared/HostWriteGuard.cs is the one AutoCAD runs; it replays the same
vectors in native/shared-tests (`dotnet run`). These tests pin the Python reference and the
cross-file contract (gated method lists, Plugin.cs dispatch order).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from all_in_cad.host_approval import (
    CONDITIONAL_METHODS,
    DEFAULT_DENYLIST,
    DEFAULT_TTL_SECONDS,
    ENV_ALLOW,
    ENV_ALLOW_LISP,
    ENV_DENY,
    ENV_REQUIRE,
    ENV_TTL,
    GATED_METHODS,
    MAX_TTL_SECONDS,
    PLAN_METHOD,
    TOKEN_PARAM,
    HostApprovalError,
    HostWriteGuard,
    SendCommandPolicy,
    canonical_json,
    operation_digest,
    requires_approval,
)

ROOT = Path(__file__).resolve().parents[1]
VECTORS = json.loads(
    (ROOT / "tests" / "fixtures" / "host_approval_vectors.json").read_text(encoding="utf-8")
)
GUARD_CS = (ROOT / "native" / "shared" / "HostWriteGuard.cs").read_text(encoding="utf-8")
PLUGIN_CS = (ROOT / "native" / "autocad2027" / "Plugin.cs").read_text(encoding="utf-8")

LINE = {"x1": 0.0, "y1": 0.0, "x2": 1234.5, "y2": 678.9}
NOW = 1_000_000


def _code(fn, *args, **kwargs) -> str | None:
    try:
        fn(*args, **kwargs)
    except HostApprovalError as exc:
        return exc.code
    return None


def _token(guard: HostWriteGuard, method: str = "host.draw_line", params=None) -> str:
    return guard.plan(method, LINE if params is None else params, now=NOW)["approval_token"]


# ---------------------------------------------------------------------- token lifecycle


def test_rejected_without_token() -> None:
    guard = HostWriteGuard()
    assert _code(guard.authorize, "host.draw_line", LINE, now=NOW) == "E_APPROVAL_REQUIRED"
    assert _code(guard.authorize, "host.draw_line", {**LINE, TOKEN_PARAM: ""}, now=NOW) == (
        "E_APPROVAL_REQUIRED"
    )


def test_every_gated_method_requires_a_token() -> None:
    guard = HostWriteGuard()
    params = {"command": "ZOOM E", "force_load": True}
    for method in sorted(GATED_METHODS | CONDITIONAL_METHODS):
        assert _code(guard.authorize, method, params, now=NOW) == "E_APPROVAL_REQUIRED", method


def test_accepted_with_valid_token_once() -> None:
    guard = HostWriteGuard()
    plan = guard.plan("host.draw_line", LINE, now=NOW)
    assert plan["operation_digest"] == operation_digest("host.draw_line", LINE)
    assert plan["expires_at"] == NOW + DEFAULT_TTL_SECONDS
    assert plan["operation"] == {"method": "host.draw_line", "params": LINE}
    record = guard.authorize(
        "host.draw_line", {**LINE, TOKEN_PARAM: plan["approval_token"]}, now=NOW + 5
    )
    assert record == {
        "plan_id": plan["plan_id"],
        "method": "host.draw_line",
        "operation_digest": plan["operation_digest"],
    }


def test_rejected_when_reused() -> None:
    guard = HostWriteGuard()
    token = _token(guard)
    guard.authorize("host.draw_line", {**LINE, TOKEN_PARAM: token}, now=NOW)
    assert _code(guard.authorize, "host.draw_line", {**LINE, TOKEN_PARAM: token}, now=NOW) == (
        "E_APPROVAL_REUSED"
    )


def test_rejected_when_expired() -> None:
    guard = HostWriteGuard(ttl_seconds=30)
    token = _token(guard)
    params = {**LINE, TOKEN_PARAM: token}
    assert _code(guard.authorize, "host.draw_line", params, now=NOW + 31) == "E_APPROVAL_EXPIRED"
    # and it stays dead
    assert _code(guard.authorize, "host.draw_line", params, now=NOW + 1) in {
        "E_APPROVAL_INVALID",
        "E_APPROVAL_EXPIRED",
    }


def test_rejected_with_wrong_params_or_method_but_planned_op_still_works() -> None:
    guard = HostWriteGuard()
    token = _token(guard)
    wrong = {**LINE, "x2": 9999.0, TOKEN_PARAM: token}
    assert _code(guard.authorize, "host.draw_line", wrong, now=NOW) == "E_APPROVAL_MISMATCH"
    assert _code(guard.authorize, "xicad.rollback", {**LINE, TOKEN_PARAM: token}, now=NOW) == (
        "E_APPROVAL_MISMATCH"
    )
    assert guard.authorize("host.draw_line", {**LINE, TOKEN_PARAM: token}, now=NOW)


@pytest.mark.parametrize(
    "token",
    ["garbage", "a.b", "....", "eyJ2IjoxfQ.AAAA"],
)
def test_rejected_with_malformed_token(token: str) -> None:
    guard = HostWriteGuard()
    assert _code(guard.authorize, "host.draw_line", {**LINE, TOKEN_PARAM: token}, now=NOW) == (
        "E_APPROVAL_INVALID"
    )


def test_rejected_with_token_from_another_worker_or_tampered() -> None:
    guard = HostWriteGuard()
    foreign = _token(HostWriteGuard())
    assert _code(guard.authorize, "host.draw_line", {**LINE, TOKEN_PARAM: foreign}, now=NOW) == (
        "E_APPROVAL_INVALID"
    )
    token = _token(guard)
    body, sig = token.split(".")
    tampered = body + "." + ("A" if sig[0] != "A" else "B") + sig[1:]
    assert _code(guard.authorize, "host.draw_line", {**LINE, TOKEN_PARAM: tampered}, now=NOW) == (
        "E_APPROVAL_INVALID"
    )
    assert _code(guard.authorize, "host.draw_line", {**LINE, TOKEN_PARAM: 5}, now=NOW) == (
        "E_APPROVAL_INVALID"
    )


def test_number_spelling_does_not_change_the_digest() -> None:
    guard = HostWriteGuard()
    token = _token(guard)
    respelled = {"y2": 678.9, "x2": 1234.5, "y1": 0, "x1": 0, TOKEN_PARAM: token}
    assert guard.authorize("host.draw_line", respelled, now=NOW)


def test_plan_validates_before_issuing() -> None:
    guard = HostWriteGuard()
    assert _code(guard.plan, "host.context", {}, now=NOW) == "E_BAD_PARAMS"
    assert _code(guard.plan, "xicad.probe", {}, now=NOW) == "E_BAD_PARAMS"
    assert _code(guard.plan, "host.draw_line", {TOKEN_PARAM: "x"}, now=NOW) == "E_BAD_PARAMS"
    assert _code(guard.plan, "host.send_command", {"command": "SHELL"}, now=NOW) == (
        "E_COMMAND_DENIED"
    )


def test_pending_plans_are_bounded() -> None:
    guard = HostWriteGuard()
    for _ in range(64):
        guard.plan("host.draw_line", LINE, now=NOW)
    assert _code(guard.plan, "host.draw_line", LINE, now=NOW) == "E_APPROVAL_BUSY"
    # expired plans free their slot
    assert guard.plan("host.draw_line", LINE, now=NOW + DEFAULT_TTL_SECONDS + 1)


def test_read_only_methods_are_never_gated() -> None:
    guard = HostWriteGuard()
    for method in ("system.ping", "host.context", "host.capabilities", "xicad.state"):
        assert guard.authorize(method, {}, now=NOW) is None
    assert guard.authorize("xicad.probe", {"force_load": False}, now=NOW) is None
    assert not requires_approval("xicad.probe", {})
    assert requires_approval("xicad.probe", {"force_load": "yes"})


# ---------------------------------------------------------------------------- opt-out


def test_gate_is_on_by_default_and_opt_out_is_explicit() -> None:
    assert HostWriteGuard.from_env({}).required
    assert HostWriteGuard.from_env({ENV_REQUIRE: "1"}).required
    assert HostWriteGuard.from_env({ENV_REQUIRE: "maybe"}).required
    for value in ("0", "false", "OFF", " no "):
        assert not HostWriteGuard.from_env({ENV_REQUIRE: value}).required


def test_opt_out_skips_token_but_keeps_command_denylist() -> None:
    guard = HostWriteGuard.from_env({ENV_REQUIRE: "0"})
    assert guard.authorize("host.draw_line", LINE, now=NOW) is None
    assert guard.authorize("host.send_command", {"command": "_.LINE 0,0 1,1 "}, now=NOW) is None
    assert _code(guard.authorize, "host.send_command", {"command": "QUIT Y"}, now=NOW) == (
        "E_COMMAND_DENIED"
    )
    # tokens still work, so the same client code runs in both modes
    token = _token(guard)
    assert guard.authorize("host.draw_line", {**LINE, TOKEN_PARAM: token}, now=NOW) is None


def test_ttl_from_env_is_clamped() -> None:
    assert HostWriteGuard.from_env({ENV_TTL: "30"}).ttl_seconds == 30
    assert HostWriteGuard.from_env({ENV_TTL: "99999"}).ttl_seconds == MAX_TTL_SECONDS
    assert HostWriteGuard.from_env({ENV_TTL: "x"}).ttl_seconds == DEFAULT_TTL_SECONDS


# ---------------------------------------------------------------------- command policy


@pytest.mark.parametrize(
    "command",
    [
        "SHELL",
        "_.LINE 0,0 1,1  ._shell dir",
        "-PURGE A * N",
        "qsave",
        "_.U",
        "NETLOAD C:/x.dll",
        '(command "_.LINE")',
        "'_SECURELOAD 0",
    ],
)
def test_send_command_denylist(command: str) -> None:
    assert _code(SendCommandPolicy().check, "host.send_command", {"command": command}) == (
        "E_COMMAND_DENIED"
    )


def test_send_command_allowlist_extension_and_lisp_opt_in() -> None:
    policy = SendCommandPolicy.from_env(
        {ENV_ALLOW: "LINE, ZOOM", ENV_DENY: "ERASE", ENV_ALLOW_LISP: "1"}
    )
    assert policy.denylist >= DEFAULT_DENYLIST | {"ERASE"}
    assert _code(policy.check_text, "_.LINE 0,0 1,1 ") is None
    assert _code(policy.check_text, "CIRCLE 0,0 5") == "E_COMMAND_DENIED"
    assert _code(policy.check_text, "ZOOM E ERASE L ") == "E_COMMAND_DENIED"
    assert _code(policy.check_text, 'LINE (getvar "LASTPOINT")') is None


def test_xicad_run_args_are_policed_too() -> None:
    policy = SendCommandPolicy()
    ok = {"command": "xiDrawWall", "args": ["0,0", "12000,0"]}
    assert _code(policy.check, "xicad.run", ok) is None
    assert _code(policy.check, "xicad.run", {**ok, "args": ["0,0", " SHELL"]}) == (
        "E_COMMAND_DENIED"
    )


# ------------------------------------------------------------- cross-language contract


def test_vectors_digests() -> None:
    for row in VECTORS["digests"]:
        params = json.loads(row["params_json"])
        assert operation_digest(row["method"], params) == row["digest"], row
        assert requires_approval(row["method"], params) is row["requires_approval"], row
        stripped = {k: v for k, v in params.items() if k != TOKEN_PARAM}
        assert canonical_json({"method": row["method"], "params": stripped}) == row["canonical"]


def test_vectors_numbers() -> None:
    for row in VECTORS["numbers"]:
        assert canonical_json(json.loads(row["json"])) == row["canonical"], row


def test_vectors_policy() -> None:
    for row in VECTORS["policy"]:
        code = _code(SendCommandPolicy().check, row["method"], json.loads(row["params_json"]))
        assert code == row["error_code"], row


def _cs_set(name: str) -> set[str]:
    block = GUARD_CS.split(name, 1)[1].split("};", 1)[0]
    return set(re.findall(r'"([^"]+)"', block))


def test_csharp_and_python_gate_the_same_methods_and_commands() -> None:
    assert _cs_set("GatedMethods =") == set(GATED_METHODS)
    assert _cs_set("ConditionalMethods =") == set(CONDITIONAL_METHODS)
    assert _cs_set("DefaultDenylist =") == set(DEFAULT_DENYLIST)
    for constant in (ENV_REQUIRE, ENV_TTL, ENV_DENY, ENV_ALLOW, ENV_ALLOW_LISP, PLAN_METHOD):
        assert f'"{constant}"' in GUARD_CS


READ_ONLY_METHODS = {
    "system.ping",
    "host.context",
    "host.capabilities",
    "xicad.state",
    "xicad.run_status",
}


def test_every_plugin_method_is_gated_or_known_read_only() -> None:
    dispatch = PLUGIN_CS.split("DispatchAsync(RpcRequest request", 1)[1].split(
        "private static RpcResponse HandlePlanWrite", 1
    )[0]
    cases = set(re.findall(r'case "([^"]+)":', dispatch))
    assert cases, "dispatch switch not found"
    unknown = cases - GATED_METHODS - CONDITIONAL_METHODS - READ_ONLY_METHODS
    assert not unknown, f"new pipe methods must be gated or listed read-only: {unknown}"
    # The gate runs before the switch, and the plan method is advertised.
    assert dispatch.index("AuthorizeWrite(request)") < dispatch.index("switch (request.Method)")
    assert '"host.plan_write"' in PLUGIN_CS.split("internal static readonly string[] All", 1)[1]
    assert "HostWriteGuard.FromEnvironment()" in PLUGIN_CS
