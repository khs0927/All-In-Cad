"""Golden-vector runner and trap unit tests for ``machine.py``.

The state machine is executed against :class:`FakeHost`, an in-memory double
of the five-method host protocol. No CAD is involved, no mouse, no keyboard.

Read this before trusting the numbers
-------------------------------------
Passing the vectors proves that the state machine implements the *spec's
decision logic*. It does not prove that a real CAD host behaves the way the
fake host is scripted to behave. The vectors are an oracle transcribed from
the original C# runner, and SPEC.md section 6 labels the executable
conformance as ``[不可]`` (not established). Every assertion below is a
statement about this module plus this fake, and about nothing else.

The fake host is deliberately dumb: it replays a scripted prompt trajectory
and a scripted entity count. It is not a CAD emulator.

One vector defect was found while implementing this module, and it was fixed
in the *vector*, not in the machine:

* **GV-10** shipped one ``prompt_sequence`` entry against four ``contract``
  entries, while ``golden_vectors.json`` states that a short array means "reuse
  the last value". Reusing ``"Specify first point: "`` made the step-1
  contract ``"Specify next point or [Undo]: "`` fail, so the run died at
  ``E_PROMPT_MISMATCH`` after one arg instead of ``E_TIMEOUT`` after two. The
  spec rule and the vector's own expectation contradicted each other; the
  vector was the wrong one. Its ``prompt_sequence`` was extended to four
  entries (2026-09-26, approved). ``machine.py`` was not touched.
  ``test_gv10_deadline_invariant_is_pinned`` keeps the pre-correction defect
  reproducible as a regression, so nobody "fixes" it by loosening the machine.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

try:  # package-relative import (normal case)
    from .machine import (
        TERMINATOR,
        CommandKind,
        RunMode,
        RunOptions,
        RunPlan,
        RunResult,
        RunStateMachine,
        invocation_form_matches_command_kind,
        terminate_every_input,
    )
except ImportError:  # pragma: no cover - flat execution fallback
    from all_in_cad.recorder.machine import (
        TERMINATOR,
        CommandKind,
        RunMode,
        RunOptions,
        RunPlan,
        RunResult,
        RunStateMachine,
        invocation_form_matches_command_kind,
        terminate_every_input,
    )

SPEC_DIR = Path(__file__).resolve().parent / "spec"
VECTOR_FILE = SPEC_DIR / "vectors" / "golden_vectors.json"

with VECTOR_FILE.open(encoding="utf-8") as handle:
    _VECTOR_DOC = json.load(handle)
VECTORS: list[dict[str, Any]] = _VECTOR_DOC["vectors"]
VECTOR_BY_ID = {vector["id"]: vector for vector in VECTORS}


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class FakeClock:
    """Virtual clock. The machine's bounded polls advance it, so a 2500 ms
    gate timeout is exercised in microseconds and without wall-clock drift."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeHost:
    """In-memory host implementing the five injected methods.

    Scripted by the vector's ``given`` block. Records every text that reaches
    it, and records whether anybody ever read ``focused`` (guard
    ``schedule_is_not_focus_dependent`` is verified by that counter staying 0).
    """

    def __init__(self, given: dict[str, Any], **overrides: Any) -> None:
        self.initial = int(given.get("initial_entities", 0))
        self.prompt_sequence: list[str | None] = list(given.get("prompt_sequence") or [])
        self.host_state_by_arg: list[bool] | None = given.get("host_state_by_arg")
        self.command_ever_active: bool = bool(given.get("command_ever_active", True))
        self.entity_count_reads: list[int] = list(given.get("entity_count_reads") or [])
        target = given.get("actual_final_entities")
        if target is None:
            target = self.initial + int(given.get("expect_delta_min") or 0)
        self.target = int(target)
        self.never_idles = bool(overrides.get("never_idles", False))
        self.undo_raises = bool(overrides.get("undo_raises", False))
        self.artifact_ok = bool(overrides.get("artifact_ok", True))

        self.active = False
        self.launched = False
        self.executed = False
        self.cursor = 0
        self.args_seen = 0
        self.count_calls = 0
        self.rolled_back = False
        self.cancel_calls = 0
        self.undo_calls = 0
        self.focus_reads = 0
        self.focused = bool(overrides.get("focused", True))
        self.inputs: list[str] = []
        self.prompts_observed: list[str | None] = []
        self.n_args = len(given.get("args") or [])

    # -- the five protocol methods -------------------------------------

    def state(self) -> dict:
        self.prompts_observed.append(self._prompt())
        return {
            "in_command": self.active,
            "prompt": self._prompt(),
            "command_name": "LINE" if self.active else None,
        }

    def send(self, text: str) -> None:
        self.inputs.append(text)
        if not self.launched:
            self.launched = True
            if self.command_ever_active:
                self.active = True
                self.executed = True
            return
        # an argument was delivered
        self.args_seen += 1
        self.cursor = min(self.cursor + 1, max(len(self.prompt_sequence) - 1, 0))
        if self.host_state_by_arg is not None:
            idx = min(self.args_seen - 1, len(self.host_state_by_arg) - 1)
            self.active = bool(self.host_state_by_arg[idx])
        elif self.never_idles:
            self.active = True
        elif self.args_seen >= self.n_args:
            # scripted host: the command closes once the last arg is in
            self.active = False
        if text.rstrip(TERMINATOR).strip() == "":
            self.active = False

    def count(self) -> int:
        self.count_calls += 1
        if self.rolled_back:
            return self.initial
        if self.count_calls == 1:
            return self.initial  # baseline read (T01 CaptureBaseline)
        if not self.executed:
            # the command never ran, so the document is still at its baseline
            return self.initial
        if self.entity_count_reads:
            return int(self.entity_count_reads.pop(0))
        return self.target

    def undo(self) -> str:
        self.undo_calls += 1
        if self.undo_raises:
            raise RuntimeError("undo refused by host")
        self.inputs.append(self.undo_text)
        self.rolled_back = True
        self.active = False
        return self.undo_text

    def artifact_valid(self) -> bool:
        return self.artifact_ok

    # -- optional, used by the machine when present --------------------

    #: the texts this host uses for cancel/undo. The machine reads them from
    #: the host instead of hardcoding command tokens, so it stays host-neutral.
    cancel_text = "^C^C^C" + TERMINATOR
    undo_text = "_.U" + TERMINATOR

    def cancel(self) -> str:
        self.cancel_calls += 1
        self.inputs.append(self.cancel_text)
        self.active = False
        return self.cancel_text

    @property
    def focus(self) -> bool:
        self.focus_reads += 1
        return self.focused

    def _prompt(self) -> str | None:
        if not self.prompt_sequence:
            return None
        if not self.prompt_sequence[0]:
            return None  # host exposes no prompt signal at all
        return self.prompt_sequence[min(self.cursor, len(self.prompt_sequence) - 1)]


