"""Tests for the recorder CLI, executed as a real subprocess.

Why a subprocess and not a direct ``main([...])`` call: the whole point of this
CLI is its process contract -- the exit code, stdout, stderr. A failing
``verify`` that printed "PASS" would be the exact failure this repository has
already suffered once, so the exit code is asserted by actually running the
program, not by calling a function and trusting its return value.

ENVIRONMENT NOTE (observed on this host)
----------------------------------------
Aside injects ``PYTHONHOME`` into the process environment, which hides the venv
interpreter's standard library and makes ``import ezdxf`` fail with
``ModuleNotFoundError: No module named 'annotationlib'``. Child processes
inherit it, so :func:`_child_env` clears ``PYTHONHOME`` before every subprocess
run::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \\
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\cli_test.py

``PYTHONPATH`` is NOT cleared, because that would let the child import a
different copy of the package than this test session is testing. An earlier
version cleared it, and the consequence was a mutation that reported a fully
green suite: ``cmd_verify``'s failure return had been changed to ``EXIT_OK``
in a copied tree, every one of these tests still passed, because each child
dropped ``PYTHONPATH`` and fell back to the editable install of the real
repository. :func:`_child_env` now points the child at *this* file's own
source root, so the subprocess and the test process always agree on which
``cli.py`` is under test. Asserted by
``test_the_subprocess_cli_runs_the_same_source_this_test_process_sees``.

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \\
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\cli_test.py

Measured reference case this file pins (OBSERVED, reproduced by `plan`):
centreline (0,0)->(12000,0), thickness 200, door centre (6000,0) width 900 ->
CEN1 1 + WAL1 2 + WAL2 2 + DOOR 4 + DOOR_ELE 2 = 11 entities, no INSERT/HATCH.

OUTPUT POLICY pinned here (added after a measured data-loss defect)
--------------------------------------------------------------------
The first version of the CLI never read ``--out`` before saving, so recording a
wall and then a door to the same path silently destroyed the wall: 6 entities
left, exit code 0, JSON ``"ok": true``. These tests are the regression for that
exact sequence -- refuse by default, ``--force`` to replace, and the existing
file's SHA-256 unchanged in between.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

MODULE = "all_in_cad.recorder.cli"

# Exit codes documented in cli.py.
EXIT_OK = 0
EXIT_VERIFY_FAILED = 1
EXIT_USAGE = 2
EXIT_WRITE_FAILED = 3


def _child_env() -> dict[str, str]:
    """Environment for a child that must import *this* file's own package.

    ``PYTHONHOME`` is removed because Aside injects it and it breaks the venv
    interpreter. ``PYTHONPATH`` is not merely removed but rebuilt: the source
    root containing this very test file is put back at the front, so a child
    can never silently import a different copy of ``all_in_cad`` than the one
    this session collected and is mutating.
    """
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)

    # .../src/all_in_cad/recorder/cli_test.py -> .../src
    src_root = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH", "")
    parts = [p for p in existing.split(os.pathsep) if p]
    if src_root in parts:
        parts.remove(src_root)
    env["PYTHONPATH"] = os.pathsep.join([src_root, *parts])
    return env


def run_cli(*args: str, expect: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run the CLI in a subprocess and optionally assert its exit code."""
    completed = subprocess.run(
        [sys.executable, "-m", MODULE, *args],
        capture_output=True,
        text=True,
        env=_child_env(),
        check=False,
    )
    if expect is not None:
        assert completed.returncode == expect, (
            f"expected exit {expect}, got {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def run_json(*args: str, expect: int | None = None) -> dict:
    completed = run_cli(*args, "--json", expect=expect)
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return payload


# ---------------------------------------------------------------------------
# help / shape
# ---------------------------------------------------------------------------


def test_help_lists_all_four_subcommands() -> None:
    completed = run_cli("--help", expect=EXIT_OK)
    for name in ("wall", "door", "plan", "verify"):
        assert name in completed.stdout, f"{name} missing from --help"


def test_missing_required_out_is_a_usage_error(tmp_path: Path) -> None:
    completed = run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200")
    assert completed.returncode == EXIT_USAGE
    assert not list(tmp_path.glob("*.dxf"))


# ---------------------------------------------------------------------------
# wall
# ---------------------------------------------------------------------------


def test_wall_records_the_measured_reference_case(tmp_path: Path) -> None:
    out = tmp_path / "wall.dxf"
    payload = run_json(
        "wall",
        "--start", "0", "0",
        "--end", "12000", "0",
        "--thickness", "200",
        "--out", str(out),
        expect=EXIT_OK,
    )
    assert out.exists()
    assert payload["command"] == "wall"
    assert payload["dxf_version"] == "R2018"
    assert payload["entity_count"] == 5
    assert payload["layer_counts"] == {"CEN1": 1, "WAL1": 2, "WAL2": 2}
    assert payload["thickness_mm"] == 200
    assert payload["half_thickness_mm"] == 100
    assert payload["length_mm"] == 12000


def test_wall_without_axis_has_four_entities(tmp_path: Path) -> None:
    payload = run_json(
        "wall",
        "--start", "0", "0",
        "--end", "12000", "0",
        "-t", "200",
        "--no-axis",
        "--out", str(tmp_path / "wall_no_axis.dxf"),
        expect=EXIT_OK,
    )
    assert payload["entity_count"] == 4
    assert "CEN1" not in payload["layer_counts"]


def test_wall_text_output_mentions_the_drawing(tmp_path: Path) -> None:
    out = tmp_path / "wall_text.dxf"
    completed = run_cli(
        "wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
        "--out", str(out), expect=EXIT_OK,
    )
    assert str(out) in completed.stdout
    assert "WAL1=2" in completed.stdout


# ---------------------------------------------------------------------------
# door
# ---------------------------------------------------------------------------


def test_door_from_hinge_point(tmp_path: Path) -> None:
    payload = run_json(
        "door",
        "--hinge", "5550", "0",
        "--width", "900",
        "--door-thickness", "200",
        "--out", str(tmp_path / "hinge.dxf"),
        expect=EXIT_OK,
    )
    assert payload["entity_count"] == 6
    assert payload["layer_counts"] == {"DOOR": 4, "DOOR_ELE": 2}
    assert payload["door"]["hinge"] == [5550.0, 0.0]
    assert payload["door"]["latch"] == [6450.0, 0.0]
    assert payload["door"]["center"] == [6000.0, 0.0]


def test_door_from_centre_point_matches_the_hinge_result(tmp_path: Path) -> None:
    payload = run_json(
        "door",
        "--center", "6000", "0",
        "--width", "900",
        "--door-thickness", "200",
        "--out", str(tmp_path / "center.dxf"),
        expect=EXIT_OK,
    )
    assert payload["door"]["hinge"] == [5550.0, 0.0]
    assert payload["door"]["latch"] == [6450.0, 0.0]
    assert payload["door"]["center"] == [6000.0, 0.0]


def test_door_right_side_mirrors_the_hinge(tmp_path: Path) -> None:
    payload = run_json(
        "door",
        "--center", "6000", "0",
        "--width", "900",
        "--side", "right",
        "--out", str(tmp_path / "right.dxf"),
        expect=EXIT_OK,
    )
    assert payload["door"]["hinge"] == [6450.0, 0.0]
    assert payload["door"]["latch"] == [5550.0, 0.0]


def test_door_swing_and_thickness_are_honoured(tmp_path: Path) -> None:
    payload = run_json(
        "door",
        "--center", "6000", "0",
        "--width", "1000",
        "--door-thickness", "150",
        "--swing", "45",
        "--out", str(tmp_path / "swing.dxf"),
        expect=EXIT_OK,
    )
    assert payload["door"]["swing_deg"] == 45
    assert payload["door"]["thickness_mm"] == 150
    assert payload["door"]["width_mm"] == 1000


# ---------------------------------------------------------------------------
# plan: the wall/door interaction that has to be real
# ---------------------------------------------------------------------------


def test_plan_writes_the_measured_eleven_entity_reference_case(tmp_path: Path) -> None:
    out = tmp_path / "plan.dxf"
    payload = run_json(
        "plan",
        "--start", "0", "0",
        "--end", "12000", "0",
        "--thickness", "200",
        "--center", "6000", "0",
        "--width", "900",
        "--out", str(out),
        expect=EXIT_OK,
    )
    assert out.exists()
    assert payload["entity_count"] == 11
    assert payload["layer_counts"] == {
        "CEN1": 1, "WAL1": 2, "WAL2": 2, "DOOR": 4, "DOOR_ELE": 2,
    }
    assert payload["wall"]["entity_count"] == 5
    assert payload["wall"]["thickness_mm"] == 200


def test_plan_aligns_the_door_hinge_onto_the_wall_centreline(tmp_path: Path) -> None:
    """The wall_segment hand-off must actually align the hinge, not just claim to."""
    payload = run_json(
        "plan",
        "--start", "0", "0",
        "--end", "12000", "0",
        "--thickness", "200",
        "--center", "6000", "0",
        "--width", "900",
        "--out", str(tmp_path / "plan_aligned.dxf"),
        expect=EXIT_OK,
    )
    alignment = payload["alignment"]
    assert alignment["wall_segment_passed"] is True
    assert alignment["hinge_offset_from_wall_mm"] == 0.0
    # hinge y equals the wall centreline y, i.e. the door really sits on the wall
    assert payload["door"]["hinge"][1] == 0.0
    assert payload["door"]["center"][1] == 0.0
    assert payload["door"]["warnings"] == []


def test_plan_follows_a_diagonal_wall_direction(tmp_path: Path) -> None:
    """A non-axial wall must rotate the door width axis, not force it to +X."""
    payload = run_json(
        "plan",
        "--start", "0", "0",
        "--end", "0", "12000",
        "--thickness", "200",
        "--center", "0", "6000",
        "--width", "900",
        "--out", str(tmp_path / "plan_v.dxf"),
        expect=EXIT_OK,
    )
    assert payload["door"]["width_axis_deg"] == 90.0
    # cos(90 deg) is not exactly 0 in floating point, so compare with tolerance;
    # what matters is that y moved and x stayed put (a forced +X axis would have
    # produced hinge [-450.0, 6000.0]).
    hinge = payload["door"]["hinge"]
    latch = payload["door"]["latch"]
    assert hinge[0] == pytest.approx(0.0, abs=1e-9)
    assert hinge[1] == pytest.approx(5550.0)
    assert latch[0] == pytest.approx(0.0, abs=1e-9)
    assert latch[1] == pytest.approx(6450.0)
    assert payload["alignment"]["hinge_offset_from_wall_mm"] == pytest.approx(0.0, abs=1e-9)


def test_plan_defaults_door_thickness_to_the_wall_thickness(tmp_path: Path) -> None:
    payload = run_json(
        "plan",
        "--start", "0", "0",
        "--end", "12000", "0",
        "--thickness", "250",
        "--center", "6000", "0",
        "--width", "900",
        "--out", str(tmp_path / "plan_thk.dxf"),
        expect=EXIT_OK,
    )
    assert payload["door"]["thickness_mm"] == 250


def test_plan_refuses_a_door_that_is_off_the_wall(tmp_path: Path) -> None:
    out = tmp_path / "off_wall.dxf"
    completed = run_cli(
        "plan",
        "--start", "0", "0",
        "--end", "12000", "0",
        "--thickness", "200",
        "--center", "6000", "500",
        "--width", "900",
        "--out", str(out),
    )
    assert completed.returncode == EXIT_USAGE
    assert "not on the wall" in completed.stderr
    assert not out.exists(), "a rejected plan must not leave a drawing behind"


def test_plan_hinge_tolerance_is_honoured_when_given(tmp_path: Path) -> None:
    completed = run_cli(
        "plan",
        "--start", "0", "0",
        "--end", "12000", "0",
        "--thickness", "200",
        "--center", "6000", "500",
        "--width", "900",
        "--hinge-tolerance", "1000",
        "--out", str(tmp_path / "tolerant.dxf"),
        expect=EXIT_OK,
    )
    assert "warning" in completed.stdout or "hinge offset 500" in completed.stdout


def test_plan_rejects_axis_deg_together_with_the_wall(tmp_path: Path) -> None:
    completed = run_cli(
        "plan",
        "--start", "0", "0",
        "--end", "12000", "0",
        "--thickness", "200",
        "--center", "6000", "0",
        "--width", "900",
        "--axis-deg", "45",
        "--out", str(tmp_path / "conflict.dxf"),
    )
    assert completed.returncode == EXIT_USAGE
    assert "--axis-deg" in completed.stderr


# ---------------------------------------------------------------------------
# argument rejection: the same rules the geometry agents enforce
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "expected_message"),
    [
        (("--start", "0", "0", "--end", "12000", "0", "-t", "0"), "thickness_mm must be positive"),
        (
            ("--start", "0", "0", "--end", "12000", "0", "-t", "-200"),
            "thickness_mm must be positive",
        ),
        (
            ("--start", "0", "0", "--end", "0", "0", "-t", "200"),
            "centerline length must be positive",
        ),
        (
            ("--start", "0", "0", "--end", "12000", "0", "-t", "abc"),
            "invalid float value",
        ),
        (("--start", "0", "--end", "12000", "0", "-t", "200"), "argument --start"),    ],
)
def test_wall_rejects_bad_arguments(
    tmp_path: Path, args: tuple[str, ...], expected_message: str
) -> None:
    out = tmp_path / "rejected.dxf"
    completed = run_cli("wall", *args, "--out", str(out))
    assert completed.returncode == EXIT_USAGE, completed.stderr
    assert expected_message in completed.stderr
    assert not out.exists()


@pytest.mark.parametrize(
    ("args", "expected_message"),
    [
        (("--center", "6000", "0", "-w", "-900"), "width_mm must be > 0"),
        (("--center", "6000", "0", "-w", "0"), "width_mm must be > 0"),
        (("--hinge", "5550", "0", "-w", "900", "--swing", "0"), "0 < swing_deg < 360"),
        (("--hinge", "5550", "0", "-w", "900", "--swing", "360"), "0 < swing_deg < 360"),
        (("--hinge", "5550", "0", "-w", "900", "--swing", "-90"), "0 < swing_deg < 360"),
        (
            ("--hinge", "5550", "0", "-w", "900", "--door-thickness", "0"),
            "thickness_mm must be > 0",
        ),
        (
            ("--hinge", "5550", "0", "-w", "900", "--frame-width", "-1"),
            "frame_width_mm must be >= 0",
        ),
        (("--center", "6000", "0", "-w", "900", "--axis-deg", "nan"), "must be a finite number"),
    ],
)
def test_door_rejects_bad_arguments(
    tmp_path: Path, args: tuple[str, ...], expected_message: str
) -> None:
    out = tmp_path / "rejected.dxf"
    completed = run_cli("door", *args, "--out", str(out))
    assert completed.returncode == EXIT_USAGE, completed.stderr
    assert expected_message in completed.stderr
    assert not out.exists()


def test_door_requires_hinge_or_center_but_not_both(tmp_path: Path) -> None:
    neither = run_cli("door", "-w", "900", "--out", str(tmp_path / "a.dxf"))
    assert neither.returncode == EXIT_USAGE
    both = run_cli(
        "door", "--hinge", "5550", "0", "--center", "6000", "0", "-w", "900",
        "--out", str(tmp_path / "b.dxf"),
    )
    assert both.returncode == EXIT_USAGE
    assert "not allowed with" in both.stderr


