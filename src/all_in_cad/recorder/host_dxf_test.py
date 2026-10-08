"""Golden vectors and unit tests against :class:`DxfFileHost` -- a real host.

What is different from ``machine_test.py``
------------------------------------------
``machine_test.py`` runs the vectors against ``FakeHost``, which is told what
prompt to print and what count to return. Nothing is written to disk there.
This module runs the same 14 vectors against a real DXF file: real entities
are created with ``ezdxf``, the entity count the state machine verifies is
counted by re-reading that file, the rollback is ``transaction.Txn.rollback``
and is checked against a SHA-256 captured before the recording, and an
external edit is checked to abort the restore instead of destroying the third
party's bytes.

How to read a result here
-------------------------
A pass proves: real geometry, real counts, real transactional rollback.
A pass does *not* prove: that a CAD program prints these prompts, that
``in_command`` means what it means in ZWCAD, or that a command in a real CAD
self-terminates (see RULE 6 in ``host_dxf.py`` -- here the recording plan's
declared arity is the terminator, which is a declared convention, not an
observation).

Every vector's prompt mapping is recorded in ``host_dxf.MAPPING_NOTES`` and
pinned by :func:`test_every_vector_has_a_declared_prompt_mapping`, so a
mapping cannot be invented silently. Vectors that cannot honestly be mapped
go into ``host_dxf.NA_VECTORS`` with a reason and are asserted to be reported
by :func:`test_na_vectors_are_reported_with_reasons`; that list is currently
empty and this test fails the moment a vector is dropped without a reason.

Environment note (observed on this host)::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \\
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\host_dxf_test.py -q
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

try:  # package-relative import (normal case)
    from .host_dxf import (
        COMMANDS,
        MAPPING_NOTES,
        NA_VECTORS,
        POINT_LAYER,
        WALL_LAYER,
        DxfFileHost,
        ReadFault,
        SessionRecord,
        WriterFaultProfile,
        seed_drawing,
        seed_session,
    )
    from .machine import CommandKind, RunMode, RunOptions, RunPlan, RunStateMachine
    from .transaction import (
        ExternalModificationError,
        capture_state,
        pending_journals,
        readback_dxf,
        recover_pending,
    )
except ImportError:  # pragma: no cover - flat execution fallback
    from all_in_cad.recorder.host_dxf import (
        COMMANDS,
        MAPPING_NOTES,
        NA_VECTORS,
        POINT_LAYER,
        WALL_LAYER,
        DxfFileHost,
        ReadFault,
        SessionRecord,
        WriterFaultProfile,
        seed_drawing,
        seed_session,
    )
    from all_in_cad.recorder.machine import (
        CommandKind,
        RunMode,
        RunOptions,
        RunPlan,
        RunStateMachine,
    )
    from all_in_cad.recorder.transaction import (
        ExternalModificationError,
        capture_state,
        pending_journals,
        readback_dxf,
        recover_pending,
    )

SPEC_DIR = Path(__file__).resolve().parent / "spec"
VECTOR_FILE = SPEC_DIR / "vectors" / "golden_vectors.json"

with VECTOR_FILE.open(encoding="utf-8") as _handle:
    _VECTOR_DOC = json.load(_handle)
VECTORS: list[dict[str, Any]] = _VECTOR_DOC["vectors"]
VECTOR_BY_ID = {vector["id"]: vector for vector in VECTORS}

#: Bounded, *real* waits. These are milliseconds of wall clock the machine
#: really waits; nothing here is virtualised.
REAL_OPTIONS = dict(
    settle_deadline_ms=600,
    prompt_gate_deadline_ms=600,
    command_end_deadline_ms=600,
    counter_retry_interval_ms=20,
    poll_interval_ms=10,
)


def build_plan(given: dict[str, Any]) -> RunPlan:
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


def make_host(path: Path, given: dict[str, Any], journal: Path, **kwargs: Any) -> DxfFileHost:
    """A real host over a real drawing, configured from the vector's ``given``."""
    if given.get("host_prompt_available") is False:
        # GV-09: a degraded build with no stage reader. Declared, not a file state.
        kwargs.setdefault("stage_reader", None)
    return DxfFileHost(
        path,
        declared_args=len(given.get("args") or ()) or 1,
        journal_dir=journal,
        **kwargs,
    )