# --------------------------------------------------------------------------
# vector execution
# --------------------------------------------------------------------------


def build_plan(given: dict[str, Any], **overrides: Any) -> RunPlan:
    given = {**given, **overrides}
    plan = RunPlan(
        command=given["command"],
        command_kind=CommandKind(given.get("command_kind", "native")),
        args=tuple(given.get("args") or ()),
        contract=tuple(given.get("contract") or ()),
        expect_delta_min=given.get("expect_delta_min"),
        expect_delta_max=given.get("expect_delta_max"),
        destructive=bool(given.get("destructive", False)),
        deadline_after_args=given.get("deadline_expires_after_args"),
        require_artifact=bool(given.get("require_artifact", False)),
    )
    if given.get("approved", True):
        plan = replace(plan, approval_token=plan.digest())
    return plan


def run_vector(vector: dict[str, Any], **overrides: Any) -> tuple[RunResult, FakeHost]:
    given = {**vector["given"], **overrides}
    host = FakeHost(given, **overrides)
    plan = build_plan(given)
    clock = FakeClock()
    result = RunStateMachine(
        host,
        plan,
        options=RunOptions(clock=clock, sleep=clock.sleep),
        mode=RunMode(given.get("mode", "strict")),
    ).run()
    return result, host


def assert_expect(vector: dict[str, Any], result: RunResult, host: FakeHost) -> None:
    expect = vector["expect"]
    vid = vector["id"]
    assert str(result.terminal_state) == expect["terminal_state"], vid
    assert result.inputs_sent == expect["inputs_sent"], vid
    assert result.rollback_called == expect["rollback_called"], vid
    assert result.final_count == expect["final_entities"], vid
    assert result.error_code == expect["error_code"], vid
    assert host.inputs == expect["inputs_sent"], vid
    for forbidden in expect.get("forbidden_inputs", ()):
        assert forbidden not in host.inputs, f"{vid}: forbidden input {forbidden!r} was sent"
    for skipped in expect.get("skipped_inputs", ()):
        assert skipped not in host.inputs, f"{vid}: skipped input {skipped!r} was sent"
    if expect.get("every_input_terminated"):
        assert all(text.endswith(TERMINATOR) for text in host.inputs), vid
    if "entity_count_read_attempts" in expect:
        assert result.count_read_attempts == expect["entity_count_read_attempts"], vid


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
def test_golden_vector(vector: dict[str, Any]) -> None:
    """One test per vector, 14 total, all loaded from the JSON on disk."""
    result, host = run_vector(vector)
    assert_expect(vector, result, host)


