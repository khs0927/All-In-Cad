"""End-to-end integration test: the whole recording stack in one continuous run.

=============================================================================
WHAT THIS FILE IS, AND WHAT IT IS NOT
=============================================================================
Every other test file in this package exercises ONE module: wall, door, the
CLI's process contract, the transaction machinery, the machine planner, the
FreeCAD wrapper. Each of them can be green while the *path a user actually
walks* is broken -- the composition seam is untested. This file walks that
path end to end, in the order a real job walks it, without hand-holding.

A PASS HERE IS A NARROW CLAIM. It says: on this host, on this commit, for the
six scenarios below, the stack composed correctly. It does NOT say the
individual modules are correct, because this file samples them through the
CLI and the transaction API rather than through their own unit oracles. The
module-level suites remain the authority on their own contracts; this file is
the authority on whether those pieces agree with each other.

=============================================================================
LABELLING CONVENTION
=============================================================================
Every claim in this file is tagged:
  [OBSERVED]  measured on this host during development; the test asserts it.
  [INFERRED]  derived from code reading, not run here.
  [BLOCKED]   could not be established; the reason is in the test name or a
              skip message.
Where a test records behaviour that is real but *undesirable*, the test name
says so (``test_defect_*``) and the test asserts what actually happens rather
than what ought to happen. A test that asserted the desirable behaviour would
be a wish, and a wish that fails is indistinguishable from a bug in the test.

=============================================================================
SCENARIO 1 - FRESH DRAWING, WHOLE PIPELINE  [OBSERVED]
=============================================================================
Empty directory -> ``plan`` (wall + door, aligned) -> ``verify`` (19 checks) ->
read the DXF back with ezdxf and confirm the reference case:
centreline (0,0)->(12000,0), thickness 200, door centre (6000,0), width 900
  => 11 entities: CEN1 1, WAL1 2, WAL2 2, DOOR 4, DOOR_ELE 2
  => LINE 10 + ARC 1, no INSERT, no HATCH
  => verify exits 0; a deliberately wrong expectation exits 1.

=============================================================================
SCENARIO 2 - ROLLBACK  [OBSERVED]
=============================================================================
The CLI has no rollback path, so this scenario drives the transaction API the
way a host would: capture a wall-only drawing, then apply a door recording
under a verifier seeded with a WRONG expected count, and require that
(a) the recording is rejected, and
(b) the file is restored to the pre-state BYTE FOR BYTE (SHA-256).
This is also where C-1..C-4 are exercised, because those claims are about this
path specifically. Each is asserted separately and named after the claim, so a
regression in one control is not hidden by the others passing.

=============================================================================
SCENARIO 3 - EXTERNAL INTERVENTION  [OBSERVED]
=============================================================================
A third party edits the drawing between our ``apply`` and our ``rollback``.
Required: ExternalModificationError, and the third party's bytes survive
untouched. This is C-4's whole point and is the strongest claim this file
makes: failing is strictly better than deleting someone else's work.

=============================================================================
SCENARIO 4 - FreeCAD ROUND TRIP  [OBSERVED / SKIPPABLE]
=============================================================================
Open the recorded DXF in real FreeCAD 1.1.3 headless and require 11
Part::Feature shapes with the 5 named layers preserved. The verdict is taken
from a JSON artifact the FreeCAD-side script writes, because the stdout marker
channel is known-dead on this host (see freecad_runner.py's own docstring).
Skips with an explicit reason if either the binary or the wrapper is absent --
"skipped for lack of environment", never silently passed.

=============================================================================
SCENARIO 5 - IDEMPOTENCE: THE ACTUAL BEHAVIOUR  [OBSERVED, MIXED]
=============================================================================
The brief asked for the real behaviour to be recorded rather than a guarantee
invented. The real behaviour is not uniform, and this is the file's most
important finding:

  * ``plan`` run twice to the same path  -> IDEMPOTENT. Still 11 entities,
    because every subcommand builds a fresh in-memory document and
    ``saveas`` replaces the file wholesale. Nothing accumulates.

  * ``door --out EXISTING.dxf``          -> DESTRUCTIVE. The standalone
    ``door`` subcommand also calls ``_new_document()`` and never reads the
    target, so recording a door into a wall drawing SILENTLY DELETES the wall
    and exits 0 claiming success. Measured: 5 wall entities -> 6 door-only
    entities, zero wall geometry left, exit code 0, JSON says ``"ok": true``.

    This is the exact failure mode cli.py's own docstring says this repository
    has already suffered once ("A failing verify must never be reported as a
    success"), in a new place. It is reported here, NOT fixed: cli.py is
    outside this task's file scope, and the fix is a design decision (should
    ``door`` append, or refuse, or warn?) that belongs to whoever owns cli.py.

=============================================================================
SCENARIO 6 - DESTRUCTIVE INPUT REJECTION  [OBSERVED]
=============================================================================
width 0, negative thickness, and a zero-length centreline must all be rejected
with a usage exit code AND leave no drawing file behind. The "no file left
behind" half matters more than the exit code: a rejected job that leaves a
half-written DXF on disk is how drawings get silently lost.

=============================================================================
SCENARIO 7 - WINDOW AND OPENING ROUND TRIP  [OBSERVED]
=============================================================================
Recorded by this file, verified by this file, through a real subprocess, with
the verdict read back from the DXF on disk. Both sides of the opening symbol.

This section exists because the audit measured a coverage claim this file
made and did not keep: it called itself the authority on composition while
holding zero window and zero opening scenarios. cli_test.py already had the
round trip, and that is not a reason to leave the gap -- a file that asserts
a boundary it does not hold is the same defect as a verifier that skips a
check it cannot run. Both files now carry it, for different reasons; the
division is spelled out in the comment above SCENARIO 7 in the body.

=============================================================================
ENVIRONMENT NOTE (observed on this host)
=============================================================================
Aside injects ``PYTHONHOME``/``PYTHONPATH`` pointing at its own runtime, which
hides the venv's standard library and breaks ``import ezdxf`` in child
processes. ``PYTHONHOME`` is cleared; ``PYTHONPATH`` is rebuilt with this
file's source root first, so subprocesses import the tree this test session
collected, same as cli_test.py::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \\
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\e2e_test.py -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# imports under test
# ---------------------------------------------------------------------------

ezdxf = pytest.importorskip("ezdxf", reason="ezdxf unavailable; cannot run the stack")

from all_in_cad.recorder import transaction as txn_mod  # noqa: E402
from all_in_cad.recorder.door import make_door_centered, write_door  # noqa: E402
from all_in_cad.recorder.transaction import (  # noqa: E402
    ExternalModificationError,
    VerificationFailed,
    begin,
    capture_state,
    dxf_verifier,
    readback_dxf,
    recover_pending,
)
from all_in_cad.recorder.wall import (  # noqa: E402
    DXF_WRITE_VERSION,
    make_wall,
    write_wall,
)
from all_in_cad.topology import Point2D  # noqa: E402

CLI_MODULE = "all_in_cad.recorder.cli"

EXIT_OK = 0
EXIT_VERIFY_FAILED = 1
EXIT_USAGE = 2

# ---- the measured reference case, pinned -----------------------------------
WALL_START = (0.0, 0.0)
WALL_END = (12000.0, 0.0)
THICKNESS = 200.0
DOOR_CENTER = (6000.0, 0.0)
DOOR_WIDTH = 900.0

EXPECTED_TOTAL_ENTITIES = 11
EXPECTED_LAYER_COUNTS = {
    "CEN1": 1,
    "DOOR": 4,
    "DOOR_ELE": 2,
    "WAL1": 2,
    "WAL2": 2,
}
EXPECTED_TYPE_COUNTS = {"ARC": 1, "LINE": 10}
# Was 19 before the five unwired modules were wired. The count grew by two and
# both additions are ALWAYS-ON checks that run on every drawing, not
# expectations: ``insert_downstream_visibility`` (the check that replaced the
# INSERT ban) and ``layers_table_has_content``. A count that shrinks is a
# signal, so this number is asserted rather than recomputed.
EXPECTED_VERIFY_CHECKS = 21
EXPECTED_FREECAD_LAYERS = ("CEN1", "WAL1", "WAL2", "DOOR", "DOOR_ELE")

#: Same single resolution rule as block_test.py and text.py: ``FREECAD_EXE``
#: from the environment is the only accepted input. The constant this replaced
#: was a hardcoded ``D:\CAD\FreeCAD\...`` path, which can never exist on a CI
#: runner, so scenario 4 took its skip branch permanently rather than
#: intermittently. No detection helper exists in ``recorder/freecad/`` to reuse
#: (``run_freecad_script`` only defaults from the env var), so there is
#: deliberately no per-host fallback: unset is a named SKIP, not a silent pass.
FREECAD_EXE_ENV = "FREECAD_EXE"

#: Candidate locations for the wrapper, most durable first.
#:
#: [OBSERVED] Only in-repo locations are listed. An earlier version carried a
#: second candidate pointing into an Aside session tmp directory
#: (``...\.aside\u\0\sessions\<id>\tmp\...\freecad_runner.py``). That is
#: scratch space with a machine-specific absolute path, so on every other
#: machine the entry could never resolve and it could only ever be dead
#: weight in the committed source. The in-repo copy below is checked first and
#: exists in the tree, so removing the fallback changes nothing here (measured:
#: see the report). Absence of every candidate is still a SKIP with a stated
#: reason, never a silent pass.
FREECAD_RUNNER_CANDIDATES = (
    Path(__file__).resolve().parent / "freecad" / "freecad_runner.py",
)


# ===========================================================================
# helpers
# ===========================================================================


def _child_env() -> dict[str, str]:
    """Point child imports at the source tree containing this test file.

    ``PYTHONHOME`` is removed because Aside injects it and it breaks the venv
    interpreter. ``PYTHONPATH`` is rebuilt, not dropped: this test file's own
    source root goes first, ensuring children execute the same package copy
    that this pytest session is testing rather than an editable install.
    """
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)

    # .../src/all_in_cad/recorder/e2e_test.py -> .../src
    src_root = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH", "")
    parts = [p for p in existing.split(os.pathsep) if p]
    if src_root in parts:
        parts.remove(src_root)
    env["PYTHONPATH"] = os.pathsep.join([src_root, *parts])
    return env


def _cli_module_path() -> Path:
    """Absolute path of the cli.py this pytest process resolves."""
    spec = importlib.util.find_spec(CLI_MODULE)
    assert spec is not None and spec.origin is not None
    return Path(spec.origin).resolve()


def test_the_subprocess_cli_runs_the_same_source_this_test_process_sees() -> None:
    """Pin the child import to the CLI source this test process resolves."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import all_in_cad.recorder.cli as m; print(m.__file__)",
        ],
        capture_output=True,
        text=True,
        env=_child_env(),
        check=True,
    )
    child_path = Path(completed.stdout.strip()).resolve()
    parent_path = _cli_module_path()
    assert child_path == parent_path, (
        "the CLI subprocess is testing a different cli.py than this test "
        f"process resolves.\n  child : {child_path}\n  parent: {parent_path}\n"
        "A green suite here does not mean the subprocess executed the code "
        "under test. Check _child_env(): it must put this file's source root "
        "first on PYTHONPATH."
    )


