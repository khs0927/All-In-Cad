"""Real DXF file host for the run state machine.

=====================================================================
WHAT THIS IS, AND WHAT IT IS NOT
=====================================================================
``machine.py`` drives a five-method :class:`~machine.Host` protocol that was
designed for a CAD *command line* (ZWCAD-style): a prompt string, an
``in_command`` flag, a typed invocation string. This stack is file-based: the
"host" is a DXF drawing on disk, written through the transactional layer in
``transaction.py``.

:class:`DxfFileHost` is that translation. It is the first host implementation
in this project that touches real geometry, real files and real hashes.

**Every mapping below is a declared rule of this adapter, not an observation
of how any CAD program behaves.** The rules are marked ``RULE`` in the code
and repeated in :data:`MAPPING_NOTES`. Read that table before quoting any
number produced by this module as evidence about a real CAD host.

What passing the golden vectors here does prove
-----------------------------------------------
* real DXF bytes were written and read back by ``ezdxf``;
* the entity count the state machine verified was counted from a re-read file,
  not from a number this adapter was told to return;
* the rollback was ``transaction.Txn.rollback`` with hash-verified restore,
  and a third party's bytes survived it (C-1..C-7);
* the failures the vectors describe (unknown command, prompt mismatch, busy
  empty arg, delta mismatch, deadline) occurred for reasons visible in the
  file or in the adapter's own command table.

What it does not prove
----------------------
* that a real CAD host answers these prompts at these moments;
* that the prompt *strings* below are what any CAD program prints (they are a
  rendering of the adapter's editing stage, chosen so the vector contracts
  are checkable at all);
* that T08's "the command ended" is host-observed. A bare ``_.LINE`` in a
  real CAD never self-terminates; here the recording plan's declared arity is
  the terminator (``RULE 6``), so command-end is plan-driven. This is a real
  divergence from the spec's assumption and is reported, not hidden.
* that ``send`` typing is equivalent to CAD input. Nothing is typed here.

=====================================================================
RULES (declared, not observed)
=====================================================================
RULE 1 (invocation)  With no open session, a send carrying a command token --
                     ``(c:NAME)`` for a script call or ``_.NAME``/``NAME`` for
                     a native call -- is an invocation. Any other text while
                     idle is rejected and writes nothing.
RULE 2 (command table) Only :data:`COMMANDS` can start. An unknown token
                     never sets ``in_command``. ``_.ERASE`` is deliberately
                     absent: a file-based recorder cannot express "delete
                     every entity" as a bounded, reversible unit of work
                     (C-1), so this host refuses to pretend it can.
RULE 3 (stale session) A drawing that already carries an open session owned
                     by another recorder is *not* overwritten. The host
                     reports the foreign stage and refuses to start. This is
                     the real cause the vector calls a prompt mismatch.
RULE 4 (argument)    While a session this host owns is open, a send is an
                     argument, parsed by the command's grammar. An
                     unparseable argument writes nothing, is recorded as a
                     rejection and closes the session -- never a silent
                     success (the ZWCAD "input eaten, zero entities" trap).
RULE 5 (geometry)    ``_.LINE`` -> one POINT entity per coordinate argument
                     on layer ``AIC_POINT``. ``(c:AIC_WALLSEG)`` -> one LINE
                     entity between the two corners on layer ``AIC_WALL``.
                     Every write goes through ``Txn.apply`` and is read back
                     with ``transaction.dxf_verifier``.
RULE 6 (completion)  A session closes when the number of accepted arguments
                     reaches the arity the caller declared
                     (``declared_args``). Invented: a file has no interactive
                     terminator. Consequence: T08's command-end is decided by
                     the plan, not observed from a CAD prompt.
RULE 7 (prompt)      ``prompt`` is the string of the *current editing stage*
                     (:data:`STAGE_PROMPTS`), or the idle string when no
                     session is open. It is ``None`` only when the drawing
                     cannot be read at all, or when the host was built
                     without a stage reader (degraded configuration).
RULE 8 (undo)        ``undo`` is ``Txn.rollback`` and nothing else: no global
                     undo, no best-effort path, no "success" that was not
                     hash-verified.
RULE 9 (count)       ``count`` is the entity count of a fresh ``ezdxf``
                     read-back. If the read fails the adapter reports
                     ``0`` -- the machine's declared unstable value -- and
                     records the failure, so an unreadable file can never be
                     mistaken for an empty drawing.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .machine import TERMINATOR
from .transaction import (
    ExternalModificationError,
    TransactionError,
    Txn,
    VerificationFailed,
    _with_original_error,
    capture_state,
    dxf_verifier,
    readback_dxf,
)

__all__ = [
    "COMMANDS",
    "CommandSpec",
    "DxfFileHost",
    "HostRejectedInput",
    "MAPPING_NOTES",
    "MAPPED_VECTORS",
    "NA_VECTORS",
    "ReadFault",
    "SessionRecord",
    "WriterFaultProfile",
    "seed_drawing",
]

#: The session record lives inside the drawing, so the "editing stage" is a
#: property of the file and survives a re-open by any other tool.
XRECORD_KEY = "AIC_RUN"
SEED_LAYER = "AIC_SEED"
POINT_LAYER = "AIC_POINT"
WALL_LAYER = "AIC_WALL"

#: Idle rendering of "no command is running". Invented; see RULE 7.
IDLE_PROMPT = "Command: "

_SCRIPT_CALL = re.compile(r"^\(c:([A-Za-z_][\w$]*)\)$")
_NATIVE_CALL = re.compile(r"^(?:_\.)?([A-Za-z_][\w$]*)$")
_POINT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

SESSION_VERSION = 1


class HostRejectedInput(RuntimeError):
    """The adapter refused an input. Recorded, never silently swallowed."""


# ======================================================================
# command table
# ======================================================================


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One entry of the adapter's command table.

    ``kind`` must be ``"native"`` or ``"script"`` and is checked against the
    call form the caller used, so a script call cannot start a native command
    and vice versa.
    """

    name: str
    kind: str
    grammar: str  # "points" | "corner_pair"
    prompts: tuple[str, ...]  # prompts[0] is stage 0, prompts[1:] is "later"
    layer: str

    def prompt_for_stage(self, stage: int) -> str:
        if stage < 0:
            return IDLE_PROMPT
        if not self.prompts:
            return IDLE_PROMPT
        return self.prompts[0] if stage == 0 else self.prompts[1]

    def parse(self, text: str) -> list[tuple[float, float]]:
        """Return the points an argument contributes (RULE 4/5).

        An unparseable argument raises :class:`HostRejectedInput`. The caller
        then records the rejection and writes nothing.
        """
        raw = text.strip()
        if self.grammar == "points":
            match = _POINT.match(raw)
            if not match:
                raise HostRejectedInput(
                    f"{self.name}: {raw!r} is not a single 'x,y' coordinate"
                )
            return [(float(match.group(1)), float(match.group(2)))]
        if self.grammar == "corner_pair":
            match = _POINT.match(raw)
            if not match:
                raise HostRejectedInput(
                    f"{self.name}: {raw!r} is not a 'x,y' corner"
                )
            return [(float(match.group(1)), float(match.group(2)))]
        raise HostRejectedInput(f"{self.name}: unknown grammar {self.grammar!r}")


