"""Unit tests for the freecad_runner artifact-first verdict logic.

No FreeCAD required: the only thing stubbed is the process launcher, so what
is under test is exactly the verdict-record / exit-code / artifact adjudication.

WHAT CHANGED AND WHY (observed, not assumed)
---------------------------------------------
On this host freecadcmd.exe ran a real script, produced its artifacts and
exited 0, while stdout carried no marker at all. So the old tests' assumption
("markers are the strongest signal") described a channel that does not work
here. The cases below now pin the OBSERVED environment:

  * markers present (legacy/auxiliary behaviour)      -> still adjudicated
  * markers absent + verdict JSON ok + artifacts      -> ok, proven by artifact
  * markers absent + no verdict JSON + exit 0         -> failed, unconditionally
  * markers absent + verdict JSON status=failed       -> failed

The last live integration case is skipped unless FREECAD_EXE points at a real
freecadcmd.exe.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import freecad_runner as fr  # noqa: E402


# --------------------------------------------------------------- fake pusher --
class FakeProc:
    """Minimal stand-in for subprocess.Popen with scripted behaviour."""

    def __init__(self, stdout="", stderr="", returncode=0, hang=False,
                 raise_exc=None, children=(), on_run=None):
        self._stdout, self._stderr = stdout, stderr
        self._returncode = returncode
        self._hang = hang
        self._raise = raise_exc
        self.pid = 424242
        self.killed = False
        self.children = list(children)   # must all be gone after a timeout
        self.kill_calls = 0
        self._on_run = on_run           # simulates the script writing files

    def communicate(self, timeout=None):
        if self._on_run is not None:
            self._on_run()
        if self._hang:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)
        if self._raise is not None:
            raise self._raise
        return self._stdout, self._stderr

    @property
    def returncode(self):
        return self._returncode

    def kill(self):
        self.kill_calls += 1
        self.killed = True
        for c in self.children:
            c.kill()


def factory(proc):
    def _f(cmd, **kwargs):
        proc.cmd = cmd
        return proc
    return _f


OK_OUT = f"{fr.MARKER_BEGIN}\nbuilding...\n{fr.MARKER_OK}\n"
FAIL_OUT = f"{fr.MARKER_BEGIN}\n{fr.MARKER_FAIL}Part.Shape error]\n"
EMPTY = ""                                  # <-- the OBSERVED reality here
PARTIAL = f"{fr.MARKER_BEGIN}\nhalfway\n"      # no terminal marker at all
FRIENDLY_FAIL = f"{fr.MARKER_BEGIN}\n{fr.MARKER_FAIL}ok, not a failure]\n"


@pytest.fixture
def script(tmp_path):
    p = tmp_path / "job.py"
    p.write_text(
        "print('top level')\n"
        "if __name__ == '__main__':\n"
        "    print('guarded body')\n",
        encoding="utf-8",
    )
    return p


def run(script, proc, **kw):
    kw.setdefault("freecad_exe", "FreeCADCmd.exe")
    kw.setdefault("workdir", Path(script).parent)
    kw.setdefault("popen", factory(proc))
    return fr.run_freecad_script(script, **kw)


def make_artifact(tmp_path, name="aic_result.json", data=b'{"status":"built"}'):
    p = tmp_path / name
    p.write_bytes(data)
    return p


LAYERS = ("CEN1", "WAL1", "WAL2", "DOOR", "DOOR_ELE")
VERDICT_NAME = "aic_verdict.json"


def make_verdict(tmp_path, status="ok", reason="", objects=19, layers=LAYERS,
                 name=VERDICT_NAME):
    p = tmp_path / name
    fr.write_verdict(p, status, reason=reason, objects=objects, layers=layers)
    return p


def run_verdict_case(script, tmp_path, status="ok", reason="", objects=19,
                     extra_artifacts=(), pre_existing=False, writer=None,
                     proc_kwargs=None, expect_artifacts=True):
    """Run the fake process while it WRITES the verdict record.

    pre_existing=True models a stale record left by an earlier run;
    writer lets a test write malformed content instead of a valid record.
    """
    verd = tmp_path / VERDICT_NAME

    def do_write():
        if writer is not None:
            writer(verd)
        else:
            fr.write_verdict(verd, status, reason=reason, objects=objects, layers=LAYERS)

    if pre_existing:
        do_write()
    kw = {"stdout": EMPTY, "returncode": 0, "on_run": None if pre_existing else do_write}
    kw.update(proc_kwargs or {})
    arts = ([verd] + list(extra_artifacts)) if expect_artifacts else list(extra_artifacts)
    res = run(script, FakeProc(**kw), expect_artifacts=arts or None, verdict_path=verd)
    return res, verd


# ------------------------------------------------------- guard neutralisation --
def test_guard_neutralised_to_top_level(script, tmp_path):
    dst, note = fr.prepare_script(script, tmp_path, run_token="AIC-RUN-test")
    text = dst.read_text(encoding="utf-8")
    assert note == "guard-neutralised"
    import ast as _ast
    tree = _ast.parse(text)
    assert not any(isinstance(n, _ast.If) and fr._is_main_test(n.test) for n in tree.body)
    assert "guarded body" in text
    # the guarded body must now be at zero indentation -> runs on import
    assert "\nguarded body" not in text and "    print('guarded body')" not in text
    # the original file on disk is untouched
    assert "__main__" in script.read_text(encoding="utf-8")
    res = run(script, FakeProc(stdout=OK_OUT))
    assert Path(res.command[3]).name == dst.name


def test_script_without_guard_is_passed_through(tmp_path):
    p = tmp_path / "plain.py"
    p.write_text("print('hi')\n", encoding="utf-8")
    dst, note = fr.prepare_script(p, tmp_path)
    assert note == "no-main-guard-found"
    assert "print('hi')" in dst.read_text(encoding="utf-8")


def test_prepared_copy_carries_run_token_header_and_footer(tmp_path):
    p = tmp_path / "plain.py"
    p.write_text("print('hi')\n", encoding="utf-8")
    dst, _ = fr.prepare_script(p, tmp_path, run_token="AIC-RUN-xyz")
    text = dst.read_text(encoding="utf-8")
    assert "_AIC_TOKEN = 'AIC-RUN-xyz'" in text
    assert "[AIC-BEGIN] " in text and "[AIC-END] " in text
    # both the header and the footer emit through the same token variable
    assert text.count("_AIC_TOKEN") >= 3


# ------------------------------------------------------------- marker parsing --
def test_parse_markers():
    assert fr.parse_markers(OK_OUT) == ("ok", True, None)
    assert fr.parse_markers(FAIL_OUT) == ("fail", True, "Part.Shape error")
    assert fr.parse_markers(EMPTY) == ("unknown", False, None)
    assert fr.parse_markers(PARTIAL) == ("unknown", True, None)
    kind, _, _ = fr.parse_markers(f"{fr.MARKER_BEGIN}\nFAIL] not a marker\n")
    assert kind == "unknown"
    assert fr.parse_markers(f"finished {fr.MARKER_OK}\n")[0] == "unknown"


# ------------------------------------------------- LEGACY marker-mode verdicts --
def test_ok_marker_zero_exit_artifact_present(script, tmp_path):
    art = make_artifact(tmp_path)
    r = run(script, FakeProc(stdout=OK_OUT, returncode=0), expect_artifacts=[art])
    assert r.status == fr.STATUS_OK and r.ok
    assert r.evidence == fr.EV_MARKER
    assert r.marker == "ok" and r.exit_code == 0 and r.artifacts_ok


def test_fail_marker_with_zero_exit_is_failed(script, tmp_path):
    art = make_artifact(tmp_path)
    r = run(script, FakeProc(stdout=FAIL_OUT, returncode=0), expect_artifacts=[art])
    assert r.status == fr.STATUS_FAILED
    assert r.marker_reason == "Part.Shape error"
    assert any("script reported failure" in x for x in r.reasons)


def test_no_markers_no_verdict_record_configured_is_unknown(script, tmp_path):
    art = make_artifact(tmp_path)
    r = run(script, FakeProc(stdout=EMPTY, returncode=0), expect_artifacts=[art])
    assert r.status == fr.STATUS_UNKNOWN
    assert not r.ok and r.has_markers is False


def test_unknown_is_never_promoted_to_ok_or_failed(script, tmp_path):
    art = make_artifact(tmp_path)
    r = run(script, FakeProc(stdout=EMPTY, returncode=0), expect_artifacts=[art])
    assert r.status not in (fr.STATUS_OK, fr.STATUS_FAILED)


# =============================================================================
# THE OBSERVED ENVIRONMENT: stdout is dead, the verdict record is the contract
# =============================================================================
def test_observed_case_script_ran_artifact_json_ok_no_markers_is_proven_ok(script, tmp_path):
    "stdout empty + exit 0 + verdict record status=ok + artifact present -> ok."
    art = make_artifact(tmp_path)
    r, verd = run_verdict_case(script, tmp_path, status="ok", objects=19,
                               extra_artifacts=[art])
    assert r.status == fr.STATUS_OK and r.ok
    assert r.evidence == fr.EV_VERDICT
    assert r.has_markers is False          # marker channel really was dead
    assert r.verdict["objects"] == 19
    assert r.verdict["layers"] == ["CEN1", "WAL1", "WAL2", "DOOR", "DOOR_ELE"]
    assert r.verdict_fresh is True
    assert any("proven by artifact" in x for x in r.reasons)


def test_observed_case_no_artifact_no_markers_exit_zero_is_failed(script, tmp_path):
    "The failure mode that would lose a drawing: unconditional failure."
    verd = tmp_path / "aic_verdict.json"      # never written
    r = run(script, FakeProc(stdout=EMPTY, returncode=0),
            expect_artifacts=[verd], verdict_path=verd)
    assert r.status == fr.STATUS_FAILED
    assert not r.ok
    # the verdict file was also declared an expected artifact, so the
    # unconditional no-artifact rule fires before the verdict branch
    assert r.verdict_fresh is False
    assert any("artifact check failed" in x or "verdict record unusable" in x
               for x in r.reasons)


def test_verdict_record_status_failed_is_failed(script, tmp_path):
    art = make_artifact(tmp_path)
    r, _ = run_verdict_case(script, tmp_path, status="failed",
                            reason="degenerate solid: volume <= 0",
                            extra_artifacts=[art])
    assert r.status == fr.STATUS_FAILED
    assert "degenerate solid" in " ".join(r.reasons)


def test_verdict_record_ok_cannot_rescue_a_missing_drawing(script, tmp_path):
    """A record saying ok must not outvote a missing artifact."""
    r, _ = run_verdict_case(script, tmp_path, status="ok",
                            extra_artifacts=[tmp_path / "drawing.dxf"])
    assert r.status == fr.STATUS_FAILED
    assert any("artifact check failed" in x for x in r.reasons)


def test_stale_verdict_from_a_previous_run_cannot_prove_success(script, tmp_path):
    """Mechanism (b): a leftover record is not proof for this run."""
    r, _ = run_verdict_case(script, tmp_path, status="ok", pre_existing=True)
    assert r.verdict_fresh is False
    assert r.status == fr.STATUS_FAILED
    assert any("stale" in x for x in r.reasons)


def test_unparseable_verdict_is_failed(script, tmp_path):
    art = make_artifact(tmp_path)
    r, _ = run_verdict_case(script, tmp_path, extra_artifacts=[art],
                            writer=lambda p: p.write_text("{not json at all", encoding="utf-8"))
    assert r.status == fr.STATUS_FAILED
    assert any("unparseable" in x for x in r.reasons)


def test_empty_verdict_file_is_failed(script, tmp_path):
    art = make_artifact(tmp_path)
    r, _ = run_verdict_case(script, tmp_path, extra_artifacts=[art],
                            writer=lambda p: p.write_bytes(b""))
    assert r.status == fr.STATUS_FAILED
    assert any("empty" in x for x in r.reasons)


def test_verdict_written_during_the_run_is_fresh(script, tmp_path):
    """Mechanism (b) positive side: written by the 'script' -> fresh."""
    art = make_artifact(tmp_path)
    r, _ = run_verdict_case(script, tmp_path, status="ok", objects=19,
                            extra_artifacts=[art])
    assert r.verdict_fresh is True
    assert r.status == fr.STATUS_OK and r.evidence == fr.EV_VERDICT


def test_timeout_beats_a_perfect_verdict_record(script, tmp_path):
    art = make_artifact(tmp_path)
    r, _ = run_verdict_case(script, tmp_path, status="ok", extra_artifacts=[art],
                            proc_kwargs={"hang": True})
    assert r.status == fr.STATUS_FAILED and r.timed_out is True


def test_nonzero_exit_beats_a_perfect_verdict_record(script, tmp_path):
    art = make_artifact(tmp_path)
    r, _ = run_verdict_case(script, tmp_path, status="ok", extra_artifacts=[art],
                            proc_kwargs={"returncode": 2})
    assert r.status == fr.STATUS_FAILED
    assert any("non-zero exit code 2" in x for x in r.reasons)


# ------------------------------------------------------------ verdict helpers --
def test_write_verdict_roundtrip(tmp_path):
    p = fr.write_verdict(tmp_path / "v.json", "ok", reason="built",
                         objects=19, layers=["CEN1", "DOOR"])
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert rec["schema"] == fr.VERDICT_SCHEMA
    assert rec["status"] == "ok" and rec["objects"] == 19
    assert rec["layers"] == ["CEN1", "DOOR"]
    assert not list(tmp_path.glob("*.part"))     # atomic replace cleaned up


def test_write_verdict_rejects_a_non_binary_status(tmp_path):
    with pytest.raises(ValueError):
        fr.write_verdict(tmp_path / "v.json", "unknown")


def test_read_verdict_reports_error_for_missing_file(tmp_path):
    rec, err = fr.read_verdict(tmp_path / "nope.json")
    assert rec is None and err == "verdict-record-missing"


# ------------------------------------------------------------------ artifacts --
def test_missing_artifact_downgrades_ok_marker_to_failed(script, tmp_path):
    r = run(script, FakeProc(stdout=OK_OUT, returncode=0),
            expect_artifacts=[tmp_path / "drawing.step"])
    assert r.status == fr.STATUS_FAILED
    assert r.artifacts[0].exists is False and r.artifacts_ok is False


def test_zero_byte_artifact_is_failed(script, tmp_path):
    art = make_artifact(tmp_path, data=b"")
    r = run(script, FakeProc(stdout=OK_OUT, returncode=0), expect_artifacts=[art])
    assert r.status == fr.STATUS_FAILED
    assert r.artifacts[0].size == 0 and r.artifacts[0].size_ok is False


def test_artifact_hash_match_and_mismatch(script, tmp_path):
    art = make_artifact(tmp_path)
    good = fr._sha256(art)
    ok = run(script, FakeProc(stdout=OK_OUT, returncode=0),
             expect_artifacts=[art], artifact_hashes={str(art): good})
    assert ok.status == fr.STATUS_OK and ok.artifacts[0].hash_ok is True
    bad = run(script, FakeProc(stdout=OK_OUT, returncode=0),
              expect_artifacts=[art], artifact_hashes={str(art): "00" * 32})
    assert bad.status == fr.STATUS_FAILED and bad.artifacts[0].hash_ok is False


def test_relative_artifact_resolved_against_workdir(script, tmp_path):
    (tmp_path / "out").mkdir()
    make_artifact(tmp_path / "out", "aic_result.json")
    r = run(script, FakeProc(stdout=OK_OUT, returncode=0),
            expect_artifacts=["out/aic_result.json"])
    assert r.artifacts_ok is True


# ------------------------------------------------------------------ exitcode ---
def test_nonzero_exit_with_ok_marker_is_failed(script, tmp_path):
    art = make_artifact(tmp_path)
    r = run(script, FakeProc(stdout=OK_OUT, returncode=3), expect_artifacts=[art])
    assert r.status == fr.STATUS_FAILED
    assert any("non-zero exit code 3" in x for x in r.reasons)


def test_friendly_bracket_text_is_still_a_fail_marker(script, tmp_path):
    art = make_artifact(tmp_path)
    r = run(script, FakeProc(stdout=FRIENDLY_FAIL, returncode=0), expect_artifacts=[art])
    assert r.status == fr.STATUS_FAILED and r.marker_reason == "ok, not a failure"


# --------------------------------------------------------- required markers ---
def test_missing_extra_required_marker_is_failed(script, tmp_path):
    art = make_artifact(tmp_path)
    r = run(script, FakeProc(stdout=OK_OUT, returncode=0), expect_artifacts=[art],
            markers=["[AIC-PART:CAD]"])
    assert r.status == fr.STATUS_FAILED
    assert "missing-required-marker" in (r.marker_reason or "")


def test_extra_required_marker_present_is_ok(script, tmp_path):
    art = make_artifact(tmp_path)
    out = OK_OUT + "[AIC-PART:CAD]\n"
    r = run(script, FakeProc(stdout=out, returncode=0), expect_artifacts=[art],
            markers=["[AIC-PART:CAD]"])
    assert r.status == fr.STATUS_OK


# ------------------------------------------------------------------ timeout ---
def test_timeout_is_failed_and_kills_children(script, tmp_path):
    art = make_artifact(tmp_path)
    kid1, kid2 = FakeProc(hang=True), FakeProc(hang=True)
    proc = FakeProc(hang=True, children=[kid1, kid2])
    r = run(script, proc, timeout=0.01, expect_artifacts=[art])
    assert r.status == fr.STATUS_FAILED and r.timed_out is True
    assert proc.killed and kid1.killed and kid2.killed


def test_timeout_wins_even_when_markers_look_ok(script, tmp_path):
    art = make_artifact(tmp_path)
    r = run(script, FakeProc(stdout=OK_OUT, hang=True), timeout=0.01,
            expect_artifacts=[art])
    assert r.status == fr.STATUS_FAILED


# ----------------------------------------------------- missing exe / logfile ---
def test_missing_executable_is_failed_not_unknown(script):
    def boom(cmd, **kwargs):
        raise FileNotFoundError("no such file")
    r = fr.run_freecad_script(script, freecad_exe="nope.exe",
                              workdir=script.parent, popen=boom)
    assert r.status == fr.STATUS_FAILED
    assert any("executable-not-found" in x for x in r.reasons)


def test_log_file_flag_always_in_command(script, tmp_path):
    r = run(script, FakeProc(stdout=OK_OUT))
    assert "--log-file" in r.command
    assert r.command[r.command.index("--log-file") + 1].endswith(".FreeCAD.log")


def test_run_token_is_unique_per_run(script, tmp_path):
    a = run(script, FakeProc(stdout=OK_OUT))
    b = run(script, FakeProc(stdout=OK_OUT))
    assert a.run_token and b.run_token and a.run_token != b.run_token


# ------------------------------------------------------------------ template ---
def test_template_has_no_main_guard_and_has_markers():
    tpl = Path(__file__).resolve().parent / "aic_headless_script.py"
    text = tpl.read_text(encoding="utf-8")
    import ast as _ast
    tree = _ast.parse(text)
    assert not any(isinstance(n, _ast.If) and fr._is_main_test(n.test) for n in tree.body)
    assert "MODULE TOP LEVEL" in text
    for tok in (fr.MARKER_BEGIN, fr.MARKER_OK, fr.MARKER_FAIL):
        assert tok in text
    dst, note = fr.prepare_script(tpl, tpl.parent)
    assert note == "no-main-guard-found"


def test_template_docstring_states_the_verdict_record_rule():
    tpl = Path(__file__).resolve().parent / "aic_headless_script.py"
    text = tpl.read_text(encoding="utf-8")
    # the rule the wrapper enforces must be documented for script authors
    assert "aic_verdict.json" in text
    assert "is a failed run" in text or "reported FAILURE" in text
    assert fr.VERDICT_NAME.split(".")[0] in text


def test_cli_maps_missing_executable_to_exit_code_1(script, tmp_path, capsys):
    assert fr.main([str(script), "--freecad", "nope.exe"]) == 1


# =============================================================================
# LIVE FreeCAD integration (skipped unless FREECAD_EXE is set)
# =============================================================================
FREECAD_EXE = os.environ.get("FREECAD_EXE")
FIXTURE_DXF = Path(
    r"C:\Users\khs09\.aside\u\0\sessions\2026-09-26_8ORnwfjJrKmHeySr\artifacts\wall_door_e2e.dxf"
)

LIVE_SCRIPT = '''
import json, time
from pathlib import Path
import FreeCAD, importDXF

VERDICT = Path("out/aic_verdict.json")
try:
    VERDICT.parent.mkdir(parents=True, exist_ok=True)
    doc = FreeCAD.newDocument("probe")
    importDXF.insert(str(DXF), doc.Name)
    doc.recompute()
    shapes = [o for o in doc.Objects if o.isDerivedFrom("Part::Feature")]
    layers = sorted({getattr(o, "Label", "") for o in doc.Objects
                     if o.isDerivedFrom("App::FeaturePython")
                     or "Layer" in type(o).__name__})
    rec = {"schema": "aic.verdict/1", "status": "ok", "reason": "opened",
           "script": "probe", "objects": len(doc.Objects),
           "shape_count": len(shapes), "layers": layers,
           "artifacts": [], "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    VERDICT.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
except BaseException as exc:
    VERDICT.parent.mkdir(parents=True, exist_ok=True)
    VERDICT.write_text(json.dumps({"schema": "aic.verdict/1", "status": "failed",
        "reason": f"{type(exc).__name__}:{exc}"[:300]}), encoding="utf-8")
    raise
'''


@pytest.mark.skipif(not (FREECAD_EXE and Path(FREECAD_EXE).is_file()),
                    reason="FREECAD_EXE not set / not found")
@pytest.mark.skipif(not FIXTURE_DXF.is_file(), reason="fixture DXF not present")
def test_live_freecad_verdict_record_proves_success(tmp_path):
    """Real freecadcmd, real DXF. Asserts artifact-based adjudication and
    records whether the stdout marker channel was alive."""
    src = tmp_path / "probe.py"
    src.write_text(f"DXF = {str(FIXTURE_DXF)!r}\n" + LIVE_SCRIPT, encoding="utf-8")
    res = fr.run_freecad_script(
        src, freecad_exe=FREECAD_EXE, timeout=180,
        workdir=tmp_path, expect_artifacts=["out/aic_verdict.json"],
        verdict_path="out/aic_verdict.json",
    )
    print("MARKER_ALIVE:", res.has_markers, "| LOG_TOKEN:", res.log_token_seen,
          "| STATUS:", res.status, "| EVIDENCE:", res.evidence)
    assert res.status == fr.STATUS_OK
    assert res.evidence == fr.EV_VERDICT
    assert res.verdict["objects"] == 19
    assert res.verdict["shape_count"] == 11
    for layer in ("CEN1", "WAL1", "WAL2", "DOOR", "DOOR_ELE"):
        assert layer in res.verdict["layers"]


# ------------------------------------------- live-regression guards -----------
def test_header_is_injected_after_docstring_and_future_import(tmp_path):
    """Regression: injecting before `from __future__` is a SyntaxError, and
    FreeCAD swallows that into a silent exit-0 no-op with no artifact. That
    was observed LIVE on this host before this test existed."""
    p = tmp_path / "futuremod.py"
    p.write_text(
        '"""docstring."""\n'
        "from __future__ import annotations\n"
        "import json\n"
        "def f(x: int) -> int:\n"
        "    return x\n",
        encoding="utf-8",
    )
    dst, _ = fr.prepare_script(p, tmp_path, run_token="AIC-RUN-t")
    text = dst.read_text(encoding="utf-8")
    import ast as _ast
    _ast.parse(text)                      # raises if the header was misplaced
    assert text.index("_AIC_TOKEN") > text.index("from __future__")
    assert text.index("_AIC_TOKEN") > text.index('"""docstring."""')


def test_template_survives_header_injection(tmp_path):
    """The shipped template uses `from __future__ import annotations`; the
    prepared copy must still parse, or the live run is a silent no-op."""
    tpl = Path(__file__).resolve().parent / "aic_headless_script.py"
    dst, _ = fr.prepare_script(tpl, tmp_path, run_token="AIC-RUN-t")
    import ast as _ast
    _ast.parse(dst.read_text(encoding="utf-8"))


def test_unparseable_script_is_failed_not_silently_no_op(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def (:\n", encoding="utf-8")
    r = fr.run_freecad_script(bad, freecad_exe="FreeCADCmd.exe", workdir=tmp_path,
                              popen=factory(FakeProc(stdout=OK_OUT)))
    assert r.status == fr.STATUS_FAILED
    assert any("not-parseable" in x for x in r.reasons)