def run_vector(
    vector: dict[str, Any], tmp_path: Path, **host_kwargs: Any
) -> tuple[Any, DxfFileHost, Path, str]:
    """Run one vector against a real DXF file. Returns result, host, path, pre-digest."""
    vid = vector["id"]
    given = vector["given"]
    path = seed_drawing(tmp_path / f"{vid}.dxf", int(given.get("initial_entities", 0)))
    if vid == "GV-03":
        # a crashed recorder left an open session in the drawing (RULE 3)
        seed_session(
            path,
            SessionRecord(
                owner="OTHER-RECORDER",
                command="AIC_DOORSEG",
                stage=1,
                declared=2,
                accepted=1,
                state="open",
            ),
        )
    host = make_host(path, given, tmp_path / "journal", **host_kwargs)
    pre_digest = capture_state(path).sha256 or ""
    plan = build_plan(given)
    result = RunStateMachine(
        host, plan, options=RunOptions(**REAL_OPTIONS), mode=RunMode.STRICT
    ).run()
    return result, host, path, pre_digest


# ======================================================================
# 1. the five methods, one at a time
# ======================================================================


def test_state_reports_no_prompt_when_there_is_no_drawing(tmp_path: Path) -> None:
    host = DxfFileHost(tmp_path / "absent.dxf", declared_args=1)
    state = host.state()
    assert state == {"in_command": False, "prompt": None, "command_name": None}


def test_state_reports_idle_prompt_for_a_real_empty_drawing(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 0)
    host = DxfFileHost(path, declared_args=1)
    assert host.state() == {
        "in_command": False,
        "prompt": "Command: ",
        "command_name": None,
    }


def test_state_reflects_the_stage_persisted_in_the_file(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 0)
    host = DxfFileHost(path, declared_args=3, journal_dir=tmp_path / "j")
    host.send("_.LINE\n")
    after_start = host.state()
    assert after_start["in_command"] is True
    assert after_start["command_name"] == "LINE"
    assert after_start["prompt"] == "Specify first point: "

    host.send("0,0\n")
    after_first = host.state()
    assert after_first["prompt"] == "Specify next point or [Undo]: "

    # the stage is in the file, not in this object: a fresh host sees it too
    other = DxfFileHost(path, declared_args=3, session_id=host.session_id)
    assert other.state()["prompt"] == after_first["prompt"]


def test_send_creates_real_geometry_in_the_file(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 2)
    host = DxfFileHost(path, declared_args=2, journal_dir=tmp_path / "j")
    before = readback_dxf(path)
    assert before["entity_count"] == 2

    host.send("_.LINE\n")
    host.send("0,0\n")
    host.send("250,125\n")

    after = readback_dxf(path)
    assert after["entity_count"] == 4, "one POINT per coordinate argument"
    assert after["entity_types"] == {"LINE": 2, "POINT": 2}
    assert after["layer_counts"].get(POINT_LAYER) == 2
    # a foreign reader (ezdxf, from the file only) can see the points
    assert host.artifact_valid() is True


def test_send_rejects_garbage_instead_of_swallowing_it(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 3)
    host = DxfFileHost(path, declared_args=1, journal_dir=tmp_path / "j")
    host.send("_.LINE\n")
    host.send("not-a-coordinate\n")
    report = readback_dxf(path)
    assert report["entity_count"] == 3, "a rejected argument writes nothing"
    assert host.state()["in_command"] is False, "the command aborts"
    assert any("not a single" in item for item in host._rejections), host._rejections


def test_count_is_a_read_back_of_the_real_file(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 7)
    host = DxfFileHost(path, declared_args=1, journal_dir=tmp_path / "j")
    assert host.count() == 7
    host.send("_.LINE\n")
    host.send("1,2\n")
    assert host.count() == 8
    assert host.count() == len(list(__import__("ezdxf").readfile(str(path)).modelspace()))


def test_count_reports_the_unstable_value_when_the_read_fails(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 4)
    host = DxfFileHost(
        path,
        declared_args=1,
        journal_dir=tmp_path / "j",
        read_fault=ReadFault(remaining=2, arm_after_applies=1),
    )
    assert host.count() == 4  # baseline: fault is not armed yet
    host.send("_.LINE\n")
    host.send("0,0\n")
    assert host.count() == 0, "a failed read-back is 0, not an empty drawing"
    assert host.count() == 0
    assert host.count() == 5, "the real count once the reads succeed again"
    assert host._count_failures == 2