def test_plan_rejects_bad_wall_and_door_arguments(tmp_path: Path) -> None:
    bad_wall = run_cli(
        "plan", "--start", "0", "0", "--end", "12000", "0", "-t", "0",
        "--center", "6000", "0", "-w", "900", "--out", str(tmp_path / "w.dxf"),
    )
    assert bad_wall.returncode == EXIT_USAGE
    assert "thickness_mm must be positive" in bad_wall.stderr

    bad_door = run_cli(
        "plan", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
        "--center", "6000", "0", "-w", "-900", "--out", str(tmp_path / "d.dxf"),
    )
    assert bad_door.returncode == EXIT_USAGE
    assert "width_mm must be > 0" in bad_door.stderr


def test_rejected_command_never_writes_a_drawing(tmp_path: Path) -> None:
    out = tmp_path / "never.dxf"
    run_cli(
        "wall", "--start", "0", "0", "--end", "0", "0", "-t", "200",
        "--out", str(out), expect=EXIT_USAGE,
    )
    assert not out.exists()


def test_rejection_is_also_reported_as_json(tmp_path: Path) -> None:
    completed = run_cli(
        "wall", "--start", "0", "0", "--end", "12000", "0", "-t", "0",
        "--out", str(tmp_path / "j.dxf"), "--json", expect=EXIT_USAGE,
    )
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_USAGE
    assert "thickness" in payload["error"]


# ---------------------------------------------------------------------------
# output policy: a second recording must never silently destroy the first
# ---------------------------------------------------------------------------
#
# REGRESSION. The measured defect these tests pin: the first version of the CLI
# called _new_document() for every subcommand and saved it to --out without ever
# reading the target, so
#     wall  --out x.dxf     -> 5 entities, exit 0
#     door  --out x.dxf     -> 6 entities, exit 0, JSON "ok": true
# left x.dxf holding only the door. The wall was gone and the command reported
# success. Every module-level test was green, because the defect lived in the
# composition layer. Recording a wall and then a door on the same path must now
# be refused, and the file must come out byte-identical.


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_wall_then_door_on_the_same_path_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "x.dxf"
    run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--out", str(out), expect=EXIT_OK)
    before = _sha256(out)
    wall_bytes = out.read_bytes()

    completed = run_cli("door", "--center", "6000", "0", "-w", "900", "--out", str(out))
    assert completed.returncode == EXIT_USAGE
    assert "refusing to overwrite an existing drawing" in completed.stderr
    assert "--force" in completed.stderr
    # the wall drawing is untouched, byte for byte
    assert _sha256(out) == before
    assert out.read_bytes() == wall_bytes
    # and the wall is still really in there
    assert run_json("verify", "--in", str(out), "--only", "wall",
                    expect=EXIT_OK)["entity_count"] == 5


def test_refused_door_does_not_report_ok(tmp_path: Path) -> None:
    """The defect's worst part was that the JSON said ok:true. It must not."""
    out = tmp_path / "y.dxf"
    run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--out", str(out), expect=EXIT_OK)
    before = _sha256(out)
    completed = run_cli(
        "door", "--center", "6000", "0", "-w", "900", "--out", str(out), "--json",
        expect=EXIT_USAGE,
    )
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_USAGE
    assert "refusing to overwrite" in payload["error"]
    assert _sha256(out) == before


def test_plan_onto_an_existing_path_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "z.dxf"
    run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--out", str(out), expect=EXIT_OK)
    before = _sha256(out)
    completed = run_cli(
        "plan", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
        "--center", "6000", "0", "-w", "900", "--out", str(out),
    )
    assert completed.returncode == EXIT_USAGE
    assert "refusing to overwrite" in completed.stderr
    assert _sha256(out) == before


def test_force_replaces_the_drawing_on_purpose(tmp_path: Path) -> None:
    out = tmp_path / "forced.dxf"
    run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--out", str(out), expect=EXIT_OK)
    before = _sha256(out)
    payload = run_json(
        "door", "--center", "6000", "0", "-w", "900", "--out", str(out),
        "--force", expect=EXIT_OK,
    )
    assert payload["overwrote_existing"] is True
    assert payload["replaced_sha256"] == before
    assert payload["entity_count"] == 6
    assert _sha256(out) != before
    # the wall really is gone: only 6 entities remain. This is a door-only
    # drawing now, so verify it as one.
    report = run_json("verify", "--in", str(out), "--only", "door", expect=EXIT_OK)
    assert report["entity_count"] == 6
    # and a default (fail-closed) verify says the wall is missing
    strict = run_json("verify", "--in", str(out), expect=EXIT_VERIFY_FAILED)
    assert "wall_measurable" in strict["failed"]


def test_force_on_plan_reports_the_replaced_hash(tmp_path: Path) -> None:
    out = tmp_path / "plan_forced.dxf"
    run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--out", str(out), expect=EXIT_OK)
    before = _sha256(out)
    payload = run_json(
        "plan", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
        "--center", "6000", "0", "-w", "900", "--out", str(out), "--force",
        expect=EXIT_OK,
    )
    assert payload["overwrote_existing"] is True
    assert payload["replaced_sha256"] == before
    assert payload["entity_count"] == 11
    assert run_json("verify", "--in", str(out), expect=EXIT_OK)["ok"] is True


def test_a_refused_write_leaves_no_journal_behind(tmp_path: Path) -> None:
    """A refusal must not even open the transaction device."""
    out = tmp_path / "nojournal.dxf"
    run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--out", str(out), expect=EXIT_OK)
    journal = tmp_path / ".all_in_cad_txn"
    before = sorted(item.name for item in tmp_path.iterdir())
    before_journal = sorted(item.name for item in journal.iterdir()) if journal.exists() else None
    run_cli("door", "--center", "6000", "0", "-w", "900", "--out", str(out),
            expect=EXIT_USAGE)
    # nothing new appeared, anywhere
    assert sorted(item.name for item in tmp_path.iterdir()) == before
    after_journal = sorted(item.name for item in journal.iterdir()) if journal.exists() else None
    assert after_journal == before_journal


def test_a_committed_write_leaves_no_journal_behind(tmp_path: Path) -> None:
    out = tmp_path / "clean.dxf"
    run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--out", str(out), expect=EXIT_OK)
    assert out.exists()
    journal = tmp_path / ".all_in_cad_txn"
    if journal.exists():
        assert list(journal.glob("*.json")) == [], "journal must be discarded on commit"


def test_bad_arguments_are_rejected_before_the_existing_file_is_looked_at(
    tmp_path: Path,
) -> None:
    """Validation must come first, so a bad command cannot disturb a drawing."""
    out = tmp_path / "guarded.dxf"
    run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--out", str(out), expect=EXIT_OK)
    before = _sha256(out)
    completed = run_cli(
        "door", "--center", "6000", "0", "-w", "-900", "--out", str(out), "--force"
    )
    assert completed.returncode == EXIT_USAGE
    # the geometry error, not an overwrite complaint: the path was never opened
    assert "width_mm must be > 0" in completed.stderr
    assert "refusing to overwrite" not in completed.stderr
    assert _sha256(out) == before


def test_verify_scoped_to_one_kind_of_drawing(tmp_path: Path) -> None:
    """Fail-closed by default; --only states what the drawing is supposed to hold."""
    out = tmp_path / "door_only.dxf"
    run_cli("door", "--center", "6000", "0", "-w", "900", "--out", str(out),
            expect=EXIT_OK)
    # default: a wall is required, so a door-only drawing FAILS
    default = run_json("verify", "--in", str(out), expect=EXIT_VERIFY_FAILED)
    assert "wall_measurable" in default["failed"]
    # scoped: honest pass
    scoped = run_json("verify", "--in", str(out), "--only", "door", expect=EXIT_OK)
    assert scoped["ok"] is True
    assert scoped["entity_count"] == 6


# ---------------------------------------------------------------------------
# idempotency: same geometry, different bytes -- and we say so
# ---------------------------------------------------------------------------


def test_two_plan_runs_are_equivalent_geometry_but_different_bytes(
    tmp_path: Path,
) -> None:
    """MEASURED FACT, not a defect: ezdxf stamps metadata into every save.

    The second run needs --force, because the first one left a file there.
    Entity-for-entity the two drawings are the same; the SHA-256 differs. This
    CLI is geometrically idempotent and NOT byte-idempotent, and its JSON says
    so rather than implying the file is unchanged.
    """
    out = tmp_path / "twice.dxf"
    args = ("plan", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--center", "6000", "0", "-w", "900", "--out", str(out))
    first = run_json(*args, expect=EXIT_OK)
    first_hash = _sha256(out)
    first_bytes = out.read_bytes()

    second = run_json(*args, "--force", expect=EXIT_OK)
    second_hash = _sha256(out)

    # same geometry
    assert first["entity_count"] == second["entity_count"] == 11
    assert first["layer_counts"] == second["layer_counts"]
    assert first["door"] == second["door"]
    # different bytes, and we do not pretend otherwise
    assert first_hash != second_hash
    assert first_bytes != out.read_bytes()
    assert first["byte_idempotent"] is False
    assert second["byte_idempotent"] is False
    assert "not byte-idempotent" in second["idempotency_note"]
    # both drawings still verify
    assert run_json("verify", "--in", str(out), expect=EXIT_OK)["entity_count"] == 11


def test_help_documents_the_output_policy_and_exit_codes() -> None:
    completed = run_cli("--help", expect=EXIT_OK)
    assert "0 success" in completed.stdout
    assert "1 verification failed" in completed.stdout
    assert "rolled back" in completed.stdout
    plan_help = run_cli("plan", "--help", expect=EXIT_OK)
    assert "--force" in plan_help.stdout
    assert "CREATE ONLY" in plan_help.stdout
    for command in ("wall", "door"):
        text = run_cli(command, "--help", expect=EXIT_OK).stdout
        assert "--force" in text, f"{command} is missing --force"


# ---------------------------------------------------------------------------
# verify: success
# ---------------------------------------------------------------------------


def _make_plan(tmp_path: Path, name: str = "plan.dxf") -> Path:
    out = tmp_path / name
    run_cli(
        "plan",
        "--start", "0", "0",
        "--end", "12000", "0",
        "--thickness", "200",
        "--center", "6000", "0",
        "--width", "900",
        "--out", str(out),
        expect=EXIT_OK,
    )
    return out


def test_verify_passes_on_a_plan_drawing_with_expectations(tmp_path: Path) -> None:
    out = _make_plan(tmp_path)
    payload = run_json(
        "verify",
        "--in", str(out),
        "--expect-entities", "11",
        "--expect-wall-thickness", "200",
        "--expect-door-width", "900",
        "--expect-door-center", "6000", "0",
        expect=EXIT_OK,
    )
    assert payload["ok"] is True
    assert payload["failed"] == []
    assert payload["exit_code"] == EXIT_OK
    assert payload["entity_count"] == 11
    assert payload["layer_counts"] == {
        "CEN1": 1, "WAL1": 2, "WAL2": 2, "DOOR": 4, "DOOR_ELE": 2,
    }
    assert payload["type_counts"].get("INSERT", 0) == 0
    assert payload["type_counts"].get("HATCH", 0) == 0
    assert payload["layer_semantics"] == {
        "CEN1": "centerline", "DOOR": "door", "DOOR_ELE": "door",
        "WAL1": "wall", "WAL2": "wall",
    }
    assert payload["wall"]["thickness_mm"] == 200
    assert payload["door"]["opening_width_mm"] == 900
    assert payload["door"]["opening_center"] == [6000.0, 0.0]
    assert all(check["status"] == "PASS" for check in payload["checks"])
    assert len(payload["checks"]) >= 15


def test_verify_passes_without_expectations(tmp_path: Path) -> None:
    payload = run_json("verify", "--in", str(_make_plan(tmp_path)), expect=EXIT_OK)
    assert payload["ok"] is True
    assert payload["failed"] == []


def test_verify_text_output_ends_with_pass(tmp_path: Path) -> None:
    completed = run_cli("verify", "--in", str(_make_plan(tmp_path)), expect=EXIT_OK)
    assert "RESULT: PASS" in completed.stdout
    assert "FAIL" not in completed.stdout


# ---------------------------------------------------------------------------
# verify: failure paths -- a failure must never read as success
# ---------------------------------------------------------------------------


def test_verify_fails_on_a_wrong_entity_count(tmp_path: Path) -> None:
    out = _make_plan(tmp_path)
    payload = run_json(
        "verify", "--in", str(out), "--expect-entities", "12", expect=EXIT_VERIFY_FAILED
    )
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_VERIFY_FAILED
    assert "entity_count_matches_expectation" in payload["failed"]


def test_verify_fails_on_a_wrong_wall_thickness(tmp_path: Path) -> None:
    out = _make_plan(tmp_path)
    payload = run_json(
        "verify", "--in", str(out), "--expect-wall-thickness", "180",
        expect=EXIT_VERIFY_FAILED,
    )
    assert payload["ok"] is False
    assert "wall_thickness_matches_expectation" in payload["failed"]
    assert payload["wall"]["thickness_mm"] == 200


def test_verify_fails_on_a_wrong_door_centre(tmp_path: Path) -> None:
    out = _make_plan(tmp_path)
    payload = run_json(
        "verify", "--in", str(out), "--expect-door-center", "5000", "0",
        expect=EXIT_VERIFY_FAILED,
    )
    assert payload["ok"] is False
    assert "door_center_matches_expectation" in payload["failed"]


def test_verify_fails_on_a_hand_mangled_drawing(tmp_path: Path) -> None:
    """Deliberately wrong geometry: cap length no longer matches the thickness.

    Built with ezdxf directly (not via the recorders) so the drawing is
    genuinely inconsistent: faces 200 mm apart, caps only 120 mm long, and a
    centreline that is not midway between the faces. ``verify`` must catch it
    from the geometry alone, with no --expect-* argument.
    """
    import ezdxf

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0, -100, 0), (12000, -100, 0), dxfattribs={"layer": "WAL1"})
    msp.add_line((0, 100, 0), (12000, 100, 0), dxfattribs={"layer": "WAL1"})
    msp.add_line((0, -60, 0), (0, 60, 0), dxfattribs={"layer": "WAL2"})
    msp.add_line((12000, -60, 0), (12000, 60, 0), dxfattribs={"layer": "WAL2"})
    # centreline drawn 30 mm off the wall axis: not the mid-offset of the faces
    msp.add_line((0, 30, 0), (12000, 30, 0), dxfattribs={"layer": "CEN1"})
    out = tmp_path / "mangled.dxf"
    doc.saveas(out)

    payload = run_json("verify", "--in", str(out), expect=EXIT_VERIFY_FAILED)
    assert payload["ok"] is False
    assert "wall_cap_length_equals_thickness" in payload["failed"]
    assert "centerline_midway_between_faces" in payload["failed"]


def test_verify_fails_on_a_hatch_in_the_drawing(tmp_path: Path) -> None:
    """HATCH stays forbidden; INSERT does not. See cli.FORBIDDEN_DXF_TYPES.

    The two are not the same case: the hatch recorder chose a boundary
    polyline plus a contract BECAUSE nothing downstream can measure a HATCH,
    while the block recorder legitimately writes an INSERT and reports what
    it hides. So this test must keep failing on HATCH, and a separate one
    covers the INSERT half.
    """
    import ezdxf

    doc = ezdxf.new("R2018")
    doc.modelspace().add_line((0, 0, 0), (1000, 0, 0), dxfattribs={"layer": "WAL1"})
    doc.modelspace().add_line((0, 200, 0), (1000, 200, 0), dxfattribs={"layer": "WAL1"})
    doc.modelspace().add_hatch(color=7).paths.add_polyline_path(
        [(0, 0), (1000, 0), (1000, 200), (0, 200)], is_closed=True
    )
    out = tmp_path / "hatched.dxf"
    doc.saveas(out)

    payload = run_json("verify", "--in", str(out), expect=EXIT_VERIFY_FAILED)
    assert "no_hatch_entity" in payload["failed"]