def test_a_child_env_keeps_the_pythonpath_that_selects_the_code_under_test() -> None:
    """Directly pin the environment behaviour that made this suite lie.

    ``_child_env`` exists to strip the host's ``PYTHONHOME`` because Aside
    injects it and the venv's stdlib breaks. Stripping ``PYTHONPATH``
    unconditionally also strips a value a caller set on purpose to point at
    the tree under test, which is what turns a mutation into a silent pass:
    every child falls back to the editable install and reports the real
    repository green. This test fails if that regression comes back.
    """
    import all_in_cad.recorder.e2e_test as self_mod

    src_root = str(Path(self_mod.__file__).resolve().parents[2])
    env = self_mod._child_env()
    assert "PYTHONHOME" not in env
    on_path = env.get("PYTHONPATH", "").split(os.pathsep) if env.get("PYTHONPATH") else []
    assert src_root in on_path, (
        "_child_env() dropped the source root that selects the code under "
        f"test. Expected {src_root!r} in PYTHONPATH, got {env.get('PYTHONPATH')!r}. "
        "Without it the child process silently imports some other copy."
    )
    assert on_path[0] == src_root, (
        "the source root must come FIRST on PYTHONPATH, otherwise an editable "
        f"install ahead of it wins. Got {on_path!r}."
    )