def test_undo_restores_the_pre_recording_bytes(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 5)
    pre = capture_state(path)
    host = DxfFileHost(path, declared_args=1, journal_dir=tmp_path / "j")
    host.send("_.LINE\n")
    host.send("0,0\n")
    assert capture_state(path).sha256 != pre.sha256

    assert host.undo() == "_.U\n"
    post = capture_state(path)
    assert post.sha256 == pre.sha256
    assert post.size == pre.size
    assert readback_dxf(path)["entity_count"] == 5


def test_undo_refuses_to_delete_another_writers_bytes(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 5)
    pre = capture_state(path)
    host = DxfFileHost(path, declared_args=1, journal_dir=tmp_path / "j")
    host.send("_.LINE\n")
    host.send("0,0\n")

    # a third party edits the drawing behind the recorder's back
    import ezdxf  # noqa: PLC0415

    third = ezdxf.readfile(str(path))
    third.modelspace().add_point((9, 9, 0.0), dxfattribs={"layer": "AIC_SEED"})
    third.saveas(str(path))
    theirs = capture_state(path)
    assert theirs.sha256 != pre.sha256

    with pytest.raises(ExternalModificationError) as excinfo:
        host.undo()
    assert excinfo.value.path == path
    # their bytes are still there, ours is not silently "rolled back" over it
    assert capture_state(path).sha256 == theirs.sha256
    # 5 seeded + our 1 point + their 1 point: nothing of theirs was destroyed,
    # and our geometry was not removed behind their back either
    assert readback_dxf(path)["entity_count"] == 7


def test_undo_without_a_transaction_of_ours_does_nothing(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 2)
    pre = capture_state(path)
    host = DxfFileHost(path, declared_args=1, journal_dir=tmp_path / "j")
    assert host.undo() == "_.U\n"
    assert capture_state(path).sha256 == pre.sha256
    assert host.undo_refusals


def test_artifact_valid_opens_the_real_file(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 1)
    assert DxfFileHost(path).artifact_valid() is True
    assert DxfFileHost(tmp_path / "absent.dxf").artifact_valid() is False

    empty = tmp_path / "empty.dxf"
    import ezdxf  # noqa: PLC0415

    ezdxf.new("R2010").saveas(str(empty))
    assert DxfFileHost(empty).artifact_valid() is False, "no entities = not an artifact"

    broken = tmp_path / "broken.dxf"
    broken.write_text("this is not a dxf", encoding="utf-8")
    assert DxfFileHost(broken).artifact_valid() is False


def test_cancel_refuses_a_foreign_session(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 0)
    seed_session(
        path,
        SessionRecord(
            owner="OTHER",
            command="AIC_DOORSEG",
            stage=1,
            declared=2,
            accepted=1,
            state="open",
        ),
    )
    before = capture_state(path)
    host = DxfFileHost(path, declared_args=2, journal_dir=tmp_path / "j")
    assert host.cancel() == "^C^C^C\n"
    assert capture_state(path).sha256 == before.sha256, "not our session to close"
    assert host.cancel_refusals


def test_the_transaction_survives_until_the_caller_commits(tmp_path: Path) -> None:
    """A finished *command* is not a finished *recording*.

    T10/T11 can still reject the result after the command closed, so the
    journal and pre-images must still be there when the verdict arrives --
    and ``recover_pending`` must not undo a recording nobody committed.
    """
    path = seed_drawing(tmp_path / "d.dxf", 1)
    journal = tmp_path / "j"
    host = DxfFileHost(path, declared_args=1, journal_dir=journal)
    host.send("_.LINE\n")
    host.send("0,0\n")
    assert host.transaction_open is True
    assert len(pending_journals(journal)) == 1, "pre-images kept for the verdict"

    host.commit()
    assert pending_journals(journal) == []


def test_a_committed_recording_survives_recover_pending(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 1)
    journal = tmp_path / "j"
    host = DxfFileHost(path, declared_args=1, journal_dir=journal)
    host.send("_.LINE\n")
    host.send("0,0\n")
    recorded = capture_state(path).sha256
    host.commit()

    assert recover_pending(journal) == []
    assert capture_state(path).sha256 == recorded