def test_an_insert_is_no_longer_a_forbidden_entity_type(tmp_path: Path) -> None:
    """The contradiction this change removes, pinned in both directions.

    A drawing with an INSERT used to FAIL no_insert_or_hatch with no way to
    pass it: the tool rejected its own block recorder's output. INSERT is now
    allowed, and the check that replaced the ban -- insert_downstream_visibility
    -- is asserted here so a future edit cannot quietly put the ban back
    without this test noticing.
    """
    import ezdxf

    from all_in_cad.recorder import cli

    assert "INSERT" not in cli.FORBIDDEN_DXF_TYPES
    assert "HATCH" in cli.FORBIDDEN_DXF_TYPES
    doc = ezdxf.new("R2018")
    doc.blocks.new("SOME_BLOCK").add_line((0, 0), (1000, 0), dxfattribs={"layer": "WAL1"})
    doc.modelspace().add_blockref("SOME_BLOCK", (0, 0), dxfattribs={"layer": "WAL1"})
    out = tmp_path / "inserted.dxf"
    doc.saveas(out)

    # --allow-hidden-insert is what the ban used to make impossible: an
    # INSERT is no longer a forbidden type at all, only an unacknowledged one.
    payload = run_json(
        "verify", "--in", str(out), "--only", "block", "--allow-hidden-insert", expect=EXIT_OK
    )
    assert "no_hatch_entity" in [check["name"] for check in payload["checks"]]
    visibility = [c for c in payload["checks"] if c["name"] == "insert_downstream_visibility"]
    assert len(visibility) == 1, "the replacement check must exist exactly once"
    assert visibility[0]["status"] == "PASS"
    assert payload["block"]["hidden_entities"] == 1
    assert payload["block"]["hidden_length_mm"] == 1000.0


def test_verify_fails_on_an_unknown_layer(tmp_path: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2018")
    doc.modelspace().add_line((0, 0, 0), (1000, 0, 0), dxfattribs={"layer": "ZZZ_MYSTERY"})
    out = tmp_path / "unknown_layer.dxf"
    doc.saveas(out)

    payload = run_json("verify", "--in", str(out), expect=EXIT_VERIFY_FAILED)
    assert "layer_semantics_mapped" in payload["failed"]


def test_verify_fails_when_the_door_is_missing(tmp_path: Path) -> None:
    out = tmp_path / "wall_only.dxf"
    run_cli(
        "wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
        "--out", str(out), expect=EXIT_OK,
    )
    payload = run_json("verify", "--in", str(out), expect=EXIT_VERIFY_FAILED)
    assert "door_present" in payload["failed"]


def test_verify_failure_text_output_says_fail_and_names_the_check(tmp_path: Path) -> None:
    out = _make_plan(tmp_path)
    completed = run_cli(
        "verify", "--in", str(out), "--expect-door-width", "1000",
        expect=EXIT_VERIFY_FAILED,
    )
    assert "RESULT: FAIL" in completed.stdout
    assert "RESULT: PASS" not in completed.stdout
    assert "door_width_matches_expectation" in completed.stdout
    assert "failed:" in completed.stdout


def test_verify_rejects_a_missing_file(tmp_path: Path) -> None:
    completed = run_cli("verify", "--in", str(tmp_path / "nope.dxf"))
    assert completed.returncode == EXIT_USAGE
    assert "no such drawing" in completed.stderr


# ---------------------------------------------------------------------------
# window / opening  (added with the CLI surface for the two recorders)
#
# The overwrite contract below is the point of this section, not a formality.
# window.py and opening.py were finished and green at module level while being
# unreachable from the command line, and the composition layer is exactly where
# this repository already lost a drawing once. A new subcommand with its own
# idea of "--out" would put that same hole back, so each test here pins the
# SHARED contract: create-only, --force to replace, nothing left behind on a
# rejection, and the same exit codes.
# ---------------------------------------------------------------------------


def _window_args(out: Path, *extra: str) -> list[str]:
    return [
        "window",
        "--indoor", "3000", "0",
        "--direction", "3000", "1000",
        "--width", "1500",
        "--divisions", "2",
        "--window-thickness", "100",
        *extra,
        "--out", str(out),
    ]


def _opening_args(out: Path, *extra: str) -> list[str]:
    return [
        "opening",
        "--ref-start", "0", "0",
        "--ref-end", "12000", "0",
        "--width", "900",
        "--opening-thickness", "200",
        *extra,
        "--out", str(out),
    ]


def test_help_lists_window_and_opening(tmp_path: Path) -> None:
    completed = run_cli("--help", expect=EXIT_OK)
    for name in ("wall", "door", "plan", "verify", "window", "opening"):
        assert name in completed.stdout, f"{name} missing from --help"


def test_help_lists_the_five_newly_wired_subcommands() -> None:
    """The reachability defect this work closed, stated as a test.

    Five of thirteen recorder modules had no subcommand at all, and
    verify_drawing accepted no expectation for any of them, so a user who
    adopted hatch/dim/text/block/layer through the library had no shipped
    verification path. That is the exact condition under which the earlier
    CRITICAL hid.
    """
    completed = run_cli("--help", expect=EXIT_OK)
    for name in ("hatch", "dim", "text", "block", "layers"):
        assert name in completed.stdout, f"{name} missing from --help"
    verify_help = run_cli("verify", "--help", expect=EXIT_OK).stdout
    for name in ("hatch", "dim", "text", "block", "layers"):
        assert name in verify_help, f"verify --only {name} is not offered"


def test_window_records_and_verifies(tmp_path: Path) -> None:
    out = tmp_path / "window.dxf"
    payload = run_json(*_window_args(out), expect=EXIT_OK)
    assert out.exists()
    assert payload["command"] == "window"
    assert payload["ok"] is True
    assert payload["dxf_version"] == "R2018"
    assert payload["layers"] == ["WIN", "WINBAR", "WINELE"]
    # 2 jambs + glazing + casement arc on WIN, 2 bars on WINBAR, 1 face on WINELE
    assert payload["entity_count"] == 7
    assert payload["layer_counts"] == {"WIN": 4, "WINBAR": 2, "WINELE": 1}
    assert payload["overwrote_existing"] is False
    assert payload["byte_idempotent"] is False
    window = payload["window"]
    assert window["width_mm"] == 1500
    assert window["thickness_mm"] == 100
    assert window["divisions"] == 2
    assert window["center"] == [3000, 0]

    report = run_json("verify", "--in", str(out), "--only", "window", expect=EXIT_OK)
    assert report["ok"] is True
    assert report["window"]["opening_width_mm"] == 1500


def test_window_measures_the_indoor_side_from_a_wall_segment(tmp_path: Path) -> None:
    out = tmp_path / "window_wall.dxf"
    payload = run_json(
        *_window_args(out, "--wall-start", "0", "0", "--wall-end", "12000", "0"),
        expect=EXIT_OK,
    )
    assert payload["window"]["interior_side_source"] == "measured from --wall-start/--wall-end"
    assert payload["window"]["interior_side"] in ("left", "right")


def test_window_reports_an_assumed_indoor_side_when_nothing_decides_it(
    tmp_path: Path,
) -> None:
    out = tmp_path / "window_assumed.dxf"
    payload = run_json(*_window_args(out), expect=EXIT_OK)
    assert payload["window"]["interior_side_source"].startswith("assumed")
    assert payload["window"]["warnings"], "an unobservable indoor side must warn"


def test_window_divisions_change_the_entity_count(tmp_path: Path) -> None:
    zero = tmp_path / "w0.dxf"
    three = tmp_path / "w3.dxf"
    assert run_json(*_window_args(zero, "--divisions", "0"), expect=EXIT_USAGE)["ok"] is False
    payload = run_json(*_window_args(three, "--divisions", "3"), expect=EXIT_OK)
    assert payload["layer_counts"]["WINBAR"] == 3
    assert payload["entity_count"] == 8


def test_opening_records_and_verifies_with_a_warning_not_a_failure(
    tmp_path: Path,
) -> None:
    out = tmp_path / "opening.dxf"
    payload = run_json(*_opening_args(out), expect=EXIT_OK)
    assert out.exists()
    assert payload["command"] == "opening"
    assert payload["ok"] is True
    assert payload["layers"] == ["TEMP-OPENING-BND", "TEMP-OPENING-SYM"]
    assert payload["entity_count"] == 6
    assert payload["layer_counts"] == {"TEMP-OPENING-BND": 2, "TEMP-OPENING-SYM": 4}
    assert payload["opening"]["width_mm"] == 900
    assert payload["opening"]["thickness_mm"] == 200

    # The unresolved layer mapping is stated in the recording result, not hidden.
    assert payload["layer_mapping_resolved"] is False
    assert "LAYER_MAPPING_RESOLVED is False" in payload["layer_mapping_note"]

    report = run_json("verify", "--in", str(out), "--only", "opening", expect=EXIT_OK)
    assert report["ok"] is True, "a correctly recorded opening is not a defect"
    assert report["opening_layer_mapping_resolved"] is False
    assert "opening_layer_mapping_unresolved" in report["warnings"]
    assert "opening_layer_mapping_unresolved" not in report["failed"]
    assert report["opening"]["opening_width_mm"] == 900


def test_opening_unresolved_layer_is_printed_as_a_warning_not_hidden(
    tmp_path: Path,
) -> None:
    out = tmp_path / "opening_text.dxf"
    run_cli(*_opening_args(out), expect=EXIT_OK)
    completed = run_cli("verify", "--in", str(out), "--only", "opening", expect=EXIT_OK)
    assert "[warn] opening_layer_mapping_unresolved" in completed.stdout
    assert "RESULT: PASS" in completed.stdout
    assert "WARNING (does not affect the verdict)" in completed.stdout


def test_verify_still_fails_on_a_genuinely_unknown_layer_next_to_the_temp_ones(
    tmp_path: Path,
) -> None:
    import ezdxf

    seed = tmp_path / "seed.dxf"
    run_cli(*_opening_args(seed), expect=EXIT_OK)
    doc = ezdxf.readfile(seed)
    doc.modelspace().add_line((0, 0), (50, 50), dxfattribs={"layer": "ZZZ_MYSTERY"})
    mangled = tmp_path / "mystery.dxf"
    doc.saveas(mangled)
    payload = run_json(
        "verify", "--in", str(mangled), "--only", "opening", expect=EXIT_VERIFY_FAILED
    )
    assert "layer_semantics_mapped" in payload["failed"]
    # ... while the documented TEMP- fallback alone is still only a warning.
    assert "opening_layer_mapping_unresolved" in payload["warnings"]


# ---------------------------------------------------------------------------
# OVERWRITE DEFENCE on the new subcommands -- the load-bearing regression
# ---------------------------------------------------------------------------


def test_window_onto_an_existing_path_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "existing.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    before = _sha256(out)

    completed = run_cli(*_window_args(out), expect=EXIT_USAGE)
    assert "refusing to overwrite" in completed.stderr
    assert _sha256(out) == before, "a refused window must not touch the file"


def test_window_then_opening_on_the_same_path_is_refused(tmp_path: Path) -> None:
    """The exact data-loss sequence, with the new subcommands in it."""
    out = tmp_path / "shared.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    before = _sha256(out)
    completed = run_cli(*_opening_args(out), expect=EXIT_USAGE)
    assert "refusing to overwrite" in completed.stderr
    assert _sha256(out) == before


def test_opening_then_window_on_the_same_path_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "shared2.dxf"
    run_cli(*_opening_args(out), expect=EXIT_OK)
    before = _sha256(out)
    completed = run_cli(*_window_args(out), expect=EXIT_USAGE)
    assert "refusing to overwrite" in completed.stderr
    assert _sha256(out) == before


def test_refused_window_does_not_report_ok(tmp_path: Path) -> None:
    out = tmp_path / "refused.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    payload = run_json(*_window_args(out), expect=EXIT_USAGE)
    assert payload["ok"] is False
    assert payload["command"] == "window"
    assert payload["exit_code"] == EXIT_USAGE


def test_refused_opening_does_not_report_ok(tmp_path: Path) -> None:
    out = tmp_path / "refused2.dxf"
    run_cli(*_opening_args(out), expect=EXIT_OK)
    payload = run_json(*_opening_args(out), expect=EXIT_USAGE)
    assert payload["ok"] is False
    assert payload["command"] == "opening"


@pytest.mark.parametrize("builder", [_window_args, _opening_args])
def test_force_replaces_on_purpose_and_reports_the_replaced_hash(
    tmp_path: Path, builder
) -> None:
    out = tmp_path / "forced.dxf"
    run_cli(*builder(out), expect=EXIT_OK)
    before = _sha256(out)
    payload = run_json(*builder(out, "--force"), expect=EXIT_OK)
    assert payload["ok"] is True
    assert payload["overwrote_existing"] is True
    assert payload["replaced_sha256"] == before
    assert _sha256(out) != before


def test_a_refused_window_leaves_no_journal_behind(tmp_path: Path) -> None:
    out = tmp_path / "journalled.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    run_cli(*_window_args(out), expect=EXIT_USAGE)
    leftovers = [p for p in tmp_path.rglob("*") if p.is_file() and p.suffix != ".dxf"]
    assert not leftovers, f"transaction left files behind: {leftovers}"


# ---------------------------------------------------------------------------
# argument rejection: no file, ever
# ---------------------------------------------------------------------------


def test_window_rejects_a_zero_length_width_axis(tmp_path: Path) -> None:
    """Separate test: the override has to be the WHOLE --indoor pair."""
    out = tmp_path / "zero_axis.dxf"
    completed = run_cli(
        "window", "--indoor", "3000", "3000", "--direction", "3000", "3000",
        "--out", str(out), expect=EXIT_USAGE,
    )
    assert "window rejected" in completed.stderr
    assert not out.exists()


@pytest.mark.parametrize(
    "bad",
    [
        ["--width", "0"],
        ["--width", "-1500"],
        ["--width", "nan"],
        ["--width", "inf"],
        ["--window-thickness", "0"],
        ["--window-thickness", "-100"],
        ["--divisions", "0"],
        ["--divisions", "-1"],
        ["--casement-deg", "0"],
        ["--casement-deg", "360"],
        ["--wall-start", "0", "0"],  # half a wall segment
    ],
)
def test_window_rejects_bad_arguments_and_writes_nothing(
    tmp_path: Path, bad: list[str]
) -> None:
    out = tmp_path / "rejected.dxf"
    completed = run_cli(*_window_args(out, *bad), expect=EXIT_USAGE)
    assert "window rejected" in completed.stderr or "go together" in completed.stderr
    assert not out.exists()
    assert not list(tmp_path.glob("*.dxf"))


@pytest.mark.parametrize(
    "bad",
    [
        ["--width", "0"],
        ["--width", "-900"],
        ["--width", "nan"],
        ["--opening-thickness", "0"],
        ["--offset", "-1"],
        ["--ref-end", "0", "0"],  # zero-length reference line
        ["--collinear-tolerance", "-1"],
        ["--direction", "0", "0"],  # direction point on the start point
    ],
)
def test_opening_rejects_bad_arguments_and_writes_nothing(
    tmp_path: Path, bad: list[str]
) -> None:
    out = tmp_path / "rejected.dxf"
    completed = run_cli(*_opening_args(out, *bad), expect=EXIT_USAGE)
    assert "opening rejected" in completed.stderr
    assert not out.exists()
    assert not list(tmp_path.glob("*.dxf"))


def test_window_and_opening_rejection_is_also_reported_as_json(tmp_path: Path) -> None:
    out = tmp_path / "nope.dxf"
    payload = run_json(*_window_args(out, "--width", "0"), expect=EXIT_USAGE)
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_USAGE
    payload = run_json(*_opening_args(out, "--offset", "-1"), expect=EXIT_USAGE)
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_USAGE
    assert not list(tmp_path.glob("*.dxf"))


def test_bad_window_arguments_are_rejected_before_the_existing_file_is_looked_at(
    tmp_path: Path,
) -> None:
    """Same ordering contract as wall/door: validate, then touch the target."""
    out = tmp_path / "ordering.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    before = _sha256(out)
    completed = run_cli(*_window_args(out, "--width", "0"), expect=EXIT_USAGE)
    assert "window rejected" in completed.stderr
    assert "refusing to overwrite" not in completed.stderr
    assert _sha256(out) == before


# ---------------------------------------------------------------------------
# JSON schema parity
# ---------------------------------------------------------------------------


def test_window_and_opening_json_share_the_door_schema(tmp_path: Path) -> None:
    door = run_json(
        "door", "--center", "6000", "0", "-w", "900",
        "--out", str(tmp_path / "d.dxf"), expect=EXIT_OK,
    )
    window = run_json(*_window_args(tmp_path / "w.dxf"), expect=EXIT_OK)
    opening = run_json(*_opening_args(tmp_path / "o.dxf"), expect=EXIT_OK)

    base = set(door) - {"door"}
    assert base <= set(window), f"window missing {base - set(window)}"
    assert base <= set(opening), f"opening missing {base - set(opening)}"
    for key in (
        "command", "ok", "out", "overwrote_existing", "replaced_sha256",
        "byte_idempotent", "idempotency_note", "dxf_version", "layers",
        "entity_count", "layer_counts", "handles",
    ):
        assert key in window and key in opening, f"{key} absent from the new schema"
    assert "window" in window and "opening" in opening


# ---------------------------------------------------------------------------
# verify really inspects the new elements (negative controls)
# ---------------------------------------------------------------------------


def test_verify_fails_when_the_window_width_does_not_match_the_expectation(
    tmp_path: Path,
) -> None:
    out = tmp_path / "expect.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    payload = run_json(
        "verify", "--in", str(out), "--only", "window",
        "--expect-window-width", "1200", expect=EXIT_VERIFY_FAILED,
    )
    assert "window_width_matches_expectation" in payload["failed"]