def run_cli(*args: str, expect: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run the real CLI in a subprocess, asserting the process exit code."""
    completed = subprocess.run(
        [sys.executable, "-m", CLI_MODULE, *args],
        capture_output=True,
        text=True,
        env=_child_env(),
        check=False,
    )
    if expect is not None:
        assert completed.returncode == expect, (
            f"expected exit {expect}, got {completed.returncode}\n"
            f"argv: {args}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def run_cli_json(*args: str, expect: int | None = None) -> dict[str, Any]:
    completed = run_cli(*args, "--json", expect=expect)
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return payload


def sha256_of(path: Path) -> str:
    return capture_state(path).sha256 or ""


def _write_wall_only(path: Path) -> list[str]:
    """Write the wall half of the reference case. Returns the handles."""
    doc = ezdxf.new(DXF_WRITE_VERSION)
    wall = make_wall(
        Point2D(*WALL_START),
        Point2D(*WALL_END),
        THICKNESS,
        include_axis=True,
    )
    record = write_wall(doc, wall)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return list(record.handles())


def _add_door_to(doc: Any) -> list[str]:
    """Add the door half of the reference case to an open document."""
    door = make_door_centered(
        DOOR_CENTER[0],
        DOOR_CENTER[1],
        DOOR_WIDTH,
        thickness_mm=THICKNESS,
        wall_segment=(WALL_START, WALL_END),
    )
    record = write_door(doc, door, ("DOOR", "DOOR_ELE"))
    return list(record.handles())


def _record_full_plan(path: Path) -> list[str]:
    """Write wall + door in one document, the way ``plan`` does."""
    doc = ezdxf.new(DXF_WRITE_VERSION)
    wall = make_wall(
        Point2D(*WALL_START),
        Point2D(*WALL_END),
        THICKNESS,
        include_axis=True,
    )
    handles = list(write_wall(doc, wall).handles())
    handles.extend(_add_door_to(doc))
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return handles


def assert_reference_case(path: Path) -> dict[str, Any]:
    """Assert a DXF on disk matches the pinned reference case. [OBSERVED]"""
    report = readback_dxf(path)
    assert report["entity_count"] == EXPECTED_TOTAL_ENTITIES, report
    assert report["layer_counts"] == EXPECTED_LAYER_COUNTS, report["layer_counts"]
    assert report["entity_types"] == EXPECTED_TYPE_COUNTS, report["entity_types"]
    assert report["dxfversion"] in ("AC1032", "R2018"), report["dxfversion"]
    assert "INSERT" not in report["entity_types"]
    assert "HATCH" not in report["entity_types"]
    return report


# ===========================================================================
# SCENARIO 1 - fresh drawing, whole pipeline
# ===========================================================================


def test_scenario1_fresh_drawing_whole_pipeline_via_real_subprocess(
    tmp_path: Path,
) -> None:
    """Empty dir -> plan -> verify -> ezdxf read-back. [OBSERVED]"""
    out = tmp_path / "fresh" / "plan.dxf"
    assert not out.exists(), "precondition: the drawing must not exist yet"

    # --- 1. record: wall + door in one pass -----------------------------
    plan = run_cli_json(
        "plan",
        "--start", "0", "0",
        "--end", "12000", "0",
        "--thickness", "200",
        "--center", "6000", "0",
        "--width", "900",
        "-o", str(out),
        expect=EXIT_OK,
    )
    assert plan["ok"] is True
    assert plan["entity_count"] == EXPECTED_TOTAL_ENTITIES
    assert plan["layer_counts"] == EXPECTED_LAYER_COUNTS
    assert plan["dxf_version"] == "R2018"
    # the wall centreline was really handed to the door agent
    assert plan["alignment"]["wall_segment_passed"] is True
    assert plan["alignment"]["hinge_offset_from_wall_mm"] == 0.0
    assert out.is_file() and out.stat().st_size > 0

    # --- 2. verify: the drawing must satisfy every expectation ---------
    verify = run_cli_json(
        "verify",
        "--in", str(out),
        "--expect-entities", "11",
        "--expect-wall-thickness", "200",
        "--expect-door-width", "900",
        "--expect-door-center", "6000", "0",
        expect=EXIT_OK,
    )
    assert verify["ok"] is True
    assert verify["failed"] == []
    assert len(verify["checks"]) == EXPECTED_VERIFY_CHECKS, len(verify["checks"])
    assert all(item["status"] == "PASS" for item in verify["checks"])

    # --- 3. an independent reader agrees -------------------------------
    assert_reference_case(out)


def test_scenario1_wrong_expectation_fails_loudly(tmp_path: Path) -> None:
    """A wrong expectation must be exit 1, never a quiet PASS. [OBSERVED]"""
    out = tmp_path / "plan.dxf"
    run_cli(
        "plan",
        "--start", "0", "0", "--end", "12000", "0", "--thickness", "200",
        "--center", "6000", "0", "--width", "900", "-o", str(out),
        expect=EXIT_OK,
    )
    payload = run_cli_json(
        "verify", "--in", str(out), "--expect-entities", "10",
        expect=EXIT_VERIFY_FAILED,
    )
    assert payload["ok"] is False
    assert payload["failed"], "a failing verify must name what failed"


# ===========================================================================
# SCENARIO 2 - rollback, and what C-1..C-4 actually do here
# ===========================================================================


def _begin_door_recording(
    path: Path, *, expected_count: int
) -> Any:
    """A transaction that will add the door onto an existing wall drawing.

    The verifier is seeded with a deliberately WRONG expected entity count, so
    the read-back check fails after the write and forces the rollback.
    """
    return begin(
        path,
        verifiers=dxf_verifier(expected_count=expected_count),
    )


def test_scenario2_verification_failure_restores_the_file_byte_for_byte(
    tmp_path: Path,
) -> None:
    """[OBSERVED] A rejected door leaves the wall exactly as it was."""
    drawing = tmp_path / "wall_only.dxf"
    _write_wall_only(drawing)

    before_sha = sha256_of(drawing)
    before_state = capture_state(drawing)
    before_report = readback_dxf(drawing)
    assert before_report["entity_count"] == 5, before_report  # CEN1 1 WAL1 2 WAL2 2
    assert "DOOR" not in before_report["layer_counts"]

    txn = _begin_door_recording(drawing, expected_count=99)  # wrong on purpose
    with pytest.raises(VerificationFailed):
        txn.apply(
            lambda t: _door_apply(t, drawing),
        )

    # byte-for-byte restore, not merely "the file exists and is non-empty"
    after_state = capture_state(drawing)
    assert after_state.sha256 == before_sha, "file was not restored byte for byte"
    assert after_state.size == before_state.size
    assert txn_mod.content_matches(after_state, before_state)

    # and semantically: the door is genuinely gone, not just hashed differently
    after_report = readback_dxf(drawing)
    assert after_report["entity_count"] == 5, after_report
    assert after_report["layer_counts"] == {
        name: count
        for name, count in before_report["layer_counts"].items()
    }
    assert "DOOR" not in after_report["layer_counts"]
    assert "DOOR_ELE" not in after_report["layer_counts"]


def _door_apply(txn: Any, path: Path) -> list[str]:
    """The recording body: reopen the drawing and append the door."""
    doc = ezdxf.readfile(str(path))
    handles = _add_door_to(doc)
    doc.saveas(path)
    txn.note_handles(path, handles)
    return handles


def test_scenario2_c1_rollback_scope_is_the_named_file_only(tmp_path: Path) -> None:
    """C-1: the unit of undo is a path list, not "whatever happened last".

    A sibling file that this transaction never touched must be byte-identical
    after the rollback. [OBSERVED]
    """
    drawing = tmp_path / "wall_only.dxf"
    bystander = tmp_path / "bystander.dxf"
    _write_wall_only(drawing)
    bystander.write_text("not a dxf, someone else's work\n", encoding="utf-8")
    bystander_sha = sha256_of(bystander)

    txn = _begin_door_recording(drawing, expected_count=99)
    with pytest.raises(VerificationFailed):
        txn.apply(lambda t: _door_apply(t, drawing))

    assert sha256_of(bystander) == bystander_sha, "rollback reached outside its scope"
    assert txn.rollback_report is not None
    assert [item["path"] for item in txn.rollback_report] == [str(drawing)]


def test_scenario2_c3_rollback_reports_only_after_proving_it(tmp_path: Path) -> None:
    """C-3: success is never claimed on the strength of "copy did not raise".

    The rollback must carry a report whose 'verified' flag was set by re-reading
    the file and comparing existence/size/sha256 against the pre-capture. [OBSERVED]
    """
    drawing = tmp_path / "wall_only.dxf"
    _write_wall_only(drawing)
    before_sha = sha256_of(drawing)

    txn = _begin_door_recording(drawing, expected_count=99)
    with pytest.raises(VerificationFailed):
        txn.apply(lambda t: _door_apply(t, drawing))

    assert txn.state == "rolled_back"
    report = txn.rollback_report
    assert report, "a verified rollback must carry evidence"
    entry = report[0]
    assert entry["verified"] is True
    assert entry["expected_sha256"] == before_sha
    assert entry["actual_sha256"] == before_sha

    # a committed transaction discards its journal, so nothing is left to
    # "recover" later and re-apply a restore that already happened
    assert not list(txn.journal_dir.glob("*.json")), "journal residue after rollback"


def test_scenario2_rollback_is_idempotent_and_rejects_wrong_states(
    tmp_path: Path,
) -> None:
    """A second rollback is a no-op; a committed transaction refuses. [OBSERVED]"""
    drawing = tmp_path / "wall_only.dxf"
    _write_wall_only(drawing)
    before_sha = sha256_of(drawing)

    txn = _begin_door_recording(drawing, expected_count=99)
    with pytest.raises(VerificationFailed):
        txn.apply(lambda t: _door_apply(t, drawing))
    txn.rollback()
    txn.rollback()  # idempotent
    assert sha256_of(drawing) == before_sha

    good = begin(drawing, verifiers=dxf_verifier(expected_count=5))
    good.apply(lambda t: None)
    good.commit()
    with pytest.raises(txn_mod.TransactionStateError):
        good.rollback()


# ===========================================================================
# SCENARIO 3 - external intervention
# ===========================================================================


def test_scenario3_third_party_edit_is_never_deleted(tmp_path: Path) -> None:
    """[OBSERVED] C-4: another writer's bytes are worth more than our tidiness."""
    drawing = tmp_path / "contested.dxf"
    _write_wall_only(drawing)

    txn = begin(drawing)
    txn.apply(lambda t: _door_apply(t, drawing))
    our_state = capture_state(drawing)
    assert readback_dxf(drawing)["entity_count"] == 11

    # --- a third party edits the file behind our back -------------------
    doc = ezdxf.readfile(str(drawing))
    doc.modelspace().add_line((0, 5000), (1000, 5000), dxfattribs={"layer": "WAL1"})
    doc.saveas(drawing)
    theirs_sha = sha256_of(drawing)
    assert theirs_sha != our_state.sha256, "precondition: the file really changed"

    # --- our rollback must refuse to touch it ---------------------------
    with pytest.raises(ExternalModificationError) as excinfo:
        txn.rollback()

    assert excinfo.value.path == drawing
    assert excinfo.value.found.sha256 == theirs_sha
    assert txn.state == "externally_modified"

    # the decisive assertion: their work is still there, byte for byte
    assert sha256_of(drawing) == theirs_sha, "the third party's work was destroyed"
    final = readback_dxf(drawing)
    assert final["entity_count"] == 12, final["entity_count"]
    assert final["layer_counts"]["WAL1"] == 3, final["layer_counts"]


def test_scenario3_crash_recovery_restores_only_our_residue(tmp_path: Path) -> None:
    """[OBSERVED] A journal left in 'applied' is restorable; a clean one is not."""
    drawing = tmp_path / "residue.dxf"
    _write_wall_only(drawing)
    before_sha = sha256_of(drawing)

    # simulate a process that died after writing but before verifying
    crashed = begin(drawing, keep_journal_on_commit=True)
    crashed.apply(lambda t: _door_apply(t, drawing), verify=False)
    assert readback_dxf(drawing)["entity_count"] == 11

    outcomes = recover_pending(crashed.journal_dir)
    assert outcomes, "the unfinished journal must be found and acted on"
    assert all(o.verified for o in outcomes), [o.as_dict() for o in outcomes]
    assert all(str(drawing) in o.restored for o in outcomes), [
        o.as_dict() for o in outcomes
    ]
    assert sha256_of(drawing) == before_sha
    assert readback_dxf(drawing)["entity_count"] == 5


def test_scenario3_the_refusal_still_carries_the_reason_it_refused(
    tmp_path: Path,
) -> None:
    """[OBSERVED] The masking defect, stated as the consumer's question.

    Scenario 3 proves the C-4 refusal is fail-closed: a third party's bytes
    survive. That contract is unchanged. What the refusal used to cost was
    the *reason*. Scenario 3 drives the transaction API directly, so the
    refusal was the only error and its message was enough. A real caller is
    inside a ``with`` block whose body has already failed -- that is the
    normal shape, not an exotic one -- and on the way out the context
    manager's rollback replaced it.

    Measured before the fix (this tree, reproduced): the body raised Boom,
    a third party then touched the file, and what escaped was
    ``ExternalModificationError``. ``isinstance(exc, Boom)`` was False, so
    ``except Boom`` no longer matched, and the only surviving copy of the
    reason sat in ``__cause__`` -- where a consumer that catches the outer
    error and reads its fields never looks. A caller asking "why did this
    fail?" got "someone else owns the bytes" and nothing else.

    The contract being protected here is honesty about the cause, not the
    choice of exception type. Both are asserted: the fail-closed type still
    propagates, AND the original is reachable without parsing a traceback.
    """

    class Boom(RuntimeError):
        pass

    drawing = tmp_path / "masked.dxf"

    def record(txn):
        ezdxf.new(DXF_WRITE_VERSION).saveas(txn.targets[0].path)
        return []

    with pytest.raises(ExternalModificationError) as excinfo:
        with begin(drawing) as txn:
            txn.apply(record, verify=False)
            # a third party owns the bytes now, so the rollback must refuse
            drawing.write_bytes(b"a third party's work\n")
            raise Boom("the verification blew up on an unusable handle")

    outer = excinfo.value
    assert outer.original_error is not None, (
        "the refusal replaced the original error and kept no field pointing at "
        "it. The type is deliberately still ExternalModificationError (a caller "
        "seeing only the original would read the refusal as harmless), but the "
        "reason has to be reachable from the exception itself."
    )
    assert isinstance(outer.original_error, Boom)
    assert str(outer.original_error) == (
        "the verification blew up on an unusable handle"
    ), "the transported error must be the original object, not a reworded copy"

    # the fail-closed half is unchanged and still asserted
    assert outer.path == drawing
    assert outer.found.sha256 != outer.expected.sha256
    assert drawing.read_bytes() == b"a third party's work\n", (
        "C-4: the refusal must leave the third party's bytes alone"
    )


def test_scenario3_the_verification_failure_path_also_carries_its_cause(
    tmp_path: Path,
) -> None:
    """[OBSERVED] The second masking site, which the context manager misses.

    ``Txn.apply`` has its own version of the same shape: the verifier fails,
    the rollback is then refused, and what escapes is a ``VerificationFailed``
    naming the restore problem. The verifier's own message survives in the
    string, but a caller that reads fields has no handle on it, and the
    exception's ``report`` belongs to the restore, not to the cause.

    This is a separate code path from ``__exit__``, so it needs its own test;
    asserting only the ``with`` block would leave this one unguarded, which is
    exactly how a defence rots.
    """
    drawing = tmp_path / "verify_failed.dxf"

    def exploding_verifier(path, handles):
        # The third party acts AFTER the transaction captured its post-apply
        # state, which is the only point at which the C-4 guard can fire. A
        # verifier that merely raises leaves the rollback free to succeed, and
        # then the masking under test never happens.
        path.write_bytes(b"a third party's work\n")
        raise ValueError("layer WAL9 was never written")

    with pytest.raises(VerificationFailed) as excinfo:
        with begin(drawing, verifiers={drawing: exploding_verifier}) as txn:
            def record(t):
                ezdxf.new(DXF_WRITE_VERSION).saveas(t.targets[0].path)
                return []

            txn.apply(record)

    outer = excinfo.value
    assert outer.original_error is not None, (
        "the verification failure was reported without a field pointing at the "
        "verifier's own error"
    )
    assert isinstance(outer.original_error, ValueError)
    assert "WAL9" in str(outer.original_error)
    assert drawing.read_bytes() == b"a third party's work\n", (
        "C-4: the refused restore must leave the third party's bytes alone"
    )


def test_scenario3_the_host_adapter_write_path_also_carries_its_cause(
    tmp_path: Path,
) -> None:
    """[OBSERVED] And the third site, one layer out in the host adapter.

    ``host_dxf._restore_after_failed_apply`` is reached when the adapter's own
    verifier rejects a write. Its ``finally: self._txn = None`` used to mean
    that a refused restore raised over the verification error with nothing
    pointing back at it. Recorded here so the third site is guarded too.
    """
    from all_in_cad.recorder import host_dxf as hdx

    drawing = tmp_path / "host.dxf"
    adapter = hdx.DxfFileHost(str(drawing))
    adapter._txn = begin(drawing)
    # the third party writes first, so the rollback the adapter attempts is
    # refused by the C-4 guard and raises instead of restoring
    drawing.parent.mkdir(parents=True, exist_ok=True)
    drawing.write_bytes(b"a third party's work\n")

    boom = RuntimeError("the adapter could not lay the casement arc")
    with pytest.raises(ExternalModificationError) as excinfo:
        adapter._restore_after_failed_apply(boom)
    assert excinfo.value.original_error is boom, (
        "the refused restore must name the failure it was cleaning up after"
    )
    assert drawing.read_bytes() == b"a third party's work\n"


def test_scenario3_the_host_adapter_write_path_transports_its_cause(
    tmp_path: Path,
) -> None:
    """[OBSERVED] The call site, not just the helper.

    The test above calls ``_restore_after_failed_apply`` directly, which
    guards the helper. This one goes through ``_write_apply`` so the *call
    site* is guarded too -- the place where the verification error and the
    restore error meet. A fix applied only to the helper, or only at the call
    site, would pass one of these two and fail the other; that is the whole
    reason both exist.
    """
    from all_in_cad.recorder import host_dxf as hdx

    drawing = tmp_path / "host_write.dxf"
    boom = ValueError("layer WAL9 was never written")

    class _SabotagingHost(hdx.DxfFileHost):
        # DxfFileHost is slotted, so `_verifier` cannot be assigned on the
        # instance; overriding it in a subclass is the supported seam.
        def _verifier(self, path, handles):
            # act as a third party AFTER the transaction recorded its
            # post-apply state, so the C-4 guard refuses the restore
            path.write_bytes(b"a third party's work\n")
            raise boom

    adapter = _SabotagingHost(str(drawing))
    adapter._txn = begin(drawing)

    def mutator(doc):
        doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "WAL1"})
        return []

    with pytest.raises(ExternalModificationError) as excinfo:
        adapter._write_apply(mutator, "WAL1")

    assert excinfo.value.original_error is boom, (
        "the write path reported the refusal without a field pointing at the "
        "verification failure that triggered it. A caller reading fields learns "
        "that the restore was refused and nothing about why the write was "
        "rejected in the first place."
    )
    assert drawing.read_bytes() == b"a third party's work\n", (
        "C-4: the refused restore must leave the third party's bytes alone"
    )


