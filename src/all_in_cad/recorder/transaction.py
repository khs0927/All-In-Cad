"""Transactional recording for the file-based (DXF) recorders.

=====================================================================
WHY THIS MODULE EXISTS: THE STRUCTURAL ANSWER TO C-1 / C-2 / C-3 /
C-4 / C-7 OF THE C# RUNNER REVIEW
=====================================================================
The C# runner (``native/zwcad2026/XicadRunner.cs``) had to undo its own work
inside a live CAD runtime, and the only undo it had was the *global* undo tab
(``_.U\\n`` or ``doc.Database.Undo()``). That produced five defects:

* **C-1** the unit of undo was "whatever the user's last operation was", not
  "this job";
* **C-2** the ``\\n`` in the undo string was a real Enter, so it could be
  re-executed at ``Command:`` or eaten as a coordinate;
* **C-3** success of the undo was reported without ever confirming it;
* **C-4** a false failure verdict (contaminated entity delta) triggered an undo
  that erased the *user's* newest work and left the job's own entities behind;
* **C-7** the rollback RPC was an unguarded global undo.

File-based recording removes the mechanism, not just the symptom. The
corresponding structural guards here are:

* **C-1** a unit of work is *one or more files*. Undo is "put those exact
  files back to the bytes we captured before this recording". There is no
  global undo stack, no "current command", no shared undo record: the blast
  radius is defined by a list of paths, so a user's unrelated edit can never be
  inside it.
* **C-2** nothing is typed. There is no command line, no Enter, no queued
  keystroke, no active prompt. Restore is a file byte copy, so the failure mode
  "the newline re-ran the last command" has no representation here.
* **C-3** :meth:`Txn.rollback` and :func:`recover_pending` never report success
  on the strength of "the copy call did not raise". Every restore is followed
  by a re-read of the file and a comparison of (existence, size, SHA-256)
  against the pre-recording capture. A mismatch raises
  :class:`RestoreUnverified` and the report says the restore is *unverified*.
* **C-4** the concurrency verdict is made on the *file hash* and on *the set of
  handles this transaction itself created* -- never on a global entity count.
  Before restoring, the on-disk state is compared against the state this
  transaction produced. If a third party touched the file, the restore is
  **aborted** with :class:`ExternalModificationError` and the third party's
  bytes are left alone. Failing is strictly better than deleting someone
  else's work.
* **C-7** rollback is not a free function that acts on "the document"; it is a
  method on one transaction object that (a) must have captured a pre-state,
  (b) must be in a state it is allowed to undo, and (c) passes the external
  modification guard. There is no unguarded entry point.

=====================================================================
JOURNAL / CRASH RECOVERY
=====================================================================
Before the first byte is written, the pre-state of every target is copied into
a journal directory and a JSON journal is written atomically (temp file +
``os.replace``). States are ``begun`` -> ``applied`` -> removed on commit. A
journal left behind in ``begun``/``applied`` is the residue of a process that
died mid-recording; :func:`recover_pending` walks the directory and restores
each such transaction to its captured pre-state, verifying every restore.

=====================================================================
ENVIRONMENT NOTE (observed on this host, verified by running it)
=====================================================================
See the note at the top of ``wall.py``: ``PYTHONHOME``/``PYTHONPATH`` injected
by the launcher must be cleared before running the venv interpreter::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \\
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\transaction_test.py -q
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # package-relative import (normal case)
    from ..semantic_layers import LayerSemantic, classify_layer
except ImportError:  # pragma: no cover - direct/flat execution fallback
    from all_in_cad.semantic_layers import LayerSemantic, classify_layer

__all__ = [
    "JOURNAL_DIRNAME",
    "JOURNAL_VERSION",
    "FileState",
    "JournalTarget",
    "RecoveryOutcome",
    "TransactionError",
    "TransactionStateError",
    "ExternalModificationError",
    "VerificationFailed",
    "RestoreUnverified",
    "Txn",
    "begin",
    "transaction",
    "capture_state",
    "content_matches",
    "readback_dxf",
    "dxf_verifier",
    "generic_verifier",
    "pending_journals",
    "recover_pending",
]

#: Directory (created next to the first target) holding journals and pre-images.
JOURNAL_DIRNAME = ".all_in_cad_txn"

#: Journal schema version. Bumping it makes older journals un-recoverable
#: rather than silently mis-restored, which is the fail-closed choice.
JOURNAL_VERSION = 1

_CHUNK = 1 << 20


# ======================================================================
# errors
# ======================================================================
class TransactionError(Exception):
    """Base class for every failure this module raises."""


class TransactionStateError(TransactionError):
    """An operation was requested in a state that does not allow it."""


class ExternalModificationError(TransactionError):
    """A third party changed a target file; our restore was aborted.

    Raised *instead of* restoring, so that somebody else's work is never
    overwritten. This is the C-4 guard.
    """

    def __init__(self, message: str, *, path: Path, expected: "FileState", found: "FileState") -> None:
        super().__init__(message)
        self.path = path
        self.expected = expected
        self.found = found


class VerificationFailed(TransactionError):
    """The recording was written but did not read back as specified."""

    def __init__(self, message: str, *, report: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = dict(report or {})


class RestoreUnverified(TransactionError):
    """A restore was attempted and could NOT be confirmed by hash comparison.

    The caller must not treat this as a successful rollback.
    """

    def __init__(self, message: str, *, report: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = dict(report or {})


# ======================================================================
# state capture
# ======================================================================
@dataclass(frozen=True, slots=True)
class FileState:
    """Everything needed to (a) detect interference and (b) prove a restore."""

    path: Path
    existed: bool
    size: int | None
    sha256: str | None
    mtime_ns: int | None = None

    def describe(self) -> str:
        if not self.existed:
            return f"{self.path} (absent)"
        return f"{self.path} size={self.size} sha256={self.sha256}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def capture_state(path: Path | str) -> FileState:
    """Snapshot existence/size/hash of ``path``.

    A missing file is a valid state (``existed=False``) and is how "this
    recording creates a new drawing" is represented.
    """
    resolved = Path(path)
    try:
        stat = resolved.stat()
    except FileNotFoundError:
        return FileState(resolved, False, None, None, None)
    return FileState(
        resolved,
        True,
        stat.st_size,
        _sha256_file(resolved),
        stat.st_mtime_ns,
    )


def content_matches(left: FileState, right: FileState) -> bool:
    """Compare the parts that prove a restore (mtime is deliberately ignored)."""
    if left.existed != right.existed:
        return False
    if not left.existed:
        return True
    return left.size == right.size and left.sha256 == right.sha256


# ======================================================================
# journal
# ======================================================================
@dataclass(slots=True)
class JournalTarget:
    path: Path
    before: FileState
    backup: Path | None
    after: FileState | None = None
    handles: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "before": {
                "existed": self.before.existed,
                "size": self.before.size,
                "sha256": self.before.sha256,
            },
            "backup": None if self.backup is None else self.backup.name,
            "after": None
            if self.after is None
            else {
                "existed": self.after.existed,
                "size": self.after.size,
                "sha256": self.after.sha256,
            },
            "handles": list(self.handles),
        }

    @staticmethod
    def from_json(data: Mapping[str, Any], journal_path: Path) -> "JournalTarget":
        before = data["before"]
        after = data.get("after")
        backup_name = data.get("backup")
        return JournalTarget(
            path=Path(data["path"]),
            before=FileState(
                Path(data["path"]),
                bool(before["existed"]),
                before.get("size"),
                before.get("sha256"),
            ),
            backup=None if backup_name is None else journal_path.parent / backup_name,
            after=None
            if after is None
            else FileState(
                Path(data["path"]),
                bool(after["existed"]),
                after.get("size"),
                after.get("sha256"),
            ),
            handles=tuple(str(item).upper() for item in data.get("handles", ())),
        )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    """Result of recovering one leftover journal."""

    journal: Path
    restored: tuple[str, ...]
    verified: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "journal": str(self.journal),
            "restored": list(self.restored),
            "verified": self.verified,
            "detail": self.detail,
        }


def pending_journals(journal_dir: Path | str) -> list[Path]:
    """All journals in ``journal_dir`` (committed ones are deleted, so any
    journal found here belongs to a transaction that did not finish)."""
    directory = Path(journal_dir)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))


def _restore_target(target: JournalTarget) -> None:
    """Put one file back to its captured pre-state (byte copy or unlink)."""
    if not target.before.existed:
        if target.path.exists():
            target.path.unlink()
        return
    if target.backup is None or not target.backup.is_file():
        raise RestoreUnverified(
            f"pre-image missing for {target.path}; cannot restore",
            report={"path": str(target.path), "backup": str(target.backup)},
        )
    target.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.path.with_name(f"{target.path.name}.{uuid.uuid4().hex}.restore")
    shutil.copyfile(target.backup, tmp)
    os.replace(tmp, target.path)


def _recover_journal(journal: Path) -> RecoveryOutcome:
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # unreadable journal: fail closed
        return RecoveryOutcome(journal, (), False, f"journal unreadable: {exc}")

    if int(payload.get("version", 0)) != JOURNAL_VERSION:
        return RecoveryOutcome(
            journal, (), False, f"unsupported journal version {payload.get('version')!r}"
        )
    if payload.get("state") not in ("begun", "applied"):
        return RecoveryOutcome(
            journal, (), False, f"unexpected journal state {payload.get('state')!r}"
        )

    restored: list[str] = []
    problems: list[str] = []
    for raw in payload.get("targets", []):
        target = JournalTarget.from_json(raw, journal)
        try:
            _restore_target(target)
        except (RestoreUnverified, OSError) as exc:
            problems.append(f"{target.path}: {exc}")
            continue
        found = capture_state(target.path)
        if not content_matches(found, target.before):
            problems.append(
                f"{target.path}: restore unverified "
                f"(expected sha256={target.before.sha256}, found {found.sha256})"
            )
            continue
        restored.append(str(target.path))

    verified = not problems
    # S8: the evidence is the whole point of a journal. Destroying it after a
    # FAILED restore made the second recover_pending() call report "nothing to
    # do" while the drawing was still corrupt and the pre-image was the only
    # thing that could have fixed it. On failure the journal and its backups
    # are KEPT, so a retry (or a human) still has the pre-image; the caller is
    # told the directory was left in place.
    if verified:
        for backup in journal.parent.glob(f"{journal.stem}.*.bak"):
            backup.unlink(missing_ok=True)
        journal.unlink(missing_ok=True)
    detail = "all targets restored and hash-verified" if verified else "; ".join(problems)
    if not verified:
        detail += (
            f" (journal {journal.name} and its backups were KEPT in "
            f"{journal.parent}: the restore did not verify, so the evidence "
            f"needed to retry was not destroyed)"
        )
    return RecoveryOutcome(journal, tuple(restored), verified, detail)


def recover_pending(journal_dir: Path | str) -> list[RecoveryOutcome]:
    """Restore every unfinished transaction found in ``journal_dir``.

    Call this at process start (or after a crash) to return the drawing files
    to their last successful state. Each outcome states whether the restore was
    *verified by hash*; ``verified=False`` means the restore is unconfirmed.
    """
    return [_recover_journal(item) for item in pending_journals(journal_dir)]


# ======================================================================
# verification
# ======================================================================
def readback_dxf(path: Path | str) -> dict[str, Any]:
    """Re-read a written DXF and report what is actually in the file.

    This is the "read it back" step: it does not trust the writer, it opens
    the file again and asks the reader what it sees.
    """
    import ezdxf  # noqa: PLC0415 - lazy: only needed for DXF targets

    document = ezdxf.readfile(str(path))
    modelspace = document.modelspace()
    handles: list[str] = []
    layers: list[str] = []
    endpoints: list[tuple[float, float]] = []
    types: dict[str, int] = {}
    for entity in modelspace:
        entity_handle = str(getattr(entity.dxf, "handle", "") or "").upper()
        if entity_handle:
            handles.append(entity_handle)
        layer = str(getattr(entity.dxf, "layer", "") or "")
        layers.append(layer)
        entity_type = entity.dxftype()
        types[entity_type] = types.get(entity_type, 0) + 1
        start = getattr(entity.dxf, "start", None)
        end = getattr(entity.dxf, "end", None)
        if start is not None:
            endpoints.append((float(start[0]), float(start[1])))
        if end is not None:
            endpoints.append((float(end[0]), float(end[1])))
    return {
        "dxfversion": str(document.dxfversion),
        "entity_count": len(handles),
        "handles": tuple(sorted(handles)),
        "layers": tuple(layers),
        "layer_counts": {
            name: layers.count(name) for name in sorted(set(layers))
        },
        "layer_semantics": {
            name: str(classify_layer(name)) for name in sorted(set(layers))
        },
        "entity_types": types,
        "endpoints": tuple(endpoints),
    }


def generic_verifier(
    path: Path, handles: Sequence[str] = ()
) -> dict[str, Any]:
    """Default check for a non-DXF target: the file must exist and be non-empty."""
    state = capture_state(path)
    if not state.existed:
        raise VerificationFailed(
            f"{path} was not created by the recording", report={"path": str(path)}
        )
    if not state.size:
        raise VerificationFailed(
            f"{path} is empty after the recording", report={"path": str(path)}
        )
    return {"path": str(path), "size": state.size, "sha256": state.sha256}


def dxf_verifier(
    *,
    expected_count: int | None = None,
    expected_handles: Iterable[str] = (),
    required_layers: Iterable[str] = (),
    layer_semantics: Mapping[str, LayerSemantic | str] | None = None,
    key_points: Mapping[str, tuple[float, float]] | None = None,
    tolerance: float = 1e-6,
) -> Callable[[Path, Sequence[str]], dict[str, Any]]:
    """Build a verifier that re-reads the DXF and checks the written content.

    ``expected_handles`` are the handles the writer returned. They are the
    *handles this recording created* -- not a global entity count -- which is
    what keeps the verdict immune to concurrent edits elsewhere in the file.
    """
    wanted_handles = tuple(item.upper() for item in expected_handles)
    required = tuple(required_layers)
    key = dict(key_points or {})

    def _verify(path: Path, handles: Sequence[str] = ()) -> dict[str, Any]:
        wanted = wanted_handles or tuple(item.upper() for item in handles)
        if path.suffix.lower() != ".dxf":
            return generic_verifier(path, handles)
        report = readback_dxf(path)
        report["path"] = str(path)
        # S5: the audited failure is that a verifier built with no criteria
        # accepts an empty file, reporting a pass having checked nothing.
        # Criteria may arrive at construction (expected_handles /
        # expected_count / required_layers / key points) or at CALL time via
        # `handles`; both count.
        #
        # It is LABELLED, not raised, and the scope is deliberate. Only the
        # caller knows whether an apply was supposed to create entities:
        # host_dxf's stage-record apply writes a state record to a sidecar and
        # legitimately leaves the DXF empty, so raising on "empty file" was
        # measured to be a false positive there (7 real tests). Raising on
        # "the caller passed an empty wanted set" would be a false positive
        # for the same reason. What can be removed without guessing is the
        # SILENCE: the report now always says whether any entity-level
        # assertion happened, so a vacuous pass can never be read as a real
        # verification. The CLI cannot reach this state at all -- every
        # subcommand passes required_layers=... derived from what it wrote.
        nothing_asserted = (
            not wanted and expected_count is None and not required and not key
        )
        if nothing_asserted:
            report["nothing_asserted"] = (
                "NO ENTITY-LEVEL ASSERTION WAS MADE: this verifier declared no "
                "handles, no expected count, no required layers and no key "
                "points. The file was readable, but nothing about its contents "
                "was verified. A pass from this verifier is not evidence."
            )
        found = set(report["handles"])
        missing = sorted(item for item in wanted if item not in found)
        if missing:
            raise VerificationFailed(
                f"{path}: {len(missing)} of {len(wanted)} recorded handles are absent "
                f"after read-back (first: {missing[0]})",
                report={**report, "missing_handles": missing},
            )
        if expected_count is not None and report["entity_count"] != expected_count:
            raise VerificationFailed(
                f"{path}: entity count {report['entity_count']} != expected {expected_count}",
                report=report,
            )
        absent_layers = [name for name in required if name not in report["layer_counts"]]
        if absent_layers:
            raise VerificationFailed(
                f"{path}: required layer(s) {absent_layers} missing after read-back",
                report={**report, "missing_layers": absent_layers},
            )
        for name, semantic in (layer_semantics or {}).items():
            actual = report["layer_semantics"].get(name)
            wanted_semantic = str(
                semantic.value if isinstance(semantic, LayerSemantic) else semantic
            )
            if actual != wanted_semantic:
                raise VerificationFailed(
                    f"{path}: layer {name} classifies as {actual!r}, expected "
                    f"{wanted_semantic!r}",
                    report=report,
                )
        unmatched = [
            label
            for label, (px, py) in key.items()
            if not any(
                abs(ex - px) <= tolerance and abs(ey - py) <= tolerance
                for ex, ey in report["endpoints"]
            )
        ]
        if unmatched:
            raise VerificationFailed(
                f"{path}: key coordinate(s) {unmatched} not found in the written file",
                report={**report, "unmatched_key_points": unmatched},
            )
        return report

    return _verify


# ======================================================================
# the transaction
# ======================================================================
class Txn:
    """An atomic recording over one or more files.

    Lifecycle: ``begin`` -> (``apply``)* -> ``commit`` | ``rollback``.
    Using it as a context manager performs the right one automatically.
    """

    def __init__(
        self,
        targets: Sequence[Path | str],
        *,
        journal_dir: Path | str | None = None,
        verifiers: Mapping[Path | str, Callable[..., Any]]
        | Callable[..., Any]
        | None = None,
        keep_journal_on_commit: bool = False,
    ) -> None:
        self.targets: tuple[JournalTarget, ...] = tuple(
            JournalTarget(path=Path(item), before=FileState(Path(item), False, None, None), backup=None)
            for item in targets
        )
        if not self.targets:
            raise ValueError("a transaction needs at least one target file")
        self._verifiers: dict[Path, Callable[..., Any]] = {}
        self._default_verifier: Callable[..., Any] | None = None
        if isinstance(verifiers, Mapping):
            self._verifiers = {
                Path(key).resolve(): value for key, value in verifiers.items()
            }
        elif verifiers is None or callable(verifiers):
            self._default_verifier = verifiers if callable(verifiers) else None
        else:
            raise TypeError("verifiers must be a callable, a mapping, or None")
        self.journal_dir = Path(
            journal_dir
            if journal_dir is not None
            else self.targets[0].path.parent / JOURNAL_DIRNAME
        )
        self.id = uuid.uuid4().hex
        self.journal = self.journal_dir / f"{self.id}.json"
        self.keep_journal_on_commit = bool(keep_journal_on_commit)
        self.state = "new"
        self.reports: dict[Path, Any] = {}
        self.rollback_report: list[dict[str, Any]] = []

    # -- introspection -------------------------------------------------
    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(item.path for item in self.targets)

    def target(self, path: Path | str) -> JournalTarget:
        resolved = Path(path)
        for item in self.targets:
            if item.path == resolved or item.path.resolve() == resolved.resolve():
                return item
        raise KeyError(f"{resolved} is not a target of this transaction")

    def _verifier_for(self, target: JournalTarget) -> Callable[..., Any]:
        found = self._verifiers.get(target.path.resolve())
        if found is not None:
            return found
        return self._default_verifier or generic_verifier

    # -- phase 1: capture ----------------------------------------------
    def capture(self) -> "Txn":
        """Copy every target's pre-state into the journal directory."""
        if self.state != "new":
            raise TransactionStateError(f"cannot capture in state {self.state!r}")
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        for index, target in enumerate(self.targets):
            before = capture_state(target.path)
            backup: Path | None = None
            if before.existed:
                backup = self.journal_dir / f"{self.id}.{index}.bak"
                shutil.copyfile(target.path, backup)
            target.before = before
            target.backup = backup
        self._write_journal("begun")
        self.state = "begun"
        return self

    def _write_journal(self, state: str) -> None:
        _atomic_write_json(
            self.journal,
            {
                "version": JOURNAL_VERSION,
                "txn_id": self.id,
                "state": state,
                "targets": [item.to_json() for item in self.targets],
            },
        )

    def note_handles(self, path: Path | str, handles: Iterable[str]) -> None:
        """Record the handles *this* recording created in ``path``.

        The verification verdict is based on these handles, so a concurrent
        edit elsewhere in the drawing cannot contaminate it (C-4).
        """
        self.target(path).handles = tuple(sorted({str(item).upper() for item in handles}))

    # -- phase 2: apply -------------------------------------------------
    def apply(self, fn: Callable[["Txn"], Any], *, verify: bool = True) -> Any:
        """Run ``fn`` (the recording) and then read the result back.

        If read-back verification fails the pre-state is restored before the
        error propagates, so a failed recording never leaves partial state.
        """
        if self.state not in ("begun", "applied"):
            raise TransactionStateError(f"cannot apply in state {self.state!r}")
        try:
            result = fn(self)
        except BaseException:
            # The recording may already be partly on disk. Snapshot exactly
            # what is there right now (that is *our* half-written state), then
            # restore, so a crashed recording never leaves partial output. The
            # original error propagates; if the restore itself fails, that
            # error propagates with the original as its context.
            for target in self.targets:
                target.after = capture_state(target.path)
            self._write_journal("applied")
            self.state = "applied"
            self.rollback()
            raise
        # S4: the hand-off used to be `if callable(handles)`, so a callback
        # that returned a plain LIST of handles -- the obvious way to write it
        # -- fell through the branch, left the wanted set empty, and the
        # verifier then passed vacuously. Returning handles you meant to be
        # checked, and having them ignored, is a silent success. Both shapes
        # are now accepted, and returning something that is neither is an
        # error rather than a shrug.
        handles = getattr(result, "handles", None)
        if callable(handles):
            handles = handles()
        elif handles is None and isinstance(result, (list, tuple, set, frozenset)):
            # A callback that just RETURNS the handles -- the plainest possible
            # spelling -- used to hit getattr(result, "handles") == None and
            # fall straight through, leaving the wanted set empty.
            handles = result
        if handles is not None and not isinstance(handles, (list, tuple, set, frozenset)):
            if isinstance(handles, str):
                raise TransactionError(
                    "the recording returned a bare string for 'handles'; "
                    "return a sequence of handle strings, or call "
                    "txn.note_handles() directly"
                )
            raise TransactionError(
                f"the recording returned {type(handles).__name__} for 'handles'; "
                "expected a callable returning handles, or a sequence of handle "
                "strings. Returning something unusable here used to be ignored, "
                "which emptied the verifier's wanted set and made the check pass "
                "without checking anything."
            )
        if handles and len(self.targets) == 1:
            self.note_handles(self.targets[0].path, handles)
        for target in self.targets:
            target.after = capture_state(target.path)
        self._write_journal("applied")
        self.state = "applied"
        if not verify:
            return result
        try:
            for target in self.targets:
                self.reports[target.path] = self._verifier_for(target)(
                    target.path, target.handles
                )
        except Exception as exc:
            # Verification failed: undo our own writing before reporting.
            try:
                self.rollback()
            except Exception as restore_exc:  # restore itself failed/locked out
                raise VerificationFailed(
                    f"{exc} (additionally: post-failure restore did not complete: "
                    f"{restore_exc})",
                    report=getattr(exc, "report", {}) or {},
                ) from restore_exc
            if isinstance(exc, TransactionError):
                raise
            raise VerificationFailed(str(exc)) from exc
        return result

    # -- phase 3: the two endings ---------------------------------------
    def commit(self) -> "Txn":
        """Accept the recording. Idempotent after a successful commit."""
        if self.state == "committed":
            return self
        if self.state not in ("begun", "applied"):
            raise TransactionStateError(f"cannot commit in state {self.state!r}")
        self.state = "committed"
        if not self.keep_journal_on_commit:
            self._discard_journal()
        return self

    def _discard_journal(self) -> None:
        self.journal.unlink(missing_ok=True)
        for backup in self.journal_dir.glob(f"{self.id}.*.bak"):
            backup.unlink(missing_ok=True)

    def rollback(self) -> "Txn":
        """Restore the captured pre-state, then PROVE it by hash comparison.

        Refuses to run (and touches nothing) if a third party modified a
        target after our apply: their bytes are worth more than our tidy state.
        """
        if self.state == "rolled_back":
            return self
        if self.state not in ("begun", "applied"):
            raise TransactionStateError(f"cannot roll back in state {self.state!r}")
        self.state = "rolling_back"
        report: list[dict[str, Any]] = []
        for target in self.targets:
            expected = target.after or target.before
            found = capture_state(target.path)
            if not content_matches(found, expected):
                # C-4 guard: someone else owns the current bytes.
                self.state = "externally_modified"
                raise ExternalModificationError(
                    f"{target.path} changed outside this transaction "
                    f"(expected sha256={expected.sha256}, found {found.sha256}); "
                    f"restore aborted to avoid deleting another writer's work",
                    path=target.path,
                    expected=expected,
                    found=found,
                )
        for target in self.targets:
            _restore_target(target)
            found = capture_state(target.path)
            verified = content_matches(found, target.before)
            report.append(
                {
                    "path": str(target.path),
                    "expected_sha256": target.before.sha256,
                    "actual_sha256": found.sha256,
                    "verified": verified,
                }
            )
        self.rollback_report = report
        if not all(item["verified"] for item in report):
            self.state = "restore_unverified"
            raise RestoreUnverified(
                "restore could not be confirmed by hash comparison: "
                + "; ".join(
                    f"{item['path']} expected {item['expected_sha256']} "
                    f"got {item['actual_sha256']}"
                    for item in report
                    if not item["verified"]
                ),
                report={"targets": report},
            )
        self.state = "rolled_back"
        self._discard_journal()
        return self

    # -- context manager -------------------------------------------------
    def __enter__(self) -> "Txn":
        if self.state == "new":
            self.capture()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.state in ("new", "begun", "applied"):
            if exc_type is None:
                self.commit()
            else:
                try:
                    self.rollback()
                except TransactionError as restore_exc:
                    # Never swallow the original error: attach and re-raise.
                    if exc is not None:
                        raise restore_exc from exc
                    raise
        return False


def begin(
    out_path: Path | str | Sequence[Path | str],
    *,
    journal_dir: Path | str | None = None,
    verifiers: Mapping[Path | str, Callable[..., Any]]
    | Callable[..., Any]
    | None = None,
    keep_journal_on_commit: bool = False,
) -> Txn:
    """Start a transaction and capture the pre-state immediately."""
    paths = (
        tuple(Path(item) for item in out_path)
        if isinstance(out_path, (list, tuple))
        else (Path(out_path),)
    )
    txn = Txn(
        paths,
        journal_dir=journal_dir,
        verifiers=verifiers,
        keep_journal_on_commit=keep_journal_on_commit,
    )
    return txn.capture()


def transaction(out_path: Any = None, **kwargs: Any) -> Txn:
    """Context-manager form: ``with transaction(path) as txn: ...``"""
    return begin(out_path, **kwargs)