def test_vector_file_actually_loaded() -> None:
    assert len(VECTORS) == 14
    assert {v["id"] for v in VECTORS} == {f"GV-{i:02d}" for i in range(1, 15)}


def test_gv10_deadline_invariant_is_pinned() -> None:
    """Regression: what a timeout vector must and must not depend on.

    The corrected GV-10 exercises ``E_TIMEOUT``: the deadline expires after two
    args, so the run must stop with exactly two args sent and a rollback. That
    path is only reachable when the host keeps serving prompts that satisfy
    each step's contract. If the prompt changes to something the contract does
    not contain, guard ``prompt_matches_contract`` fires **first** and the
    verdict is ``E_PROMPT_MISMATCH``, not ``E_TIMEOUT``.

    This test replays the pre-correction defect (a one-entry
    ``prompt_sequence``, whose "reuse the last value" rule makes step 1
    mismatch) and pins that outcome, so the defect cannot silently return
    through a loosened machine or a loosened guard.
    """
    # 1. the corrected vector, verbatim from disk: deadline, not mismatch
    result, host = run_vector(VECTOR_BY_ID["GV-10"])
    assert_expect(VECTOR_BY_ID["GV-10"], result, host)
    assert result.error_code == "E_TIMEOUT"
    assert result.inputs_sent == ["_.LINE\n", "0,0\n", "1000,0\n", "^C^C^C\n", "_.U\n"]
    assert result.entity_delta is None
    assert "E_PROMPT_MISMATCH" not in {result.error_code}

    # 2. the data invariant that makes (1) reachable
    given = VECTOR_BY_ID["GV-10"]["given"]
    assert len(given["prompt_sequence"]) == len(given["args"]) == len(given["contract"])
    for index, contract in enumerate(given["contract"]):
        assert contract and contract in given["prompt_sequence"][index], (
            f"step {index}: contract {contract!r} is not served by prompt "
            f"{given['prompt_sequence'][index]!r}"
        )
    assert given["deadline_expires_after_args"] == 2

    # 3. the pre-correction defect, still a mismatch and never a timeout
    regressed, regressed_host = run_vector(
        VECTOR_BY_ID["GV-10"], prompt_sequence=["Specify first point: "]
    )
    assert regressed.error_code == "E_PROMPT_MISMATCH"
    assert regressed.inputs_sent == ["_.LINE\n", "0,0\n", "^C^C^C\n", "_.U\n"]
    assert regressed_host.undo_calls == 1


# --------------------------------------------------------------------------
# trap tests, one per trap
# --------------------------------------------------------------------------


def test_trap1_every_input_is_terminated_exactly_once() -> None:
    """Trap 1: no terminator -> inputs concatenate ("_.LINE_.U")."""
    assert terminate_every_input("_.LINE") == "_.LINE\n"
    assert terminate_every_input("_.LINE\n") == "_.LINE\n"
    assert terminate_every_input("_.LINE\n\n") == "_.LINE\n"
    for vector in VECTORS:
        result, host = run_vector(vector)
        for text in result.inputs_sent:
            assert text.endswith(TERMINATOR), f"{vector['id']}: unterminated {text!r}"
            assert not text.endswith(TERMINATOR * 2), f"{vector['id']}: doubled {text!r}"