# ===========================================================================
# SCENARIO 4 - FreeCAD round trip
# ===========================================================================
_FREECAD_PROBE_TEMPLATE = '''\
"""FreeCAD-side probe: open a recorded DXF and describe what FreeCAD built.

Runs inside FreeCAD, so it cannot import the wrapper. It writes the same
verdict contract inline: a UTF-8 JSON record whose "status" field is the
authoritative signal the wrapper reads.
"""
import json
import os
import traceback

DXF_PATH = {dxf!r}
VERDICT_PATH = {verdict!r}

try:
    import FreeCAD
    import importDXF

    doc = FreeCAD.newDocument("aic_e2e")
    importDXF.insert(DXF_PATH, doc.Name)
    doc.recompute()

    objects = list(doc.Objects)
    shapes = [o for o in objects
              if o.isDerivedFrom("Part::Feature")
              and o.Shape is not None
              and not o.Shape.isNull()]
    labels = [o.Label for o in objects if not o.isDerivedFrom("Part::Feature")]

    record = {{
        "schema": "aic.verdict/1",
        "status": "ok",
        "reason": "",
        "run_token": {token!r},
        "script": "e2e_probe",
        "objects": len(objects),
        "shapes": len(shapes),
        "layers": labels,
        "part_features": len([o for o in objects
                              if o.isDerivedFrom("Part::Feature")]),
    }}
except Exception:
    record = {{
        "schema": "aic.verdict/1",
        "status": "failed",
        "reason": traceback.format_exc(),
        "run_token": {token!r},
        "script": "e2e_probe",
        "objects": None,
        "shapes": None,
        "layers": [],
        "part_features": None,
    }}

_tmp = VERDICT_PATH + ".part"
with open(_tmp, "w", encoding="utf-8") as _fh:
    json.dump(record, _fh, ensure_ascii=False)
os.replace(_tmp, VERDICT_PATH)
'''


