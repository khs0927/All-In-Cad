"""Tests for the file-based recording transaction.

These tests are the *proof obligation* for the C# review findings:

* C-1 (global undo swallows the user) -> :func:`test_rollback_only_touches_declared_files`
* C-2 (Enter re-executes / feeds a live command) -> no keystrokes exist; asserted by
  :func:`test_no_rollback_path_uses_queued_input` (source-level invariant) and by the
  fact that a rollback is a byte copy of a named file.
* C-3 (rollback reported as success without checking) -> every restore is hash-verified;
  :func:`test_rollback_verifies_by_hash` and :func:`test_restore_failure_is_reported_not_hidden`
* C-4 (entity delta contaminated by the user's work -> partial rollback) ->
  :func:`test_external_modification_aborts_instead_of_deleting`
* C-7 (unguarded global rollback) -> rollback only exists on a Txn that captured a
  pre-state: :func:`test_rollback_requires_a_captured_pre_state`

Run with (PYTHONHOME/PYTHONPATH must be cleared on this host, see ``wall.py``)::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \\
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\transaction_test.py -q
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path

import pytest

try:  # package-relative import (normal case)
    from . import transaction as tx
    from .wall import make_wall, write_wall
except ImportError:  # pragma: no cover - flat execution fallback
    import transaction as tx  # type: ignore[no-redef]

    from all_in_cad.recorder.wall import make_wall, write_wall  # type: ignore[no-redef]


# ======================================================================
# helpers
# ======================================================================
def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def always_fail(path: Path, handles: tuple[str, ...] = ()) -> None:
    raise tx.VerificationFailed(f"deliberate verifier failure for {path}")


def journals(journal_dir: Path) -> list[Path]:
    return tx.pending_journals(journal_dir)


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    """``workdir/`` holds the drawing, ``workdir/journals/`` the journal dir."""
    (tmp_path / "journals").mkdir()
    return tmp_path


# ======================================================================
# 1. normal commit
# ======================================================================
def test_commit_keeps_the_new_file_and_clears_the_journal(workdir: Path) -> None:
    out = workdir / "plan.dxf"
    jdir = workdir / "journals"
    with tx.transaction(out, journal_dir=jdir) as txn:
        result = txn.apply(lambda t: write_text(out, "v1\n"))
    assert result is None or result == "v1\n"
    assert out.read_text(encoding="utf-8") == "v1\n"
    assert journals(jdir) == [], "a committed transaction must leave no journal"
    assert txn.state == "committed"


def test_commit_keeps_a_real_dxf_recording(workdir: Path) -> None:
    """End-to-end with the real recorder: ezdxf writes, the file is read back."""
    ezdxf = pytest.importorskip("ezdxf")
    out = workdir / "wall.dxf"
    jdir = workdir / "journals"
    verifier = tx.dxf_verifier(
        required_layers=("WAL1", "WAL2"),
        key_points={
            "start_face_lower": (0.0, -100.0),
            "start_face_upper": (0.0, 100.0),
            "end_face_lower": (12000.0, -100.0),
            "end_face_upper": (12000.0, 100.0),
        },
        tolerance=1e-6,
    )
    with tx.transaction(out, journal_dir=jdir, verifiers=verifier) as txn:
        record = txn.apply(_write_wall_recording(out))
    assert len(record.handles()) > 0
    report = txn.reports[out]
    assert report["entity_count"] == record.entity_count
    # The file really is a DXF that ezdxf can re-open.
    assert ezdxf.readfile(str(out)) is not None
    assert out.exists() and out.stat().st_size > 0


def _write_wall_recording(out: Path):
    import ezdxf

    def _run(_txn):
        document = ezdxf.new("R2018")
        wall = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0)
        record = write_wall(document, wall)
        document.saveas(out)
        return record

    return _run


# ======================================================================
# 2. verification failure after the write -> full restore
# ======================================================================
def test_verification_failure_rolls_back_to_pre_state(workdir: Path) -> None:
    out = workdir / "plan.dxf"
    write_text(out, "original\n")
    jdir = workdir / "journals"
    before = sha(out)

    with pytest.raises(tx.VerificationFailed):
        with tx.transaction(out, journal_dir=jdir, verifiers=always_fail) as txn:
            txn.apply(lambda t: write_text(out, "half-written garbage\n"))

    assert out.read_text(encoding="utf-8") == "original\n"
    assert sha(out) == before
    assert journals(jdir) == []


def test_verification_failure_on_a_new_file_removes_it(workdir: Path) -> None:
    out = workdir / "new.dxf"
    jdir = workdir / "journals"
    with pytest.raises(tx.VerificationFailed):
        with tx.transaction(out, journal_dir=jdir, verifiers=always_fail) as txn:
            txn.apply(lambda t: write_text(out, "created then rejected\n"))
    assert not out.exists(), "a rejected recording must not leave a file behind"
    assert journals(jdir) == []


# ======================================================================
# 3. the restore is proven by hash, not assumed
# ======================================================================
def test_rollback_verifies_by_hash(workdir: Path) -> None:
    out = workdir / "plan.dxf"
    write_text(out, "original\n")
    jdir = workdir / "journals"
    before_state = tx.capture_state(out)

    txn = tx.begin(out, journal_dir=jdir)
    txn.apply(lambda t: write_text(out, "changed\n"))
    txn.rollback()

    after_state = tx.capture_state(out)
    assert tx.content_matches(after_state, before_state)
    assert after_state.sha256 == before_state.sha256
    assert all(item["verified"] for item in txn.rollback_report)
    assert txn.state == "rolled_back"


def test_restore_failure_is_reported_not_hidden(workdir: Path, monkeypatch) -> None:
    """If the byte copy silently does nothing, we must say 'unverified'."""
    out = workdir / "plan.dxf"
    write_text(out, "original\n")
    jdir = workdir / "journals"
    # captured but never asserted on (kept, not deleted): the report shows this
    # reads like a dropped post-failure content check
    _before_state = tx.capture_state(out)

    txn = tx.begin(out, journal_dir=jdir)
    txn.apply(lambda t: write_text(out, "changed\n"))
    monkeypatch.setattr(tx, "_restore_target", lambda target: None)
    with pytest.raises(tx.RestoreUnverified) as excinfo:
        txn.rollback()
    assert not excinfo.value.report["targets"][0]["verified"]


# ======================================================================
# 4. a mid-transaction failure leaves no partial state
# ======================================================================
def test_exception_in_apply_restores_every_target(workdir: Path) -> None:
    wall = workdir / "wall.dxf"
    door = workdir / "door.dxf"
    write_text(wall, "wall-original\n")
    write_text(door, "door-original\n")
    jdir = workdir / "journals"
    pre_wall, _pre_door = sha(wall), sha(door)

    def plan(txn):
        write_text(wall, "wall-new\n")
        write_text(door, "door-partially-written")
        raise RuntimeError("door recording blew up")

    with pytest.raises(RuntimeError):
        with tx.transaction([wall, door], journal_dir=jdir) as txn:
            txn.apply(plan)

    assert sha(wall) == pre_wall
    assert door.read_text(encoding="utf-8") == "door-original\n"
    assert journals(jdir) == []


# ======================================================================
# 5. external work is never deleted (C-4)
# ======================================================================
def test_external_modification_aborts_instead_of_deleting(workdir: Path) -> None:
    out = workdir / "plan.dxf"
    write_text(out, "original\n")
    jdir = workdir / "journals"
    our_content = "our recording\n"
    theirs = "user drew this after us\n"

    txn = tx.begin(out, journal_dir=jdir)
    txn.apply(lambda t: write_text(out, our_content))
    # Somebody else edits the same drawing after our apply.
    write_text(out, theirs)

    with pytest.raises(tx.ExternalModificationError) as excinfo:
        txn.rollback()
    assert excinfo.value.path == out
    assert excinfo.value.found.sha256 == sha(out)
    # Their bytes survive; ours are not forced back over them.
    assert out.read_text(encoding="utf-8") == theirs
    assert txn.state == "externally_modified"
    # The journal is kept so recovery/reporting can still see the conflict.
    assert journals(jdir) == [txn.journal]


def test_explicit_rollback_of_an_untouched_file_also_guards(workdir: Path) -> None:
    out = workdir / "plan.dxf"
    write_text(out, "original\n")
    jdir = workdir / "journals"
    txn = tx.begin(out, journal_dir=jdir)
    write_text(out, "someone else's work\n")
    with pytest.raises(tx.ExternalModificationError):
        txn.rollback()
    assert out.read_text(encoding="utf-8") == "someone else's work\n"


# ======================================================================
# 6. crash recovery through the journal
# ======================================================================
def test_crash_recovery_restores_the_last_good_state(workdir: Path) -> None:
    out = workdir / "plan.dxf"
    write_text(out, "good\n")
    jdir = workdir / "journals"
    good = sha(out)

    # A transaction that "dies" after apply: journal left behind, no commit.
    crashed = tx.begin(out, journal_dir=jdir)
    crashed.apply(lambda t: write_text(out, "half of a recording\n"))
    assert out.read_text(encoding="utf-8") == "half of a recording\n"
    assert len(journals(jdir)) == 1

    # "Process restart": recovery reads the journal and puts the file back.
    outcomes = tx.recover_pending(jdir)
    assert len(outcomes) == 1
    assert outcomes[0].verified is True
    assert sha(out) == good
    assert journals(jdir) == []


def test_crash_recovery_deletes_a_file_that_did_not_exist_before(workdir: Path) -> None:
    out = workdir / "fresh.dxf"
    jdir = workdir / "journals"
    crashed = tx.begin(out, journal_dir=jdir)
    crashed.apply(lambda t: write_text(out, "partially created\n"))
    outcomes = tx.recover_pending(jdir)
    assert [item.verified for item in outcomes] == [True]
    assert not out.exists()


def test_crash_recovery_restores_all_files_of_a_multi_file_plan(workdir: Path) -> None:
    wall = workdir / "wall.dxf"
    door = workdir / "door.dxf"
    write_text(wall, "wall-good\n")
    write_text(door, "door-good\n")
    jdir = workdir / "journals"
    pre = (sha(wall), sha(door))

    crashed = tx.begin([wall, door], journal_dir=jdir)
    crashed.apply(
        lambda t: (write_text(wall, "wall-new\n"), write_text(door, "door-new\n"))
    )
    outcomes = tx.recover_pending(jdir)
    assert [item.verified for item in outcomes] == [True]
    assert (sha(wall), sha(door)) == pre


def test_recovery_ignores_nothing_left_and_reports_unreadable_journal(
    workdir: Path,
) -> None:
    jdir = workdir / "journals"
    (jdir / "broken.json").write_text("{not json", encoding="utf-8")
    outcomes = tx.recover_pending(jdir)
    assert [item.verified for item in outcomes] == [False]
    assert "unreadable" in outcomes[0].detail


# ======================================================================
# 7. multi-file all-or-nothing
# ======================================================================
def test_multi_file_verification_failure_restores_both(workdir: Path) -> None:
    wall = workdir / "wall.dxf"
    door = workdir / "door.dxf"
    write_text(wall, "wall-good\n")
    write_text(door, "door-good\n")
    jdir = workdir / "journals"
    pre = (sha(wall), sha(door))

    def wall_ok(path: Path, handles: tuple[str, ...] = ()) -> None:
        return tx.generic_verifier(path, handles)

    def door_bad(path: Path, handles: tuple[str, ...] = ()) -> None:
        raise tx.VerificationFailed(f"{path} is wrong")

    with pytest.raises(tx.VerificationFailed):
        with tx.transaction(
            [wall, door], journal_dir=jdir, verifiers={door: door_bad, wall: wall_ok}
        ) as txn:
            txn.apply(
                lambda t: (
                    write_text(wall, "wall-new\n"),
                    write_text(door, "door-new\n"),
                )
            )
    assert (sha(wall), sha(door)) == pre
    assert journals(jdir) == []


def test_multi_file_commit_keeps_both(workdir: Path) -> None:
    wall = workdir / "wall.dxf"
    door = workdir / "door.dxf"
    jdir = workdir / "journals"
    with tx.transaction([wall, door], journal_dir=jdir) as txn:
        txn.apply(
            lambda t: (
                write_text(wall, "wall-new\n"),
                write_text(door, "door-new\n"),
            )
        )
    assert wall.read_text(encoding="utf-8") == "wall-new\n"
    assert door.read_text(encoding="utf-8") == "door-new\n"
    assert journals(jdir) == []


# ======================================================================
# 8. the guards themselves (C-1, C-2, C-7)
# ======================================================================
def test_rollback_only_touches_declared_files(workdir: Path) -> None:
    """C-1: the blast radius is the declared path list, nothing else."""
    drawing = workdir / "plan.dxf"
    neighbour = workdir / "user-notes.dxf"
    write_text(drawing, "original\n")
    write_text(neighbour, "user's other work\n")
    jdir = workdir / "journals"
    neighbour_before = sha(neighbour)

    txn = tx.begin(drawing, journal_dir=jdir)
    txn.apply(lambda t: write_text(drawing, "our recording\n"))
    txn.rollback()
    assert drawing.read_text(encoding="utf-8") == "original\n"
    assert sha(neighbour) == neighbour_before


def test_rollback_requires_a_captured_pre_state(workdir: Path) -> None:
    """C-7: there is no unguarded rollback; it must be inside a Txn state."""
    out = workdir / "plan.dxf"
    write_text(out, "x\n")
    jdir = workdir / "journals"
    txn = tx.Txn([out], journal_dir=jdir)
    with pytest.raises(tx.TransactionStateError):
        txn.rollback()
    with pytest.raises(tx.TransactionStateError):
        txn.commit()
    with pytest.raises(ValueError):
        tx.Txn([], journal_dir=jdir)


def test_no_rollback_path_uses_queued_input() -> None:
    """C-2: executable code must not contain a command string, Enter, or a CAD call.

    The scan is done on the AST and deliberately excludes docstrings: the
    module *explains* the C-2 finding in prose, and prose is not a code path.
    """
    source = Path(inspect.getsourcefile(tx)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    code_strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    code_text = "\n".join(code_strings)
    for forbidden in ("_.U", "^C", "SendStringToExecute", "win32com", "pyautogui"):
        assert forbidden not in code_text, (
            f"C-2 regression: {forbidden!r} appears in a runtime string literal"
        )
    # No attribute call of a CAD undo/global-command namespace either.
    attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    for forbidden in ("Undo", "SendStringToExecute", "StartUndoRecord", "EndUndoRecord"):
        assert forbidden not in attributes, (
            f"C-1/C-2 regression: {forbidden!r} is called somewhere in the module"
        )
    # The only side-effecting file primitives are an atomic copy/replace.
    assert "os.replace" in source and "shutil.copyfile" in source


def test_journal_is_written_before_any_write_and_records_hashes(workdir: Path) -> None:
    out = workdir / "plan.dxf"
    write_text(out, "original\n")
    jdir = workdir / "journals"
    pre_state = tx.capture_state(out)
    seen: list[dict] = []

    def record_then_write(_txn):
        payload = json.loads(txn.journal.read_text(encoding="utf-8"))
        seen.append(payload)
        write_text(out, "new\n")

    txn = tx.begin(out, journal_dir=jdir)
    txn.apply(record_then_write)
    assert seen[0]["state"] == "begun"
    before = seen[0]["targets"][0]["before"]
    assert before["existed"] is True
    assert before["sha256"] == pre_state.sha256
    after_state = tx.capture_state(out)
    applied = json.loads(txn.journal.read_text(encoding="utf-8"))["targets"][0]["after"]
    assert applied["sha256"] == after_state.sha256
    txn.commit()


def test_verifier_can_own_the_whole_drawing_outside_the_transaction(workdir: Path) -> None:
    """The transaction never rolls back on its own; rollback is explicit."""
    out = workdir / "plan.dxf"
    jdir = workdir / "journals"
    txn = tx.begin(out, journal_dir=jdir)
    txn.apply(lambda t: write_text(out, "bad\n"), verify=False)
    assert out.read_text(encoding="utf-8") == "bad\n"
    assert journals(jdir) != []  # still recoverable while uncommitted
    txn.rollback()
    assert not out.exists()