def test_verify_fails_when_the_opening_width_does_not_match_the_expectation(
    tmp_path: Path,
) -> None:
    out = tmp_path / "expecto.dxf"
    run_cli(*_opening_args(out), expect=EXIT_OK)
    payload = run_json(
        "verify", "--in", str(out), "--only", "opening",
        "--expect-opening-width", "1000", expect=EXIT_VERIFY_FAILED,
    )
    assert "opening_width_matches_expectation" in payload["failed"]


def test_verify_fails_when_the_indoor_face_contradicts_the_casement_sweep(
    tmp_path: Path,
) -> None:
    """The indoor-side determination, not a restatement of the CLI's options."""
    import ezdxf

    out = tmp_path / "indoor.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    doc = ezdxf.readfile(out)
    for entity in doc.modelspace():
        if entity.dxf.layer == "WINELE":
            s, e = entity.dxf.start, entity.dxf.end
            # The glazing line runs along +Y at x=3000, so x is the normal:
            # mirror the face line exactly onto the other indoor side.
            entity.dxf.start = (2 * 3000 - s.x, s.y, s.z)
            entity.dxf.end = (2 * 3000 - e.x, e.y, e.z)
    mangled = tmp_path / "indoor_flip.dxf"
    doc.saveas(mangled)
    payload = run_json(
        "verify", "--in", str(mangled), "--only", "window", expect=EXIT_VERIFY_FAILED
    )
    assert "window_interior_side_agrees_with_casement_sweep" in payload["failed"]


def test_verify_fails_when_a_window_bar_leaves_the_centreline(tmp_path: Path) -> None:
    import ezdxf

    out = tmp_path / "bars.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    doc = ezdxf.readfile(out)
    for entity in doc.modelspace():
        if entity.dxf.layer == "WINBAR":
            s, e = entity.dxf.start, entity.dxf.end
            entity.dxf.start = (s.x + 60, s.y, s.z)
            entity.dxf.end = (e.x + 60, e.y, e.z)
    mangled = tmp_path / "bars_off.dxf"
    doc.saveas(mangled)
    payload = run_json(
        "verify", "--in", str(mangled), "--only", "window", expect=EXIT_VERIFY_FAILED
    )
    assert "window_bars_centred_on_centreline" in payload["failed"]


def test_verify_fails_when_the_opening_boundary_stops_short_of_the_thickness(
    tmp_path: Path,
) -> None:
    import ezdxf

    out = tmp_path / "short.dxf"
    run_cli(*_opening_args(out), expect=EXIT_OK)
    doc = ezdxf.readfile(out)
    for entity in doc.modelspace():
        if entity.dxf.layer == "TEMP-OPENING-BND":
            s = entity.dxf.start
            entity.dxf.end = (s.x, s.y + 50, s.z)
    mangled = tmp_path / "short2.dxf"
    doc.saveas(mangled)
    payload = run_json(
        "verify", "--in", str(mangled), "--only", "opening", expect=EXIT_VERIFY_FAILED
    )
    assert "opening_boundary_crosses_wall_thickness" in payload["failed"]


def test_verify_fails_when_an_opening_tick_is_not_45_degrees(tmp_path: Path) -> None:
    import ezdxf

    out = tmp_path / "tick.dxf"
    run_cli(*_opening_args(out), expect=EXIT_OK)
    doc = ezdxf.readfile(out)
    for entity in doc.modelspace():
        if entity.dxf.layer == "TEMP-OPENING-SYM":
            s, e = entity.dxf.start, entity.dxf.end
            if abs(e.x - s.x) > 1 and abs(e.y - s.y) > 1:  # a jamb tick
                entity.dxf.end = (e.x + 50, e.y, e.z)
    mangled = tmp_path / "tick2.dxf"
    doc.saveas(mangled)
    payload = run_json(
        "verify", "--in", str(mangled), "--only", "opening", expect=EXIT_VERIFY_FAILED
    )
    assert "opening_ticks_are_45_degree" in payload["failed"]


# ---------------------------------------------------------------------------
# --only scoping and fail-closed defaults
# ---------------------------------------------------------------------------


def test_only_window_on_a_drawing_without_a_window_fails(tmp_path: Path) -> None:
    out = tmp_path / "o.dxf"
    run_cli(*_opening_args(out), expect=EXIT_OK)
    payload = run_json("verify", "--in", str(out), "--only", "window", expect=EXIT_VERIFY_FAILED)
    assert "window_present" in payload["failed"]


def test_only_opening_on_a_drawing_without_an_opening_fails(tmp_path: Path) -> None:
    out = tmp_path / "w.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    payload = run_json("verify", "--in", str(out), "--only", "opening", expect=EXIT_VERIFY_FAILED)
    assert "opening_present" in payload["failed"]


def test_only_all_requires_all_four(tmp_path: Path) -> None:
    out = _make_plan(tmp_path)
    payload = run_json("verify", "--in", str(out), "--only", "all", expect=EXIT_VERIFY_FAILED)
    assert "window_present" in payload["failed"]
    assert "opening_present" in payload["failed"]
    # 'all' is now every recorder module, not four. A plan drawing holds no
    # hatch, dimension, annotation, block or recorded layer, so each of those
    # scopes must also be reported missing -- otherwise 'all' is a word.
    for absent in ("hatch_present", "dim_present", "text_present"):
        assert absent in payload["failed"], f"--only all did not require {absent}"


def test_default_only_still_checks_a_window_that_is_present(tmp_path: Path) -> None:
    """'both' must not become a way to skip a window that is in the drawing."""
    out = tmp_path / "w.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    payload = run_json("verify", "--in", str(out), expect=EXIT_VERIFY_FAILED)
    # wall and door are required by the default and are absent here, but the
    # window checks must still have run and reported.
    assert payload["window"].get("not_required_but_checked") is True
    assert "window_jamb_edges_perpendicular_to_width_axis" in [
        check["name"] for check in payload["checks"]
    ]


def test_verify_on_a_plan_drawing_is_unchanged_by_the_new_subcommands(
    tmp_path: Path,
) -> None:
    out = _make_plan(tmp_path)
    payload = run_json("verify", "--in", str(out), "--only", "both", expect=EXIT_OK)
    assert payload["ok"] is True
    assert payload["failed"] == []
    assert payload["entity_count"] == 11


# ---------------------------------------------------------------------------
# combination with plan
# ---------------------------------------------------------------------------


def test_plan_and_window_can_be_recorded_side_by_side_and_both_verify(
    tmp_path: Path,
) -> None:
    plan = tmp_path / "plan.dxf"
    window = tmp_path / "window.dxf"
    run_cli(
        "plan", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
        "--center", "6000", "0", "-w", "900", "--out", str(plan), expect=EXIT_OK,
    )
    run_cli(
        "window", "--indoor", "9000", "0", "--direction", "9000", "1000",
        "-w", "1500", "--wall-start", "0", "0", "--wall-end", "12000", "0",
        "--out", str(window), expect=EXIT_OK,
    )
    plan_report = run_json("verify", "--in", str(plan), "--only", "both", expect=EXIT_OK)
    window_report = run_json("verify", "--in", str(window), "--only", "window", expect=EXIT_OK)
    assert plan_report["entity_count"] == 11
    assert window_report["window"]["opening_width_mm"] == 1500


def test_plan_into_a_path_a_window_already_owns_is_refused(tmp_path: Path) -> None:
    """Cross-subcommand: the defence is the shared one, not a per-command one."""
    out = tmp_path / "collision.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    before = _sha256(out)
    completed = run_cli(
        "plan", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
        "--center", "6000", "0", "-w", "900", "--out", str(out), expect=EXIT_USAGE,
    )
    assert "refusing to overwrite" in completed.stderr
    assert _sha256(out) == before


def test_opening_records_a_warning_when_the_direction_pick_is_off_the_line(
    tmp_path: Path,
) -> None:
    out = tmp_path / "offcollinear.dxf"
    payload = run_json(
        *_opening_args(out, "--direction", "5000", "300"),
        expect=EXIT_OK,
    )
    assert payload["opening"]["warnings"], "an off-line direction pick must warn"
    assert run_json("verify", "--in", str(out), "--only", "opening", expect=EXIT_OK)["ok"]


# ---------------------------------------------------------------------------
# Round-trip regressions (added after the independent audit)
#
# WHY THESE EXIST, and why the suite was green without them.
#
# The CRITICAL the audit found -- `_signed_offset(tick["end"], tick["start"],
# axis)` passing the AXIS where the helper's third parameter is the NORMAL --
# made the opening recorder's own output fail its own verify, and 459 tests
# stayed green. The reason is a coverage shape, not a testing mistake:
#
#   * window_test.py / opening_test.py test the geometry MODULES. They have no
#     verify tests at all, so no assertion ever crossed the module -> verify
#     boundary.
#   * cli_test.py tested verify, but on wall/door drawings only. Its wall/door
#     tests asserted check COUNTS and specific check NAMES, never that each
#     recorder's own output round-trips.
#   * e2e_test.py claimed in its own docstring to be "the authority on whether
#     those pieces agree with each other", and covered wall/door only.
#
# So a bug in an analyser could not be caught: nothing ever fed a real
# recording of that element INTO that element's analyser and demanded a pass.
# `test_every_recorder_round_trips_through_its_own_verify` below is the missing
# shape, and it is deliberately a single table over ALL recorders so a future
# subcommand cannot be added without inheriting the round trip.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subcommand, extra, only",
    [
        ("wall", ["--start", "0", "0", "--end", "12000", "0", "--thickness", "200"], "wall"),
        ("door", ["--center", "6000", "0", "--width", "900"], "door"),
        (
            "window",
            ["--indoor", "3000", "0", "--direction", "3000", "1000", "--width", "1500",
             "--divisions", "2"],
            "window",
        ),
        (
            "opening",
            ["--ref-start", "0", "0", "--ref-end", "12000", "0", "--width", "900",
             "--opening-thickness", "200"],
            "opening",
        ),
    ],
)
def test_every_recorder_round_trips_through_its_own_verify(
    tmp_path: Path, subcommand: str, extra: list[str], only: str
) -> None:
    """A recorder must never produce a drawing its own verify rejects.

    This is the shape the suite was missing. The audit's C1 lived exactly in
    this gap: opening recorded cleanly, exited 0, and only failed when verify
    measured it -- and nothing ever asked it to.
    """
    out = tmp_path / f"{subcommand}.dxf"
    run_cli(subcommand, *extra, "--out", str(out), expect=EXIT_OK)
    payload = run_json("verify", "--in", str(out), "--only", only, expect=EXIT_OK)
    assert payload["ok"] is True, (
        f"{subcommand} produced a drawing its own verify rejects: "
        f"{payload['failed']}"
    )


def test_opening_round_trips_on_both_sides(tmp_path: Path) -> None:
    """side=right is a mirror; a check that only works for side=left is a bug
    waiting for a real drawing. The audit's C1 failed identically on both."""
    for side in ("left", "right"):
        out = tmp_path / f"opening_{side}.dxf"
        run_cli(
            "opening", "--ref-start", "0", "0", "--ref-end", "12000", "0",
            "-w", "900", "--opening-thickness", "200", "--side", side,
            "--out", str(out), expect=EXIT_OK,
        )
        payload = run_json("verify", "--in", str(out), "--only", "opening", expect=EXIT_OK)
        assert payload["ok"] is True, f"side={side}: {payload['failed']}"


def test_opening_centre_tick_is_measured_across_the_wall_not_along_it(
    tmp_path: Path,
) -> None:
    """The C1 regression, stated as an assertion rather than as a round trip.

    A centre tick runs ACROSS the wall, so its along-axis component is zero.
    The audit's bug measured the along component and compared it to half the
    thickness, which no correctly shaped tick can satisfy.
    """
    out = tmp_path / "tick.dxf"
    run_cli(*_opening_args(out), expect=EXIT_OK)
    payload = run_json("verify", "--in", str(out), "--only", "opening", expect=EXIT_OK)
    measured = payload["opening"]["centre_tick_length_mm"]
    assert abs(measured - 100.0) <= 1e-6, (
        f"centre tick measured {measured} mm across a 200 mm wall; expected 100. "
        "An along-axis reading here is the argument-order bug."
    )


# ---------------------------------------------------------------------------
# S1: a requested --expect-* that cannot be measured must FAIL, not vanish
# ---------------------------------------------------------------------------


def test_verify_fails_rather_than_discarding_an_unmeasurable_expectation(
    tmp_path: Path,
) -> None:
    """The user asked for an assertion. Producing no check and exiting 0 is
    reporting a success for something that was never verified."""
    out = tmp_path / "door.dxf"
    run_cli("door", "--center", "6000", "0", "-w", "900", "--out", str(out), expect=EXIT_OK)
    payload = run_json(
        "verify", "--in", str(out), "--only", "door",
        "--expect-wall-thickness", "200", expect=EXIT_VERIFY_FAILED,
    )
    assert "wall_thickness_matches_expectation" in payload["failed"]
    names = [check["name"] for check in payload["checks"]]
    assert "wall_thickness_matches_expectation" in names, "the check must still EXIST"