def _load_freecad_runner(runner_path: Path) -> Any:
    """Import the wrapper by path; it is not an importable package module.

    It MUST be registered in ``sys.modules`` before execution: the wrapper
    defines dataclasses, and ``dataclasses`` resolves annotations through
    ``sys.modules[cls.__module__]``. An unregistered module name makes every
    dataclass in it raise AttributeError during class creation.
    """
    name = "aic_freecad_runner_e2e"
    spec = importlib.util.spec_from_file_location(name, runner_path)
    assert spec and spec.loader, "could not build a spec for the FreeCAD wrapper"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _freecad_exe() -> Path | None:
    """The binary named by ``FREECAD_EXE``, or None when it is not usable."""
    raw = os.environ.get(FREECAD_EXE_ENV, "").strip()
    if not raw:
        return None
    exe = Path(raw)
    return exe if exe.is_file() else None


def _freecad_skip_reason() -> str | None:
    exe = _freecad_exe()
    if exe is None:
        raw = os.environ.get(FREECAD_EXE_ENV, "").strip()
        if not raw:
            return (
                f"{FREECAD_EXE_ENV} 미설정: real FreeCAD needed for scenario 4, "
                "and no detection helper exists to locate it"
            )
        return f"{FREECAD_EXE_ENV} points at {raw}, which is not a file"
    if _freecad_runner_path() is None:
        return "FreeCAD wrapper not found at any of: " + ", ".join(
            str(item) for item in FREECAD_RUNNER_CANDIDATES
        )
    return None


def _freecad_runner_path() -> Path | None:
    for candidate in FREECAD_RUNNER_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def test_scenario4_freecad_opens_the_recording_and_keeps_the_layers(
    tmp_path: Path,
) -> None:
    """[OBSERVED] Real FreeCAD 1.1.3, real headless run, artifact-based verdict."""
    reason = _freecad_skip_reason()
    if reason:
        pytest.skip(f"SKIPPED FOR LACK OF ENVIRONMENT: {reason}")

    drawing = tmp_path / "plan.dxf"
    _record_full_plan(drawing)
    assert_reference_case(drawing)

    work = tmp_path / "freecad"
    work.mkdir(parents=True, exist_ok=True)
    script = work / "probe.py"
    verdict = work / "verdict.json"
    token = "AIC-E2E-PROBE"
    script.write_text(
        _FREECAD_PROBE_TEMPLATE.format(
            dxf=str(drawing), verdict=str(verdict), token=token
        ),
        encoding="utf-8",
    )

    runner_path = _freecad_runner_path()
    assert runner_path is not None, reason
    exe = _freecad_exe()
    assert exe is not None, reason
    runner = _load_freecad_runner(runner_path)
    result = runner.run_freecad_script(
        script,
        freecad_exe=exe,
        timeout=300.0,
        workdir=work,
        verdict_path=verdict,
        expect_artifacts=[verdict],
        run_token=token,
    )
    if result.status == runner.STATUS_FAILED:
        if result.verdict and "freecad-executable-not-found" in " ".join(result.reasons):
            pytest.skip(f"SKIPPED FOR LACK OF ENVIRONMENT: {result.reasons}")
        pytest.fail(
            "FreeCAD run failed: "
            + json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
        )

    assert result.status == runner.STATUS_OK
    # the verdict must be artifact-proven, not merely marker-adjacent
    assert result.evidence == runner.EV_VERDICT, result.evidence
    assert result.verdict_fresh is True, "stale verdict record must not count"

    record = result.verdict or {}
    assert record["status"] == "ok", record.get("reason")
    assert record["part_features"] == EXPECTED_TOTAL_ENTITIES, record
    assert record["shapes"] == EXPECTED_TOTAL_ENTITIES, record
    present = set(record["layers"])
    missing = [name for name in EXPECTED_FREECAD_LAYERS if name not in present]
    assert not missing, f"FreeCAD lost layer(s) {missing}; it reported {present}"


# ===========================================================================
# SCENARIO 5 - idempotence: record what actually happens
# ===========================================================================