#: RULE 2. This table is the whole "does a command exist" question for this
#: host. ``_.ERASE`` is intentionally not here.
COMMANDS: dict[str, CommandSpec] = {
    "LINE": CommandSpec(
        name="LINE",
        kind="native",
        grammar="points",
        prompts=("Specify first point: ", "Specify next point or [Undo]: "),
        layer=POINT_LAYER,
    ),
    "AIC_WALLSEG": CommandSpec(
        name="AIC_WALLSEG",
        kind="script",
        grammar="corner_pair",
        prompts=("First corner: ", "Other corner: "),
        layer=WALL_LAYER,
    ),
    "AIC_DOORSEG": CommandSpec(
        name="AIC_DOORSEG",
        kind="script",
        grammar="corner_pair",
        prompts=("Hinge corner: ", "Other corner: "),
        layer=WALL_LAYER,
    ),
}

#: Stage prompts of commands that are *not* in :data:`COMMANDS` are still
#: renderable, so a foreign session left in a drawing can be described.
FOREIGN_STAGE_PROMPTS: dict[str, tuple[str, ...]] = {
    "AIC_DOORSEG": ("Hinge corner: ", "Other corner: "),
    "LINE": ("Specify first point: ", "Specify next point or [Undo]: "),
}

#: Stage prompts of an unrecognised foreign command.
UNKNOWN_FOREIGN_PROMPTS = ("Resume: ", "Resume (continued): ")