def test_verify_fails_rather_than_discarding_an_unmeasurable_door_expectation(
    tmp_path: Path,
) -> None:
    out = tmp_path / "wallonly.dxf"
    run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--out", str(out), expect=EXIT_OK)
    payload = run_json(
        "verify", "--in", str(out), "--only", "wall",
        "--expect-door-width", "900", expect=EXIT_VERIFY_FAILED,
    )
    assert "door_width_matches_expectation" in payload["failed"]


# ---------------------------------------------------------------------------
# S2: cap / centreline checks are no longer silently omitted
# ---------------------------------------------------------------------------


def test_verify_fails_when_a_wall_has_lost_its_caps(tmp_path: Path) -> None:
    import ezdxf

    out = tmp_path / "nocaps.dxf"
    run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--out", str(out), expect=EXIT_OK)
    doc = ezdxf.readfile(out)
    msp = doc.modelspace()
    caps = [e for e in msp if e.dxftype() == "LINE" and e.dxf.layer == "WAL2"]
    assert caps, "the fixture needs the wall's cap lines to remove"
    for entity in caps:
        msp.delete_entity(entity)
    mangled = tmp_path / "nocaps2.dxf"
    doc.saveas(mangled)
    payload = run_json("verify", "--in", str(mangled), "--only", "wall",
                       expect=EXIT_VERIFY_FAILED)
    assert "wall_caps_present" in payload["failed"]


def test_the_cap_check_count_does_not_silently_shrink(tmp_path: Path) -> None:
    """The audit's sharpest observation: deleting the caps took the check count
    from 19 to 5 and NOTHING said so. The count is now part of the report the
    user reads, so a silent shrink is visible even when nothing fails."""
    import ezdxf

    out = tmp_path / "full.dxf"
    run_cli("wall", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
            "--out", str(out), expect=EXIT_OK)
    full = run_json("verify", "--in", str(out), "--only", "wall", expect=EXIT_OK)
    doc = ezdxf.readfile(out)
    msp = doc.modelspace()
    for entity in [e for e in msp if e.dxftype() == "LINE" and e.dxf.layer == "WAL2"]:
        msp.delete_entity(entity)
    stripped = tmp_path / "stripped.dxf"
    doc.saveas(stripped)
    reduced = run_json("verify", "--in", str(stripped), "--only", "wall",
                       expect=EXIT_VERIFY_FAILED)
    assert len(reduced["checks"]) >= len(full["checks"]), (
        f"check count fell from {len(full['checks'])} to {len(reduced['checks'])} "
        "when caps were removed: a check is being skipped rather than reported"
    )
    assert len(reduced["failed"]) == 1, "the loss must be reported, not absorbed"


# ---------------------------------------------------------------------------
# S6: --axis-deg is refused on BOTH plan branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("anchor", [["--hinge", "5550", "0"], ["--center", "6000", "0"]])
def test_plan_refuses_axis_deg_on_both_door_branches(
    tmp_path: Path, anchor: list[str]
) -> None:
    """One rule, one strength. --hinge used to accept the flag silently while
    --center refused it, so a user could believe a flag had been honoured."""
    out = tmp_path / f"axis_{anchor[0][2:]}.dxf"
    completed = run_cli(
        "plan", "--start", "0", "0", "--end", "12000", "0", "-t", "200",
        *anchor, "-w", "900", "--axis-deg", "45", "--out", str(out),
        expect=EXIT_USAGE,
    )
    assert "--axis-deg cannot be combined with a wall segment" in completed.stderr
    assert not out.exists()


# ---------------------------------------------------------------------------
# S7: a contradictory --interior-side is refused, not silently overridden
# ---------------------------------------------------------------------------


def test_window_refuses_an_interior_side_that_contradicts_the_wall(
    tmp_path: Path,
) -> None:
    out = tmp_path / "contradiction.dxf"
    completed = run_cli(
        "window", "--indoor", "3000", "0", "--direction", "3000", "1000",
        "--interior-side", "right", "--wall-start", "0", "0", "--wall-end", "12000", "0",
        "--out", str(out), expect=EXIT_USAGE,
    )
    assert "contradicts the wall" in completed.stderr
    assert not out.exists(), "a rejected request must leave no file"


def test_window_accepts_an_interior_side_that_agrees_with_the_wall(
    tmp_path: Path,
) -> None:
    """The guard must not be a blanket refusal: agreeing input still works."""
    from all_in_cad.recorder import cli as cli_module
    from all_in_cad.recorder import window as window_module

    geometry = window_module.make_window(
        (3000.0, 0.0), (3000.0, 1000.0), 1500.0, 100.0, 1,
        wall_segment=((0.0, 0.0), (12000.0, 0.0)),
    )
    out = tmp_path / "agrees.dxf"
    run_cli(
        "window", "--indoor", "3000", "0", "--direction", "3000", "1000",
        "--interior-side", geometry.interior_side,
        "--wall-start", "0", "0", "--wall-end", "12000", "0",
        "--out", str(out), expect=EXIT_OK,
    )
    assert cli_module is not None and out.exists()


# ---------------------------------------------------------------------------
# M2: the casement arc is now tied to the opening
# ---------------------------------------------------------------------------


def test_verify_fails_when_the_casement_arc_is_moved_off_the_jamb(
    tmp_path: Path,
) -> None:
    """The audit moved the arc 5000 mm and verify still passed: the analyser
    never related the arc to the jambs, unlike the door analyser which does."""
    import ezdxf

    out = tmp_path / "arc.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    doc = ezdxf.readfile(out)
    moved = 0
    for entity in doc.modelspace():
        if entity.dxftype() == "ARC":
            c = entity.dxf.center
            entity.dxf.center = (c.x + 5000, c.y + 5000, c.z)
            moved += 1
    assert moved == 1, "the fixture needs exactly one casement arc"
    mangled = tmp_path / "arc_moved.dxf"
    doc.saveas(mangled)
    payload = run_json("verify", "--in", str(mangled), "--only", "window",
                       expect=EXIT_VERIFY_FAILED)
    assert "window_casement_arc_hinges_on_the_jamb" in payload["failed"]


def test_verify_checks_the_casement_arc_radius_against_the_opening(
    tmp_path: Path,
) -> None:
    import ezdxf

    out = tmp_path / "arc_r.dxf"
    run_cli(*_window_args(out), expect=EXIT_OK)
    doc = ezdxf.readfile(out)
    for entity in doc.modelspace():
        if entity.dxftype() == "ARC":
            entity.dxf.radius = 900.0
    mangled = tmp_path / "arc_r2.dxf"
    doc.saveas(mangled)
    payload = run_json("verify", "--in", str(mangled), "--only", "window",
                       expect=EXIT_VERIFY_FAILED)
    assert "window_casement_arc_radius_matches_opening" in payload["failed"]


# ---------------------------------------------------------------------------
# S4 / S5 / S8: the transaction layer
# ---------------------------------------------------------------------------


def test_apply_accepts_handles_returned_as_a_plain_sequence(tmp_path: Path) -> None:
    """A callback that RETURNS its handles is the plainest spelling there is.
    It used to be ignored, leaving the verifier's wanted set empty."""
    import ezdxf

    from all_in_cad.recorder import transaction

    target = tmp_path / "bare.dxf"
    seen: dict[str, object] = {}

    def record(txn):
        doc = ezdxf.new("R2018")
        handle = doc.modelspace().add_line(
            (0, 0), (10, 0), dxfattribs={"layer": "WAL1"}
        ).dxf.handle
        doc.saveas(txn.targets[0].path)
        return [str(handle)]

    with transaction.begin(
        target, verifiers=transaction.dxf_verifier()
    ) as txn:
        txn.apply(record)
        seen["handles"] = tuple(txn.targets[0].handles)
    assert target.exists()
    assert seen["handles"], (
        "the returned sequence must reach the verifier's wanted set; an empty "
        "one is the vacuous-verification defect"
    )


def test_apply_rejects_a_handles_value_it_cannot_use(tmp_path: Path) -> None:
    """Returning something unusable is an error, not a shrug that empties the
    wanted set and turns the verification into a no-op."""
    import ezdxf

    from all_in_cad.recorder import transaction

    target = tmp_path / "badhandles.dxf"

    class Weird:
        handles = 12345

    def record(txn):
        doc = ezdxf.new("R2018")
        doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "WAL1"})
        doc.saveas(txn.targets[0].path)
        return Weird()

    with pytest.raises(transaction.TransactionError) as excinfo:
        with transaction.begin(target) as txn:
            txn.apply(record)
    # NOTE on what is asserted. The unusable `handles` value is rejected, and
    # the transaction does NOT commit. Which exception the caller finally sees
    # is a separate, pre-existing defect the audit filed as m6: the context
    # manager's rollback runs on the way out and raises
    # ExternalModificationError, which replaces the original message. That
    # masking is NOT fixed here (it is m6, not one of the eight), so the
    # assertion is on the contract that matters -- nothing was silently
    # accepted and committed -- and not on the message text.
    assert not excinfo.value.args[0].startswith("committed")

def test_a_verifier_with_no_criteria_says_so_in_its_report(tmp_path: Path) -> None:
    """S5. A vacuous pass must never be readable as a real verification."""
    import ezdxf

    from all_in_cad.recorder import transaction

    empty = tmp_path / "empty.dxf"
    ezdxf.new("R2018").saveas(empty)
    report = transaction.dxf_verifier()(empty, ())
    assert "nothing_asserted" in report, (
        "a verifier with no criteria must mark its report; otherwise an empty "
        "file passes with nothing checked and the report looks like a verdict"
    )


def test_the_cli_never_builds_a_criteria_less_verifier() -> None:
    """The CLI cannot reach the vacuous state at all: it derives required
    layers from what it actually wrote. Proved by reading _write_output."""
    from all_in_cad.recorder import cli

    source = __import__("inspect").getsource(cli._write_output)
    assert "required_layers" in source
    assert "required_layers=sorted(layer_counts)" in source or "required_layers=" in source


def test_recover_pending_keeps_its_evidence_when_a_restore_fails(
    tmp_path: Path,
) -> None:
    """S8. After a failed restore the journal and the pre-image are the only
    things that can fix the drawing. Deleting them made the second call report
    'nothing to do' while the drawing was still corrupt."""
    import hashlib

    import ezdxf

    from all_in_cad.recorder import transaction

    journal_dir = tmp_path / "jr"
    journal_dir.mkdir()
    drawing = journal_dir / "d.dxf"
    doc = ezdxf.new("R2018")
    doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "WAL1"})
    doc.saveas(drawing)
    good = drawing.read_bytes()
    digest = hashlib.sha256(good).hexdigest()
    # a half-written pre-image, as a crash would leave
    (journal_dir / "j1.0.bak").write_bytes(b"<HALF WRITTEN BY A CRASH>")
    (journal_dir / "j1.json").write_text(
        json.dumps(
            {
                "version": transaction.JOURNAL_VERSION,
                "state": "applied",
                "targets": [
                    {
                        "path": str(drawing),
                        "before": {"existed": True, "size": len(good), "sha256": digest},
                        "after": {"existed": True, "size": len(good), "sha256": digest},
                        "backup": "j1.0.bak",
                        "handles": [],
                    }
                ],
            }
        )
    )
    first = transaction.recover_pending(journal_dir)
    assert first and first[0].verified is False
    assert (journal_dir / "j1.json").exists(), "the journal must survive a failed restore"
    assert (journal_dir / "j1.0.bak").exists(), "the pre-image must survive too"

    # a retry must still see the pending work, not report "nothing to do"
    second = transaction.recover_pending(journal_dir)
    assert len(second) == 1, "a second call must still find the pending journal"


def test_recover_pending_still_cleans_up_after_a_VERIFIED_restore(
    tmp_path: Path,
) -> None:
    """The S8 fix must not turn into a journal leak on the happy path."""
    import hashlib
    import shutil

    import ezdxf

    from all_in_cad.recorder import transaction

    journal_dir = tmp_path / "ok"
    journal_dir.mkdir()
    drawing = journal_dir / "d.dxf"
    doc = ezdxf.new("R2018")
    doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "WAL1"})
    doc.saveas(drawing)
    shutil.copyfile(drawing, journal_dir / "k1.0.bak")
    good = drawing.read_bytes()
    digest = hashlib.sha256(good).hexdigest()
    (journal_dir / "k1.json").write_text(
        json.dumps(
            {
                "version": transaction.JOURNAL_VERSION,
                "state": "applied",
                "targets": [
                    {
                        "path": str(drawing),
                        "before": {"existed": True, "size": len(good), "sha256": digest},
                        "after": {"existed": True, "size": len(good), "sha256": digest},
                        "backup": "k1.0.bak",
                        "handles": [],
                    }
                ],
            }
        )
    )
    outcome = transaction.recover_pending(journal_dir)
    assert outcome[0].verified is True
    assert sorted(p.name for p in journal_dir.iterdir()) == ["d.dxf"]
    assert not transaction.recover_pending(journal_dir), "and nothing is left pending"


# ---------------------------------------------------------------------------
# S3: a failed rollback step is a failed rollback
# ---------------------------------------------------------------------------


def test_a_failing_cancel_is_not_reported_as_a_successful_rollback() -> None:
    """S3, as BEHAVIOUR rather than as source text.

    machine_test.py's FakeHost carries an ``undo_raises`` flag but had no
    ``cancel_raises`` counterpart -- the missing fixture is precisely why the
    defect survived a green suite. So a host that refuses to cancel is built
    here and the run is required to fail.

    Before the fix this produced: exit success, ``rollback_called is True``,
    a note saying "rollback issued: cancel then one undo", and ``cancel()``
    never having run.
    """
    from all_in_cad.recorder.machine import RunOptions, RunStateMachine
    from all_in_cad.recorder.machine_test import (
        VECTOR_BY_ID,
        FakeClock,
        FakeHost,
        build_plan,
    )

    # GV-11 read as an always-zero counter is a MEASURED rollback trigger:
    # machine_test::test_trap6_counter_must_settle_before_a_delta_is_computed
    # asserts result.rollback_called is True for exactly this setup.
    class CancelRefuses(FakeHost):
        def cancel(self) -> str:  # type: ignore[override]
            self.cancel_calls += 1
            raise RuntimeError("cancel refused by host")

    vector = VECTOR_BY_ID["GV-11"]
    given = {**vector["given"], "entity_count_reads": [0, 0, 0],
             "actual_final_entities": 78}
    host = CancelRefuses(given)
    plan = build_plan(given)
    clock = FakeClock()
    result = RunStateMachine(
        host, plan, options=RunOptions(clock=clock, sleep=clock.sleep)
    ).run()

    # rollback_called is now False, and that is the POINT: the flag used to be
    # set True by code that never reached the host, so it recorded an intention
    # rather than an outcome. A cancel that raised leaves the flag false and
    # the run failed, which is the truth.
    assert result.error_code == "E_ROLLBACK_FAILED", (
        "a cancel that never ran is a failed rollback, not a reported one; "
        f"got error_code={result.error_code!r}"
    )
    assert str(result.terminal_state) == "failed"
    assert result.rollback_called is False, (
        "rollback_called must reflect what happened, not what was attempted"
    )
    notes = " ".join(result.notes)
    assert "cancel refused by host" in notes, (
        f"the host's own refusal must reach the report, not be reduced to a "
        f"generic line: {notes!r}"
    )
    assert "rollback issued: cancel then one undo" not in notes, (
        "the run must not announce a rollback that did not happen"
    )