def test_scenario5_plan_twice_is_idempotent(tmp_path: Path) -> None:
    """[OBSERVED] Second run needs --force; with it, nothing accumulates.

    CHANGED (output-policy fix): the second run is no longer a silent
    overwrite. Since the first run left a file at ``-o``, the same argv now
    REFUSES (exit 2) unless ``--force`` is given, which is the whole point of
    the fix. The semantic idempotence claim is unchanged and still asserted:
    nothing accumulates, the entity count stays 11, the layers stay the same.
    """
    out = tmp_path / "plan.dxf"
    argv = (
        "plan",
        "--start", "0", "0", "--end", "12000", "0", "--thickness", "200",
        "--center", "6000", "0", "--width", "900", "-o", str(out),
    )
    first = run_cli_json(*argv, expect=EXIT_OK)
    first_sha = sha256_of(out)

    # re-running the identical command onto an existing drawing is refused,
    # and the drawing is left alone
    refused = run_cli(*argv, expect=EXIT_USAGE)
    assert "refusing to overwrite" in refused.stderr
    assert sha256_of(out) == first_sha

    second = run_cli_json(*argv, "--force", expect=EXIT_OK)
    second_sha = sha256_of(out)

    assert first["entity_count"] == second["entity_count"] == EXPECTED_TOTAL_ENTITIES
    assert_reference_case(out)

    # The two runs are NOT byte-identical: ezdxf stamps per-document metadata
    # (creation timestamp / handle GUIDs), so the SHA-256 differs. That is
    # recorded rather than asserted either way -- the meaningful idempotence
    # claim is semantic (nothing accumulated), asserted above. Asserting byte
    # identity here would manufacture a guarantee the stack does not make.
    # The CLI says the same thing in its own results rather than implying the
    # file is unchanged.
    assert first_sha != second_sha, (
        "plan output is now byte-reproducible; the comment in this test is stale"
    )
    assert first["byte_idempotent"] is False
    assert second["byte_idempotent"] is False
    assert "not byte-idempotent" in second["idempotency_note"]
    assert second["overwrote_existing"] is True
    assert second["replaced_sha256"] == first_sha
    assert second["layer_counts"] == EXPECTED_LAYER_COUNTS


def test_scenario5_standalone_door_no_longer_destroys_an_existing_drawing(
    tmp_path: Path,
) -> None:
    """[DEFECT FOUND, NOW FIXED] The door subcommand used to delete the wall.

    HISTORY (why this test changed): the standalone ``door`` subcommand never
    read its ``--out`` target -- it built a fresh document and ``saveas`` over
    the file. Recording a door into a wall drawing DELETED THE WALL, exited 0
    and reported ok. This test used to pin that behaviour, with a comment
    saying it would fail if someone fixed cli.py. cli.py is now in scope and
    the fix is deliberate, so the test pins the FIXED contract instead:

    * default: refuse, exit 2, the wall drawing survives byte for byte
    * --force: replace on purpose, and report the SHA-256 that was replaced

    The scenario itself is kept -- a wall and a door on one path -- because the
    defect lived in exactly this composition, not in either agent.
    """
    out = tmp_path / "wall.dxf"
    run_cli(
        "wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
        "-o", str(out), expect=EXIT_OK,
    )
    wall_report = readback_dxf(out)
    assert wall_report["entity_count"] == 5
    assert wall_report["layer_counts"] == {"CEN1": 1, "WAL1": 2, "WAL2": 2}
    wall_sha = sha256_of(out)

    # the user adds a door to the drawing they just made
    payload = run_cli_json(
        "door", "--hinge", "5550", "0", "-w", "900", "-o", str(out),
        expect=EXIT_USAGE,
    )

    # FIXED: it refuses, and says why
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_USAGE
    assert "refusing to overwrite" in payload["error"]
    # FIXED: the wall is still there, byte for byte
    assert sha256_of(out) == wall_sha
    after = readback_dxf(out)
    assert after["entity_count"] == 5, after["entity_count"]
    assert after["layer_counts"] == {"CEN1": 1, "WAL1": 2, "WAL2": 2}
    for kept in ("CEN1", "WAL1", "WAL2"):
        assert kept in after["layer_counts"], f"the {kept} layer must survive"

    # and the replacement is still available, but only when asked for
    forced = run_cli_json(
        "door", "--hinge", "5550", "0", "-w", "900", "-o", str(out), "--force",
        expect=EXIT_OK,
    )
    assert forced["ok"] is True
    assert forced["overwrote_existing"] is True
    assert forced["replaced_sha256"] == wall_sha
    replaced = readback_dxf(out)
    assert replaced["entity_count"] == 6, replaced["entity_count"]
    assert replaced["layer_counts"] == {"DOOR": 4, "DOOR_ELE": 2}


# ===========================================================================
# SCENARIO 6 - destructive input rejection
# ===========================================================================


#: The valid baseline, as a flag -> value mapping. Each case below perturbs
#: exactly one of these, so a rejection is attributable to that value alone.
_VALID_PARAMS: dict[str, tuple[str, ...]] = {
    "--start": ("0", "0"),
    "--end": ("12000", "0"),
    "--thickness": ("200",),
    "--center": ("6000", "0"),
    "--width": ("900",),
}

DESTRUCTIVE_CASES = [
    pytest.param({"--width": ("0",)}, "width 0", id="door-width-zero"),
    pytest.param({"--thickness": ("-5",)}, "negative thickness",
                 id="wall-thickness-negative"),
    pytest.param({"--thickness": ("0",)}, "zero thickness",
                 id="wall-thickness-zero"),
    pytest.param({"--end": ("0", "0")}, "zero-length centreline",
                 id="zero-length-centerline"),
]


def _plan_argv(params: dict[str, tuple[str, ...]], out: Path) -> list[str]:
    """Render a `plan` argv from a flag->value mapping (order is fixed)."""
    argv: list[str] = ["plan"]
    for flag, values in _VALID_PARAMS.items():
        argv.append(flag)
        argv.extend(params.get(flag, values))
    argv.extend(["-o", str(out)])
    return argv


@pytest.mark.parametrize("override,label", DESTRUCTIVE_CASES)
def test_scenario6_destructive_input_is_rejected_and_leaves_no_file(
    tmp_path: Path, override: dict[str, tuple[str, ...]], label: str
) -> None:
    """[OBSERVED] Usage exit AND no drawing left on disk."""
    params = {**_VALID_PARAMS, **override}
    out = tmp_path / "rejected.dxf"

    completed = run_cli(*_plan_argv(params, out), expect=EXIT_USAGE)
    assert "error:" in completed.stderr.lower(), completed.stderr
    assert not out.exists(), f"{label} left a drawing behind"
    assert not list(tmp_path.glob("*.dxf")), "a rejected job wrote a file"


# ===========================================================================
# SCENARIO 7 - WINDOW AND OPENING ROUND TRIP  [OBSERVED]
# ===========================================================================
# WHY THIS DUPLICATES cli_test.py's ROUND-TRIP TESTS
#
# The audit (M7) measured this file at 0 window scenarios and 0 opening
# scenarios while its own docstring claimed to be "the authority on whether
# those pieces agree with each other". That mismatch is the finding, and the
# cheap fix -- deleting this file's claim, or trusting the CLI file -- was
# rejected. A file that asserts a coverage boundary it does not hold is the
# same class of defect as a verifier that skips a check it cannot run: the
# claim is what other people rely on.
#
# So the claim is paid for instead: the round trip now exists on BOTH sides,
# for different reasons, and the division is deliberate.
#
#   cli_test.py  -- the CLI's PROCESS contract. One subcommand, one file, one
#                  verdict. It answers "does this subcommand's exit code and
#                  JSON agree with what verify says". Fast, wide, per-flag.
#   this file    -- the COMPOSITION seam, through real subprocesses. It
#                  answers "does a drawing a user actually produced survive
#                  being read back by the verifier that is supposed to police
#                  it", including the entity counts and layer sets on disk.
#
# A defect that only shows up when the recorded bytes are re-opened by a
# different code path -- which is exactly what the audit's C1 was -- cannot
# be caught by the first kind alone, because there the recording and the
# check share one process and one import.

_WINDOW_ARGS = ("--indoor", "0", "0", "--direction", "12000", "0", "-w", "1500")
_OPENING_ARGS = (
    "--ref-start", "0", "0", "--ref-end", "12000", "0",
    "-w", "900", "--opening-thickness", "200",
)