def test_trap2_bare_enter_on_idle_is_never_sent() -> None:
    """Trap 2: a bare Enter on an idle prompt re-runs the last command."""
    given = {
        **VECTOR_BY_ID["GV-06"]["given"],
        # after the first arg the host is idle, so the empty arg must be skipped
        "host_state_by_arg": [False, False, False],
    }
    host = FakeHost(given)
    plan = build_plan(given)
    clock = FakeClock()
    result = RunStateMachine(
        host, plan, options=RunOptions(clock=clock, sleep=clock.sleep)
    ).run()

    assert TERMINATOR not in result.inputs_sent, "a bare enter reached the host"
    assert result.inputs_sent == ["_.LINE\n", "0,0\n", "1000,0\n"]
    assert ("T06", "awaiting_input", "awaiting_input", "no_bare_enter_when_idle=skip") in (
        result.transitions
    )
    assert str(result.terminal_state) == "committed"


def test_trap2_empty_arg_while_busy_is_refused() -> None:
    """The other half of trap 2: empty while active must stop, not send."""
    result, host = run_vector(VECTOR_BY_ID["GV-06"])
    assert result.error_code == "E_EMPTY_ARG_WHILE_BUSY"
    assert TERMINATOR not in host.inputs


def test_trap3_run_does_not_depend_on_focus() -> None:
    """Trap 3: the pump must not be an idle/focus event pump."""
    result, host = run_vector(VECTOR_BY_ID["GV-01"], focused=False)
    assert str(result.terminal_state) == "committed"
    assert host.focus_reads == 0, "the machine consulted window focus"
    assert host.focused is False, "the host was not actually unfocused"
    # the machine exposes no event surface at all
    assert not any(
        name in vars(RunStateMachine) for name in ("focus", "idle", "on_timer", "pump")
    )


def test_trap4_no_undo_for_a_command_that_never_started() -> None:
    """Trap 4: undoing an unstarted command destroys the user's work."""
    for vid in ("GV-04", "GV-05"):
        result, host = run_vector(VECTOR_BY_ID[vid])
        assert result.error_code == "E_COMMAND_NOT_STARTED"
        assert result.command_started is False
        assert result.rollback_called is False
        assert host.undo_calls == 0 and host.cancel_calls == 0
        for forbidden in VECTOR_BY_ID[vid]["expect"].get("forbidden_inputs", ()):
            assert forbidden not in host.inputs


def test_trap5_invocation_form_must_match_command_kind() -> None:
    """Trap 5: script prefix on a native command fails, and vice versa."""
    assert invocation_form_matches_command_kind("_.LINE", CommandKind.NATIVE) is True
    assert invocation_form_matches_command_kind("(c:AIC)", CommandKind.SCRIPT) is True
    assert invocation_form_matches_command_kind("(c:AIC)", CommandKind.NATIVE) is False
    assert invocation_form_matches_command_kind("AIC", CommandKind.SCRIPT) is False

    native = run_vector(VECTOR_BY_ID["GV-13"], command="(c:AIC_WALLSEG)", command_kind="native")
    assert native[0].error_code == "E_INVALID_INVOCATION"
    assert native[0].inputs_sent == []
    assert native[1].inputs == []

    script = run_vector(VECTOR_BY_ID["GV-13"], command="AIC_WALLSEG", command_kind="script")
    assert script[0].error_code == "E_INVALID_INVOCATION"
    assert script[1].inputs == []

    good, host = run_vector(VECTOR_BY_ID["GV-13"])
    assert str(good.terminal_state) == "committed"
    assert host.inputs[0] == "(c:AIC_WALLSEG)\n"


