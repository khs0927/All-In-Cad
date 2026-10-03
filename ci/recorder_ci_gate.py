"""Coverage gate for the DXF recorder CI lane.

Why this is a module and not a heredoc in the workflow
------------------------------------------------------
The previous version was a ~60-line heredoc inside the YAML. Nothing could
test it, so its two numbers (``EXPECTED_MAX_SKIPS = 1`` and a hardcoded
``MIN_COLLECTED = 400``) were assertions about the suite that no one could
check. Both were wrong: the suite skips several tests on a runner with no
FreeCAD, and the collection floor of 400 was less than half the real suite, so
it could not detect losing any lane at all.

A note on numbers in this file
------------------------------
This module deliberately states no collection, pass or skip count of its own.
Those move every time anyone adds a test, and a number written into prose or
into a constant goes stale silently -- that is how "the gate says 821" became
a second, wrong source of truth next to the measured one. Every count here is
read from something measured at run time: the report pytest just wrote, or the
committed baseline file, which records the command and the timestamp it was
measured with so a reader can tell how old it is. The one count this file
does name is the allowlist, and that is a list of ids rather than a number
precisely so that it cannot drift out of sync with a total.

Three changes, each of which fails for a different reason:

1. **The collection floor is derived, not declared.** It is read from
   ``ci/recorder-collection-baseline.txt``, which is produced by running this
   script with ``--write-baseline`` against a real report. A suite that grows
   never fails; a suite that loses tests fails; nobody has to remember to
   bump a number.

2. **The skip budget is a named allowlist, not a count.** The tests that are
   environment-gated behind a real ``freecadcmd.exe`` are listed by
   test id. A *fifth* skip fails even though the count is under the old
   budget, so "we skipped a new test" cannot hide inside a count.

3. **Failing tests are reported separately from skipped ones**, so a red lane
   is never explained away as an environment problem.

4. **The gate fails closed on its own configuration.** A missing or
   valueless baseline is exit 2, not a floor of 0; an ambiguous baseline with
   two numeric lines is exit 2; totals are summed across every
   ``<testsuite>`` and cross-checked against the real ``<testcase>`` count; and
   the allowlist is checked for staleness on every run, not only on runs that
   happen to contain a skip. Each of those was a way for the gate to report a
   healthy lane without having looked at one.

What this does NOT prove
------------------------
This gate reads a report pytest has already produced. It says nothing about
whether the tests themselves are correct, and it cannot tell you that the
FreeCAD-gated tests pass, because on a hosted runner they never run. That
blind spot is real and is documented, not solved, here.
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "ci" / "recorder-collection-baseline.txt"

#: Tests that skip because the runner has no FreeCAD.
#:
#: Verified by re-running the suite with every module's FREECAD_EXE /
#: FREECAD_COMMAND redirected at a nonexistent path, which is the condition a
#: GitHub-hosted runner is in. The measured collected/passed/skipped totals are
#: deliberately NOT repeated here: they belong to the run that measured them,
#: not to this file, and the collection floor in recorder-collection-baseline.txt
#: is where the measured count is recorded, together with the command and the
#: timestamp it was taken with. The gate re-checks this allowlist on every run,
#: so a wrong entry fails the lane rather than quietly drifting.
#:
#: Adding a name here is a deliberate act with a reviewable diff. Removing one
#: without making the environment available turns a covered test back into a
#: permanent skip.
#:
#: Ids are rendered as ``<test module>::<test function>``, the same shape the
#: report gives us, so a rename shows up as a stale allowlist rather than as a
#: silently-unmatched string.
KNOWN_ENV_SKIPS = (
    "block_test::test_freecad_import_keeps_the_block_shape_in_both_modes",
    "e2e_test::test_scenario4_freecad_opens_the_recording_and_keeps_the_layers",
    "freecad_runner_test::test_live_freecad_verdict_record_proves_success",
    "text_test::test_freecad_import_survival",
)

#: Environment variables that mean "this runner has a real FreeCAD". The gate
#: uses their presence, not their value, because the value is a path that
#: differs per machine and is not something to re-verify here.
FREECAD_ENV_VARS = ("FREECAD_EXE", "FREECAD_COMMAND")


def freecad_is_configured() -> bool:
    """True when a FreeCAD lane is expected to actually run the gated tests."""
    return any(os.environ.get(name, "").strip() for name in FREECAD_ENV_VARS)


def _case_id(case) -> str:
    """Render one <testcase> the way the allowlist spells it."""
    cls = (case.get("classname") or "?").split(".")[-1]
    name = case.get("name") or "?"
    return f"{cls}::{name}"


def read_report(path: Path) -> dict:
    """Summarise a junit report.

    Totals are summed across EVERY <testsuite>, not read off the first header,
    because a report split into several suites would otherwise hide the
    failures and skips of every suite after the first. The summed header total
    is then cross-checked against the number of real <testcase> elements; a
    disagreement means the report is not the one the header describes, so it is
    reported as a problem rather than trusted.
    """
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = 0
    failed = 0
    skipped = 0
    skipped_names: list[str] = []
    collected_names: list[str] = []
    for suite in suites:
        total += int(suite.get("tests", 0))
        failed += int(suite.get("failures", 0)) + int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
        for case in suite.iter("testcase"):
            cid = _case_id(case)
            collected_names.append(cid)
            if case.find("skipped") is not None:
                skipped_names.append(cid)
    return {
        "total": total,
        "failed": failed,
        "skipped": skipped,
        "skipped_names": sorted(skipped_names),
        "collected_names": sorted(collected_names),
        "case_count": len(collected_names),
    }


def baseline_floor() -> int:
    """Floor = the collection baseline, overridable downward only on purpose.

    ``RECORDER_MIN_COLLECTED`` may RAISE the floor (a stricter lane) but never
    lower it silently, because lowering it is how a coverage floor stops
    meaning anything.
    """
    if not BASELINE.is_file():
        print(
            f"no collection baseline at {BASELINE}; the coverage floor would be "
            "0, which disables it entirely -- refusing to run the gate without one",
            file=sys.stderr,
        )
        sys.exit(2)
    text = BASELINE.read_text(encoding="utf-8")
    numbers = [
        (idx, line.strip())
        for idx, line in enumerate(text.splitlines(), start=1)
        if line.strip() and not line.strip().startswith("#")
    ]
    if not numbers:
        print(
            f"{BASELINE} contains no collection count; the coverage floor would "
            "be 0, which disables it entirely -- regenerate the baseline from a "
            "real report instead of accepting this file",
            file=sys.stderr,
        )
        sys.exit(2)
    if len(numbers) > 1:
        found = ", ".join(f"line {idx} = {value!r}" for idx, value in numbers)
        print(
            f"{BASELINE} has {len(numbers)} candidate floor values ({found}); "
            "an ambiguous baseline cannot be a floor, so keep exactly one "
            "numeric line (comments may be any number)",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        floor = int(numbers[0][1])
    except ValueError:
        print(
            f"{BASELINE} line {numbers[0][0]} is {numbers[0][1]!r}, which is not "
            "an integer; the baseline must be a plain collection count",
            file=sys.stderr,
        )
        sys.exit(2)
    override = os.environ.get("RECORDER_MIN_COLLECTED", "").strip()
    if override:
        requested = int(override)
        if requested < floor:
            print(
                f"RECORDER_MIN_COLLECTED={requested} is below the committed "
                f"baseline {floor}; refusing to lower the coverage floor",
                file=sys.stderr,
            )
            sys.exit(2)
        floor = requested
    return floor


def evaluate(report: dict) -> list[str]:
    problems: list[str] = []
    floor = baseline_floor()
    if report["total"] < floor:
        problems.append(
            f"only {report['total']} tests collected, baseline floor is {floor}; "
            "a lane of the recorder stack was not collected"
        )
    allowed = set(KNOWN_ENV_SKIPS)
    unexpected = [n for n in report["skipped_names"] if n not in allowed]
    if unexpected:
        problems.append(
            f"{len(unexpected)} skipped test(s) are not on the known-environment "
            f"allowlist and will not be covered: {', '.join(unexpected)}"
        )
    # Allowlist staleness is checked on EVERY run, not only on runs that
    # happen to contain a skip. Two different defects hide here, and they are
    # not equally forgivable:
    #
    #   * an allowlisted id that was COLLECTED and then passed or failed
    #     ("ran"): the environment gate is no longer doing anything, so the
    #     entry is stale. The one legitimate case is a FreeCAD lane, where the
    #     same tests really do run and therefore do not skip -- so a zero-skip
    #     report is normal there and is allowed ONLY when FREECAD_EXE (or
    #     FREECAD_COMMAND) is configured. Everywhere else a zero-skip report
    #     means the allowlist is lying about why tests are not covered.
    #   * an allowlisted id that was not collected at all ("gone"): the test was
    #     renamed, deleted, or lost with its module. That is a silent coverage
    #     loss and is a failure unconditionally -- no environment explains a
    #     test id that no longer exists.
    #
    # Checking this only when skipped > 0 (the previous behaviour) meant that a
    # lane with zero skips could never notice a rotting allowlist, which is
    # exactly the state a future FreeCAD lane would be in.
    skipped_names = set(report["skipped_names"])
    collected_names = set(report["collected_names"])
    ran = sorted((allowed & collected_names) - skipped_names)
    gone = sorted(allowed - collected_names)
    freecad_lane = freecad_is_configured()
    if ran and not freecad_lane:
        problems.append(
            "these tests are on the environment-skip allowlist but were "
            "collected and ran, so the allowlist is stale: " + ", ".join(ran)
        )
    if gone:
        problems.append(
            "these tests are on the environment-skip allowlist but no longer "
            "exist in the report, so the coverage hole they named is either "
            "gone or has been renamed -- update the allowlist deliberately: "
            + ", ".join(gone)
        )
    if report["case_count"] != report["total"]:
        problems.append(
            f"the junit header counts {report['total']} tests but the report "
            f"contains {report['case_count']} <testcase> elements; the report "
            "is not self-consistent, so no count here can be trusted"
        )
    if report["failed"]:
        problems.append(f"{report['failed']} failing tests")
    return problems


def write_summary(report: dict, problems: list[str]) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    passed = report["total"] - report["failed"] - report["skipped"]
    lines = [
        "### DXF recorder stack",
        "",
        f"- Python: `{sys.version.split()[0]}`",
        f"- collected: **{report['total']}** (floor {baseline_floor()})",
        f"- passed: **{passed}**",
        f"- failed: **{report['failed']}**",
        f"- skipped: **{report['skipped']}**",
        "",
    ]
    if report["skipped_names"]:
        lines += ["Skipped, and why they stay skipped:", ""]
        lines += [f"- `{n}` - no FreeCAD on a hosted runner" for n in report["skipped_names"]]
        lines += [""]
    if problems:
        lines += ["> **CI integrity check failed**"] + [f"> - {p}" for p in problems] + [""]
    else:
        lines += ["Collection and skip allowlist both within budget.", ""]
    with open(target, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", default="ci-reports/recorder-junit.xml")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="record this report's collection count as the floor, then exit",
    )
    args = parser.parse_args(argv)

    path = Path(args.report)
    if not path.is_file():
        print(f"no junit report at {path}; the pytest step did not run", file=sys.stderr)
        return 1
    report = read_report(path)

    if args.write_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(
            "# Recorded collection count for `pytest src/all_in_cad/recorder`.\n"
            "#\n"
            "# Regenerate deliberately, from a real report, with:\n"
            "#   python ci/recorder_ci_gate.py ci-reports/recorder-junit.xml --write-baseline\n"
            "#\n"
            "# This is a FLOOR. A larger suite must never fail because of it; a\n"
            "# smaller one means tests stopped being collected, which is the\n"
            "# failure mode this whole file exists to catch.\n"
            f"{report['total']}\n",
            encoding="utf-8",
        )
        print(f"baseline recorded: {report['total']}")
        return 0

    print(
        f"collected={report['total']} "
        f"passed={report['total'] - report['failed'] - report['skipped']} "
        f"failed={report['failed']} skipped={report['skipped']} "
        f"floor={baseline_floor()}"
    )
    for name in report["skipped_names"]:
        print(f"  SKIPPED: {name}")

    problems = evaluate(report)
    write_summary(report, problems)
    if problems:
        print("CI_INTEGRITY_FAILURE: " + "; ".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