def test_script_command_writes_a_wall_segment(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 0)
    host = DxfFileHost(path, declared_args=2, journal_dir=tmp_path / "j")
    host.send("(c:AIC_WALLSEG)\n")
    assert host.state()["prompt"] == "First corner: "
    host.send("0,0\n")
    host.send("1000,0\n")
    report = readback_dxf(path)
    assert report["entity_count"] == 1
    assert report["entity_types"] == {"LINE": 1}
    assert report["layer_counts"].get(WALL_LAYER) == 1
    assert (0.0, 0.0) in report["endpoints"] and (1000.0, 0.0) in report["endpoints"]


def test_erase_is_absent_from_the_command_table() -> None:
    assert "ERASE" not in COMMANDS
    assert "_.ERASE".split(".")[-1] not in COMMANDS


# ======================================================================
# 2. golden vectors against this real host
# ======================================================================

#: How each vector is set up on the real host. Anything not listed uses the
#: vector's own ``given`` block. Values are *factories*: a fault object holds
#: mutable state, so a shared instance would silently disarm the second test.
VECTOR_HOST_KWARGS: dict[str, Callable[[], dict[str, Any]]] = {
    "GV-07": lambda: {"faults": WriterFaultProfile(extra_entity_per_arg=True)},
    "GV-11": lambda: {"read_fault": ReadFault(remaining=2, arm_after_applies=1)},
}


def vector_host_kwargs(vector_id: str) -> dict[str, Any]:
    factory = VECTOR_HOST_KWARGS.get(vector_id)
    return factory() if factory is not None else {}


def assert_vector_expectations(vector: dict[str, Any], result: Any) -> None:
    expect = vector["expect"]
    vid = vector["id"]
    assert str(result.terminal_state) == expect["terminal_state"], vid
    assert result.inputs_sent == expect["inputs_sent"], vid
    assert result.rollback_called == expect["rollback_called"], vid
    assert result.final_count == expect["final_entities"], f"{vid}: final_count"
    if "error_code" in expect:
        assert result.error_code == expect["error_code"], vid


def assert_real_file(vector: dict[str, Any], path: Path, pre_digest: str) -> None:
    """The file itself must agree with the verdict -- read back, not asserted."""
    vid = vector["id"]
    expect = vector["expect"]
    if expect["terminal_state"] == "committed":
        report = readback_dxf(path)
        assert report["entity_count"] == expect["final_entities"], f"{vid}: entities"
        if vid in ("GV-13",):
            assert report["entity_types"] == {"LINE": 1}, vid
        else:
            assert report["entity_types"].get("POINT", 0) >= 1, vid
    else:
        # every failure vector must have left the drawing byte-identical
        assert capture_state(path).sha256 == pre_digest, (
            f"{vid}: a failed run must restore the pre-recording bytes"
        )


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
def test_golden_vector_against_real_dxf_host(vector: dict[str, Any], tmp_path: Path) -> None:
    result, host, path, pre_digest = run_vector(
        vector, tmp_path, **vector_host_kwargs(vector["id"])
    )
    assert_vector_expectations(vector, result)
    assert_real_file(vector, path, pre_digest)
    if result.ok:
        # the adapter does not decide a verdict; the recorder commits on one
        assert host.transaction_open is True
        host.commit()
        assert host.transaction_open is False


def test_all_fourteen_vectors_ran_against_real_files(tmp_path: Path) -> None:
    """No vector may be skipped, and none may be classified N/A silently."""
    ran = []
    for vector in VECTORS:
        assert vector["id"] not in NA_VECTORS, (
            f"{vector['id']} is marked N/A: {NA_VECTORS.get(vector['id'])}"
        )
        result, host, path, pre_digest = run_vector(
            vector, tmp_path / vector["id"], **vector_host_kwargs(vector["id"])
        )
        assert path.is_file(), f"{vector['id']} produced no real file"
        if result.error_code not in ("E_PROMPT_UNAVAILABLE", "E_APPROVAL_REQUIRED"):
            # GV-09 stops at T13 and GV-12 at T02, both before a baseline read
            assert result.baseline_count == int(
                vector["given"].get("initial_entities", 0)
            ), f"{vector['id']}: the baseline count came from the seeded drawing"
        ran.append(vector["id"])
    assert len(ran) == 14, ran
    assert len(set(ran)) == 14