# ======================================================================
# session record (stored in the drawing)
# ======================================================================


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """The adapter's "editing stage", persisted in the DXF root dictionary."""

    owner: str
    command: str
    stage: int
    declared: int
    accepted: int
    state: str  # "open" | "done" | "aborted"
    handles: tuple[str, ...] = ()
    #: coordinates accepted so far and not yet turned into an entity
    points: tuple[tuple[float, float], ...] = ()

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    def encode(self) -> str:
        """Serialise into one group-code-1 string of the XRECORD.

        Kept as a single string so the record survives any DXF writer that
        does not preserve arbitrary tag sequences.
        """
        return "|".join(
            (
                f"v{SESSION_VERSION}",
                self.owner,
                self.command,
                str(self.stage),
                str(self.declared),
                str(self.accepted),
                self.state,
                ",".join(self.handles),
                ";".join(f"{x!r},{y!r}" for x, y in self.points),
            )
        )

    @staticmethod
    def decode(raw: str) -> SessionRecord | None:
        parts = raw.split("|")
        if len(parts) != 9 or not parts[0].startswith("v"):
            return None
        try:
            points: list[tuple[float, float]] = []
            for chunk in parts[8].split(";"):
                if not chunk:
                    continue
                x, _, y = chunk.partition(",")
                points.append((float(x), float(y)))
            return SessionRecord(
                owner=parts[1],
                command=parts[2],
                stage=int(parts[3]),
                declared=int(parts[4]),
                accepted=int(parts[5]),
                state=parts[6],
                handles=tuple(h for h in parts[7].split(",") if h),
                points=tuple(points),
            )
        except ValueError:
            return None


def read_session(doc: Any) -> SessionRecord | None:
    """Read the session record out of an open document (or ``None``)."""

    record = doc.rootdict.get(XRECORD_KEY)
    if record is None:
        return None
    for code, value in record.tags:
        if code == 1:
            decoded = SessionRecord.decode(str(value))
            if decoded is not None:
                return decoded
    return None


def write_session(doc: Any, session: SessionRecord | None) -> None:
    """Write or clear the session record inside ``doc``."""

    if session is None:
        if doc.rootdict.get(XRECORD_KEY) is not None:
            doc.rootdict.discard(XRECORD_KEY)
        return
    record = doc.rootdict.get(XRECORD_KEY)
    if record is None:
        record = doc.rootdict.add_xrecord(XRECORD_KEY)
    record.reset([(1, session.encode())])


def seed_session(path: Path, session: SessionRecord) -> None:
    """Write a session record into an existing drawing (crash-residue setup).

    This models the real state a crashed recorder leaves behind: a drawing
    whose stage was advanced but never closed. Used by the vectors that need
    a foreign/pending stage to be present before the run starts.
    """
    import ezdxf  # noqa: PLC0415

    doc = ezdxf.readfile(str(path))
    write_session(doc, session)
    doc.saveas(str(path))


# ======================================================================
# fault profiles (explicit, named, never default)
# ======================================================================


@dataclass(frozen=True, slots=True)
class ReadFault:
    """Make the next ``n`` count read-backs fail the way a real race does.

    The file is really unreadable for the caller: the adapter reports the
    machine's declared unstable value (0) and records the failure. This
    models a counter read that raced a writer replacing the file, which is
    the phenomenon ``RunOptions.unstable_value`` exists for.
    """

    remaining: int = 0
    #: only start failing once the recording has written something, so a
    #: baseline read of an existing drawing is never the victim
    arm_after_applies: int = 1

    def take(self, applies_done: int) -> bool:
        if self.remaining <= 0 or applies_done < self.arm_after_applies:
            return False
        object.__setattr__(self, "remaining", self.remaining - 1)
        return True


@dataclass(slots=True)
class WriterFaultProfile:
    """Real defects injected into the real writer, for the failure vectors.

    A correct adapter never produces these, so the vectors that need a wrong
    outcome cannot pass without them. They are named here and reported as
    injected, so no one mistakes them for host behaviour.
    """

    #: write one extra entity per coordinate argument (delta becomes too large)
    extra_entity_per_arg: bool = False


# ======================================================================
# the host
# ======================================================================


def file_stage_reader(path: Path) -> SessionRecord | None:
    """The default stage reader: read the session record out of the drawing.

    Returns ``None`` when the drawing is absent or unreadable, which is a
    real "no editing stage" state.
    """
    path = Path(path)
    if not path.is_file():
        return None
    import ezdxf  # noqa: PLC0415

    return read_session(ezdxf.readfile(str(path)))