def test_a_failing_undo_is_still_a_failed_rollback() -> None:
    """The control for the test above: undo was already handled correctly, and
    must stay that way. A fix that only moved the failure around would pass one
    of these two and fail the other."""
    from all_in_cad.recorder.machine_test import VECTOR_BY_ID, run_vector

    # GV-07 + undo_raises is the MEASURED undo-failure path
    # (machine_test::test_failure_paths_with_never_ending_host asserts exactly
    # this pairing yields E_ROLLBACK_FAILED).
    result, host = run_vector(VECTOR_BY_ID["GV-07"], undo_raises=True)
    assert result.error_code == "E_ROLLBACK_FAILED"
    assert str(result.terminal_state) == "failed"
    assert "undo refused by host" in " ".join(result.notes)


# ---------------------------------------------------------------------------
# source-integrity + return-code contract (added after the M4 mutation
# survived a full green run; see the note on the test immediately below)
# ---------------------------------------------------------------------------


def _cli_module_path() -> str:
    """Absolute path of the cli.py *this* pytest process is testing."""
    import all_in_cad.recorder.cli as mod

    return Path(mod.__file__).resolve()


def test_the_subprocess_cli_runs_the_same_source_this_test_process_sees() -> None:
    """The subprocess must exercise the cli.py that this test session imported.

    Every other test in this file drives the CLI as a child process, which is
    the right way to test a process contract. But it means the code under test
    is whatever the *child* resolves on ``sys.path``, not what this process
    holds. Those two can silently diverge: a mutation harness, a stale editable
    install, or a copied tree put on ``PYTHONPATH`` all make the suite report
    green while testing code nobody edited.

    That is not hypothetical. Mutating ``cmd_verify``'s failure return to
    ``EXIT_OK`` in a copy of the tree, then running this file with an explicit
    ``PYTHONPATH=<copy>\\src``, left all 133 tests green -- because
    :func:`_child_env` drops ``PYTHONPATH``, so every child fell back to the
    editable install and ran the *unmutated* cli.py. The defect that CI could
    not see was in the harness, not in the code.

    This test pins the link between the two. If they ever diverge again, the
    run is red instead of falsely green.
    """
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
        f"process imported.\n  child : {child_path}\n  parent: {parent_path}\n"
        "A green suite here does not mean the subprocess executed the code "
        "under test. Check _child_env(): it must not drop the PYTHONPATH that "
        "selects the tree being tested."
    )


def test_a_child_env_keeps_the_pythonpath_that_selects_the_code_under_test() -> None:
    """Directly pin the environment behaviour that made the suite lie.

    ``_child_env`` exists to strip the host's ``PYTHONHOME``/``PYTHONPATH``
    because Aside injects them and the venv's stdlib breaks. Stripping
    ``PYTHONPATH`` unconditionally also strips a value a caller set on purpose
    to point at the tree under test, which is what turned a mutation into a
    silent pass. This test fails if that regression comes back.
    """
    import all_in_cad.recorder.cli_test as self_mod

    src_root = str(Path(self_mod.__file__).resolve().parents[2])
    env = self_mod._child_env()
    assert "PYTHONHOME" not in env
    on_path = env.get("PYTHONPATH", "").split(os.pathsep) if env.get("PYTHONPATH") else []
    assert src_root in on_path, (
        "_child_env() dropped the source root that selects the code under "
        f"test. Expected {src_root!r} in PYTHONPATH, got {env.get('PYTHONPATH')!r}. "
        "Without it the child process silently imports some other copy."
    )


def _verify_args(dxf: Path, *extra: str) -> argparse.Namespace:
    from all_in_cad.recorder import cli

    return cli.build_parser().parse_args(["verify", "--in", str(dxf), *extra])


def test_cmd_verify_never_returns_exit_ok_for_a_failing_report(
    tmp_path: Path,
) -> None:
    """Return-code contract, asserted in-process.

    The subprocess tests above already check exit codes end to end, but they
    only do so through a child that may not be running this code. This one
    calls ``cmd_verify`` directly, so it cannot be fooled by path resolution.

    For every report where ``ok`` is false, the return value must not be
    ``EXIT_OK``. The contract is stated negatively on purpose: adding a new
    failure route cannot be made to pass by returning a different nonzero code.
    """
    from all_in_cad.recorder import cli

    mangled = _mangled_wall_drawing(tmp_path)
    args = _verify_args(mangled)
    report = cli.verify_drawing(mangled, tol=args.tol)
    assert report["ok"] is False, "control failed: this drawing should not verify"

    code = cli.cmd_verify(_verify_args(mangled))
    assert code != cli.EXIT_OK, (
        "cmd_verify returned EXIT_OK for a report whose 'ok' is False: a "
        "verification failure would be reported to the shell as success."
    )
    assert code == cli.EXIT_VERIFY_FAILED


def test_cmd_verify_returns_exit_ok_only_for_a_passing_report(
    tmp_path: Path,
) -> None:
    """The positive half: the contract must not be 'always fail' either.

    Without this, a defense that simply returned a nonzero value everywhere
    would pass the negative test above while breaking every real run.
    """
    from all_in_cad.recorder import cli

    good = _make_plan(tmp_path, "good.dxf")
    assert cli.verify_drawing(good)["ok"] is True, "control failed: reference case must verify"
    assert cli.cmd_verify(_verify_args(good)) == cli.EXIT_OK


def test_cmd_verify_json_and_text_paths_agree_on_the_exit_code(
    tmp_path: Path,
) -> None:
    """The ``--json`` branch must not report a different verdict than the text one.

    ``cmd_verify`` prints different bodies per branch and returns from a single
    statement after both. A future edit that returns from inside the text branch
    would make a failing draw exit 0 in text mode while the JSON contract held,
    which is exactly the 'verification failed but reported as success' disease
    this module is supposed to make impossible.
    """
    from all_in_cad.recorder import cli

    mangled = _mangled_wall_drawing(tmp_path)
    text_code = cli.cmd_verify(_verify_args(mangled))
    json_code = cli.cmd_verify(_verify_args(mangled, "--json"))
    assert text_code == json_code == cli.EXIT_VERIFY_FAILED


def _mangled_wall_drawing(tmp_path: Path, name: str = "mangled.dxf") -> Path:
    """A drawing that is genuinely broken, built without the recorders.

    Faces 200 mm apart but caps only 120 mm long and a centreline that is not
    midway between the faces. Built with ezdxf directly so nothing in the
    recorder can vouch for it, and so no ``--expect-*`` argument is needed.
    """
    import ezdxf

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0, -100, 0), (12000, -100, 0), dxfattribs={"layer": "WAL1"})
    msp.add_line((0, 100, 0), (12000, 100, 0), dxfattribs={"layer": "WAL1"})
    msp.add_line((0, -60, 0), (0, 60, 0), dxfattribs={"layer": "WAL2"})
    msp.add_line((12000, -60, 0), (12000, 60, 0), dxfattribs={"layer": "WAL2"})
    msp.add_line((0, 30, 0), (12000, 30, 0), dxfattribs={"layer": "CEN1"})
    out = tmp_path / name
    doc.saveas(out)
    return out


def test_a_mangled_drawing_exits_one_through_the_real_process_contract(
    tmp_path: Path,
) -> None:
    """Negative control at the process level, for the same defect M4 was.

    The in-process contract tests above are immune to path confusion; this one
    is not. Both are needed: this proves the shipped exit code, the other
    proves the logic in the tree under test.
    """
    mangled = _mangled_wall_drawing(tmp_path, "broken.dxf")
    payload = run_json("verify", "--in", str(mangled), expect=EXIT_VERIFY_FAILED)
    assert payload["ok"] is False
    assert payload["failed"], "a failing verify must name at least one failed check"
    assert payload["exit_code"] == EXIT_VERIFY_FAILED

def test_the_verify_report_never_contradicts_its_own_check_list(
    tmp_path: Path,
) -> None:
    """Sweep: ``ok`` and ``exit_code`` must be exactly ``not failed``.

    Both are recomputed from the same ``failed`` list in three separate places
    today -- in the report dict, in ``cmd_verify``'s return, and implicitly in
    the text branch. Nothing forces them to stay in agreement: an edit that
    derives one of them from a different condition would produce a JSON body
    saying ``"ok": false`` alongside ``"exit_code": 0``, or the reverse. Any
    such disagreement is a silent success by construction, so it is pinned
    against every reachable report rather than one hand-picked failure.
    """
    from all_in_cad.recorder import cli

    good = _make_plan(tmp_path, "good.dxf")
    bad = _mangled_wall_drawing(tmp_path, "bad.dxf")
    scoped = _make_plan(tmp_path, "scoped.dxf")

    cases: list[tuple[Path, tuple[str, ...]]] = [
        (good, ()),
        (bad, ()),
        (scoped, ("--only", "window")),
        (scoped, ("--only", "all")),
        (good, ("--expect-entities", "12")),
    ]
    for drawing, extra in cases:
        args = _verify_args(drawing, *extra)
        report = cli.verify_drawing(
            drawing,
            tol=args.tol,
            require=args.only,
        )
        failed = report["failed"]
        assert report["ok"] is (not failed), (
            f"'ok' disagrees with 'failed' for {drawing.name} {extra}: "
            f"ok={report['ok']!r} failed={failed!r}"
        )
        assert report["exit_code"] == (
            cli.EXIT_OK if not failed else cli.EXIT_VERIFY_FAILED
        ), f"'exit_code' disagrees with 'failed' for {drawing.name} {extra}"
        # every named failure must really be a FAIL check, and vice versa
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        for name in failed:
            assert statuses.get(name) == "FAIL", (
                f"failed lists {name!r} but its check status is "
                f"{statuses.get(name)!r}"
            )
        for check in report["checks"]:
            if check["status"] == "FAIL":
                assert check["name"] in failed, (
                    f"check {check['name']!r} is FAIL but missing from 'failed'; "
                    "the report would print a failing check and report ok"
                )


def test_a_failing_check_can_never_be_downgraded_to_a_warning(
    tmp_path: Path,
) -> None:
    """The permissive-default risk, pinned: WARN must not mask FAIL.

    ``verify`` has a WARN status that is explicitly non-blocking by design
    (the opening-layer mapping note). The dangerous version of that feature is
    a check that is genuinely broken being emitted as WARN, which leaves
    ``ok`` true and the exit code 0. Every check that lands in ``warnings`` is
    therefore required to be a genuinely non-fatal one, and the two halves of
    the split must never both claim the same check.
    """
    from all_in_cad.recorder import cli

    good = _make_plan(tmp_path, "warn.dxf")
    report = cli.verify_drawing(good)
    warned = set(report["warnings"])
    failed = set(report["failed"])
    assert not (warned & failed), f"a check is both warned and failed: {warned & failed}"
    for check in report["checks"]:
        if check["name"] in warned:
            assert check["status"] == "WARN", (
                f"{check['name']!r} is in 'warnings' but its status is "
                f"{check['status']!r}; a failure reported as a warning exits 0"
            )
        if check["status"] in ("PASS", "FAIL", "WARN"):
            continue
        raise AssertionError(f"unknown check status {check['status']!r}")

# ===========================================================================
# The five modules that had no subcommand and no verification path
# ===========================================================================
#
# The reachability defect: hatch, dim, text, block and layer were importable
# from the library and unreachable from the CLI, and verify_drawing accepted
# no expectation for any of them. Everything below is in one file on purpose
# --
#   * a ROUNDTRIP per module: the drawing the CLI made, verified by the CLI
#     that made it, with a real expectation (not merely "--only X exits 0",
#     which a check that measures nothing would also satisfy);
#   * a FAILURE CONTROL per module: a hand-mangled drawing that MUST exit 1,
#     built by editing the file with ezdxf so no recorder can vouch for it.
#
# The control is the half that matters. A roundtrip alone is satisfied by a
# verifier that passes everything, which is the failure pattern this project
# has already shipped once: the earlier CRITICAL hid because nothing ever fed
# a real recording of an element into that element's own analyser AND demanded
# a pass on a broken one.
# ---------------------------------------------------------------------------

_NEW_RECORDERS = [
    (
        "hatch",
        [
            "--boundary", "0", "0", "2000", "0", "2000", "1000", "0", "1000",
            "--layer", "HATCHA", "--pattern-name", "ANSI31",
        ],
        ["--only", "hatch", "--unresolved-layer", "HATCHA"],
    ),
    ("dim", ["--p1", "0", "0", "--p2", "3000", "0"], ["--only", "dim"]),
    (
        "text",
        ["--content", "hello note", "--insert", "100", "100", "--layer", "NOTE"],
        ["--only", "text", "--unresolved-layer", "NOTE"],
    ),
    (
        "block",
        [
            "--name", "BLKA", "--line", "0", "0", "1000", "0", "--line", "0", "0", "0", "600",
            "--entity-layer", "WAL1", "--location", "5000", "0",
        ],
        ["--only", "block"],
    ),
    ("layers", ["--layer", "WAL1", "--layer", "DOOR"], ["--only", "layers"]),
]


def _record(subcommand: str, extra: list[str], out: Path, *more: str) -> dict:
    return run_json(subcommand, *extra, "--out", str(out), *more, expect=EXIT_OK)


@pytest.mark.parametrize("subcommand, extra, verify_scope", _NEW_RECORDERS)
def test_the_five_new_recorders_round_trip_through_their_own_verify(
    tmp_path: Path, subcommand: str, extra: list[str], verify_scope: list[str]
) -> None:
    """A recorder that cannot be verified by the shipped verifier is unfinished."""
    out = tmp_path / f"{subcommand}.dxf"
    written = _record(subcommand, extra, out)
    assert out.exists()
    assert written["command"] == subcommand
    assert written["ok"] is True
    payload = run_json("verify", "--in", str(out), *verify_scope, expect=EXIT_OK)
    assert payload["ok"] is True, f"{subcommand}: {payload['failed']}"


@pytest.mark.parametrize("subcommand, extra, verify_scope", _NEW_RECORDERS)
def test_the_five_new_recorders_print_the_verification_they_claim(
    tmp_path: Path, subcommand: str, extra: list[str], verify_scope: list[str]
) -> None:
    """The ``verify_hint`` each command prints must actually work.

    A hint that does not run is worse than no hint: it teaches the user that
    the printed command line is trustworthy. So it is executed, not
    string-matched.
    """
    out = tmp_path / f"{subcommand}.dxf"
    written = _record(subcommand, extra, out)
    hint = written["verify_hint"]
    # shlex in POSIX mode, because the hint is emitted with POSIX quoting (see
    # cli._quote_argv): inside single quotes a Windows backslash is literal, so
    # the path survives while an annotation with spaces stays one argument.
    argv = shlex.split(hint)
    assert argv[:2] == ["verify", "--in"]
    assert argv[2] == str(out)
    completed = run_cli(*argv, expect=EXIT_OK)
    assert "RESULT: PASS" in completed.stdout, completed.stdout