def test_trap6_counter_must_settle_before_a_delta_is_computed() -> None:
    """Trap 6: a transient 0 count is undecidable, not evidence."""
    result, host = run_vector(VECTOR_BY_ID["GV-11"])
    assert result.count_read_attempts == 3
    assert result.entity_delta == 1
    assert str(result.terminal_state) == "committed"

    always_zero = run_vector(
        VECTOR_BY_ID["GV-11"], entity_count_reads=[0, 0, 0], actual_final_entities=78
    )
    result2, host2 = always_zero
    assert result2.error_code == "E_COUNTER_UNSTABLE"
    assert result2.entity_delta is None, "a delta was computed from an unstable counter"
    assert str(result2.terminal_state) == "failed"
    assert result2.rollback_called is True


# --------------------------------------------------------------------------
# run modes and remaining error codes
# --------------------------------------------------------------------------


def test_observe_mode_sends_nothing_and_says_so() -> None:
    given = dict(VECTOR_BY_ID["GV-01"]["given"])
    host = FakeHost(given)
    plan = build_plan(given)
    clock = FakeClock()
    result = RunStateMachine(
        host, plan, options=RunOptions(clock=clock, sleep=clock.sleep), mode=RunMode.OBSERVE
    ).run()

    assert host.inputs == [], "observe mode wrote to the host"
    assert result.observed_only is True
    assert result.mode is RunMode.OBSERVE
    assert result.command_start_observed is None
    assert result.inputs_sent == ["_.LINE\n", "0,0\n"]  # what it *would* have sent
    assert result.would_send == ["_.LINE\n", "0,0\n"]
    assert "observe" in result.as_dict()["mode"]


def test_observe_mode_does_not_pass_a_gate_it_has_no_contract_for() -> None:
    """C-5: with no contract, a gate must not pass, and nothing is sent."""
    given = dict(VECTOR_BY_ID["GV-03"]["given"])
    host = FakeHost(given)
    plan = build_plan(given)
    clock = FakeClock()
    result = RunStateMachine(
        host, plan, options=RunOptions(clock=clock, sleep=clock.sleep), mode=RunMode.OBSERVE
    ).run()
    assert result.error_code == "E_PROMPT_MISMATCH"
    assert host.inputs == []

    blank = dict(VECTOR_BY_ID["GV-01"]["given"])
    blank["contract"] = [""]
    host2 = FakeHost(blank)
    clock2 = FakeClock()
    result2 = RunStateMachine(
        host2,
        build_plan(blank),
        options=RunOptions(clock=clock2, sleep=clock2.sleep),
        mode=RunMode.OBSERVE,
    ).run()
    assert result2.contract_missing == [0]
    assert host2.inputs == []
    assert result2.observed_only is True


@pytest.mark.parametrize(
    ("vector_id", "overrides", "expected_code"),
    [
        ("GV-01", {"never_idles": True}, "E_COMMAND_STILL_ACTIVE"),
        ("GV-07", {"undo_raises": True}, "E_ROLLBACK_FAILED"),
    ],
)
def test_failure_paths_with_never_ending_host(
    vector_id: str, overrides: dict[str, Any], expected_code: str
) -> None:
    result, host = run_vector(VECTOR_BY_ID[vector_id], **overrides)
    assert result.error_code == expected_code
    assert str(result.terminal_state) == "failed"
    assert result.stop_state is not None
    assert result.stop_reason


def test_busy_and_no_document_guards() -> None:
    given = dict(VECTOR_BY_ID["GV-01"]["given"])
    host = FakeHost(given)
    clock = FakeClock()
    busy = RunStateMachine(
        host,
        build_plan(given),
        options=RunOptions(clock=clock, sleep=clock.sleep),
        running_job=True,
    ).run()
    assert busy.error_code == "E_BUSY"
    assert busy.inputs_sent == []

    host2 = FakeHost(given)
    clock2 = FakeClock()
    nodoc = RunStateMachine(
        host2,
        build_plan(given),
        options=RunOptions(clock=clock2, sleep=clock2.sleep),
        active_document=False,
    ).run()
    assert nodoc.error_code == "E_NO_DOCUMENT"
    assert nodoc.inputs_sent == []


