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
ENVIRONMENT NOTE (observed on this host)
=============================================================================
Aside injects ``PYTHONHOME``/``PYTHONPATH`` pointing at its own runtime, which
hides the venv's standard library and breaks ``import ezdxf`` in child
processes. Cleared for every subprocess run here, same as cli_test.py::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \\
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\e2e_test.py -q
"""

from __future__ import annotations

import hashlib
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
EXPECTED_VERIFY_CHECKS = 19
EXPECTED_FREECAD_LAYERS = ("CEN1", "WAL1", "WAL2", "DOOR", "DOOR_ELE")

FREECAD_EXE = Path(
    r"D:\CAD\FreeCAD\FreeCAD_1.1.3-Windows-x86_64-py311\bin\freecadcmd.exe"
)

#: Candidate locations for the wrapper, most durable first.
#:
#: [OBSERVED] The brief named a path inside an Aside session tmp directory, which
#: is scratch space and can be cleaned at any time. A copy has since landed at a
#: stable in-repo path, so that one is preferred and the session path is kept
#: only as a fallback. Either way, absence is a SKIP with a stated reason, never
#: a silent pass.
FREECAD_RUNNER_CANDIDATES = (
    Path(__file__).resolve().parent / "freecad" / "freecad_runner.py",
    Path(
        r"C:\Users\khs09\.aside\u\0\sessions"
        r"\2026-09-26_8ORnwfjJrKmHeySr\tmp\w2-freecad\freecad_runner.py"
    ),
)


# ===========================================================================
# helpers
# ===========================================================================


def _child_env() -> dict[str, str]:
    """Environment with the Aside-injected PYTHONHOME/PYTHONPATH removed."""
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    return env


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


def _freecad_skip_reason() -> str | None:
    if not FREECAD_EXE.is_file():
        return f"FreeCAD binary not present at {FREECAD_EXE}"
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
    runner = _load_freecad_runner(runner_path)
    result = runner.run_freecad_script(
        script,
        freecad_exe=FREECAD_EXE,
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