def _layers_of(path: Path) -> dict[str, int]:
    doc = ezdxf.readfile(str(path))
    counts: dict[str, int] = {}
    for entity in doc.modelspace():
        counts[entity.dxf.layer] = counts.get(entity.dxf.layer, 0) + 1
    return counts


@pytest.mark.parametrize("side", ["left", "right"])
def test_scenario7_opening_round_trips_through_its_own_verify(
    tmp_path: Path, side: str
) -> None:
    """[OBSERVED] A recorder must never write a drawing its own verify rejects.

    Both sides, because the audit's C1 failed identically on side=left and
    side=right: a check that only holds for one mirror of a mirrored symbol
    is a bug waiting for a real drawing. `side` is a free choice of the
    caller, so "works on the default" is not coverage.
    """
    out = tmp_path / f"opening_{side}.dxf"
    run_cli("opening", *_OPENING_ARGS, "--side", side, "--out", str(out),
            expect=EXIT_OK)

    payload = run_cli_json("verify", "--in", str(out), "--only", "opening",
                           expect=EXIT_OK)
    assert payload["ok"] is True, (
        f"opening (side={side}) produced a drawing its own verify rejects: "
        f"{payload.get('failed')}"
    )
    # and the recorded bytes are what verify claims to have measured
    assert _layers_of(out) == payload["layer_counts"], (
        "verify's own report disagrees with the file on disk; the round trip "
        "is not actually re-reading the recording"
    )


def test_scenario7_window_round_trips_through_its_own_verify(
    tmp_path: Path,
) -> None:
    """[OBSERVED] Same contract for the window recorder."""
    out = tmp_path / "window.dxf"
    run_cli("window", *_WINDOW_ARGS, "--out", str(out), expect=EXIT_OK)

    payload = run_cli_json("verify", "--in", str(out), "--only", "window",
                           expect=EXIT_OK)
    assert payload["ok"] is True, (
        f"window produced a drawing its own verify rejects: "
        f"{payload.get('failed')}"
    )
    assert _layers_of(out) == payload["layer_counts"]


def test_scenario7_a_hand_mangled_opening_drawing_is_rejected(
    tmp_path: Path,
) -> None:

    """[OBSERVED] The other half of a round trip: it must be able to FAIL.

    A round-trip test that only ever records clean geometry cannot tell a
    working verifier from one that waves everything through, which is the
    failure mode this package actually has. So the same path is driven once
    with the recorded geometry disturbed, and the verdict must flip to a
    named failure rather than to `ok: true`.
    """
    out = tmp_path / "mangled.dxf"
    run_cli("opening", *_OPENING_ARGS, "--out", str(out), expect=EXIT_OK)

    doc = ezdxf.readfile(str(out))
    for entity in doc.modelspace():
        if entity.dxf.layer.endswith("SYM") and entity.dxftype() == "LINE":
            entity.dxf.start = (entity.dxf.start.x, entity.dxf.start.y + 7.0)
            break
    else:  # pragma: no cover - guards against a vacuous pass
        pytest.fail("no opening symbol line was found to disturb")
    doc.saveas(out)

    payload = run_cli_json("verify", "--in", str(out), "--only", "opening",
                           expect=EXIT_VERIFY_FAILED)
    assert payload["ok"] is False
    assert payload["failed"], "a rejected drawing must name what failed"


# ===========================================================================
# DOCUMENTED CONSTRAINT, NOT FIXED - the create-only composition rule
# ===========================================================================
# [OBSERVED, DESIGN DECISION, DELIBERATELY NOT CHANGED]
#
# The audit (M6) measured that a wall and a window/opening cannot share one
# drawing through the CLI, and called it the price of the create-only rule
# that replaced a silent-overwrite data-loss defect. Re-measured on this tree
# before writing this down, because the audit's hash no longer matches:
#
#   wall          -> exit 0, 5 entities (CEN1 1, WAL1 2, WAL2 2)
#   opening       -> exit 2, "refusing to overwrite an existing drawing", the
#                    file byte-identical afterwards. Same for window.
#   opening --force -> exit 0, and the wall is GONE: 6 opening entities
#                    replace all 5. Same for window.
#
# The first half is right and is the fix for the original defect: a refusal
# that says "refusing" and changes nothing cannot be mistaken for success.
# The second half is why this is a design question rather than a bug --
# `--force` REPLACES the drawing instead of composing into it. That is
# destructive, so it was checked for silence, and it is not silent: the JSON
# reports `overwrote_existing: true` and the `replaced_sha256` of what was
# destroyed.
#
# So the shape is: composition is impossible, and the way past it says so in
# two places. NOT FIXED, because making these subcommands append is a design
# decision (append, or refuse, or warn?) that belongs to whoever owns cli.py,
# and because a stack that can only ever hold one object cannot express "a
# window is in this wall" -- a real modelling gap, not a regression. Recorded
# here so the next reader inherits a measurement instead of re-deriving one.


def test_scenario7_wall_and_opening_cannot_share_a_drawing_and_say_so(
    tmp_path: Path,
) -> None:
    """[OBSERVED] The create-only constraint, pinned as measured behaviour.

    Asserted rather than merely documented, because a constraint nobody
    measures eventually gets "fixed" by accident. The two halves that matter
    are the refusal being loud and the escape hatch being labelled.
    """
    import collections

    out = tmp_path / "combo.dxf"
    run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--out", str(out), expect=EXIT_OK)
    wall_layers = dict(collections.Counter(
        e.dxf.layer for e in ezdxf.readfile(str(out)).modelspace()))
    wall_bytes = out.read_bytes()

    refused = run_cli("opening", *_OPENING_ARGS, "--out", str(out),
                      expect=EXIT_USAGE)
    assert "refusing to overwrite" in refused.stderr.lower(), (
        "the create-only rule must refuse LOUDLY. A refusal that reads like a "
        "success is the very defect this rule was written to replace."
    )
    assert out.read_bytes() == wall_bytes, "a refused job changed the drawing"

    forced = run_cli_json("opening", *_OPENING_ARGS, "--out", str(out),
                          "--force", expect=EXIT_OK)
    assert forced["overwrote_existing"] is True, (
        "replacing a drawing must be self-reported in the JSON, or the "
        "destruction is silent and this is the same class of defect again"
    )
    assert forced["replaced_sha256"], "the destroyed drawing must be identified"

    after = dict(collections.Counter(
        e.dxf.layer for e in ezdxf.readfile(str(out)).modelspace()))
    assert not any(layer in after for layer in wall_layers), (
        "recorded for honesty, not endorsed: --force REPLACES the wall rather "
        "than composing into it. Composition is unimplemented; that is a design "
        "gap, not a bug."
    )


# ===========================================================================
# Scenario 7b: the five recorders that were unreachable, at the process
# contract level
# ===========================================================================
#
# [OBSERVED] Same two halves as scenario 7 -- a round trip AND a failure
# control -- for hatch, dim, text, block and layer. Until this, the only
# end-to-end coverage was wall/door/window/opening, which is how five of
# thirteen modules could be complete as library code and absent as features.
#
# These run the CLI IN-PROCESS through ``cli.main``, not as a subprocess. The
# in-process route is kept deliberately: ``cli.main`` returns the very exit
# code ``SystemExit(main())`` would raise, and no environment is involved at
# all. (``_child_env`` no longer drops PYTHONPATH -- it is rebuilt with this
# file's source root first, same as cli_test.py -- but these scenarios do not
# need a process boundary, so they do not pay for one.)

_EXIT_OK = 0
_EXIT_VERIFY_FAILED = 1
_EXIT_USAGE = 2