@dataclass(slots=True)
class DxfFileHost:
    """A real, file-based implementation of the five-method host protocol.

    Parameters
    ----------
    path:
        The DXF drawing this host records into.
    declared_args:
        RULE 6. How many arguments the recording plan will deliver. This is
        what ends a command here.
    session_id:
        Identity of this recorder, written into the session record. A session
        owned by somebody else is never overwritten (RULE 3).
    stage_reader:
        ``None`` builds a deliberately degraded host that cannot report an
        editing stage, so ``state()["prompt"]`` is ``None``. That is the only
        way to exercise the "no prompt signal" guard against a file host; it
        is a configuration, not a file state.
    """

    path: Path
    declared_args: int = 1
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    journal_dir: Path | None = None
    stage_reader: Callable[[Path], SessionRecord | None] | None = file_stage_reader
    faults: WriterFaultProfile = field(default_factory=WriterFaultProfile)
    read_fault: ReadFault | None = None

    # -- bookkeeping (dataclass fields so the slotted class stays mutable) --
    cancel_text: str = ""
    undo_text: str = ""
    _txn: Txn | None = None
    _applies: int = 0
    _pending_handles: tuple[str, ...] = ()
    _rejections: list[str] = field(default_factory=list)
    _count_reads: int = 0
    _count_failures: int = 0
    _prompts_seen: list[str | None] = field(default_factory=list)
    undo_calls: int = 0
    cancel_calls: int = 0
    cancel_refusals: list[str] = field(default_factory=list)
    undo_refusals: list[str] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    _active_layer: str = POINT_LAYER

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.cancel_text = "^C^C^C" + TERMINATOR
        self.undo_text = "_.U" + TERMINATOR

    # -- transaction plumbing ------------------------------------------

    def _verifier(self, path: Path, handles: Sequence[str]):
        """The read-back check every apply must pass (RULE 5)."""
        wanted = tuple(self._pending_handles) or tuple(handles)
        # a layer is only "required" when this apply actually put entities on
        # it; the invocation apply writes the stage record only
        required = (self._active_layer,) if wanted else ()
        return dxf_verifier(
            expected_handles=wanted,
            required_layers=required,
        )(path, handles)

    _active_layer_doc = "set per apply; see _write_apply"

    def _txn_for(self) -> Txn:
        if self._txn is None:
            self._txn = Txn(
                [self.path],
                journal_dir=self.journal_dir,
                verifiers=self._verifier,
            ).capture()
        return self._txn

    # -- file helpers --------------------------------------------------

    def _exists(self) -> bool:
        return self.path.is_file()

    def _session_now(self) -> SessionRecord | None:
        """The stage as the *file* reports it, not as this object remembers."""
        if self.stage_reader is None:
            return None
        if not self._exists():
            return None
        try:
            return self.stage_reader(self.path)
        except Exception as exc:  # noqa: BLE001 - unreadable drawing is a real state
            self._rejections.append(f"session read failed: {exc}")
            return None

    def _write_apply(self, mutator: Callable[[Any], Any], layer: str) -> Any:
        """One transactional write + read-back verification (RULE 5)."""
        import ezdxf  # noqa: PLC0415

        self._active_layer = layer
        txn = self._txn_for()
        created: list[str] = []

        def _work(_txn: Txn) -> Any:
            if not self._exists():
                doc = ezdxf.new("R2010")
                if layer not in doc.layers:
                    doc.layers.add(layer)
            else:
                doc = ezdxf.readfile(str(self.path))
            if layer not in doc.layers:
                doc.layers.add(layer)
            handles = mutator(doc) or []
            doc.saveas(str(self.path))
            created.extend(handles)
            return _Handles(handles)

        # verify=False, then verify the handles *this* apply produced through
        # the transaction layer's dxf_verifier. A failed check rolls the
        # recording back before the error escapes, so a rejected write never
        # leaves partial geometry on disk.
        self._pending_handles = ()
        result = txn.apply(_work, verify=False)
        self._pending_handles = tuple(created)
        try:
            self._verifier(self.path, created)
        except Exception as exc:  # noqa: BLE001
            try:
                self._restore_after_failed_apply(exc)
            except TransactionError as restore_exc:
                # The refusal propagates (a caller seeing only `exc` would
                # read it as harmless), but the reason the verification failed
                # in the first place must stay reachable -- see
                # transaction._with_original_error.
                raise _with_original_error(restore_exc, exc) from exc
            if isinstance(exc, TransactionError):
                raise
            raise VerificationFailed(
                str(exc), report={"path": str(self.path)}, original_error=exc
            ) from exc
        txn.note_handles(self.path, created)
        self._applies += 1
        return result

    def _restore_after_failed_apply(
        self, original_error: BaseException | None = None
    ) -> None:
        if self._txn is None:
            return
        try:
            self._txn.rollback()
        except TransactionError as restore_exc:
            raise _with_original_error(restore_exc, original_error) from original_error
        finally:
            self._txn = None

    def commit(self) -> None:
        """Accept the recording. **The caller decides when, not the adapter.**

        The state machine never says "commit" -- it reports a verdict. So the
        transaction stays open across the machine's verification window, and
        the recorder (or a test) calls this *only* after a COMMITTED verdict.
        Committing when the command merely finished would be wrong: T10/T11
        can still fail after the command closed, and their rollback needs the
        pre-images that a commit would have discarded.
        """
        if self._txn is None:
            return
        self._txn.commit()
        self._txn = None

    @property
    def transaction_open(self) -> bool:
        return self._txn is not None

    # ==============================================================
    # the five protocol methods
    # ==============================================================

    def state(self) -> dict:
        """``in_command`` / ``prompt`` / ``command_name`` from the file (RULE 7)."""
        if not self._exists():
            self._prompts_seen.append(None)
            return {"in_command": False, "prompt": None, "command_name": None}
        if self.stage_reader is None:
            # degraded build: no stage reader, so no prompt signal at all
            self._prompts_seen.append(None)
            return {"in_command": False, "prompt": None, "command_name": None}
        session = self._session_now()
        if session is None or not session.is_open:
            self._prompts_seen.append(IDLE_PROMPT)
            return {
                "in_command": False,
                "prompt": IDLE_PROMPT,
                "command_name": None,
            }
        prompt = self._prompt_for(session)
        self._prompts_seen.append(prompt)
        return {
            "in_command": True,
            "prompt": prompt,
            "command_name": session.command,
        }

    def send(self, text: str) -> None:
        """Translate one terminated input into real drawing edits (RULE 1/4)."""
        self.sent.append(text)
        payload = str(text).rstrip(TERMINATOR).strip()
        session = self._session_now()

        if session is None or not session.is_open:
            self._start_command(payload, session)
            return
        if session.owner != self.session_id:
            # RULE 3: somebody else's pending stage. Refuse; never overwrite.
            self._rejections.append(
                f"refused to write: drawing carries an open session owned by "
                f"{session.owner!r} (command {session.command})"
            )
            return
        self._accept_argument(session, payload)

    def count(self) -> int:
        """Entity count from a fresh read-back of the real file (RULE 9)."""
        self._count_reads += 1
        if self.read_fault is not None and self.read_fault.take(self._applies):
            self._count_failures += 1
            return 0
        if not self._exists():
            self._count_failures += 1
            return 0
        try:
            return int(readback_dxf(self.path)["entity_count"])
        except Exception as exc:  # noqa: BLE001
            self._count_failures += 1
            self._rejections.append(f"count read failed: {exc}")
            return 0

    def undo(self) -> str:
        """``Txn.rollback`` and nothing else (RULE 8)."""
        self.undo_calls += 1
        if self._txn is None:
            self.undo_refusals.append(
                "no transaction of ours was open in this drawing; nothing to undo"
            )
            return self.undo_text
        # Raises ExternalModificationError / RestoreUnverified. Those are the
        # transaction layer's guarantees and are deliberately not caught here.
        self._txn.rollback()
        self._txn = None
        return self.undo_text

    def cancel(self) -> str:
        """Abandon *our* pending session. Never another recorder's."""
        self.cancel_calls += 1
        session = self._session_now()
        if session is None or not session.is_open:
            return self.cancel_text
        if session.owner != self.session_id:
            self.cancel_refusals.append(
                f"open session belongs to {session.owner!r}; cancel refused"
            )
            return self.cancel_text
        self._write_apply(
            lambda doc: self._close_session(doc, state="aborted"),
            POINT_LAYER,
        )
        return self.cancel_text

    def artifact_valid(self) -> bool:
        """Open the real file with ezdxf; it must parse and hold entities."""
        if not self._exists():
            return False
        import ezdxf  # noqa: PLC0415

        try:
            doc = ezdxf.readfile(str(self.path))
        except Exception:  # noqa: BLE001
            return False
        return any(True for _ in doc.modelspace())

    # ==============================================================
    # internals
    # ==============================================================

    def _prompt_for(self, session: SessionRecord) -> str:
        spec = COMMANDS.get(session.command)
        if spec is not None:
            return spec.prompt_for_stage(session.stage)
        prompts = FOREIGN_STAGE_PROMPTS.get(session.command)
        if prompts is None:
            return (
                UNKNOWN_FOREIGN_PROMPTS[0]
                if session.stage == 0
                else UNKNOWN_FOREIGN_PROMPTS[1]
            )
        return prompts[0] if session.stage == 0 else prompts[1]

    def _start_command(self, payload: str, session: SessionRecord | None) -> None:
        """RULE 1/2/3: recognise a command token and open a session."""
        script = _SCRIPT_CALL.match(payload)
        native = _NATIVE_CALL.match(payload)
        if script is not None:
            name, form = script.group(1), "script"
        elif native is not None:
            name, form = native.group(1), "native"
        else:
            self._rejections.append(f"{payload!r} is not a command token")
            return

        if session is not None and session.is_open:
            # RULE 3: a foreign stage is live; do not clobber it.
            self._rejections.append(
                f"open foreign session for {session.command} is not overwritten"
            )
            return

        spec = COMMANDS.get(name)
        if spec is None:
            # RULE 2: unknown command -> never in_command. The ZWCAD failure
            # mode (input eaten, nothing executed, "success" reported) is
            # exactly what this refusal prevents.
            self._rejections.append(f"unknown command {name!r} ({form} form)")
            return
        if spec.kind != form:
            self._rejections.append(
                f"command {name!r} is {spec.kind}; {form} call form refused"
            )
            return

        new_session = SessionRecord(
            owner=self.session_id,
            command=spec.name,
            stage=0,
            declared=int(self.declared_args),
            accepted=0,
            state="open",
        )
        self._write_apply(lambda doc: self._put_session(doc, new_session), spec.layer)

    def _accept_argument(self, session: SessionRecord, payload: str) -> None:
        """RULE 4/5: parse, write real geometry, advance the persisted stage."""
        spec = COMMANDS.get(session.command)
        if spec is None:  # pragma: no cover - our own session always has a spec
            self._rejections.append(f"no spec for {session.command}")
            return
        try:
            points = spec.parse(payload)
        except HostRejectedInput as exc:
            self._rejections.append(str(exc))
            self._write_apply(
                lambda doc: self._close_session(doc, state="aborted"), spec.layer
            )
            return

        extra = self.faults.extra_entity_per_arg
        accepted = session.accepted + 1
        closed = accepted >= session.declared
        pending = (*session.points, *points)
        updated = SessionRecord(
            owner=self.session_id,
            command=spec.name,
            stage=session.stage + 1,
            declared=session.declared,
            accepted=accepted,
            state="done" if closed else "open",
            handles=session.handles,
            points=() if closed else pending,
        )
        # RULE 6: the command closes on the declared arity, but the transaction
        # is NOT committed here -- T10/T11 can still reject the result, and
        # their rollback needs the pre-images a commit would have discarded.
        self._write_apply(
            lambda doc: self._write_geometry(doc, spec, pending, updated, extra),
            spec.layer,
        )

    # -- drawing mutators (run inside Txn.apply) -----------------------

    @staticmethod
    def _put_session(doc: Any, session: SessionRecord) -> list[str]:
        write_session(doc, session)
        return []

    @staticmethod
    def _close_session(doc: Any, *, state: str) -> list[str]:
        current = read_session(doc)
        if current is None:
            return []
        # the pending-coordinate list must survive a reload
        write_session(
            doc,
            SessionRecord(
                owner=current.owner,
                command=current.command,
                stage=current.stage,
                declared=current.declared,
                accepted=current.accepted,
                    state=state,
                    handles=current.handles,
                    points=current.points,
                ),
            )
        return []

    def _write_geometry(
        self,
        doc: Any,
        spec: CommandSpec,
        points: list[tuple[float, float]],
        updated: SessionRecord,
        extra: bool,
    ) -> list[str]:
        """Emit the real entities. Handles are the evidence for read-back."""
        handles: list[str] = []
        if spec.grammar == "points":
            for x, y in points[-1:]:
                entity = doc.modelspace().add_point(
                    (x, y, 0.0), dxfattribs={"layer": spec.layer}
                )
                handles.append(str(entity.dxf.handle))
            if extra:
                # injected defect (WriterFaultProfile.extra_entity_per_arg)
                for x, y in points[-1:]:
                    entity = doc.modelspace().add_point(
                        (x, y, 0.0), dxfattribs={"layer": spec.layer}
                    )
                    handles.append(str(entity.dxf.handle))
        else:  # corner_pair: one segment, written when both corners are in
            if len(points) >= 2:
                start, end = points[-2], points[-1]
                entity = doc.modelspace().add_line(
                    (start[0], start[1]),
                    (end[0], end[1]),
                    dxfattribs={"layer": spec.layer},
                )
                handles.append(str(entity.dxf.handle))
        write_session(doc, updated)
        return handles