def test_every_vector_has_a_declared_prompt_mapping() -> None:
    for vector in VECTORS:
        vid = vector["id"]
        assert vid in MAPPING_NOTES, f"{vid} has no declared prompt mapping"
        assert MAPPING_NOTES[vid].strip(), vid


def test_na_vectors_are_reported_with_reasons() -> None:
    """An N/A vector must carry a reason; the report prints this set verbatim."""
    for vid, reason in NA_VECTORS.items():
        assert reason.strip(), f"{vid} is N/A without a stated reason"
    # nothing is currently N/A; if this ever changes, the reason must be a
    # full sentence that names what the host cannot express
    assert NA_VECTORS == {}


def test_gv11_transient_counter_is_a_real_read_failure(tmp_path: Path) -> None:
    vector = VECTOR_BY_ID["GV-11"]
    result, host, path, _pre = run_vector(vector, tmp_path, **vector_host_kwargs("GV-11"))
    assert result.count_read_attempts == 3, vector["expect"]["entity_count_read_attempts"]
    assert host._count_failures == 2
    assert readback_dxf(path)["entity_count"] == vector["expect"]["final_entities"]


def test_wall_clock_deadline_also_stops_a_real_run(tmp_path: Path) -> None:
    """GV-10's other form: a real total deadline, not an arg budget.

    The arg budget (``deadline_after_args``) is how the vector is written. A
    real host can also blow a wall-clock budget, and that path is checked here
    with a real clock and a real deadline of 0 ms.
    """
    path = seed_drawing(tmp_path / "d.dxf", 4)
    pre = capture_state(path)
    host = DxfFileHost(path, declared_args=4, journal_dir=tmp_path / "j")
    plan = RunPlan(
        command="_.LINE",
        args=("0,0", "1000,0", "2000,0", "3000,0"),
        contract=("Specify first point: ",),
        expect_delta_min=4,
        expect_delta_max=4,
    )
    result = RunStateMachine(
        host,
        plan,
        options=RunOptions(
            total_deadline_ms=0,  # already spent, a real clock
            settle_deadline_ms=600,
            prompt_gate_deadline_ms=600,
            command_end_deadline_ms=600,
            poll_interval_ms=10,
        ),
        mode=RunMode.STRICT,
    ).run()
    assert result.error_code == "E_TIMEOUT"
    assert result.inputs_sent == [], "no input is spent after an expired deadline"
    assert capture_state(path).sha256 == pre.sha256


def test_observe_mode_never_touches_the_real_file(tmp_path: Path) -> None:
    path = seed_drawing(tmp_path / "d.dxf", 3)
    pre = capture_state(path)
    host = DxfFileHost(path, declared_args=1, journal_dir=tmp_path / "j")
    plan = RunPlan(command="_.LINE", args=("0,0",), contract=("Specify first point: ",))
    result = RunStateMachine(
        host, plan, options=RunOptions(**REAL_OPTIONS), mode=RunMode.OBSERVE
    ).run()
    assert result.observed_only is True
    # nothing was sent, so the drawing has no stage and the contract gate
    # cannot be satisfied: OBSERVE reports, it does not pass
    assert result.would_send == ["_.LINE\n"]
    assert result.error_code == "E_PROMPT_MISMATCH"
    assert capture_state(path).sha256 == pre.sha256
    assert host.sent == [], "OBSERVE must not reach the host at all"


def test_rollback_is_skipped_when_the_command_never_started(tmp_path: Path) -> None:
    """Trap 4: no undo may be issued for a command that never ran."""
    path = seed_drawing(tmp_path / "d.dxf", 9)
    pre = capture_state(path)
    host = DxfFileHost(path, declared_args=2, journal_dir=tmp_path / "j")
    plan = RunPlan(command="_.NOSUCHCMD", args=(), expect_delta_min=0, expect_delta_max=0)
    result = RunStateMachine(
        host, plan, options=RunOptions(**REAL_OPTIONS), mode=RunMode.STRICT
    ).run()
    assert result.error_code == "E_COMMAND_NOT_STARTED"
    assert result.rollback_called is False
    assert host.undo_calls == 0
    assert capture_state(path).sha256 == pre.sha256
    assert any("unknown command" in item for item in host._rejections)
