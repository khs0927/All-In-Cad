"""Run state machine: 8 states, 13 transitions, 16 guards, 13 error codes.

Authority
---------
``spec/run_spec.json`` is the authority (SPEC.md section 1). This module is an
implementation of that spec and of nothing else. No transition, guard or error
code was invented here; every branch below names the transition id it belongs
to. Where the spec is silent or internally inconsistent, the situation is
recorded in :class:`RunResult.notes` instead of being patched silently -- see
``docs`` notes on GV-06 / GV-10 in the test module.

Honesty about verification
--------------------------
Passing the golden vectors against the in-memory :class:`FakeHost` in
``machine_test.py`` proves that this state machine implements the spec's
decision logic. It does **not** prove that any real CAD host behaves the way
the fake host is made to behave. The vectors are an oracle authored from the
original C# runner, not from a fresh live-host experiment (SPEC.md section 6
labels that conformance question as not yet established). Passing here is not
evidence about a real CAD host.

Host neutrality
---------------
The machine never imports a CAD SDK, never touches a mouse or a keyboard, and
never subscribes to an idle/focus event. The host is injected as a five-method
protocol (:class:`Host`). Every wait in this module is a bounded poll driven by
an injectable clock, so the run advances identically whether or not the
application window has focus (guard ``schedule_is_not_focus_dependent``).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

__all__ = [
    "TERMINATOR",
    "CommandKind",
    "Host",
    "RunMode",
    "RunOptions",
    "RunPlan",
    "RunResult",
    "RunState",
    "RunStateMachine",
    "invocation_form_matches_command_kind",
    "terminate_every_input",
]

#: Guard ``terminate_every_input`` -- exactly one of this, on every send.
TERMINATOR = "\n"

#: Guard ``rollback_requires_started_command`` reason, kept for result clarity.
_ROLLBACK_FORBIDDEN = "forbidden"


class RunState(StrEnum):
    """The eight states of run_spec.json -> states."""

    IDLE = "idle"
    LAUNCHING = "launching"
    AWAITING_INPUT = "awaiting_input"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMMITTED = "committed"
    ROLLING_BACK = "rolling_back"
    FAILED = "failed"


class CommandKind(StrEnum):
    """Guard ``invocation_form_matches_command_kind``."""

    NATIVE = "native"
    SCRIPT = "script"


class RunMode(StrEnum):
    """Execution mode. STRICT is the safe default; see ``run()`` for OBSERVE."""

    STRICT = "strict"
    OBSERVE = "observe"


@runtime_checkable
class Host(Protocol):
    """The entire surface this state machine is allowed to touch.

    Five methods cover ZWCAD-style command hosts, file-based hosts and test
    doubles alike. There is deliberately no click/keyboard/event member: the
    project failed before because input went through the OS event layer.
    """

    def state(self) -> dict:
        """Current host state.

        Keys used by this module: ``in_command`` (bool), ``prompt``
        (str | None), ``command_name`` (str | None). A host that reports no
        prompt signal at all (``prompt is None``) fails guard
        ``host_prompt_available``.
        """

    def send(self, text: str) -> None:
        """Deliver one already-terminated text input to the host."""

    def count(self) -> int:
        """Entity count for verification. May transiently return 0."""

    def undo(self) -> str | None: ...

    def artifact_valid(self) -> bool: ...


#: Optional host members this module uses when present. Both are advisory:
#: the machine never requires them.
_CANCEL = "cancel"


def terminate_every_input(text: str, terminator: str = TERMINATOR) -> str:
    """Guard ``terminate_every_input``.

    Return ``text`` with exactly one terminator appended. Input that already
    carries terminators is normalised rather than double-terminated: two
    terminators in a row is as wrong as none at all, and a missing terminator
    is the observed cause of ``"_.LINE_.U" -> Unknown command``.
    """
    if not isinstance(text, str):
        raise TypeError(f"host input must be str, got {type(text).__name__}")
    if terminator != TERMINATOR:
        raise ValueError(f"the spec fixes the terminator to {TERMINATOR!r}")
    return text.rstrip(TERMINATOR) + terminator


_SCRIPT_CALL = re.compile(r"^\(c:[A-Za-z_][\w$:.-]*\)$")


def invocation_form_matches_command_kind(command: str, kind: CommandKind) -> bool:
    """Guard ``invocation_form_matches_command_kind`` (trap 5).

    A native command is a plain globalised token and must not carry the
    script call form. A script command must be a ``(c:NAME)`` call. The spec
    names the two forms without fixing their exact spelling, so the check is
    on the observable distinction (script call wrapper present or absent)
    rather than on a particular token table.
    """
    command = str(command)
    if kind is CommandKind.SCRIPT:
        return bool(_SCRIPT_CALL.match(command))
    return bool(command) and not command.lstrip().startswith("(c:")


@dataclass(frozen=True, slots=True)
class RunPlan:
    """One recording run: a command, its args, and the contract per arg."""

    command: str
    command_kind: CommandKind = CommandKind.NATIVE
    args: tuple[str, ...] = ()
    contract: tuple[str, ...] = ()
    expect_delta_min: int | None = None
    expect_delta_max: int | None = None
    destructive: bool = False
    approval_token: str | None = None
    #: Total deadline expressed in args sent (``None`` = no deadline). Used by
    #: GV-10, where the total deadline expires mid-arg-drain.
    deadline_after_args: int | None = None
    require_artifact: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", tuple(str(a) for a in self.args))
        object.__setattr__(self, "contract", tuple(str(c) for c in self.contract))
        if len(self.contract) > len(self.args):
            raise ValueError("contract has more entries than args")

    def digest(self) -> str:
        """Plan digest, for guard ``destructive_requires_approval``.

        Approval is bound to this exact plan, so a token minted for another
        plan cannot authorise a destructive run.
        """
        payload = json.dumps(
            {
                "command": self.command,
                "command_kind": str(self.command_kind),
                "args": self.args,
                "contract": self.contract,
                "expect_delta_min": self.expect_delta_min,
                "expect_delta_max": self.expect_delta_max,
                "destructive": self.destructive,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def approval_is_valid(self) -> bool:
        return bool(self.approval_token) and self.approval_token == self.digest()

    def invocation_text(self) -> str | None:
        """The single execution string, or ``None`` if the form is wrong."""
        if not invocation_form_matches_command_kind(self.command, self.command_kind):
            return None
        return self.command


@dataclass(slots=True)
class RunOptions:
    """Timing and scheduler knobs. The clock is injectable so tests are fast."""

    settle_deadline_ms: int = 1500  # guards command_started / start_settle_...
    prompt_gate_deadline_ms: int = 2500  # guard prompt_matches_contract
    command_end_deadline_ms: int = 1500  # guard command_completed (no spec value)
    counter_retries: int = 3  # guard entity_count_stable
    counter_retry_interval_ms: int = 120  # guard entity_count_stable
    unstable_value: int = 0  # guard entity_count_stable
    poll_interval_ms: int = 20
    #: ``None`` = no wall-clock total deadline (a fake clock makes it a no-op).
    total_deadline_ms: int | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


@dataclass(slots=True)
class RunResult:
    """Full trace. A failed run must say which state stopped and why."""

    mode: RunMode
    terminal_state: RunState
    error_code: str | None = None
    stop_state: RunState | None = None
    stop_reason: str = ""
    #: ``(transition_id, from, to, guard)`` for every transition taken.
    transitions: list[tuple[str, str, str, str]] = field(default_factory=list)
    states_visited: list[str] = field(default_factory=list)
    inputs_sent: list[str] = field(default_factory=list)
    #: What the machine *would* have sent. Populated in OBSERVE mode only.
    would_send: list[str] = field(default_factory=list)
    prompts_seen: list[str | None] = field(default_factory=list)
    contract_missing: list[int] = field(default_factory=list)
    command_started: bool = False
    #: None when command start was not observable at all (observe mode, where
    #: nothing was sent and therefore nothing could be observed to start).
    command_start_observed: bool | None = None
    cancel_called: bool = False
    rollback_called: bool = False
    baseline_count: int | None = None
    count_read_attempts: int = 0
    final_count: int | None = None
    entity_delta: int | None = None
    artifact_valid: bool | None = None
    observed_only: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.terminal_state is RunState.COMMITTED

    def as_dict(self) -> dict:
        return {
            "mode": str(self.mode),
            "terminal_state": str(self.terminal_state),
            "error_code": self.error_code,
            "stop_state": str(self.stop_state) if self.stop_state else None,
            "stop_reason": self.stop_reason,
            "transitions": [list(t) for t in self.transitions],
            "states_visited": list(self.states_visited),
            "inputs_sent": list(self.inputs_sent),
            "would_send": list(self.would_send),
            "prompts_seen": list(self.prompts_seen),
            "contract_missing": list(self.contract_missing),
            "command_started": self.command_started,
            "command_start_observed": self.command_start_observed,
            "rollback_called": self.rollback_called,
            "baseline_count": self.baseline_count,
            "count_read_attempts": self.count_read_attempts,
            "final_count": self.final_count,
            "entity_delta": self.entity_delta,
            "artifact_valid": self.artifact_valid,
            "observed_only": self.observed_only,
            "notes": list(self.notes),
        }


class RunStateMachine:
    """Executes one :class:`RunPlan` against one injected :class:`Host`."""

    def __init__(
        self,
        host: Host,
        plan: RunPlan,
        *,
        options: RunOptions | None = None,
        mode: RunMode = RunMode.STRICT,
        running_job: bool = False,
        active_document: bool = True,
    ) -> None:
        self._host = host
        self._plan = plan
        self._opt = options or RunOptions()
        self._mode = mode
        self._running_job = running_job
        self._active_document = active_document
        self._result = RunResult(mode=mode, terminal_state=RunState.IDLE)
        self._state = RunState.IDLE
        self._started_at = self._opt.clock()
        # OBSERVE mode: no host.send / cancel / undo is ever issued.
        self._sending_enabled = mode is RunMode.STRICT
        self._result.observed_only = not self._sending_enabled

    # ---------------------------------------------------------------- utils

    def _visit(self, state: RunState) -> None:
        self._state = state
        if not self._result.states_visited or self._result.states_visited[-1] != state:
            self._result.states_visited.append(str(state))

    def _take(self, tid: str, to_state: RunState, guard: str) -> None:
        self._result.transitions.append((tid, str(self._state), str(to_state), guard))
        self._visit(to_state)

    def _send(self, text: str) -> None:
        """Every host write goes through here, so guard 1 cannot be bypassed."""
        payload = terminate_every_input(text)
        if self._sending_enabled:
            self._host.send(payload)
        else:
            self._result.would_send.append(payload)
        self._result.inputs_sent.append(payload)

    def _obs(self) -> dict:
        observed = self._host.state() or {}
        return {
            "in_command": bool(observed.get("in_command", False)),
            "prompt": observed.get("prompt"),
            "command_name": observed.get("command_name"),
        }

    def _wait_until(self, predicate: Callable[[], bool], timeout_ms: int) -> bool:
        """Bounded poll.

        This is the whole scheduler. It has no event source, no idle hook and
        no focus test, which is what guard
        ``schedule_is_not_focus_dependent`` demands; a host that cannot be
        polled cannot be driven by this machine.
        """
        step = max(self._opt.poll_interval_ms, 1) / 1000.0
        waited = 0.0
        while True:
            if predicate():
                return True
            if waited >= timeout_ms / 1000.0:
                return False
            self._opt.sleep(step)
            waited += step

    def _deadline_reached(self) -> bool:
        if self._opt.total_deadline_ms is None:
            return False
        return (self._opt.clock() - self._started_at) * 1000.0 >= self._opt.total_deadline_ms

    def _note(self, text: str) -> None:
        self._result.notes.append(text)

    # --------------------------------------------------------------- exits

    def _stop(
        self,
        state: RunState,
        code: str,
        reason: str,
        stop_state: RunState | None = None,
    ) -> RunResult:
        self._result.terminal_state = state
        self._result.error_code = code
        # the state the failure was *detected* in, not the terminal state
        self._result.stop_state = stop_state or self._state
        self._result.stop_reason = reason
        self._result.final_count = self._count_quietly()
        return self._result

    def _count_quietly(self) -> int | None:
        if self._result.final_count is not None:
            return self._result.final_count
        try:
            return self._host.count()
        except Exception:  # noqa: BLE001 - a count failure must not mask the real error
            return None

    def _enter_rolling_back(self, tid: str, code: str, reason: str) -> RunResult:
        """on_failure path shared by T05/T06/T08/T09/T10/T11 style failures."""
        origin = self._state
        self._take(tid, RunState.ROLLING_BACK, "on_failure")
        return self._rollback_or_skip(code, reason, stop_state=origin)

    def _rollback_or_skip(
        self, code: str, reason: str, stop_state: RunState | None = None
    ) -> RunResult:
        """T12 -- guard ``rollback_requires_started_command``.

        This is the only place a cancel/undo is issued. The sole precondition
        is ``command_started``, which is set only by observing the host enter
        ``in_command=True`` after the execution string -- never by the act of
        sending it. Trap 4.
        """
        if not self._sending_enabled:
            self._note(
                "observe mode: no cancel/undo issued, and no rollback verdict is implied"
            )
            self._take("T12", RunState.FAILED, "observe_mode:no_rollback")
            return self._stop(RunState.FAILED, code, reason, stop_state)
        if not self._result.command_started:
            self._note(
                f"rollback skipped ({_ROLLBACK_FORBIDDEN}): {code} -- the command never "
                "started, so undoing would destroy the user's own work"
            )
            self._take("T12", RunState.FAILED, "rollback_requires_started_command=false")
            return self._stop(RunState.FAILED, code, reason, stop_state)
        # Cancel the running command first, then undo exactly once. The texts
        # are whatever the host declares -- the machine never hardcodes a
        # command token, so the same code drives any host's cancel/undo form.
        for _step, member, flag in (
            (_CANCEL, "cancel", "cancel_called"),
            ("undo", "undo", "rollback_called"),
        ):
            text = None
            if self._sending_enabled:
                call = getattr(self._host, member, None)
                if not callable(call):
                    self._note(f"host has no {member}(); rollback step skipped")
                    continue
                try:
                    text = call()
                except Exception as exc:  # noqa: BLE001
                    self._note(f"{member} raised: {exc}")
                    # S3: undo failure already failed loudly below. cancel
                    # failure used to `continue`, so the run went on to report
                    # "rollback issued: cancel then one undo" with
                    # cancel_called=True while cancel had in fact never run --
                    # a rollback that never began, announced as one that did.
                    # A rollback step that fails is a failed rollback, on the
                    # same footing as undo.
                    self._take("T12", RunState.FAILED, "rollback_requires_started_command")
                    return self._stop(
                        RunState.FAILED,
                        "E_ROLLBACK_FAILED",
                        f"{member} failed: {exc}",
                        stop_state,
                    )
                if text is None:
                    text = getattr(self._host, f"{member}_text", None)
            else:
                text = getattr(self._host, f"{member}_text", None)
            if text:
                payload = terminate_every_input(str(text))
                self._result.inputs_sent.append(payload)
                if not self._sending_enabled:
                    self._result.would_send.append(payload)
            setattr(self._result, flag, True)
        # the document changed under us; the reported count must be re-read
        # after the rollback, not reused from the pre-rollback verification
        self._result.final_count = None
        self._note("rollback issued: cancel then one undo")
        self._take("T12", RunState.FAILED, "rollback_requires_started_command=true")
        return self._stop(RunState.FAILED, code, reason, stop_state)

    # ------------------------------------------------------------------ run

    def run(self) -> RunResult:
        plan = self._plan
        self._visit(RunState.IDLE)

        # --- T01 guard: job_admissible -----------------------------------
        if self._running_job:
            self._take("T01", RunState.FAILED, "job_admissible=false")
            return self._stop(
                RunState.FAILED, "E_BUSY", "a run is already in progress", RunState.IDLE
            )
        if not self._active_document:
            self._take("T01", RunState.FAILED, "job_admissible=false")
            return self._stop(
                RunState.FAILED,
                "E_NO_DOCUMENT",
                "no active document on the host",
                RunState.IDLE,
            )
        if self._deadline_reached():
            self._take("T01", RunState.FAILED, "job_admissible=false")
            return self._stop(
                RunState.FAILED, "E_TIMEOUT", "total deadline already spent", RunState.IDLE
            )

        # --- T02: destructive_requires_approval --------------------------
        # Approval is taken here, in idle, and nowhere later. The reject path
        # sends nothing at all.
        if plan.destructive and not plan.approval_is_valid():
            self._take("T02", RunState.FAILED, "destructive_requires_approval=false")
            return self._stop(
                RunState.FAILED,
                "E_APPROVAL_REQUIRED",
                "destructive plan without a valid approval token for this plan digest",
                RunState.IDLE,
            )

        self._take("T01", RunState.LAUNCHING, "job_admissible=true")

        # --- T01 body: invocation form, then prompt availability ---------
        invocation = plan.invocation_text()
        if invocation is None:
            self._note(
                f"invocation form rejected: {plan.command!r} is not a valid "
                f"{plan.command_kind} call form"
            )
            self._take("T01", RunState.FAILED, "invocation_form_matches_command_kind=false")
            return self._stop(
                RunState.FAILED,
                "E_INVALID_INVOCATION",
                f"call form does not match command kind {plan.command_kind}",
                RunState.LAUNCHING,
            )

        # --- T13: host_prompt_available ----------------------------------
        # Checked before the execution string is sent: a host with no prompt
        # signal cannot run a contract-driven execution, and must not be fed.
        probe = self._obs()
        if probe["prompt"] is None:
            self._note("host reports no prompt signal; contract-driven run is impossible")
            self._take("T13", RunState.FAILED, "host_prompt_available=false")
            return self._stop(
                RunState.FAILED, "E_PROMPT_UNAVAILABLE", "host exposes no prompt signal",
                RunState.LAUNCHING,
            )

        # T01 action: baseline snapshot, then exactly one terminated send.
        self._result.baseline_count = self._count_quietly()
        self._send(invocation)

        # --- T03 / T04: did the command actually start? ------------------
        if self._sending_enabled:
            started = self._wait_until(
                lambda: self._obs()["in_command"],
                self._opt.settle_deadline_ms,
            )
            self._result.command_start_observed = bool(started)
        else:
            # Nothing was sent, so a start cannot be observed. Reporting
            # E_COMMAND_NOT_STARTED here would be a false verdict about the
            # host; the drain below is a report of what would be sent.
            self._note(
                "observe mode: command start is not observable because no execution "
                "string was sent; steps after launch are reported, not verified"
            )
            self._result.command_start_observed = None
            started = True
        if started:
            if self._sending_enabled:
                self._result.command_started = True
            self._take("T03", RunState.AWAITING_INPUT, "command_started=true")
        else:
            self._take("T04", RunState.ROLLING_BACK, "start_settle_deadline_expired=true")
            return self._rollback_or_skip(
                "E_COMMAND_NOT_STARTED",
                f"no in_command observation within {self._opt.settle_deadline_ms}ms of "
                "the execution string; the string may still sit in the input buffer",
                stop_state=RunState.LAUNCHING,
            )

        return self._drain_args()

    # ------------------------------------------------------- T05 / T06 / T07

    def _drain_args(self) -> RunResult:
        plan = self._plan
        step = 0
        total = len(plan.args)

        while step < total:
            # Guard: total deadline, checked before any prompt contract so an
            # expired run cannot spend another input. (GV-10 path.)
            if plan.deadline_after_args is not None and step >= plan.deadline_after_args:
                return self._enter_rolling_back(
                    "T07", "E_TIMEOUT", f"total deadline expired after {step} args"
                )
            if self._deadline_reached():
                return self._enter_rolling_back(
                    "T07", "E_TIMEOUT", "total deadline expired while draining args"
                )

            observed = self._obs()
            prompt = observed["prompt"]
            self._result.prompts_seen.append(prompt)
            if prompt is None:
                return self._enter_rolling_back(
                    "T05", "E_PROMPT_UNAVAILABLE", "prompt signal disappeared mid-run"
                )

            contract = plan.contract[step] if step < len(plan.contract) else ""
            arg = plan.args[step]

            if not contract:
                # No contract for this step: observation only, never a gate.
                self._result.contract_missing.append(step)
                self._note(f"step {step}: no contract string, observed prompt only")

            if arg == "":
                # T06 -- guard no_bare_enter_when_idle. A bare Enter on an idle
                # prompt re-runs the previous command; an empty arg while the
                # command is live must not be sent at all. Trap 2.
                if not observed["in_command"]:
                    self._take("T06", RunState.AWAITING_INPUT, "no_bare_enter_when_idle=skip")
                    self._note(f"step {step}: empty arg on an idle host, nothing sent")
                    step += 1
                    continue
                return self._enter_rolling_back(
                    "T06",
                    "E_EMPTY_ARG_WHILE_BUSY",
                    f"step {step}: empty arg while the command is active; not sent",
                )

            if contract:
                # T05 -- guard prompt_matches_contract (substring, ordinal). The
                # contract is bound as a default argument rather than read late from
                # the enclosing ``while``: ``_wait_until`` invokes this predicate
                # synchronously before returning, so the live value would be correct
                # anyway, and the binding only removes the dependence on that calling
                # discipline (ruff B023).
                if not self._wait_until(
                    lambda c=contract: self._prompt_matches(self._obs()["prompt"], c),
                    self._opt.prompt_gate_deadline_ms,
                ):
                    return self._enter_rolling_back(
                        "T05",
                        "E_PROMPT_MISMATCH",
                        f"step {step}: prompt {prompt!r} does not contain contract "
                        f"{contract!r} within {self._opt.prompt_gate_deadline_ms}ms",
                    )
                seen = self._obs()["prompt"]
                self._result.prompts_seen.append(seen)

            # T05 action: one arg, one terminator.
            self._send(arg)
            self._take(
                "T05", RunState.AWAITING_INPUT, "prompt_matches_contract=true (arg sent)"
            )
            step += 1

        # T07 -- guard all_args_sent. No further input.
        self._take("T07", RunState.EXECUTING, "all_args_sent=true")
        return self._wait_for_command_end()

    def _prompt_matches(self, prompt: str | None, contract: str) -> bool:
        if not contract:
            return True
        return prompt is not None and contract in prompt

    # ------------------------------------------------------------ T08

    def _wait_for_command_end(self) -> RunResult:
        if self._wait_until(
            lambda: not self._obs()["in_command"], self._opt.command_end_deadline_ms
        ):
            self._take("T08", RunState.VERIFYING, "command_completed=true")
            return self._verify()
        return self._enter_rolling_back(
            "T08",
            "E_COMMAND_STILL_ACTIVE",
            f"command still active after {self._opt.command_end_deadline_ms}ms with all "
            "args sent",
        )

    # ------------------------------------------------- T09 / T10 / T11

    def _verify(self) -> RunResult:
        plan = self._plan
        unstable_code: str | None = None

        # T09 -- guard entity_count_stable. A transient 0 is undecidable, not
        # evidence, so it is retried and never turned into a delta. Trap 6.
        stable_value: int | None = None
        for _ in range(max(self._opt.counter_retries, 1)):
            value = int(self._host.count())
            self._result.count_read_attempts += 1
            if value != self._opt.unstable_value:
                stable_value = value
                break
            self._opt.sleep(self._opt.counter_retry_interval_ms / 1000.0)
        if stable_value is None:
            unstable_code = "E_COUNTER_UNSTABLE"
            self._note(
                f"counter unstable after {self._result.count_read_attempts} reads "
                f"(all {self._opt.unstable_value}); undecidable, not a failure verdict"
            )
        else:
            self._take("T09", RunState.VERIFYING, "entity_count_stable=true")

        if plan.require_artifact:
            try:
                self._result.artifact_valid = bool(self._host.artifact_valid())
            except Exception as exc:  # noqa: BLE001
                self._result.artifact_valid = False
                self._note(f"artifact_valid raised: {exc}")
            # The spec defines no transition on artifact_valid, so it is
            # recorded as verification evidence and is not a gate. [see report]
            self._note(
                f"artifact_valid={self._result.artifact_valid} recorded; run_spec.json "
                "defines no transition for it, so it does not gate T10/T11"
            )

        if unstable_code is not None:
            return self._enter_rolling_back(
                "T09",
                unstable_code,
                "entity counter never left the unstable value; delta is undecidable",
            )

        self._result.final_count = stable_value
        baseline = self._result.baseline_count
        assert baseline is not None  # set in run() before the first send
        delta = int(stable_value) - int(baseline)
        self._result.entity_delta = delta
        low = plan.expect_delta_min if plan.expect_delta_min is not None else 0
        high = plan.expect_delta_max if plan.expect_delta_max is not None else float("inf")
        in_range = low <= delta <= high

        if in_range:
            self._take("T10", RunState.COMMITTED, "entity_delta_in_range=true")
            return self._stop(
                RunState.COMMITTED, None, f"delta {delta} within [{low}, {high}]"
            )
        return self._enter_rolling_back(
            "T11",
            "E_DELTA_MISMATCH",
            f"delta {delta} outside [{low}, {high}]",
        )


def run_plan(
    host: Host,
    plan: RunPlan,
    *,
    options: RunOptions | None = None,
    mode: RunMode = RunMode.STRICT,
    running_job: bool = False,
    active_document: bool = True,
) -> RunResult:
    """Convenience wrapper: build the machine and run it once."""
    return RunStateMachine(
        host,
        plan,
        options=options,
        mode=mode,
        running_job=running_job,
        active_document=active_document,
    ).run()