_FIVE_RECORDERS = [
    (
        "hatch",
        ["--boundary", "0", "0", "2000", "0", "2000", "1000", "0", "1000",
         "--layer", "HATCHA", "--pattern-name", "ANSI31"],
        ["--only", "hatch", "--unresolved-layer", "HATCHA"],
        ["--expect-hatch-area", "2000000", "--expect-hatch-pattern", "ANSI31"],
    ),
    (
        "dim",
        ["--p1", "0", "0", "--p2", "3000", "0"],
        ["--only", "dim"],
        ["--expect-dim-measurement", "3000"],
    ),
    (
        "text",
        ["--content", "room 101", "--insert", "100", "100", "--layer", "NOTE"],
        ["--only", "text", "--unresolved-layer", "NOTE"],
        ["--expect-text-content", "room 101"],
    ),
    (
        "block",
        ["--name", "BLKA", "--line", "0", "0", "1000", "0",
         "--line", "0", "0", "0", "600", "--entity-layer", "WAL1"],
        ["--only", "block"],
        ["--expect-block-name", "BLKA"],
    ),
    (
        "layers",
        ["--layer", "WAL1", "--layer", "DOOR"],
        ["--only", "layers"],
        ["--expect-layers", "WAL1", "DOOR"],
    ),
]


def _record_in_process(subcommand: str, extra: list[str], out: Path) -> int:
    from all_in_cad.recorder import cli

    return int(cli.main([subcommand, *extra, "--out", str(out)]))


def _verify_in_process(out: Path, *extra: str) -> int:
    from all_in_cad.recorder import cli

    return int(cli.main(["verify", "--in", str(out), *extra]))


def _report_for(out: Path, **kwargs: Any) -> dict[str, Any]:
    from all_in_cad.recorder import cli

    return cli.verify_drawing(out, **kwargs)


def _wipe_drawing_entities(doc: Any) -> int:
    removed = 0
    for entity in list(doc.modelspace()):
        if entity.dxftype() in ("LINE", "LWPOLYLINE", "TEXT", "MTEXT", "DIMENSION"):
            entity.destroy()
            removed += 1
    return removed


def _drop_first_block(doc: Any) -> int:
    """Delete a recorded block definition, leaving the modelspace alone.

    FLATTEN mode keeps the definition in the block table and the geometry in
    the modelspace, so removing only the drawn lines would leave the drawing
    verifying. The definition is the half that has to go.
    """
    for entry in list(doc.blocks):
        if not entry.name.startswith("*") and entry.name != "_CLOSEDFILLED":
            for item in list(entry):
                item.destroy()
            doc.blocks.delete_block(entry.name, safe=False)
            return 1
    return 0


def _drop_first_layer(doc: Any) -> int:
    for entry in list(doc.layers):
        if entry.dxf.name not in ("0", "Defpoints"):
            doc.layers.remove(entry.dxf.name)
            return 1
    return 0


#: What "damaged" means per module. A layers recording has no modelspace
#: entity to remove at all, and a flatten-mode block keeps its definition in
#: the block table, so deleting the drawn lines alone would leave both
#: verifying. Damage that cannot actually damage proves nothing, so each case
#: removes something the module's own check looks at.
_DAMAGE = {
    "hatch": _wipe_drawing_entities,
    "dim": _wipe_drawing_entities,
    "text": _wipe_drawing_entities,
    "block": lambda doc: (_wipe_drawing_entities(doc), _drop_first_block(doc)),
    "layers": _drop_first_layer,
}


@pytest.mark.parametrize("subcommand, extra, scope, expect", _FIVE_RECORDERS)
def test_scenario7b_every_recorder_round_trips_through_its_own_verify(
    tmp_path: Path, subcommand: str, extra: list[str], scope: list[str], expect: list[str]
) -> None:
    """[OBSERVED] Record through the CLI, verify through the CLI, in one file.

    A module can be perfect and still be absent. The assertion is the exit
    code of the real command, not a call into an analyser -- and the scope is
    paired with a real expectation, because "--only X exits 0" is also what a
    verifier that measures nothing returns.
    """
    out = tmp_path / f"{subcommand}.dxf"
    assert _record_in_process(subcommand, extra, out) == _EXIT_OK
    assert _verify_in_process(out, *scope, *expect) == _EXIT_OK, (
        f"{subcommand} produced a drawing its own verify rejects"
    )


@pytest.mark.parametrize("subcommand, extra, scope, expect", _FIVE_RECORDERS)
def test_scenario7b_the_damaged_drawing_is_rejected_at_the_process_contract(
    tmp_path: Path,
    subcommand: str,
    extra: list[str],
    scope: list[str],
    expect: list[str],
) -> None:
    """[OBSERVED] The other half: every scope can say no.

    A verifier that can only pass is not a verifier. This is the control for
    the round trip above, and it is the half the earlier audits showed was
    missing: the CRITICAL hid because no test ever demanded a FAIL from the
    analyser of a recording it had just watched pass.
    """
    out = tmp_path / f"{subcommand}.dxf"
    assert _record_in_process(subcommand, extra, out) == _EXIT_OK

    damaged = tmp_path / f"{subcommand}_damaged.dxf"
    doc = ezdxf.readfile(str(out))
    _DAMAGE[subcommand](doc)
    doc.saveas(str(damaged))

    assert _verify_in_process(damaged, *scope, *expect) == _EXIT_VERIFY_FAILED, (
        f"{subcommand}: a drawing with its content removed still verified. "
        "A scope that cannot fail is not a check."
    )


def test_scenario7b_the_insert_drawing_verifies_only_when_the_cover_is_declared(
    tmp_path: Path,
) -> None:
    """[OBSERVED] The shipped contradiction, closed, as a process contract.

    ``block --mode insert`` used to write a drawing the shipped verifier
    rejected, with no argument that could make it pass. Now the concealment is
    a named, measurable, acknowledged condition -- and the unacknowledged case
    still exits 1, so nothing was quietly downgraded to a warning.
    """
    out = tmp_path / "insert.dxf"
    assert _record_in_process(
        "block",
        ["--name", "BLKB", "--line", "0", "0", "1000", "0", "--line", "0", "0", "0", "600",
         "--entity-layer", "WAL1", "--mode", "insert"],
        out,
    ) == _EXIT_OK

    assert _verify_in_process(out, "--only", "block") == _EXIT_VERIFY_FAILED, (
        "an unacknowledged INSERT must not verify: it contributes nothing to "
        "any downstream measurement"
    )

    assert _verify_in_process(out, "--only", "block", "--allow-hidden-insert") == _EXIT_OK
    report = _report_for(out, require="block", allow_hidden_insert=True)
    assert report["block"]["insert_count"] == 1
    assert report["block"]["hidden_entities"] > 0
    assert report["block"]["hidden_length_mm"] > 0.0


def test_scenario7b_a_rejected_recording_leaves_no_drawing_behind(
    tmp_path: Path,
) -> None:
    """[OBSERVED] The exit-2 contract, for the new subcommands, in one sweep.

    A command that validates its arguments AFTER touching the target is the
    data-loss defect wearing a different hat.
    """
    cases = [
        ("hatch", ["--boundary", "0", "0", "2000", "0", "--layer", "H", "--pattern-name", "A"]),
        ("dim", ["--p1", "5", "5", "--p2", "5", "5"]),
        ("text", ["--content", "x", "--insert", "0", "0", "--layer", "N", "--height", "-1"]),
        ("block", ["--name", "B", "--line", "0", "0", "0", "0", "--entity-layer", "WAL1"]),
        ("layers", ["--set", "WAL1.not_a_key=1"]),
    ]
    for subcommand, extra in cases:
        out = tmp_path / f"{subcommand}_never.dxf"
        assert _record_in_process(subcommand, extra, out) == _EXIT_USAGE, (
            f"{subcommand} accepted an invalid request"
        )
        assert not out.exists(), f"{subcommand} left a drawing behind after a refusal"