def test_approval_token_is_bound_to_the_plan_digest() -> None:
    given = dict(VECTOR_BY_ID["GV-12"]["given"])
    plan = build_plan(given, approved=False)
    host = FakeHost(given)
    clock = FakeClock()
    rejected = RunStateMachine(
        host, plan, options=RunOptions(clock=clock, sleep=clock.sleep)
    ).run()
    assert rejected.error_code == "E_APPROVAL_REQUIRED"
    assert rejected.inputs_sent == []

    stale = replace(plan, approval_token="0" * 64)
    host2 = FakeHost(given)
    clock2 = FakeClock()
    again = RunStateMachine(
        host2, stale, options=RunOptions(clock=clock2, sleep=clock2.sleep)
    ).run()
    assert again.error_code == "E_APPROVAL_REQUIRED"
    assert host2.inputs == []


def test_result_trace_is_readable() -> None:
    result, _host = run_vector(VECTOR_BY_ID["GV-07"])
    payload = result.as_dict()
    assert payload["terminal_state"] == "failed"
    assert payload["stop_state"] == "verifying"  # detected there, not in rolling_back
    assert "rolling_back" in payload["states_visited"]
    assert [t[0] for t in payload["transitions"]] == [
        "T01", "T03", "T05", "T07", "T08", "T09", "T11", "T12",
    ]
    assert payload["states_visited"][0] == "idle"
    assert payload["states_visited"][-1] == "failed"
    assert payload["entity_delta"] == 2


def test_t05_gate_predicate_keeps_its_own_contract_after_the_loop() -> None:
    """Each T05 prompt-gate predicate must stay bound to the contract of its own
    step, not to whichever contract the loop happened to leave behind.

    Two contracts are used (``First corner: `` / ``Other corner: ``) and each
    prompt contains only one of them, so a predicate that reads the wrong
    contract can only answer ``False``. The predicates are replayed *after* the
    run, when a late-bound closure would already be pointing at the last
    contract, so this pins the binding itself and not just the run outcome.
    """
    given = dict(VECTOR_BY_ID["GV-13"]["given"])
    assert len(given["contract"]) == 2, "this test needs a two-contract plan"
    host = FakeHost(given)
    plan = build_plan(given)
    clock = FakeClock()
    machine = RunStateMachine(
        host, plan, options=RunOptions(clock=clock, sleep=clock.sleep)
    )

    gates: list[tuple[object, str]] = []
    real_wait = machine._wait_until
    inside = {"draining": False}

    def spy_wait(predicate, timeout_ms):
        # only the T05 contract gate is of interest here; T08's
        # ``not in_command`` wait also runs inside _drain_args, so the
        # capture window stops at _wait_for_command_end.
        if inside["draining"]:
            gates.append((predicate, machine._obs()["prompt"]))
        return real_wait(predicate, timeout_ms)

    real_drain = machine._drain_args
    real_end = machine._wait_for_command_end

    def spy_drain():
        inside["draining"] = True
        try:
            return real_drain()
        finally:
            inside["draining"] = False

    def spy_end():
        inside["draining"] = False
        try:
            return real_end()
        finally:
            inside["draining"] = False

    machine._wait_until = spy_wait
    machine._drain_args = spy_drain
    machine._wait_for_command_end = spy_end
    result = machine.run()

    assert result.error_code is None, result.error_code
    assert result.inputs_sent == [(given["command"]) + "\n", "0,0\n", "1000,0\n"]

    prompts = [prompt for _predicate, prompt in gates]
    assert prompts == ["First corner: ", "Other corner: "], prompts

    # Replay each gate against every recorded prompt: it must accept only the
    # prompt of its own step.
    real_obs = machine._obs
    try:
        for index, (predicate, _own_prompt) in enumerate(gates):
            for other, other_prompt in enumerate(prompts):
                machine._obs = lambda p=other_prompt: {
                    "in_command": True,
                    "prompt": p,
                    "command_name": None,
                }
                got = predicate()
                if other == index:
                    assert got is True, (
                        f"gate {index} rejected its own prompt {other_prompt!r} "
                        f"(late binding read {prompts[-1]!r} instead)"
                    )
                else:
                    assert got is False, (
                        f"gate {index} accepted step {other}'s prompt {other_prompt!r}"
                    )
    finally:
        machine._obs = real_obs