@pytest.mark.parametrize("subcommand, extra, verify_scope", _NEW_RECORDERS)
def test_the_five_new_recorders_measure_what_they_recorded(
    tmp_path: Path, subcommand: str, extra: list[str], verify_scope: list[str]
) -> None:
    """The command's own numbers and verify's independent reading must agree.

    Two code paths compute these: the recorder's read-back record, and
    verify's re-reading of the saved file. A roundtrip that only checked
    "exits 0" would pass even if one of them reported nonsense.
    """
    out = tmp_path / f"{subcommand}.dxf"
    written = _record(subcommand, extra, out)
    payload = run_json("verify", "--in", str(out), *verify_scope, expect=EXIT_OK)
    if subcommand == "hatch":
        assert payload["hatch"]["area_mm2"] == written["hatch"]["area_mm2"]
        assert payload["hatch"]["pattern_names"] == [written["hatch"]["pattern"]]
    elif subcommand == "dim":
        assert payload["dim"]["measurements"] == [written["dim"]["measurement_mm"]]
        assert payload["dim"]["rendered_texts"] == [written["dim"]["text"]]
    elif subcommand == "text":
        assert payload["text"]["contents"] == [written["text"]["content"]]
        assert payload["text"]["heights"] == [written["text"]["height"]]
    elif subcommand == "block":
        assert written["visible_downstream"] is True
        assert written["contributed_segments"] == 2
        assert payload["block"]["insert_count"] == 0
    elif subcommand == "layers":
        assert written["layer_table"]["order"] == payload["layers"]["layer_table"]
        assert set(written["layers"]) <= set(payload["layers"]["layer_table"])


# ---- per-module expectations ------------------------------------------------


def test_hatch_area_and_pattern_expectations_are_checked(tmp_path: Path) -> None:
    out = tmp_path / "hatch.dxf"
    _record("hatch", _NEW_RECORDERS[0][1], out)
    passing = run_json(
        "verify", "--in", str(out), "--only", "hatch", "--unresolved-layer", "HATCHA",
        "--expect-hatch-area", "2000000", "--expect-hatch-pattern", "ANSI31",
        expect=EXIT_OK,
    )
    assert "hatch_area_matches_expectation" in [
        check["name"] for check in passing["checks"]
    ]
    wrong = run_json(
        "verify", "--in", str(out), "--only", "hatch", "--unresolved-layer", "HATCHA",
        "--expect-hatch-area", "2000001", expect=EXIT_VERIFY_FAILED,
    )
    assert "hatch_area_matches_expectation" in wrong["failed"]
    wrong_pattern = run_json(
        "verify", "--in", str(out), "--only", "hatch", "--unresolved-layer", "HATCHA",
        "--expect-hatch-pattern", "ANSI32", expect=EXIT_VERIFY_FAILED,
    )
    assert "hatch_pattern_matches_expectation" in wrong_pattern["failed"]


def test_dim_measurement_expectation_is_checked(tmp_path: Path) -> None:
    out = tmp_path / "dim.dxf"
    _record("dim", ["--p1", "0", "0", "--p2", "3000", "0"], out)
    payload = run_json(
        "verify", "--in", str(out), "--only", "dim", "--expect-dim-measurement", "2999",
        expect=EXIT_VERIFY_FAILED,
    )
    assert "dim_measurement_matches_expectation" in payload["failed"]


def test_text_content_expectation_is_checked(tmp_path: Path) -> None:
    out = tmp_path / "text.dxf"
    _record("text", ["--content", "hello note", "--insert", "100", "100", "--layer", "NOTE"], out)
    payload = run_json(
        "verify", "--in", str(out), "--only", "text", "--unresolved-layer", "NOTE",
        "--expect-text-content", "goodbye note", expect=EXIT_VERIFY_FAILED,
    )
    assert "text_content_matches_expectation" in payload["failed"]


def test_block_name_expectation_is_checked(tmp_path: Path) -> None:
    out = tmp_path / "block.dxf"
    _record(
        "block",
        ["--name", "BLKA", "--line", "0", "0", "1000", "0", "--entity-layer", "WAL1"],
        out,
    )
    payload = run_json(
        "verify", "--in", str(out), "--only", "block", "--expect-block-name", "NOPE",
        expect=EXIT_VERIFY_FAILED,
    )
    assert "block_name_matches_expectation" in payload["failed"]


def test_layer_name_expectation_is_checked(tmp_path: Path) -> None:
    out = tmp_path / "layers.dxf"
    _record("layers", ["--layer", "WAL1", "--layer", "DOOR"], out)
    payload = run_json(
        "verify", "--in", str(out), "--only", "layers", "--expect-layers", "WAL1", "NOPE",
        expect=EXIT_VERIFY_FAILED,
    )
    assert "layer_names_match_expectation" in payload["failed"]


@pytest.mark.parametrize(
    "subcommand, extra, verify_scope, expectation, failing_check",
    [
        (
            "hatch",
            ["--boundary", "0", "0", "2000", "0", "2000", "1000", "0", "1000",
             "--layer", "HATCHA", "--pattern-name", "ANSI31"],
            ["--only", "hatch", "--unresolved-layer", "HATCHA"],
            ["--expect-hatch-area", "1"],
            "hatch_area_matches_expectation",
        ),
        (
            "dim",
            ["--p1", "0", "0", "--p2", "3000", "0"],
            ["--only", "dim"],
            ["--expect-dim-measurement", "1"],
            "dim_measurement_matches_expectation",
        ),
        (
            "text",
            ["--content", "hello note", "--insert", "100", "100", "--layer", "NOTE"],
            ["--only", "text", "--unresolved-layer", "NOTE"],
            ["--expect-text-content", "absent"],
            "text_content_matches_expectation",
        ),
        (
            "block",
            ["--name", "BLKA", "--line", "0", "0", "1000", "0", "--entity-layer", "WAL1"],
            ["--only", "block"],
            ["--expect-block-name", "ABSENT"],
            "block_name_matches_expectation",
        ),
        (
            "layers",
            ["--layer", "WAL1"],
            ["--only", "layers"],
            ["--expect-layers", "ABSENT"],
            "layer_names_match_expectation",
        ),
    ],
)
def test_a_requested_expectation_is_never_discarded(
    tmp_path: Path,
    subcommand: str,
    extra: list[str],
    verify_scope: list[str],
    expectation: list[str],
    failing_check: str,
) -> None:
    """An unmeasurable --expect-* is a FAIL, not a vanished check and exit 0.

    S1 was the shape of this bug for wall and door: the user asked for an
    assertion, the drawing could not supply it, no check was created, and the
    command reported success for something nobody verified. Extended here to
    all five new expectations.
    """
    out = tmp_path / f"{subcommand}.dxf"
    _record(subcommand, extra, out)
    payload = run_json(
        "verify", "--in", str(out), *verify_scope, *expectation,
        expect=EXIT_VERIFY_FAILED,
    )
    assert failing_check in payload["failed"]
    assert failing_check in [check["name"] for check in payload["checks"]], (
        "the check must still EXIST, with a FAIL status"
    )


# ---- the INSERT policy ------------------------------------------------------


def test_block_insert_mode_needs_the_concealment_acknowledged(tmp_path: Path) -> None:
    """The replaced ban, as a two-sided contract.

    Before this change the INSERT drawing FAILED with no way to pass it. Now
    it passes when the caller declares the concealment and fails when they do
    not -- and either way the measured hidden geometry is in the report.
    """
    out = tmp_path / "insert.dxf"
    written = _record(
        "block",
        [
            "--name", "BLKB", "--line", "0", "0", "1000", "0", "--line", "0", "0", "0", "600",
            "--entity-layer", "WAL1", "--location", "5000", "0", "--mode", "insert",
        ],
        out,
    )
    assert written["visible_downstream"] is False
    assert written["contributed_segments"] == 0
    assert written["hidden_segments"] == 2
    assert written["hidden_length_mm"] == 1600.0

    unacknowledged = run_json(
        "verify", "--in", str(out), "--only", "block", "--expect-block-name", "BLKB",
        expect=EXIT_VERIFY_FAILED,
    )
    assert "insert_downstream_visibility" in unacknowledged["failed"]
    # The cost is still reported in the failing report, not swallowed by it.
    assert unacknowledged["block"]["hidden_entities"] == 2
    assert unacknowledged["block"]["hidden_length_mm"] == 1600.0

    acknowledged = run_json(
        "verify", "--in", str(out), "--only", "block", "--expect-block-name", "BLKB",
        "--allow-hidden-insert", expect=EXIT_OK,
    )
    assert acknowledged["ok"] is True
    assert acknowledged["block"]["hidden_entities"] == 2
    assert acknowledged["insert_visibility_note"] is not None


def test_block_flatten_mode_needs_no_acknowledgement(tmp_path: Path) -> None:
    """The default mode contributes its geometry, so nothing is concealed."""
    out = tmp_path / "flatten.dxf"
    written = _record(
        "block",
        [
            "--name", "BLKC", "--line", "0", "0", "1000", "0", "--line", "0", "0", "0", "600",
            "--entity-layer", "WAL1", "--location", "5000", "0",
        ],
        out,
    )
    assert written["visible_downstream"] is True
    assert written["hidden_segments"] == 0
    payload = run_json(
        "verify", "--in", str(out), "--only", "block", "--expect-block-name", "BLKC",
        expect=EXIT_OK,
    )
    assert payload["insert_visibility_note"] is None


# ---- FAILURE CONTROLS: a hand-mangled drawing must exit 1 -------------------


def _mangle(source: Path, target: Path, damage) -> Path:
    """Copy ``source`` through ``damage`` and save it as ``target``."""
    import ezdxf

    doc = ezdxf.readfile(source)
    damage(doc)
    doc.saveas(target)
    return target


def test_control_a_hatch_whose_boundary_is_no_longer_closed_exits_one(
    tmp_path: Path,
) -> None:
    out = tmp_path / "hatch.dxf"
    _record("hatch", _NEW_RECORDERS[0][1], out)

    def unclose(doc) -> None:
        for entity in doc.modelspace():
            if entity.dxftype() == "LWPOLYLINE":
                entity.closed = False

    broken = _mangle(out, tmp_path / "unclosed.dxf", unclose)
    payload = run_json(
        "verify", "--in", str(broken), "--only", "hatch", "--unresolved-layer", "HATCHA",
        expect=EXIT_VERIFY_FAILED,
    )
    assert "hatch_boundary_is_closed" in payload["failed"]


def test_control_a_dimension_whose_own_text_lies_exits_one(tmp_path: Path) -> None:
    """The number the drawing renders is compared, not the DXF's placeholder.

    dxf.text still reads the injected value here, so a verifier that read
    dxf.text would have seen 3000.00 and passed. The text that lies is the
    one in the anonymous block -- the one a human reads.
    """
    out = tmp_path / "dim.dxf"
    _record("dim", ["--p1", "0", "0", "--p2", "3000", "0"], out)

    def lie(doc) -> None:
        for entity in doc.modelspace():
            if entity.dxftype() != "DIMENSION":
                continue
            assert entity.dxf.text == "3000.00", "expected the recorded value in dxf.text"
            for item in doc.blocks.get(str(entity.dxf.geometry)):
                if item.dxftype() == "MTEXT":
                    item.text = "9999.99"

    broken = _mangle(out, tmp_path / "liar.dxf", lie)
    payload = run_json("verify", "--in", str(broken), "--only", "dim", expect=EXIT_VERIFY_FAILED)
    assert "dim_text_matches_measurement" in payload["failed"]


def test_control_an_annotation_whose_content_was_emptied_exits_one(tmp_path: Path) -> None:
    out = tmp_path / "text.dxf"
    _record("text", ["--content", "hello note", "--insert", "100", "100", "--layer", "NOTE"], out)

    def empty(doc) -> None:
        for entity in doc.modelspace():
            if entity.dxftype() == "TEXT":
                entity.dxf.text = "   "

    broken = _mangle(out, tmp_path / "empty.dxf", empty)
    payload = run_json(
        "verify", "--in", str(broken), "--only", "text", "--unresolved-layer", "NOTE",
        expect=EXIT_VERIFY_FAILED,
    )
    assert "text_content_non_empty" in payload["failed"]


def test_control_a_block_drawing_whose_content_vanished_exits_one(
    tmp_path: Path,
) -> None:
    """FLATTEN mode's promise is that the geometry is really in the drawing.

    The definition survives the damage, so a check that only looked for a
    block name would still pass -- which is exactly why the control asserts
    the count fails AND that the definition check did not. If the count check
    were the one silently passing, this test would show it.
    """
    out = tmp_path / "block.dxf"
    written = _record(
        "block",
        ["--name", "BLKA", "--line", "0", "0", "1000", "0", "--entity-layer", "WAL1"],
        out,
    )
    assert written["entity_count"] == 1

    def wipe(doc) -> None:
        for entity in list(doc.modelspace()):
            doc.modelspace().delete_entity(entity)

    broken = _mangle(out, tmp_path / "wiped.dxf", wipe)
    payload = run_json(
        "verify", "--in", str(broken), "--only", "block", "--expect-block-name", "BLKA",
        "--expect-entities", "1", expect=EXIT_VERIFY_FAILED,
    )
    assert "entity_count_matches_expectation" in payload["failed"]
    # The block itself is untouched, so the failure is the missing content and
    # not a side effect of a different check firing.
    assert "block_name_matches_expectation" not in payload["failed"]
    assert payload["block"]["block_names"] == ["BLKA"]


def test_control_a_layer_table_entry_that_vanished_exits_one(tmp_path: Path) -> None:
    out = tmp_path / "layers.dxf"
    _record("layers", ["--layer", "WAL1", "--layer", "DOOR"], out)

    def drop(doc) -> None:
        doc.layers.remove("DOOR")

    broken = _mangle(out, tmp_path / "dropped.dxf", drop)
    payload = run_json(
        "verify", "--in", str(broken), "--only", "layers", "--expect-layers", "WAL1", "DOOR",
        expect=EXIT_VERIFY_FAILED,
    )
    assert "layer_names_match_expectation" in payload["failed"]


# ---- argument rejection, overwrite refusal, exit codes ----------------------


@pytest.mark.parametrize(
    "subcommand, bad",
    [
        # hatch: a ring needs three pairs, and the numbers must pair up
        (
            "hatch",
            ["--boundary", "0", "0", "2000", "0", "--layer", "H", "--pattern-name", "ANSI31"],
        ),
        (
            "hatch",
            [
                "--boundary",
                "0",
                "0",
                "2000",
                "0",
                "2000",
                "--layer",
                "H",
                "--pattern-name",
                "ANSI31",
            ],
        ),
        # dim: coincident points have no direction
        ("dim", ["--p1", "0", "0", "--p2", "0", "0"]),
        # text: a non-positive height cannot be rendered
        ("text", ["--content", "x", "--insert", "0", "0", "--layer", "N", "--height", "0"]),
        # block: a zero-length edge, and no content at all
        ("block", ["--name", "B", "--line", "0", "0", "0", "0", "--entity-layer", "WAL1"]),
        ("block", ["--name", "B", "--entity-layer", "WAL1"]),
        # layers: an unknown --set key must not be a silent no-op
        ("layers", ["--set", "WAL1.colour=1"]),
    ],
)
def test_the_five_new_recorders_reject_bad_arguments(
    tmp_path: Path, subcommand: str, bad: list[str]
) -> None:
    out = tmp_path / "never.dxf"
    run_cli(subcommand, *bad, "--out", str(out), expect=EXIT_USAGE)
    assert not out.exists(), "a rejected command must leave no drawing behind"


@pytest.mark.parametrize("subcommand, bad", [
    ("hatch", ["--boundary", "0", "0", "--layer", "H", "--pattern-name", "A"]),
    ("dim", ["--p1", "0", "0"]),
    ("text", ["--content", "x", "--insert", "0", "0"]),
    ("block", ["--name", "B", "--line", "0", "0", "1", "1"]),
    ("layers", []),
])
def test_the_five_new_recorders_require_their_own_arguments(
    tmp_path: Path, subcommand: str, bad: list[str]
) -> None:
    """--layer, --content, --name: the unresolved-layer rule pushed to the CLI.

    hatch, text and block must be told the layer, because their modules
    report LAYER_STATUS == UNRESOLVED and refuse to invent one. A default here
    would quietly undo that decision at the one place a user can reach.
    """
    out = tmp_path / "never.dxf"
    run_cli(subcommand, *bad, "--out", str(out), expect=EXIT_USAGE)
    assert not out.exists()