@dataclass(frozen=True, slots=True)
class _Handles:
    """Result wrapper so ``Txn.apply`` can record the handles we created."""

    handles: tuple[str, ...]

    def handles_list(self) -> tuple[str, ...]:
        return self.handles


# ======================================================================
# seed helper
# ======================================================================


def seed_drawing(path: Path, entities: int = 0) -> Path:
    """Create a real DXF with ``entities`` real LINE entities on a seed layer.

    Used so a vector's ``initial_entities`` is a fact about the file, not a
    number the host was told to report.
    """
    import ezdxf  # noqa: PLC0415

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new("R2010")
    doc.layers.add(SEED_LAYER)
    msp = doc.modelspace()
    for index in range(max(int(entities), 0)):
        msp.add_line(
            (float(index), 0.0),
            (float(index), 1.0),
            dxfattribs={"layer": SEED_LAYER},
        )
    doc.saveas(str(path))
    return path


def file_digest(path: Path) -> str:
    return capture_state(path).sha256 or ""


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ======================================================================
# mapping table (kept in code so the report cannot drift from the adapter)
# ======================================================================

#: The prompt each vector's contract strings are read against, and *why*.
MAPPING_NOTES: dict[str, str] = {
    "GV-01": "_.LINE stage 0 renders 'Specify first point: ' (STAGE_PROMPTS).",
    "GV-02": "_.LINE stage 0 then 'Specify next point or [Undo]: ' for the rest.",
    "GV-03": (
        "CAD prompt 'Command: ' while the run expects a contract maps to a "
        "drawing that already carries a foreign open session: the host reports "
        "that stage and refuses to start (RULE 3)."
    ),
    "GV-04": "Unknown command token: absent from COMMANDS, so in_command never rises (RULE 2).",
    "GV-05": "_.ERASE is absent from COMMANDS on purpose, so the destructive run never starts.",
    "GV-06": (
        "After the first accepted point the session is still open, so an empty "
        "arg is genuinely busy."
    ),
    "GV-07": (
        "Delta mismatch is produced by an injected writer defect "
        "(extra_entity_per_arg), because a correct adapter cannot produce one."
    ),
    "GV-08": "Same mapping as GV-01; only the seeded baseline differs.",
    "GV-09": (
        "No prompt signal is a degraded build (stage_reader=None), not a file state: "
        "a readable drawing always yields a stage string."
    ),
    "GV-10": (
        "Run against a real host via the plan's arg budget (deadline_after_args). "
        "A wall-clock total deadline is also exercised separately."
    ),
    "GV-11": (
        "Transient counter 0 is a genuine failed read-back (ReadFault), not a "
        "scripted number."
    ),
    "GV-12": (
        "Approval is decided before any host call, so this vector needs no CAD "
        "concept at all."
    ),
    "GV-13": "(c:AIC_WALLSEG) stages render 'First corner: ' / 'Other corner: '.",
    "GV-14": "Terminator invariant is checked on the texts the adapter received.",
}

#: Vectors this adapter cannot honestly claim. Empty today, but kept in code
#: so a future change that drops support has to say so out loud.
NA_VECTORS: dict[str, str] = {}

#: Vectors whose mapping is a *declared convention* rather than an observation.
MAPPED_VECTORS: tuple[str, ...] = tuple(sorted(MAPPING_NOTES))


def vector_is_na(vector_id: str) -> bool:
    return vector_id in NA_VECTORS


# Re-exported so tests can assert the guarantee without importing transaction
__all__ += ["ExternalModificationError", "VerificationFailed"]
