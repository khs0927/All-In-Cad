"""Contract tests for the CI coverage gate.

The gate exists to stop a quiet green. That property is worth exactly nothing
if the gate itself can be quietly weakened, so the failure modes it is meant
to catch are reproduced here against synthetic reports, and a regression in
the gate fails this file.

These run in milliseconds and need no CAD, so they can be a required check.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

GATE_PATH = Path(__file__).resolve().parents[1] / "ci" / "recorder_ci_gate.py"
spec = importlib.util.spec_from_file_location("recorder_ci_gate", GATE_PATH)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
sys.modules["recorder_ci_gate"] = gate
spec.loader.exec_module(gate)


def report(tmp_path: Path, total: int, failed: int = 0, skipped=(), name="r.xml") -> Path:
    cases = []
    for i in range(total - len(skipped) - failed):
        cases.append(f'<testcase classname="mod{i}.x_test" name="test_ok_{i}"/>')
    for i in range(failed):
        cases.append(
            f'<testcase classname="mod.y_test" name="test_bad_{i}">'
            f"<failure message=\"boom\">tb</failure></testcase>"
        )
    for label in skipped:
        cases.append(
            f'<testcase classname="{label.split("::")[0]}" name="{label.split("::")[1]}">'
            f'<skipped message="no environment" type="pytest.skip"/></testcase>'
        )
    path = tmp_path / name
    path.write_text(
        f'<testsuites name="pytest tests"><testsuite name="pytest tests" tests="{total}" '
        f'failures="{failed}" errors="0" skipped="{len(skipped)}">'
        + "".join(cases)
        + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def multi_suite_report(tmp_path: Path, suites, name="m.xml") -> Path:
    """Build a report made of several <testsuite> blocks, each (header, cases).

    pytest --junitxml emits one suite, so this shape is defence-in-depth: it
    reproduces a report that was split, or concatenated by another tool.
    """
    body = "".join(
        f'<testsuite name="s{i}" tests="{header["tests"]}" failures="{header["failures"]}" '
        f'errors="0" skipped="{header["skipped"]}">{"".join(cases)}</testsuite>'
        for i, (header, cases) in enumerate(suites)
    )
    path = tmp_path / name
    path.write_text(f'<testsuites name="pytest tests">{body}</testsuites>', encoding="utf-8")
    return path


def passing_cases(count: int, start: int = 0) -> list[str]:
    return [
        f'<testcase classname="mod{i}.x_test" name="test_ok_{i}"/>'
        for i in range(start, start + count)
    ]


def failing_case(name: str = "test_bad") -> str:
    return (
        f'<testcase classname="mod.y_test" name="{name}">'
        f'<failure message="boom">tb</failure></testcase>'
    )


def skipped_case(label: str) -> str:
    return (
        f'<testcase classname="{label.split("::")[0]}" name="{label.split("::")[1]}">'
        f'<skipped message="no environment" type="pytest.skip"/></testcase>'
    )


def passing_case_for(label: str) -> str:
    """An allowlisted id rendered as a collected testcase that did NOT skip."""
    module, name = label.split("::")
    return f'<testcase classname="{module}" name="{name}"/>'


#: The one numeric line of the committed baseline file, read at import time.
#: The synthetic reports below are sized to sit exactly on the floor, so they
#: have to track that file rather than restate a number that goes stale the
#: next time anyone adds a test.
FLOOR = int(
    [
        line.strip()
        for line in (GATE_PATH.parent / "recorder-collection-baseline.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.strip().startswith("#")
    ][0]
)


def runner_report(tmp_path: Path, total: int = FLOOR, name: str = "clean.xml") -> Path:
    """A report shaped like a real GitHub-hosted runner's.

    The four FreeCAD-gated tests are always *collected* on a runner without
    FreeCAD -- that is precisely why they skip. A synthetic report that simply
    omits them would describe a suite where those tests no longer exist, which
    the gate now (correctly) refuses to accept, so the healthy fixture has to
    contain them as skips rather than pretend they are absent.
    """
    return report(tmp_path, total, skipped=known_as_rendered(), name=name)


def known_as_rendered() -> list[str]:
    """The allowlist exactly as a junit report renders it."""
    return list(gate.KNOWN_ENV_SKIPS)


def test_a_clean_report_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    monkeypatch.delenv("FREECAD_EXE", raising=False)
    monkeypatch.delenv("FREECAD_COMMAND", raising=False)
    path = runner_report(tmp_path)
    assert gate.main([str(path)]) == 0


def test_the_declared_baseline_is_not_a_magic_number(tmp_path, monkeypatch):
    """The floor comes from a recorded file, and the number in it is the real one.

    Read the file back, so "the gate parses what the file says" is asserted
    rather than assumed, and then collect the suite and compare. A baseline
    that is merely self-consistent is exactly how a stale 821 outlived the
    suite it described: nothing in the old test could notice that the file and
    the suite had drifted apart.
    """
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    floor = gate.baseline_floor()
    assert floor == FLOOR, (
        f"the gate read a floor of {floor} from the baseline file, but the "
        f"file's single numeric line says {FLOOR}"
    )
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "src/all_in_cad/recorder", "--collect-only", "-q",
         "-o", "addopts="],
        cwd=GATE_PATH.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert collected.returncode == 0, (
        "collecting the recorder suite failed, so the baseline cannot be "
        f"checked against reality:\n{collected.stdout[-2000:]}"
    )
    match = re.search(r"(\d+) tests? collected", collected.stdout)
    assert match, f"no collection count in pytest output:\n{collected.stdout[-2000:]}"
    measured = int(match.group(1))
    assert FLOOR == measured, (
        f"baseline says {FLOOR} but the suite actually collects {measured}; "
        "regenerate it from a real report "
        "(python ci/recorder_ci_gate.py ci-reports/recorder-junit.xml --write-baseline) "
        "and record the command and timestamp in its header"
    )


def test_losing_half_the_suite_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    # What the old hardcoded floor of 400 would have called "fine": a report
    # far below the committed floor, whatever that floor currently says.
    path = report(tmp_path, FLOOR - 1)
    assert gate.main([str(path)]) == 1


def test_a_fifth_environment_skip_fails_even_under_the_count(tmp_path, monkeypatch):
    """The old gate only compared counts, so a new silent skip hid inside it."""
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    skips = known_as_rendered() + ["new_test.py::test_something_new"]
    path = report(tmp_path, FLOOR, skipped=skips)
    assert gate.main([str(path)]) == 1


def test_a_failing_test_fails_and_is_not_reported_as_a_skip(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    path = report(tmp_path, FLOOR, failed=1, skipped=known_as_rendered())
    assert gate.main([str(path)]) == 1


def test_lowering_the_floor_is_refused(tmp_path, monkeypatch):
    """A floor that can be lowered by an env var is not a floor."""
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "10")
    with pytest.raises(SystemExit) as exc:
        gate.baseline_floor()
    assert exc.value.code == 2


def test_raising_the_floor_still_applies(tmp_path, monkeypatch):
    raised = FLOOR + 50
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", str(raised))
    assert gate.baseline_floor() == raised
    path = report(tmp_path, FLOOR, skipped=known_as_rendered())
    assert gate.main([str(path)]) == 1


def test_a_missing_report_is_a_failure_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    assert gate.main([str(tmp_path / "nope.xml")]) == 1


def test_a_stale_allowlist_is_caught(tmp_path, monkeypatch):
    """If a known-gated test stops skipping, the allowlist must be corrected."""
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    path = report(tmp_path, FLOOR, skipped=known_as_rendered()[:2])
    assert gate.main([str(path)]) == 1


# --- the baseline itself is part of the gate -------------------------------


@pytest.mark.parametrize(
    "contents",
    [None, "", "# only a comment\n", "\n\n"],
    ids=["absent", "empty", "comments-only", "blank"],
)
def test_a_missing_or_valueless_baseline_is_a_config_error(
    tmp_path, monkeypatch, contents
):
    """A deleted or empty baseline used to mean "floor = 0", i.e. no floor.

    That is fail-open: a lane could collect one test and pass. It must be exit 2
    (the gate cannot be configured), never exit 0 and never a pass.
    """
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    monkeypatch.delenv("FREECAD_EXE", raising=False)
    monkeypatch.delenv("FREECAD_COMMAND", raising=False)
    baseline = tmp_path / "baseline.txt"
    if contents is not None:
        baseline.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(gate, "BASELINE", baseline)
    path = report(tmp_path, 1)
    with pytest.raises(SystemExit) as exc:
        gate.main([str(path)])
    assert exc.value.code == 2, "a missing floor must be a configuration error (2)"


def test_an_ambiguous_baseline_is_a_config_error(tmp_path, monkeypatch):
    """Two numeric lines: which one is the floor? Previously the first won.

    A stale `400` parked above the real floor silently halved it, so a suite
    that had lost half its tests passed. An ambiguous baseline is exit 2.
    """
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    baseline = tmp_path / "baseline.txt"
    baseline.write_text(f"# header\n400\n{FLOOR}\n", encoding="utf-8")
    monkeypatch.setattr(gate, "BASELINE", baseline)
    with pytest.raises(SystemExit) as exc:
        gate.baseline_floor()
    assert exc.value.code == 2


def test_a_non_numeric_baseline_is_a_config_error(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("about eight hundred\n", encoding="utf-8")
    monkeypatch.setattr(gate, "BASELINE", baseline)
    with pytest.raises(SystemExit) as exc:
        gate.baseline_floor()
    assert exc.value.code == 2


# --- totals are the report's, not the first header's -----------------------


def test_a_failure_in_a_later_suite_is_still_counted(tmp_path, monkeypatch):
    """A split report must not hide suite #2's failure behind suite #1's header.

    Suite #1's header alone already satisfies the floor and is clean, so
    the ONLY thing wrong with this report is the failing testcase in suite #2.
    Reading totals off suites[0] makes it pass.
    """
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    monkeypatch.delenv("FREECAD_EXE", raising=False)
    monkeypatch.delenv("FREECAD_COMMAND", raising=False)
    known = known_as_rendered()
    first_cases = passing_cases(FLOOR - len(known)) + [skipped_case(k) for k in known]
    path = multi_suite_report(
        tmp_path,
        [
            ({"tests": FLOOR, "failures": 0, "skipped": len(known)}, first_cases),
            ({"tests": 1, "failures": 1, "skipped": 0}, [failing_case()]),
        ],
    )
    assert gate.main([str(path)]) == 1, "a failure in the second suite must still fail"


def test_a_header_that_disagrees_with_its_testcases_is_rejected(tmp_path, monkeypatch):
    """Header totals are cross-checked against real <testcase> elements.

    The header claims exactly the floor while the report really
    holds 7 testcases. The four known environment skips are present and skipped,
    so the allowlist is clean and the floor is met: the header/testcase
    disagreement is the only defect, and trusting the header alone reports a
    healthy lane.
    """
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    monkeypatch.delenv("FREECAD_EXE", raising=False)
    monkeypatch.delenv("FREECAD_COMMAND", raising=False)
    known = known_as_rendered()
    cases = [skipped_case(k) for k in known] + passing_cases(3)
    path = tmp_path / "lying.xml"
    path.write_text(
        '<testsuites name="pytest tests">'
        f'<testsuite name="pytest tests" tests="{FLOOR}" failures="0" errors="0" '
        f'skipped="{len(known)}">'
        + "".join(cases)
        + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    assert gate.main([str(path)]) == 1


# --- allowlist staleness is checked on every run ---------------------------


def _zero_skip_report(tmp_path: Path, cases, name: str) -> Path:
    path = tmp_path / name
    path.write_text(
        '<testsuites name="pytest tests">'
        f'<testsuite name="pytest tests" tests="{FLOOR}" failures="0" errors="0" skipped="0">'
        + "".join(cases)
        + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def test_a_zero_skip_report_still_reports_a_stale_allowlist(tmp_path, monkeypatch):
    """skipped == 0 used to skip the staleness check entirely.

    Nothing skipped, so the four allowlisted ids were collected and passed:
    the environment gate is no longer gating anything. With no FreeCAD
    configured that is stale, and must fail.
    """
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    monkeypatch.delenv("FREECAD_EXE", raising=False)
    monkeypatch.delenv("FREECAD_COMMAND", raising=False)
    known = known_as_rendered()
    cases = passing_cases(FLOOR - len(known)) + [passing_case_for(k) for k in known]
    path = _zero_skip_report(tmp_path, cases, "zero.xml")
    assert gate.main([str(path)]) == 1


def test_a_zero_skip_report_is_fine_on_a_freecad_lane(tmp_path, monkeypatch):
    """The legitimate exception: FREECAD_EXE set means those tests really run.

    On such a lane the allowlisted ids are collected and do not skip, so
    `skipped == 0` is normal and the staleness check must not fire.
    """
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    monkeypatch.setenv("FREECAD_EXE", "/usr/bin/freecadcmd")
    known = known_as_rendered()
    cases = passing_cases(FLOOR - len(known)) + [passing_case_for(k) for k in known]
    path = _zero_skip_report(tmp_path, cases, "freecad.xml")
    assert gate.main([str(path)]) == 0


def test_an_allowlisted_id_that_vanished_fails_even_on_a_freecad_lane(tmp_path, monkeypatch):
    """"Ran" is forgivable on a FreeCAD lane; "no longer exists" is not.

    A renamed or deleted allowlisted id means the coverage hole it named is
    gone or silently replaced. No environment setting can explain a test id
    that is absent from the report entirely, so this fails unconditionally.
    """
    monkeypatch.setenv("RECORDER_MIN_COLLECTED", "")
    monkeypatch.setenv("FREECAD_EXE", "/usr/bin/freecadcmd")
    path = _zero_skip_report(tmp_path, passing_cases(FLOOR), "gone.xml")
    assert gate.main([str(path)]) == 1