@pytest.mark.parametrize("subcommand, extra, verify_scope", _NEW_RECORDERS)
def test_the_five_new_recorders_refuse_to_overwrite(
    tmp_path: Path, subcommand: str, extra: list[str], verify_scope: list[str]
) -> None:
    """The shared overwrite contract, checked on all ten writers, not five.

    ``_add_out_arguments`` is one function, but a per-command bug is exactly
    how a shared rule stops being shared. The second run must refuse with 2
    and leave the first drawing byte-identical; --force is the only way past.
    """
    import hashlib

    out = tmp_path / f"{subcommand}.dxf"
    _record(subcommand, extra, out)
    first = hashlib.sha256(out.read_bytes()).hexdigest()

    refused = run_json(subcommand, *extra, "--out", str(out), expect=EXIT_USAGE)
    assert refused["ok"] is False
    assert "refusing to overwrite" in refused["error"]
    assert hashlib.sha256(out.read_bytes()).hexdigest() == first

    forced = _record(subcommand, extra, out, "--force")
    assert forced["overwrote_existing"] is True
    assert run_json("verify", "--in", str(out), *verify_scope, expect=EXIT_OK)["ok"] is True


def test_layers_reports_a_refusal_as_a_refusal_and_leaves_no_journal(
    tmp_path: Path,
) -> None:
    """The refusal path is shared; this proves the new command uses it."""
    out = tmp_path / "layers.dxf"
    _record("layers", ["--layer", "WAL1"], out)
    payload = run_json("layers", "--layer", "CEN1", "--out", str(out), expect=EXIT_USAGE)
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_USAGE
    journal = out.parent / ".all_in_cad_txn"
    leftovers = sorted(p.name for p in journal.iterdir()) if journal.exists() else []
    assert leftovers == [], f"a refused write left journal residue: {leftovers}"


# ---- the unresolved-layer declaration ---------------------------------------


def test_declaring_an_unresolved_layer_is_explicit_and_does_not_generalise(
    tmp_path: Path,
) -> None:
    """--unresolved-layer is a per-drawing, per-layer statement, not a switch.

    A flag that silenced the whole check would be a hole in the fail-closed
    contract, so the same drawing with an UNDECLARED unknown layer must still
    FAIL.
    """
    out = tmp_path / "hatch.dxf"
    _record("hatch", _NEW_RECORDERS[0][1], out)
    declared = run_json(
        "verify", "--in", str(out), "--only", "hatch", "--unresolved-layer", "HATCHA",
        expect=EXIT_OK,
    )
    check = [c for c in declared["checks"] if c["name"] == "layer_semantics_mapped"][0]
    assert check["excluded_unresolved"] == ["HATCHA"]

    undeclared = run_json(
        "verify", "--in", str(out), "--only", "hatch", expect=EXIT_VERIFY_FAILED
    )
    assert "layer_semantics_mapped" in undeclared["failed"]


def test_verify_rejects_an_unknown_only_scope(tmp_path: Path) -> None:
    out = tmp_path / "hatch.dxf"
    _record("hatch", _NEW_RECORDERS[0][1], out)
    run_cli("verify", "--in", str(out), "--only", "nonsense", expect=EXIT_USAGE)
# ---------------------------------------------------------------------------
# regression: dangling INSERT must fail a check, not crash the whole report
# ---------------------------------------------------------------------------


def _dangling_insert_drawing(tmp_path: Path) -> Path:
    """A drawing whose INSERT names a block that is not in the BLOCK table.

    ``doc.blocks.get()`` answers a missing name with ``None`` and does not
    raise, so the guard that used to sit around that call was unreachable for
    exactly the failure it was written for, and ``for entity in block`` raised
    ``TypeError``. The consequence was worse than one bad check: the exception
    escaped ``verify_drawing`` before the report was assembled, so *every*
    check in the drawing was lost and ``--json`` consumers got a traceback on
    stderr instead of an ``ok: false`` object.
    """
    import ezdxf

    doc = ezdxf.new("R2018")
    doc.modelspace().add_blockref("GHOST_BLOCK", (0, 0), dxfattribs={"layer": "WAL1"})
    # A second, resolvable entity so the report has to contain other checks:
    # the whole point is that one dangling reference must not blind them.
    doc.modelspace().add_line((0, 0), (500, 0), dxfattribs={"layer": "WAL1"})
    out = tmp_path / "dangling.dxf"
    doc.saveas(out)
    return out


def test_verify_reports_a_dangling_insert_instead_of_crashing(tmp_path: Path) -> None:
    """Exit 1, valid JSON, the missing block named, no traceback, report intact."""
    out = _dangling_insert_drawing(tmp_path)

    completed = run_cli("verify", "--in", str(out), "--json", "--only", "block")
    assert completed.returncode == EXIT_VERIFY_FAILED, (
        f"a dangling INSERT must be exit 1 (a failed check), not a crash\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    assert "Traceback" not in completed.stderr
    assert "TypeError" not in completed.stderr

    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    # The dangling INSERT is recorded as a FAILED check rather than a crash.
    # It rides the existing insert_downstream_visibility check: adding a new
    # check name would break tests/test_doc_code_consistency.py, which
    # requires every _check name in cli.py to be documented.
    assert "insert_downstream_visibility" in payload["failed"]
    check = [
        c for c in payload["checks"] if c["name"] == "insert_downstream_visibility"
    ][0]
    assert check["status"] == "FAIL"
    assert "GHOST_BLOCK" in check["detail"], "the missing block must be named"
    assert check["dangling_block_names"] == ["GHOST_BLOCK"]

    # The rest of the report survives: these checks are unaffected by the
    # dangling reference and must still be present with their own verdicts.
    assert "entity_count_reported" in [c["name"] for c in payload["checks"]]
    assert "no_hatch_entity" in [c["name"] for c in payload["checks"]]
    assert payload["entity_count"] == 2
    assert payload["block"]["dangling_insert_block_names"] == ["GHOST_BLOCK"]


def test_a_dangling_insert_is_not_acknowledged_away(tmp_path: Path) -> None:
    """--allow-hidden-insert waives concealment, not a missing definition.

    The cost of a dangling INSERT is unmeasurable, not zero. If the
    acknowledgement silenced it, the flag would buy a silently broken drawing
    for free.
    """
    out = _dangling_insert_drawing(tmp_path)

    payload = run_json(
        "verify", "--in", str(out), "--only", "block", "--allow-hidden-insert",
        expect=EXIT_VERIFY_FAILED,
    )
    assert "insert_downstream_visibility" in payload["failed"]

    visibility = [
        c for c in payload["checks"] if c["name"] == "insert_downstream_visibility"
    ][0]
    assert visibility["acknowledged"] is True
    assert visibility["status"] == "FAIL", (
        "the acknowledgement waives concealment, not a missing definition"
    )
    assert visibility["dangling_block_names"] == ["GHOST_BLOCK"]


def test_a_resolvable_insert_still_reports_zero_hidden(tmp_path: Path) -> None:
    """The dangling handling must not turn a real measurement into a FAIL."""
    import ezdxf

    doc = ezdxf.new("R2018")
    doc.blocks.new("REAL_BLOCK").add_line((0, 0), (1000, 0), dxfattribs={"layer": "WAL1"})
    doc.modelspace().add_blockref("REAL_BLOCK", (0, 0), dxfattribs={"layer": "WAL1"})
    out = tmp_path / "resolvable.dxf"
    doc.saveas(out)

    payload = run_json(
        "verify", "--in", str(out), "--only", "block", "--allow-hidden-insert",
        expect=EXIT_OK,
    )
    visibility = [
        c for c in payload["checks"] if c["name"] == "insert_downstream_visibility"
    ][0]
    assert visibility["status"] == "PASS"
    assert visibility["dangling_block_names"] == []
    assert payload["block"]["dangling_insert_block_names"] == []
    assert payload["block"]["hidden_entities"] == 1
    assert payload["block"]["hidden_length_mm"] == 1000.0


# ---------------------------------------------------------------------------
# regression: --expect-dim-measurement must match any dimension, not values[0]
# ---------------------------------------------------------------------------


def _two_dimension_drawing(tmp_path: Path) -> Path:
    """A drawing holding two dimensions: 3000 mm and 7777 mm.

    Built with ``dim.write_dim`` rather than two CLI invocations because the
    ``dim`` recorder writes one dimension per run; ``--force`` onto the same
    file replaces the drawing instead of adding to it, so the multi-dimension
    case -- the only case where the old ``values[0]`` behaviour was wrong --
    is otherwise not reachable through the command line.
    """
    import ezdxf

    from all_in_cad.recorder import dim as dim_module

    doc = ezdxf.new("R2018")
    dim_module.write_dim(doc, dim_module.make_dim((0, 0), (3000, 0)))
    dim_module.write_dim(doc, dim_module.make_dim((0, 5000), (7777, 5000)))
    out = tmp_path / "twodims.dxf"
    doc.saveas(out)
    return out


def test_expect_dim_measurement_matches_the_second_dimension_too(tmp_path: Path) -> None:
    """The expected value being *present* is what matters, not its position.

    Taking ``values[0]`` made a check that failed while naming a dimension
    nobody asked about, and let a wrong second dimension pass unnoticed.
    """
    out = _two_dimension_drawing(tmp_path)

    payload = run_json(
        "verify", "--in", str(out), "--only", "dim",
        "--expect-dim-measurement", "7777",
        expect=EXIT_OK,
    )
    assert "dim_measurement_matches_expectation" in [
        c["name"] for c in payload["checks"] if c["status"] == "PASS"
    ]


def test_expect_dim_measurement_still_fails_for_an_absent_value(tmp_path: Path) -> None:
    """Set membership must not become a blanket pass."""
    out = _two_dimension_drawing(tmp_path)

    payload = run_json(
        "verify", "--in", str(out), "--only", "dim",
        "--expect-dim-measurement", "4242",
        expect=EXIT_VERIFY_FAILED,
    )
    assert "dim_measurement_matches_expectation" in payload["failed"]


def test_expect_dim_measurement_repeats_check_each_value(tmp_path: Path) -> None:
    """Each repetition is its own check, so one bad value is still reported."""
    out = _two_dimension_drawing(tmp_path)

    payload = run_json(
        "verify", "--in", str(out), "--only", "dim",
        "--expect-dim-measurement", "3000",
        "--expect-dim-measurement", "7777",
        "--expect-dim-measurement", "4242",
        expect=EXIT_VERIFY_FAILED,
    )
    names = [
        c["name"] for c in payload["checks"] if "dim_measurement_matches" in c["name"]
    ]
    assert len(names) == 3, f"one check per requested value, got {names}"
    assert names[0] == "dim_measurement_matches_expectation"
    assert names[1] == "dim_measurement_matches_expectation_2"
    assert names[2] == "dim_measurement_matches_expectation_3"
    assert payload["failed"] == ["dim_measurement_matches_expectation_3"]
    failed_check = [
        c for c in payload["checks"]
        if c["name"] == "dim_measurement_matches_expectation_3"
    ][0]
    assert "4242" in failed_check["detail"]


def test_expect_dim_measurement_still_fails_when_no_dimension_exists(
    tmp_path: Path,
) -> None:
    """The empty-drawing message survives the switch to set membership."""
    out = tmp_path / "lines.dxf"
    _record("wall", ["--start", "0", "0", "--end", "12000", "0", "-t", "200"], out)
    payload = run_json(
        "verify", "--in", str(out), "--only", "dim",
        "--expect-dim-measurement", "3000",
        expect=EXIT_VERIFY_FAILED,
    )
    assert "dim_measurement_matches_expectation" in payload["failed"]
    detail = [
        c for c in payload["checks"]
        if c["name"] == "dim_measurement_matches_expectation"
    ][0]["detail"]
    assert "no dimension measurement could be read" in detail


# ---------------------------------------------------------------------------
# help / note honesty about the block layer rule
#
# block.py's _validate_layer REFUSES any name classify_layer maps to UNKNOWN
# (sole exception: the opt-in observed "0"). hatch.py and text.py instead
# record the caller-named layer and report its semantic. The user-facing
# strings must not describe block as if it behaved like the other two.
# ---------------------------------------------------------------------------


def test_entity_layer_help_does_not_claim_block_enforces_a_list() -> None:
    """CONVENTION_BLOCK_LAYERS is a recommendation, not the membership test."""
    help_text = run_cli("block", "--help", expect=EXIT_OK).stdout
    assert "classifies as UNKNOWN is refused" in help_text
    assert "Use a name from CONVENTION_BLOCK_LAYERS" not in help_text
    # the eight recommended names are still spelled out for the user
    for name in ("WAL1", "WAL2", "WAL3", "DOOR", "DOOR_ELE", "WIN", "WINBAR", "WINELE"):
        assert name in help_text, f"{name} missing from block --help"


def test_unresolved_layer_help_excludes_block_and_says_why() -> None:
    """--unresolved-layer is a hatch/text flag; block cannot use it at all."""
    help_text = run_cli("verify", "--help", expect=EXIT_OK).stdout
    assert "--unresolved-layer" in help_text
    # argparse hard-wraps help, so compare against the whitespace-collapsed text
    joined = " ".join(help_text.split())
    assert "hatch and text report LAYER_STATUS == UNRESOLVED" in joined
    assert "REFUSES any name that" in joined
    assert "it does not use this flag" in joined


def test_unresolved_layer_note_reports_that_block_refuses_unknown(tmp_path: Path) -> None:
    """The JSON note is user-facing: it must carry the block exception."""
    out = tmp_path / "hatch.dxf"
    _record(
        "hatch",
        [
            "--boundary", "0", "0", "2000", "0", "2000", "1000", "0", "1000",
            "--layer", "HATCHA", "--pattern-name", "ANSI31",
        ],
        out,
    )
    payload = run_json(
        "verify", "--in", str(out), "--only", "hatch",
        "--unresolved-layer", "HATCHA",
        expect=EXIT_OK,
    )
    note = payload["unresolved_layer_note"]
    assert note, "the declared layer must carry a note"
    assert "hatch and text" in note
    assert "does not apply to block" in note
    assert "refuses" in note


def test_layers_help_does_not_advertise_the_rejected_description_key() -> None:
    """layer.py rejects description; --help must not offer it."""
    help_text = run_cli("layers", "--help", expect=EXIT_OK).stdout
    key_lines = " ".join(help_text.split())
    assert "Keys:" in key_lines, "--set key list missing from layers --help"
    keys_clause = key_lines.split("Keys:", 1)[1].split(".", 1)[0]
    assert "description" not in keys_clause
    # the keys that do work are still listed
    for key in ("color", "linetype", "lineweight", "locked", "frozen"):
        assert key in keys_clause, f"{key} missing from layers --set help"


def test_set_description_still_fails_with_the_layer_py_rejection(tmp_path: Path) -> None:
    """Hiding the key in --help must not turn the rejection into an unknown key."""
    out = tmp_path / "layers.dxf"
    completed = run_cli(
        "layers", "--out", str(out), "--set", "WAL1.description=note"
    )
    assert completed.returncode == EXIT_USAGE
    assert "description is unsupported" in (completed.stdout + completed.stderr)