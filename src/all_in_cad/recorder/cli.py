"""Natural-command CLI for the wall + door recorders.

This module is a thin user-facing shell. It contains **no geometry logic of
its own**: every shape is built by :func:`all_in_cad.recorder.wall.make_wall` /
:func:`all_in_cad.recorder.door.make_door` / ``make_door_centered`` and written by
``write_wall`` / ``write_door``, so the validation rules those modules already
enforce are the rules the CLI enforces. Nothing here silently corrects an
argument: an invalid value is rejected with a message and a non-zero exit code.

Subcommands
-----------
``wall``    one straight wall from a centreline
``door``    one plan-view door symbol, from a hinge point or a centre point
``plan``    wall + door into one drawing, with the wall centreline handed to the
            door agent as ``wall_segment`` so the hinge is aligned for real
``window``  one plan-view mullion window (LINE + ARC), from the indoor-side pick
            and the width-direction pick
``opening`` one wall opening boundary + symbol (LINE only), placed on a
            reference wall line
``hatch``   one hatch region, written as a closed boundary polyline plus the
            pattern contract as XDATA -- deliberately never a HATCH entity
``dim``     one dimension, measured and read back out of the file
``text``    one TEXT or MTEXT annotation
``block``   one block definition and one instance of it, in ``flatten``
            (default, stays measurable) or ``insert`` (a real INSERT
            reference, which hides its geometry from downstream measurement)
``layers``  LAYER table entries, in creation order, with asserted attributes
``verify``  read a drawing back and check the things that must still be true

Every subcommand requires ``--out`` (the DXF to write) except ``verify``, which
reads the drawing named by ``--in``. ``--json`` switches stdout to a single
machine-readable JSON object.

REACHABILITY
------------
All thirteen recorder modules are reachable from this CLI: ``wall``, ``door``,
``plan`` (wall+door), ``window``, ``opening``, ``hatch``, ``dim``, ``text``,
``block``, ``layers``; ``transaction`` and ``host_dxf`` run inside every write;
``machine`` is the input driver behind the host adapter; ``verify`` is this
module. ``verify`` has one ``--only`` scope per recording module and one
``--expect-*`` argument per module, so a recording made here can be verified
here -- a subcommand that cannot be verified is not finished.

INSERT IS ALLOWED, AND WHAT REPLACED THE BAN
--------------------------------------------
``FORBIDDEN_DXF_TYPES`` used to contain ``INSERT`` and no longer does, because
``block --mode insert`` writes one and a tool that rejects its own output is
not a verifier. ``HATCH`` stays forbidden, on different evidence: the hatch
recorder chose a polyline plus a contract precisely because nothing downstream
can measure a HATCH. An INSERT's cost is instead a named check,
``insert_downstream_visibility``, which reports the entity count and length the
INSERT hides and FAILS until the caller passes ``--allow-hidden-insert``. See
:data:`INSERT_VISIBILITY_NOTE`.

EXIT CODES (contract, also asserted by cli_test.py)
---------------------------------------------------
0  success -- and for ``verify`` this means every check PASSED
1  verification failed (``verify`` only): at least one check FAILED
2  usage / argument error: argparse failure, or a value the geometry agents
   reject (``WallValidationError`` / ``DoorGeometryError``), or a file that
   cannot be written or read

EXIT OK / 1 / 2 as above, plus:

3  the write itself failed: the transaction device rolled the change back
   (``VerificationFailed`` / ``RestoreUnverified``), or a third party modified
   the target while we were writing it (``ExternalModificationError``). The
   target is left as it was found, or owned by the other writer -- never in a
   half-written state of ours.

OUTPUT POLICY (added after a measured data-loss defect)
------------------------------------------------------
A failing ``verify`` must never be reported as a success. This repository has
already lost a drawing to exactly that confusion, so the non-zero exit is the
whole point of the subcommand.

The first version of this CLI called :func:`_new_document` for every
subcommand and then saved it to ``--out`` without ever reading the target. The
measured consequence: ``wall`` wrote 5 entities to ``x.dxf``, ``door`` was then
run on the SAME path, and the file ended up holding 6 door entities and no wall
-- exit code 0, JSON ``"ok": true``. Silent total loss of a drawing, reported as
success. Every module-level test was green while this was true, because the
defect lived in the composition layer, not in the wall or door agents.

So: **creating a drawing and replacing one are different contracts.**

* ``wall`` / ``door`` / ``plan`` / ``window`` / ``opening`` / ``hatch`` / ``dim`` /
  ``text`` / ``block`` / ``layers`` CREATE by default.
  If ``--out`` already exists, the command refuses with exit code 2 and does
  not touch the file.
* ``--force`` is the only way to replace an existing drawing, and the same flag
  means the same thing in every subcommand. It is one shared
  ``_add_out_arguments``, called by all ten writers rather than
  re-implemented per module: if one subcommand had its own idea of overwrite,
  the composition-level data-loss defect would simply move there.
* Nothing is ever appended. Appending would still be an unrequested mutation of
  an existing drawing, and it would produce a file whose contents no single
  invocation declared -- the entity counts and layer semantics the recorders
  guarantee are per-recording, not per-file.
* Overwriting goes through the existing transaction device in
  ``all_in_cad.recorder.transaction``: the pre-state is captured and backed up,
  the write is read back, and the verdict is scoped to the handles THIS
  recording created rather than a global count, so a concurrent edit elsewhere
  in the file cannot contaminate it. A failed verification is rolled back and
  the restore is proved by SHA-256; if somebody else changed the file meanwhile,
  the restore is refused and their bytes are kept.
* Argument validation happens BEFORE any of this: a rejected command never
  creates the journal, never touches the target, and leaves no file behind.
* KNOWN SIDE EFFECT: the transaction device keeps its journal directory
  (``.all_in_cad_txn``) next to the first target, so a successful write leaves
  that directory behind, empty. It is deliberately not moved to a temp
  location: recovery has to be able to find the journal next to the file it
  describes. Journal files and pre-image backups are discarded on commit.

IDEMPOTENCY (measured, documented, deliberately not "fixed")
-------------------------------------------------------------
Running ``plan`` twice with the same arguments produces 11 entities both times
and two DIFFERENT SHA-256 digests, because ezdxf stamps document metadata
(created/updated timestamps, fingerprint GUID) into every save. This CLI is
therefore **geometrically idempotent and NOT byte-idempotent**, and it does not
claim otherwise: each JSON result carries ``"byte_idempotent": false``. A second
run also now needs ``--force``, since the target exists. Use the ``verify``
subcommand (entity count, layers, coordinates) to compare two runs; do not
compare file hashes to decide whether a drawing is unchanged.

=====================================================================
ENVIRONMENT NOTE (observed on this host)
=====================================================================
Python is ``C:\\Users\\khs09\\all-in-cad\\.venv`` with ezdxf 1.4.4. Aside injects
``PYTHONHOME`` (pointing at its own bundled runtime) into the process
environment, which hides the venv interpreter's standard library and makes
``import ezdxf`` fail with ``ModuleNotFoundError: No module named
'annotationlib'``. Clear both variables before running anything here::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m \\
        all_in_cad.recorder.cli plan --start 0 0 --end 12000 0 \\
        --thickness 200 --center 6000 0 --width 900 --out plan.dxf

This is a host quirk of the launcher, not a defect in this module. Child
processes inherit it, so :mod:`cli_test` clears it for the subprocesses it runs
too.

DXF version: written as R2018 (AC1032) by the recorder modules, which
``write_wall`` enforces. This CLI never writes any other version.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

try:  # package-relative import (normal case)
    from ..semantic_layers import LayerSemantic, classify_layer
    from ..topology import Point2D
    from .block import (
        BlockInsertMode,
        BlockValidationError,
        arc_entity,
        line_entity,
        make_block_definition,
        make_block_insert,
        write_block_definition,
        write_block_insert,
    )
    from .dim import (
        DimGeometryError,
        DimKind,
        make_dim,
        ensure_dim,
        rendered_text as dim_rendered_text,
        write_dim,
    )
    from .dim import (
        DEFAULT_LAYERS as DEFAULT_DIM_LAYERS,
    )
    from .door import (
        DEFAULT_FRAME_WIDTH_MM,
        DoorGeometryError,
        DoorRecord,
        make_door,
        make_door_centered,
        write_door,
    )
    from .door import (
        DEFAULT_LAYERS as DEFAULT_DOOR_LAYERS,
    )
    from .door import (
        DEFAULT_THICKNESS_MM as DEFAULT_DOOR_THICKNESS_MM,
    )
    from .hatch import (
        HatchValidationError,
        make_hatch,
        read_hatch_metadata,
        write_hatch,
    )
    from .layer import (
        DestructiveLayerChange,
        LayerValidationError,
        make_layer,
        write_layers,
    )
    from .opening import (
        LAYER_MAPPING_RESOLVED as OPENING_LAYER_MAPPING_RESOLVED,
    )
    from .opening import (
        DEFAULT_LAYERS as DEFAULT_OPENING_LAYERS,
    )
    from .opening import (
        DEFAULT_OPENING_WIDTH_MM,
        DEFAULT_THICKNESS_MM as DEFAULT_OPENING_THICKNESS_MM,
        OpeningGeometryError,
        make_opening,
        write_opening,
    )
    from .transaction import (
        FileState,
        TransactionError,
        VerificationFailed,
        capture_state,
        dxf_verifier,
    )
    from .text import (
        TextValidationError,
        make_mtext,
        make_text,
        write_text,
    )
    from .transaction import (
        begin as begin_transaction,
    )
    from .wall import (
        DEFAULT_DOC_LAYER_CENTERS,
        DXF_WRITE_VERSION,
        WallValidationError,
        layer_plan,
        make_wall,
        write_wall,
    )
    from .window import (
        DEFAULT_DIVISIONS,
        DEFAULT_LAYERS as DEFAULT_WINDOW_LAYERS,
        DEFAULT_THICKNESS_MM as DEFAULT_WINDOW_THICKNESS_MM,
        DEFAULT_WINDOW_WIDTH_MM,
        WindowGeometryError,
        make_window,
        write_window,
    )
except ImportError:  # pragma: no cover - direct/flat execution fallback
    from all_in_cad.recorder.block import (  # type: ignore[no-redef]
        BlockInsertMode,
        BlockValidationError,
        arc_entity,
        line_entity,
        make_block_definition,
        make_block_insert,
        write_block_definition,
        write_block_insert,
    )
    from all_in_cad.recorder.dim import (  # type: ignore[no-redef]
        DimGeometryError,
        DimKind,
        make_dim,
        ensure_dim,
        rendered_text as dim_rendered_text,
        write_dim,
    )
    from all_in_cad.recorder.dim import (  # type: ignore[no-redef]
        DEFAULT_LAYERS as DEFAULT_DIM_LAYERS,
    )
    from all_in_cad.recorder.hatch import (  # type: ignore[no-redef]
        HatchValidationError,
        make_hatch,
        read_hatch_metadata,
        write_hatch,
    )
    from all_in_cad.recorder.layer import (  # type: ignore[no-redef]
        DestructiveLayerChange,
        LayerValidationError,
        make_layer,
        write_layers,
    )
    from all_in_cad.recorder.text import (  # type: ignore[no-redef]
        TextValidationError,
        make_mtext,
        make_text,
        write_text,
    )
    from all_in_cad.recorder.door import (  # type: ignore[no-redef]
        DEFAULT_FRAME_WIDTH_MM,
        DoorGeometryError,
        DoorRecord,
        make_door,
        make_door_centered,
        write_door,
    )
    from all_in_cad.recorder.door import (
        DEFAULT_LAYERS as DEFAULT_DOOR_LAYERS,
    )
    from all_in_cad.recorder.door import (
        DEFAULT_THICKNESS_MM as DEFAULT_DOOR_THICKNESS_MM,
    )
    from all_in_cad.recorder.opening import (  # type: ignore[no-redef]
        LAYER_MAPPING_RESOLVED as OPENING_LAYER_MAPPING_RESOLVED,
    )
    from all_in_cad.recorder.opening import (  # type: ignore[no-redef]
        DEFAULT_LAYERS as DEFAULT_OPENING_LAYERS,
    )
    from all_in_cad.recorder.opening import (  # type: ignore[no-redef]
        DEFAULT_OPENING_WIDTH_MM,
        DEFAULT_THICKNESS_MM as DEFAULT_OPENING_THICKNESS_MM,
        OpeningGeometryError,
        make_opening,
        write_opening,
    )
    from all_in_cad.recorder.transaction import (  # type: ignore[no-redef]
        FileState,
        TransactionError,
        VerificationFailed,
        capture_state,
        dxf_verifier,
    )
    from all_in_cad.recorder.transaction import (
        begin as begin_transaction,
    )
    from all_in_cad.recorder.wall import (  # type: ignore[no-redef]
        DEFAULT_DOC_LAYER_CENTERS,
        DXF_WRITE_VERSION,
        WallValidationError,
        layer_plan,
        make_wall,
        write_wall,
    )
    from all_in_cad.recorder.window import (  # type: ignore[no-redef]
        DEFAULT_DIVISIONS,
        DEFAULT_LAYERS as DEFAULT_WINDOW_LAYERS,
        DEFAULT_THICKNESS_MM as DEFAULT_WINDOW_THICKNESS_MM,
        DEFAULT_WINDOW_WIDTH_MM,
        WindowGeometryError,
        make_window,
        write_window,
    )
    from all_in_cad.semantic_layers import LayerSemantic, classify_layer
    from all_in_cad.topology import Point2D

__all__ = [
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VERIFY_FAILED",
    "EXIT_WRITE_FAILED",
    "BYTE_IDEMPOTENT",
    "OPENING_LAYER_MAPPING_NOTE",
    "FORBIDDEN_DXF_TYPES",
    "INSERT_VISIBILITY_NOTE",
    "UNRESOLVED_LAYER_NOTE",
    "CliError",
    "WriteError",
    "main",
    "verify_drawing",
]

EXIT_OK = 0
EXIT_VERIFY_FAILED = 1
EXIT_USAGE = 2
#: The recording was attempted and did not stand: read-back verification failed
#: and was rolled back, the restore could not be proven, or a third party owned
#: the target while we wrote it. Distinct from 1 (a drawing that was already on
#: disk does not verify) and from 2 (the request was never valid).
EXIT_WRITE_FAILED = 3

#: Recorded in every JSON result so no consumer mistakes a re-run for an
#: unchanged file. See the IDEMPOTENCY note in the module docstring: re-running
#: the same recording reproduces the same entities, but ezdxf stamps document
#: metadata into each save, so the bytes -- and the SHA-256 -- differ.
BYTE_IDEMPOTENT = False
BYTE_IDEMPOTENCY_NOTE = (
    "geometrically idempotent, not byte-idempotent: the same arguments "
    "reproduce the same entities, but ezdxf document metadata stamps make the "
    "file bytes and sha256 differ on every run. Compare drawings with the "
    "'verify' subcommand, not with file hashes."
)

#: Default hinge-to-centreline tolerance for ``plan``, in millimetres. The door
#: agent only *warns* when the hinge is off the wall; ``plan`` additionally
#: treats exceeding this tolerance as a failure, because a "door on a wall"
#: command that silently draws a detached door is how drawings get lost.
DEFAULT_HINGE_TOLERANCE_MM = 1.0

#: Entity types this recorder must never produce, and which ``verify`` rejects.
#:
#: [DESIGN] ``INSERT`` was on this list and has been REMOVED, deliberately and
#: with a reason. It was not decoration: ``block.make_block_insert(...,
#: mode=BlockInsertMode.INSERT)`` writes a real ``INSERT`` entity, so a
#: recording made by this repository was rejected by this repository's own
#: verifier -- the same class of divergence as the earlier ``"DXF only"``/DWG
#: incident. A tool that refuses its own output is not a verifier.
#:
#: ``HATCH`` STAYS, and the two are not the same case. The hatch module chose a
#: closed boundary polyline plus an XDATA pattern contract *because* this list
#: exists: ``extraction_runtime._normalize_ezdxf_entity`` has no contract for
#: a HATCH, so a HATCH here would be geometry nothing downstream can measure.
#: The INSERT case is the opposite -- the block module reports, measures and
#: records exactly what an INSERT hides (``visible_downstream``,
#: ``contributed_segments``, ``hidden_length_mm``), so its cost is knowable and
#: belongs in a CHECK, not in a ban. See :data:`INSERT_VISIBILITY_NOTE` and the
#: ``insert_downstream_visibility`` check.
FORBIDDEN_DXF_TYPES = ("HATCH",)

#: [DESIGN] How ``verify`` treats an ``INSERT`` now that one is allowed.
#:
#: An ``INSERT`` conceals its definition from every downstream measurement:
#: ``topology`` sees the reference, not the four lines inside it. That is a real
#: cost and must never be silent, so an ``INSERT`` in the drawing FAILS
#: ``insert_downstream_visibility`` unless the caller acknowledges it with
#: ``--allow-hidden-insert`` -- which is exactly the state ``block --mode
#: insert`` writes, and the report then carries the measured hidden segment
#: count and length rather than hiding them.
#:
#: A ban could not have said any of this: it could only have said "no", to a
#: recorder whose documented, opt-in job is to write one.
INSERT_VISIBILITY_NOTE = (
    "an INSERT hides its block definition from downstream measurement. This "
    "drawing contains one, which contributes 0 segments downstream. Pass "
    "--allow-hidden-insert to record that this was intended (it is what "
    "'block --mode insert' writes); the measured hidden segment count and "
    "length stay in the report either way."
)

#: [DESIGN] ``--unresolved-layer`` exists because two modules refuse to invent
#: a layer name: ``hatch.HATCH_LAYER_STATUS`` and ``text.TEXT_LAYER_STATUS`` are
#: both ``"UNRESOLVED"``, and ``block.OPENING_BLOCK_LAYER_STATUS`` with them.
#: Those recorders REQUIRE the caller to name the layer. How each one then
#: treats a name that classifies as ``UNKNOWN`` is NOT the same, so
#: ``--unresolved-layer`` means only what the two permissive modules mean by it:
#:   * ``hatch`` and ``text`` record the caller-named layer and report its
#:     ``LayerSemantic``, so an UNKNOWN name is a real, documented state there --
#:     exactly like the opening recorder's ``TEMP-`` layers. Declaring it is
#:     explicit, per-drawing and reported in ``layer_semantics_mapped``; an
#:     UNDECLARED unknown layer is still a FAIL.
#:   * ``block`` is the opposite: ``block._validate_layer`` REFUSES any name that
#:     ``classify_layer`` maps to UNKNOWN (the sole exception is the opt-in
#:     OBSERVED XiCAD spelling ``"0"`` behind ``allow_observed_layer_zero=True``).
#:     So a caller-named UNKNOWN layer is never a valid ``block --entity-layer``
#:     value, and ``CONVENTION_BLOCK_LAYERS`` there is a recommendation in the
#:     refusal message, not the membership test.
#: This string is surfaced to users verbatim in ``verify --json`` under
#: ``unresolved_layer_note``, so it must not claim block behaves like hatch/text.
UNRESOLVED_LAYER_NOTE = (
    "declared as an unresolved mapping on purpose (hatch and text report "
    "LAYER_STATUS == 'UNRESOLVED' and make the caller name the layer, which "
    "they then record and classify rather than refuse), so it is excluded from "
    "the layer_semantics_mapped check and reported there. Any unknown layer "
    "that is NOT declared is still a FAIL. Note this does not apply to block: "
    "block.OPENING_BLOCK_LAYER_STATUS is also 'UNRESOLVED', but block refuses "
    "any name that classifies as UNKNOWN, so block --entity-layer requires a "
    "convention name (CONVENTION_BLOCK_LAYERS is recommended, not enforced as "
    "a list)."
)

#: [UNRESOLVED] The opening recorder ships ``TEMP-`` prefixed layers because
#: configs/architectural-layers.json has no opening entry, and
#: ``all_in_cad.recorder.opening.LAYER_MAPPING_RESOLVED`` is False. Both default
#: layer names therefore resolve to ``LayerSemantic.UNKNOWN``. This is the
#: opening module's own documented, deliberate state -- not a broken drawing --
#: so it is surfaced as a WARN in ``verify`` (see OPENING_UNRESOLVED_LAYERS).
OPENING_LAYER_MAPPING_NOTE = (
    "unresolved layer mapping: the opening recorder's default layers carry the "
    "TEMP- marker and classify_layer() resolves them to UNKNOWN, because "
    "configs/architectural-layers.json has no opening entry "
    "(opening.LAYER_MAPPING_RESOLVED is False). 'verify' reports this as a "
    "warning and keeps checking the geometry; it deliberately does not fail, "
    "because a correctly recorded opening is not a defect, and it deliberately "
    "does not pass silently either."
)
OPENING_UNRESOLVED_LAYERS: frozenset[str] = frozenset(DEFAULT_OPENING_LAYERS)


class CliError(Exception):
    """A user-facing error: printed as ``error: ...`` and exits with code 2."""


class WriteError(CliError):
    """The recording was attempted and did not stand: exits with code 3.

    Distinct from :class:`CliError` (the request was never valid, nothing was
    touched) because here the transaction device DID open, write, read back and
    roll back. Reporting it as a usage error would tell the user their command
    line was wrong; reporting it as success would lose a drawing.
    """


# ---------------------------------------------------------------------------
# small geometry helpers (measurement only -- no shape is created here)
# ---------------------------------------------------------------------------


def _point(value: Sequence[float], name: str) -> Point2D:
    if len(value) != 2:
        raise CliError(f"{name} needs exactly two numbers: X Y")
    return Point2D(float(value[0]), float(value[1]))


def _require_ezdxf() -> Any:
    try:
        import ezdxf
    except ModuleNotFoundError as exc:  # pragma: no cover - env guard
        raise CliError(
            "ezdxf is required. On this host run the project venv with a cleared "
            "PYTHONHOME ($env:PYTHONHOME=$null; $env:PYTHONPATH=$null), otherwise "
            "the standard library is hidden and the import fails with "
            "'No module named annotationlib'."
        ) from exc
    return ezdxf


def _new_document() -> Any:
    ezdxf = _require_ezdxf()
    return ezdxf.new(DXF_WRITE_VERSION)  # R2018 / AC1032


def _resolve_output_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.exists() and not path.is_file():
        raise CliError(f"--out {path} exists and is not a regular file")
    return path


def _refuse_if_present(path: Path, force: bool, command: str) -> FileState:
    """Create-only by default: an existing drawing is never touched silently.

    Returns the captured pre-state so the caller can report what was there
    (nothing is written here -- this runs before the transaction opens).
    """
    before = capture_state(path)
    if before.existed and not force:
        raise CliError(
            f"refusing to overwrite an existing drawing: {path} "
            f"(size={before.size} sha256={before.sha256}). "
            f"'{command}' creates a new drawing and never appends to one. "
            f"Pass --force to replace the contents, or choose a different --out path."
        )
    return before


def _write_output(
    path: Path,
    *,
    before: FileState,
    save: Any,
    required_layers: Sequence[str],
) -> None:
    """Write ``save`` to ``path`` inside the repository's transaction device.

    ``save`` is a callable that writes the DXF and returns the handles it
    created. The transaction captures the pre-state (keeping a backup), writes,
    then reads the file back and verifies only the handles this recording
    created -- a global entity count would be contaminated by anything else
    that later appears in the same file. A failed verification is rolled back
    and the restore is proved by hash; if a third party changed the file while
    we were writing, the restore is refused and their bytes are kept.

    Every failure here is an error, never a silent success: the caller maps
    them to :data:`EXIT_WRITE_FAILED`.
    """

    def apply(txn: Any) -> None:
        handles = save(txn.targets[0].path)
        if handles:
            txn.note_handles(txn.targets[0].path, handles)

    try:
        with begin_transaction(
            path, verifiers=dxf_verifier(required_layers=required_layers)
        ) as txn:
            txn.apply(apply)
    except TransactionError as exc:
        raise WriteError(f"write failed and was rolled back: {exc}") from exc
    except OSError as exc:
        raise WriteError(f"cannot write {path}: {exc}") from exc
    # The transaction is committed, but prove the file is actually there rather
    # than trusting the absence of an exception.
    after = capture_state(path)
    if not after.existed:
        raise WriteError(f"write reported success but {path} does not exist")


def _print_written(
    command: str,
    path: Path,
    before: FileState,
    detail: str,
    entity_count: int,
    layer_counts: dict[str, int],
) -> None:
    if before.existed:
        print(f"replaced {path} with the {command} recording (previous sha256 {before.sha256})")
    else:
        print(f"wrote {path} ({command})")
    for line in detail.splitlines():
        print(line)
    print(f"  {entity_count} entities: {_format_counts(layer_counts)}")


def _output_note(args: argparse.Namespace, path: Path, before: FileState) -> dict[str, Any]:
    """Common JSON/text fields describing what happened to the target file."""
    return {
        "out": str(path),
        "overwrote_existing": before.existed,
        "replaced_sha256": before.sha256 if before.existed else None,
        "byte_idempotent": BYTE_IDEMPOTENT,
        "idempotency_note": BYTE_IDEMPOTENCY_NOTE if args.json else None,
    }


def _xy(value: Any) -> tuple[float, float]:
    return (float(value[0]), float(value[1]))


# ---------------------------------------------------------------------------
# shared argument parsing
# ---------------------------------------------------------------------------


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one machine-readable JSON object on stdout instead of text",
    )


def _add_out_arguments(parser: argparse.ArgumentParser) -> None:
    """--out plus the one flag that may replace an existing drawing.

    Deliberately shared by ``wall``, ``door``, ``plan``, ``window`` and
    ``opening``: if one subcommand had its own idea of overwrite, the
    composition-level data-loss defect would simply move there.
    """
    parser.add_argument(
        "-o",
        "--out",
        required=True,
        metavar="FILE.dxf",
        help=(
            "output DXF path (R2018 / AC1032). CREATE ONLY: if the path already "
            "exists the command refuses with exit code 2 and leaves the file "
            "untouched"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "replace an existing drawing at --out instead of refusing. The "
            "replacement goes through the transaction device: pre-state backup, "
            "read-back of the handles this recording created, and a "
            "hash-proven rollback if that read-back fails"
        ),
    )


def _add_wall_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--start",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        required=True,
        help="centreline start point, e.g. --start 0 0",
    )
    parser.add_argument(
        "--end",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        required=True,
        help="centreline end point, e.g. --end 12000 0",
    )
    parser.add_argument(
        "-t",
        "--thickness",
        type=float,
        required=True,
        metavar="MM",
        help="wall thickness in mm; must be > 0 (thickness 200 -> faces at Y=+/-100)",
    )
    parser.add_argument(
        "--cap-style",
        choices=("line", "miter", "none"),
        default="line",
        help="end cap style (default: line)",
    )
    parser.add_argument(
        "--axis",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "draw the wall centreline on the CEN1 layer. Default is on, because "
            "the measured reference case (centreline (0,0)->(12000,0), thickness "
            "200) is CEN1 1 + WAL1 2 + WAL2 2 = 5 entities; use --no-axis for 4"
        ),
    )
    parser.add_argument(
        "--face-layer",
        default=DEFAULT_DOC_LAYER_CENTERS[0],
        help=f"wall face layer (default: {DEFAULT_DOC_LAYER_CENTERS[0]})",
    )
    parser.add_argument(
        "--cap-layer",
        default=DEFAULT_DOC_LAYER_CENTERS[1],
        help=f"wall cap layer (default: {DEFAULT_DOC_LAYER_CENTERS[1]})",
    )
    parser.add_argument(
        "--detail-layer",
        default=DEFAULT_DOC_LAYER_CENTERS[2],
        help=f"wall detail layer (default: {DEFAULT_DOC_LAYER_CENTERS[2]})",
    )
    parser.add_argument(
        "--axis-layer",
        default=None,
        help="centreline layer (default: CEN1)",
    )


def _add_door_arguments(parser: argparse.ArgumentParser, *, include_out: bool) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--hinge",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        help="hinge point, e.g. --hinge 5550 0",
    )
    group.add_argument(
        "--center",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        help="opening centre point, e.g. --center 6000 0 (hinge = center -/+ width/2)",
    )
    parser.add_argument(
        "-w",
        "--width",
        type=float,
        required=True,
        metavar="MM",
        help="opening width in mm; must be > 0",
    )
    parser.add_argument(
        "--door-thickness",
        type=float,
        default=None,
        metavar="MM",
        help=(
            "door thickness in mm; must be > 0. In 'plan' the default is the WALL "
            f"thickness. Standalone default: {DEFAULT_DOOR_THICKNESS_MM:g}"
        ),
    )
    parser.add_argument(
        "--swing",
        type=float,
        default=90.0,
        metavar="DEG",
        help="opening angle in degrees; must satisfy 0 < swing < 360 (0 and 360 rejected)",
    )
    parser.add_argument(
        "--side",
        choices=("left", "right"),
        default="left",
        help="hinge side along the width axis (default: left)",
    )
    parser.add_argument(
        "--center-on-wall",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="straddle the wall centreline with the door thickness (default: enabled)",
    )
    parser.add_argument(
        "--axis-deg",
        type=float,
        default=0.0,
        metavar="DEG",
        help="width axis direction when no wall segment is given (default: 0 = +X)",
    )
    parser.add_argument(
        "--frame-width",
        type=float,
        default=DEFAULT_FRAME_WIDTH_MM,
        metavar="MM",
        help="door-frame face line offset; must be >= 0 (default: 0)",
    )
    parser.add_argument(
        "--door-layer",
        default=DEFAULT_DOOR_LAYERS[0],
        help=f"door layer (default: {DEFAULT_DOOR_LAYERS[0]})",
    )
    parser.add_argument(
        "--frame-layer",
        default=DEFAULT_DOOR_LAYERS[1],
        help=f"door frame layer (default: {DEFAULT_DOOR_LAYERS[1]})",
    )
    if include_out:
        _add_out_arguments(parser)


def _add_window_arguments(parser: argparse.ArgumentParser) -> None:
    """Window picks, in the order the [OBSERVED] prompt asks for them.

    ``--indoor`` is the '실내측 점' pick and ``--direction`` the
    '동일 선상의 창문폭 방향 점지정' pick, so the names mirror the module.
    """
    parser.add_argument(
        "--indoor",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        required=True,
        help="실내측 점: the indoor-side pick, e.g. --indoor 3000 0",
    )
    parser.add_argument(
        "--direction",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        required=True,
        help=(
            "동일 선상의 창문폭 방향 점지정: a second point on the same line that "
            "sets the width-axis direction, e.g. --direction 3000 1000"
        ),
    )
    parser.add_argument(
        "-w",
        "--width",
        type=float,
        default=DEFAULT_WINDOW_WIDTH_MM,
        metavar="MM",
        help=(
            "window width in mm; must be > 0 "
            f"(default: {DEFAULT_WINDOW_WIDTH_MM:g}, the [OBSERVED] prompt default)"
        ),
    )
    parser.add_argument(
        "--window-thickness",
        type=float,
        default=DEFAULT_WINDOW_THICKNESS_MM,
        metavar="MM",
        help=(
            "wall thickness across the window in mm; must be > 0 "
            f"(default: {DEFAULT_WINDOW_THICKNESS_MM:g})"
        ),
    )
    parser.add_argument(
        "--divisions",
        type=int,
        default=DEFAULT_DIVISIONS,
        metavar="N",
        help=(
            "number of window-bar divisions; must be an int >= 1. "
            f"[DESIGN, NOT OBSERVED] default: {DEFAULT_DIVISIONS}"
        ),
    )
    parser.add_argument(
        "--casement-deg",
        type=float,
        default=90.0,
        metavar="DEG",
        help="casement sweep in degrees; must satisfy 0 < casement-deg < 360",
    )
    parser.add_argument(
        "--interior-side",
        choices=("left", "right"),
        default=None,
        help=(
            "indoor side when there is no wall segment. Omit it and the window "
            "agent records a warning, because with neither a wall nor an "
            "explicit side the side is not observable"
        ),
    )
    parser.add_argument(
        "--wall-start",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        default=None,
        help=(
            "optional wall centreline start; supply BOTH --wall-start and "
            "--wall-end to have the indoor side MEASURED from the wall instead "
            "of assumed"
        ),
    )
    parser.add_argument(
        "--wall-end",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        default=None,
        help="optional wall centreline end (see --wall-start)",
    )
    parser.add_argument(
        "--window-layer",
        default=DEFAULT_WINDOW_LAYERS[0],
        help=f"window layer (default: {DEFAULT_WINDOW_LAYERS[0]})",
    )
    parser.add_argument(
        "--bar-layer",
        default=DEFAULT_WINDOW_LAYERS[1],
        help=f"window bar layer (default: {DEFAULT_WINDOW_LAYERS[1]})",
    )
    parser.add_argument(
        "--window-element-layer",
        default=DEFAULT_WINDOW_LAYERS[2],
        help=f"window element / interior-face layer (default: {DEFAULT_WINDOW_LAYERS[2]})",
    )


def _add_opening_arguments(parser: argparse.ArgumentParser) -> None:
    """Opening picks, in the order the [OBSERVED] prompt asks for them."""
    parser.add_argument(
        "--ref-start",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        required=True,
        help="'상대쪽 벽체 선' pick start, e.g. --ref-start 0 0",
    )
    parser.add_argument(
        "--ref-end",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        required=True,
        help="'상대쪽 벽체 선' pick end, e.g. --ref-end 12000 0",
    )
    parser.add_argument(
        "-w",
        "--width",
        type=float,
        default=DEFAULT_OPENING_WIDTH_MM,
        metavar="MM",
        help=(
            "개구부 폭 in mm; must be > 0 "
            f"(default: {DEFAULT_OPENING_WIDTH_MM:g}, [DESIGN, NOT OBSERVED])"
        ),
    )
    parser.add_argument(
        "--opening-thickness",
        type=float,
        default=DEFAULT_OPENING_THICKNESS_MM,
        metavar="MM",
        help=(
            "wall thickness the opening boundary crosses, in mm; must be > 0 "
            f"(default: {DEFAULT_OPENING_THICKNESS_MM:g})"
        ),
    )
    parser.add_argument(
        "--side",
        choices=("left", "right"),
        default="left",
        help="which way the symbol is drawn from the reference line (default: left)",
    )
    parser.add_argument(
        "--offset",
        type=float,
        default=0.0,
        metavar="MM",
        help=(
            "'시작점에서 개구부 띄울 거리': gap between the start point and the "
            "first opening edge; must be >= 0 (default: 0)"
        ),
    )
    parser.add_argument(
        "--start-point",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        default=None,
        help=(
            "where the opening starts along the reference line "
            "(default: --ref-start)"
        ),
    )
    parser.add_argument(
        "--direction",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        default=None,
        help=(
            "optional '동일 선상의 개구부폭 방향 점지정' pick. A point that is not "
            "collinear with the reference line is a warning, never a silent "
            "axis change"
        ),
    )
    parser.add_argument(
        "--collinear-tolerance",
        type=float,
        default=1.0,
        metavar="MM",
        help="how far --direction may sit off the reference line (default: 1.0)",
    )
    parser.add_argument(
        "--boundary-layer",
        default=DEFAULT_OPENING_LAYERS[0],
        help=f"opening boundary layer (default: {DEFAULT_OPENING_LAYERS[0]})",
    )
    parser.add_argument(
        "--symbol-layer",
        default=DEFAULT_OPENING_LAYERS[1],
        help=f"opening symbol layer (default: {DEFAULT_OPENING_LAYERS[1]})",
    )


# ---------------------------------------------------------------------------
# geometry construction (delegates validation to the wall / door agents)
# ---------------------------------------------------------------------------


def _add_hatch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--boundary",
        nargs="+",
        type=float,
        metavar="XY",
        required=True,
        help=(
            "boundary ring as flat X Y numbers, at least three pairs, e.g. "
            "--boundary 0 0 2000 0 2000 1000 0 1000. Implicitly closed: do NOT "
            "repeat the first point"
        ),
    )
    parser.add_argument(
        "--layer",
        required=True,
        metavar="NAME",
        help=(
            "layer for the boundary polyline. REQUIRED and undefaulted because "
            "no hatch layer is observed in configs/architectural-layers.json; "
            "the module refuses to invent one"
        ),
    )
    parser.add_argument(
        "--pattern-name",
        required=True,
        metavar="NAME",
        help=(
            "pattern contract name, e.g. ANSI31. REQUIRED: the hatch module's "
            "all-defaults call is an invalid state, so an unnamed pattern is "
            "refused rather than drawn blank"
        ),
    )
    parser.add_argument("--scale", type=float, default=1.0, metavar="F", help="pattern scale (default: 1.0)")
    parser.add_argument(
        "--angle-deg", type=float, default=0.0, metavar="DEG", help="pattern angle (default: 0)"
    )
    parser.add_argument("--name", default="", metavar="TEXT", help="optional hatch name (default: empty)")
    parser.add_argument(
        "--min-area",
        type=float,
        default=0.0,
        metavar="MM2",
        help="reject a boundary whose enclosed area is below this (default: 0)",
    )
    parser.add_argument(
        "--allow-self-intersection",
        action="store_true",
        help="accept a self-intersecting boundary instead of rejecting it",
    )


def _add_dim_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--p1", nargs=2, type=float, metavar=("X", "Y"), required=True,
        help="first measured point, e.g. --p1 0 0",
    )
    parser.add_argument(
        "--p2", nargs=2, type=float, metavar=("X", "Y"), required=True,
        help="second measured point; coincident with --p1 is rejected",
    )
    parser.add_argument(
        "--kind",
        choices=tuple(kind.value for kind in DimKind),
        default=DimKind.LINEAR_HORIZONTAL.value,
        help="dimension shape (default: linear_horizontal)",
    )
    parser.add_argument(
        "--offset-mm", type=float, default=800.0, metavar="MM",
        help="distance of the dimension line from the measured points (default: 800)",
    )
    parser.add_argument(
        "--text", default=None, metavar="TEXT",
        help=(
            "override the measured string. Omit it to let the recorder measure "
            "and inject; use '<>' to delegate formatting to the renderer"
        ),
    )
    parser.add_argument(
        "--decimals", type=int, default=2, metavar="N",
        help="decimals in the measured string (default: 2)",
    )
    parser.add_argument(
        "--layer", default=DEFAULT_DIM_LAYERS[0], metavar="NAME",
        help=(
            f"dimension layer (default: {DEFAULT_DIM_LAYERS[0]}). This one IS "
            "observed in configs/architectural-layers.json, which is why it has "
            "a default and hatch/text/block do not"
        ),
    )
    parser.add_argument(
        "--allow-duplicate",
        action="store_true",
        help=(
            "use the one-shot write path and place a second dimension on the "
            "same geometry. Default is the idempotent ensure path"
        ),
    )


def _add_text_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--content", required=True, metavar="TEXT",
        help="annotation text. For multi-line, use --mtext (a TEXT entity cannot hold a break)",
    )
    parser.add_argument(
        "--insert", nargs=2, type=float, metavar=("X", "Y"), required=True,
        help="insertion point, e.g. --insert 100 200",
    )
    parser.add_argument(
        "--layer", required=True, metavar="NAME",
        help=(
            "layer for the text entity. REQUIRED and undefaulted because no "
            "text layer is observed in configs/architectural-layers.json"
        ),
    )
    parser.add_argument("--height", type=float, default=2.5, metavar="MM", help="cap height (default: 2.5)")
    parser.add_argument(
        "--rotation-deg", type=float, default=0.0, metavar="DEG", help="rotation (default: 0)"
    )
    parser.add_argument("--style", default="Standard", metavar="NAME", help="text style (default: Standard)")
    parser.add_argument(
        "--mtext", action="store_true",
        help="write an MTEXT (multi-line capable) instead of a single-line TEXT",
    )
    parser.add_argument(
        "--width-mm", type=float, default=None, metavar="MM",
        help="MTEXT wrap column. Opt-in: a wrap column nobody asked for is a silent change",
    )
    parser.add_argument(
        "--attachment-point", type=int, default=1, metavar="N",
        help="MTEXT attachment point, 1-10 (default: 1)",
    )
    parser.add_argument("--halign", type=int, default=0, metavar="N", help="TEXT horizontal alignment (default: 0)")
    parser.add_argument("--valign", type=int, default=0, metavar="N", help="TEXT vertical alignment (default: 0)")
    parser.add_argument("--role", default="note", metavar="NAME", help="recorded role (default: note)")


def _add_block_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", required=True, metavar="NAME", help="block definition name")
    parser.add_argument(
        "--line",
        action="append",
        nargs=4,
        type=float,
        metavar=("X1", "Y1", "X2", "Y2"),
        default=None,
        help="straight edge in block-local coordinates; repeatable",
    )
    parser.add_argument(
        "--arc",
        action="append",
        nargs=5,
        type=float,
        metavar=("CX", "CY", "R", "A0", "A1"),
        default=None,
        help="arc edge, angles in degrees CCW; repeatable",
    )
    parser.add_argument(
        "--base", nargs=2, type=float, metavar=("X", "Y"), default=(0.0, 0.0),
        help="block base point (default: 0 0)",
    )
    parser.add_argument(
        "--entity-layer", required=True, metavar="NAME",
        help=(
            "layer for the block's own entities. REQUIRED and undefaulted: "
            "block.OPENING_BLOCK_LAYER_STATUS is UNRESOLVED, so the caller "
            "names the layer. Any name that classifies as UNKNOWN is refused, "
            "so pass a convention name; recommended: WAL1, WAL2, WAL3, DOOR, "
            "DOOR_ELE, WIN, WINBAR, WINELE (CONVENTION_BLOCK_LAYERS)"
        ),
    )
    parser.add_argument(
        "--location", nargs=2, type=float, metavar=("X", "Y"), default=(0.0, 0.0),
        help="where the instance is placed in world millimetres (default: 0 0)",
    )
    parser.add_argument(
        "--instance-layer", default=None, metavar="NAME",
        help="layer for the INSERT/flattened entities; derived from content when omitted",
    )
    parser.add_argument(
        "--rotation-deg", type=float, default=0.0, metavar="DEG", help="instance rotation (default: 0)"
    )
    parser.add_argument("--scale", type=float, default=1.0, metavar="F", help="instance scale (default: 1.0)")
    parser.add_argument(
        "--mode",
        choices=tuple(mode.value for mode in BlockInsertMode),
        default=BlockInsertMode.FLATTEN.value,
        help=(
            "flatten (default, everything stays measurable) or insert (a real "
            "INSERT reference, which contributes 0 segments downstream and "
            "needs 'verify --allow-hidden-insert' to pass)"
        ),
    )


def _add_layers_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--layer", action="append", default=None, metavar="NAME",
        help="layer to create, in this order; repeatable",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=None,
        metavar="NAME.KEY=VALUE",
        help=(
            "assert one layer attribute, e.g. --set WAL1.color=1. Keys: "
            + ", ".join(_HELP_SET_KEYS)
            + ". Repeatable; the layer is created if it is not there"
        ),
    )
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help=(
            "permit changing an existing layer's attributes. Default refuses "
            "with DestructiveLayerChange, because a layer change applies to a "
            "whole drawing"
        ),
    )
    parser.add_argument(
        "--no-protect-layer0",
        action="store_true",
        help=(
            "drop the extra guard on the layer literally named '0'. Default "
            "protects it even with --allow-overwrite"
        ),
    )


def _build_wall(args: argparse.Namespace) -> Any:
    start = _point(args.start, "--start")
    end = _point(args.end, "--end")
    try:
        return make_wall(
            start,
            end,
            args.thickness,
            layer_plan(
                (args.face_layer, args.cap_layer, args.detail_layer),
                axis_layer=args.axis_layer or "CEN1",
            ),
            include_axis=bool(args.axis),
            cap_style=args.cap_style,
        )
    except (WallValidationError, ValueError) as exc:
        raise CliError(f"wall rejected: {exc}") from exc


def _build_door(
    args: argparse.Namespace,
    *,
    thickness_mm: float,
    wall_segment: tuple[tuple[float, float], tuple[float, float]] | None = None,
    hinge_tolerance: float = 1.0,
) -> Any:
    if args.door_thickness is not None:
        thickness_mm = args.door_thickness
    # The rule belongs to the SUBCOMMAND, not to one branch of it. It used to
    # sit inside the `if args.hinge is not None:` block, so `plan --hinge
    # --axis-deg 45` was silently accepted (exit 0, the flag discarded) while
    # `plan --center --axis-deg 45` was refused (exit 2). Two different
    # strengths for one rule on the same command line is exactly how a user
    # comes to trust a flag that does nothing. Checked before either branch.
    if wall_segment is not None and args.axis_deg != 0.0:
        raise CliError(
            "--axis-deg cannot be combined with a wall segment in 'plan': "
            "the wall direction already defines the width axis"
        )
    try:
        if args.hinge is not None:
            return make_door(
                _point(args.hinge, "--hinge"),
                args.width,
                thickness_mm,
                args.swing,
                args.side,
                args.center_on_wall,
                width_axis_deg=args.axis_deg,
                wall_segment=wall_segment,
                hinge_tolerance=hinge_tolerance,
                frame_width_mm=args.frame_width,
            )
        return make_door_centered(
            args.center[0],
            args.center[1],
            args.width,
            thickness_mm,
            args.swing,
            args.side,
            args.center_on_wall,
            width_axis_deg=args.axis_deg,
            wall_segment=wall_segment,
            hinge_tolerance=hinge_tolerance,
            frame_width_mm=args.frame_width,
        )
    except DoorGeometryError as exc:
        raise CliError(f"door rejected: {exc}") from exc


def _door_layers(args: argparse.Namespace) -> tuple[str, str]:
    return (args.door_layer, args.frame_layer)


def _snapshot_layer_counts(record: Any) -> dict[str, int]:
    """Per-layer entity counts taken from the record's own readback.

    Typed loosely on purpose: the door, window and opening records all expose
    the same ``.snapshots`` / ``.handles()`` shape, and one implementation here
    is what keeps their reported counts consistent.
    """
    counts: dict[str, int] = {}
    for snapshot in record.snapshots:
        counts[snapshot.layer] = counts.get(snapshot.layer, 0) + 1
    return counts


def _door_summary(door: Any) -> dict[str, Any]:
    return {
        "center": [door.center.x, door.center.y],
        "hinge": [door.hinge.x, door.hinge.y],
        "latch": [door.latch.x, door.latch.y],
        "leaf_tip": [door.leaf_tip.x, door.leaf_tip.y],
        "width_mm": door.width_mm,
        "thickness_mm": door.thickness_mm,
        "swing_deg": door.swing_deg,
        "side": door.side,
        "center_on_wall": door.center_on_wall,
        "width_axis_deg": door.width_axis_deg,
        "warnings": list(door.warnings),
    }


def _measure_interior_side(
    indoor: Point2D,
    wall_segment: tuple[tuple[float, float], tuple[float, float]],
    direction: Point2D,
) -> str:
    """Which side of the wall the indoor pick sits on, using the same rule as
    ``window.make_window``: the sign of the indoor point's offset along the
    left-hand normal of the width axis."""
    dx = direction.x - indoor.x
    dy = direction.y - indoor.y
    length = math.hypot(dx, dy)
    if length == 0.0:
        return "left"
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    wx = wall_segment[1][0] - wall_segment[0][0]
    wy = wall_segment[1][1] - wall_segment[0][1]
    length_sq = wx * wx + wy * wy
    if length_sq == 0.0:
        return "left"
    t = ((indoor.x - wall_segment[0][0]) * wx + (indoor.y - wall_segment[0][1]) * wy) / length_sq
    cx = wall_segment[0][0] + t * wx
    cy = wall_segment[0][1] + t * wy
    return "left" if (indoor.x - cx) * nx + (indoor.y - cy) * ny >= 0 else "right"


def _build_window(args: argparse.Namespace) -> Any:
    """Validate and build the window, delegating every rule to the agent."""
    indoor = _point(args.indoor, "--indoor")
    direction = _point(args.direction, "--direction")
    if (args.wall_start is None) != (args.wall_end is None):
        raise CliError(
            "--wall-start and --wall-end go together: the indoor side can only be "
            "measured from a complete wall centreline"
        )
    wall_segment = None
    if args.wall_start is not None and args.wall_end is not None:
        wall_segment = (
            (_point(args.wall_start, "--wall-start").x,
             _point(args.wall_start, "--wall-start").y),
            (_point(args.wall_end, "--wall-end").x,
             _point(args.wall_end, "--wall-end").y),
        )
    # S7: make_window() lets a wall segment OVERRIDE an explicit
    # interior_side, silently: request 'right' plus a wall, and you get
    # 'left' with no warning. window.py is read-only for this task, so the
    # contradiction is caught here -- where both inputs are still in hand --
    # rather than being papered over by the caller.
    if wall_segment is not None and args.interior_side is not None:
        measured = _measure_interior_side(indoor, wall_segment, direction)
        if measured != args.interior_side:
            raise CliError(
                f"--interior-side {args.interior_side!r} contradicts the wall "
                f"given by --wall-start/--wall-end, which puts the indoor point "
                f"on the {measured!r} side. Passing both used to be accepted and "
                f"silently recorded as {measured!r}. Give the wall (it is the "
                f"measured truth) or drop --interior-side."
            )
    try:
        return make_window(
            indoor,
            direction,
            args.width,
            args.window_thickness,
            args.divisions,
            interior_side=args.interior_side,
            wall_segment=wall_segment,
            casement_deg=args.casement_deg,
        )
    except WindowGeometryError as exc:
        raise CliError(f"window rejected: {exc}") from exc


def _window_layers(args: argparse.Namespace) -> tuple[str, str, str]:
    return (args.window_layer, args.bar_layer, args.window_element_layer)


def _window_summary(window: Any, *, wall_given: bool, side_given: bool) -> dict[str, Any]:
    return {
        "center": [window.center.x, window.center.y],
        "jamb_start": [window.jamb_start.x, window.jamb_start.y],
        "jamb_end": [window.jamb_end.x, window.jamb_end.y],
        "width_mm": window.width_mm,
        "thickness_mm": window.thickness_mm,
        "divisions": window.divisions,
        "width_axis_deg": window.width_axis_deg,
        "interior_side": window.interior_side,
        "interior_side_source": (
            "measured from --wall-start/--wall-end"
            if wall_given
            else (
                "explicit --interior-side"
                if side_given
                else "assumed (neither a wall segment nor --interior-side)"
            )
        ),
        "warnings": list(window.warnings),
    }


def _build_opening(args: argparse.Namespace) -> Any:
    """Validate and build the opening, delegating every rule to the agent."""
    ref_start = _point(args.ref_start, "--ref-start")
    ref_end = _point(args.ref_end, "--ref-end")
    start_point = (
        _point(args.start_point, "--start-point")
        if args.start_point is not None
        else None
    )
    direction = (
        _point(args.direction, "--direction")
        if args.direction is not None
        else None
    )
    try:
        return make_opening(
            ((ref_start.x, ref_start.y), (ref_end.x, ref_end.y)),
            args.width,
            args.opening_thickness,
            start_point=(start_point.x, start_point.y) if start_point else None,
            direction_point=(direction.x, direction.y) if direction else None,
            side=args.side,
            offset_mm=args.offset,
            collinear_tolerance=args.collinear_tolerance,
        )
    except OpeningGeometryError as exc:
        raise CliError(f"opening rejected: {exc}") from exc


def _opening_layers(args: argparse.Namespace) -> tuple[str, str]:
    return (args.boundary_layer, args.symbol_layer)


def _opening_summary(opening: Any) -> dict[str, Any]:
    return {
        "reference_line": {
            "start": [opening.reference_start.x, opening.reference_start.y],
            "end": [opening.reference_end.x, opening.reference_end.y],
        },
        "origin": [opening.origin.x, opening.origin.y],
        "start_point": [opening.start_point.x, opening.start_point.y],
        "end_point": [opening.end_point.x, opening.end_point.y],
        "center": [opening.center.x, opening.center.y],
        "width_mm": opening.width_mm,
        "thickness_mm": opening.thickness_mm,
        "offset_mm": opening.offset_mm,
        "side": opening.side,
        "width_axis_deg": opening.width_axis_deg,
        "warnings": list(opening.warnings),
    }


# ---------------------------------------------------------------------------
# subcommand: wall
# ---------------------------------------------------------------------------


def cmd_wall(args: argparse.Namespace) -> int:
    # Geometry first: argument validation must complete before the output file
    # is even looked at, so a rejected command cannot disturb an existing one.
    wall = _build_wall(args)
    doc = _new_document()
    record = write_wall(doc, wall)
    path = _resolve_output_path(args.out)
    before = _refuse_if_present(path, args.force, "wall")

    def save(target: Path) -> tuple[str, ...]:
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(target)
        return record.handles()

    _write_output(
        path,
        before=before,
        save=save,
        required_layers=sorted(record.layer_counts()),
    )
    payload = {
        "command": "wall",
        "ok": True,
        **_output_note(args, path, before),
        "dxf_version": record.dxf_version,
        "centerline": {
            "start": [wall.centerline_start.x, wall.centerline_start.y],
            "end": [wall.centerline_end.x, wall.centerline_end.y],
        },
        "thickness_mm": record.thickness_mm,
        "length_mm": record.length,
        "half_thickness_mm": wall.half_thickness,
        "include_axis": wall.include_axis,
        "cap_style": wall.cap_style,
        "entity_count": record.entity_count,
        "layer_counts": record.layer_counts(),
        "handles": list(record.handles()),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_written("wall", path, before,
            f"wall {wall.length:g} mm long, {wall.thickness_mm:g} mm thick "
            f"(faces at +/-{wall.half_thickness:g} mm from the centreline)",
            record.entity_count, record.layer_counts(),
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# subcommand: door
# ---------------------------------------------------------------------------


def cmd_door(args: argparse.Namespace) -> int:
    door = _build_door(args, thickness_mm=DEFAULT_DOOR_THICKNESS_MM)
    doc = _new_document()
    record = write_door(doc, door, _door_layers(args))
    path = _resolve_output_path(args.out)
    before = _refuse_if_present(path, args.force, "door")

    def save(target: Path) -> tuple[str, ...]:
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(target)
        return record.handles()

    layer_counts = _snapshot_layer_counts(record)
    _write_output(
        path, before=before, save=save, required_layers=sorted(layer_counts)
    )
    payload = {
        "command": "door",
        "ok": True,
        **_output_note(args, path, before),
        "dxf_version": DXF_WRITE_VERSION,
        "layers": list(record.layers),
        "entity_count": len(record.handles()),
        "layer_counts": layer_counts,
        "handles": list(record.handles()),
        "door": _door_summary(door),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"  {len(record.handles())} entities: {_format_counts(layer_counts)}")
        for warning in door.warnings:
            print(f"  warning: {warning}")
        _print_written(
            "door",
            path,
            before,
            f"door width {door.width_mm:g} mm, thickness {door.thickness_mm:g} mm, "
            f"swing {door.swing_deg:g} deg, side {door.side}\n"
            f"  hinge ({door.hinge.x:g},{door.hinge.y:g}) -> latch "
            f"({door.latch.x:g},{door.latch.y:g}), centre "
            f"({door.center.x:g},{door.center.y:g})",
            len(record.handles()),
            layer_counts,
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# subcommand: plan  (wall + door in one drawing, aligned for real)
# ---------------------------------------------------------------------------


def cmd_plan(args: argparse.Namespace) -> int:
    wall = _build_wall(args)
    # The interaction that has to be real, not assumed: the wall centreline is
    # handed to the door agent as wall_segment, so the door width axis follows
    # the wall direction and the hinge is checked against the centreline.
    wall_segment = (
        (wall.centerline_start.x, wall.centerline_start.y),
        (wall.centerline_end.x, wall.centerline_end.y),
    )
    door = _build_door(
        args,
        thickness_mm=wall.thickness_mm,
        wall_segment=wall_segment,
        hinge_tolerance=args.hinge_tolerance,
    )
    hinge_offset = _point_segment_distance(
        door.hinge, Point2D(*wall_segment[0]), Point2D(*wall_segment[1])
    )
    if hinge_offset > args.hinge_tolerance:
        raise CliError(
            f"door hinge is {hinge_offset:.3f} mm from the wall centreline "
            f"(tolerance {args.hinge_tolerance:.3f} mm): refusing to record a door "
            "that is not on the wall; move --center/--hinge onto the wall or pass "
            "--hinge-tolerance explicitly"
        )

    doc = _new_document()
    wall_record = write_wall(doc, wall)
    door_record = write_door(doc, door, _door_layers(args))
    path = _resolve_output_path(args.out)
    before = _refuse_if_present(path, args.force, "plan")
    layer_counts = wall_record.layer_counts()
    for name, count in _snapshot_layer_counts(door_record).items():
        layer_counts[name] = layer_counts.get(name, 0) + count
    total = wall_record.entity_count + len(door_record.handles())
    handles = tuple(wall_record.handles()) + tuple(door_record.handles())

    def save(target: Path) -> tuple[str, ...]:
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(target)
        return handles

    _write_output(
        path, before=before, save=save, required_layers=sorted(layer_counts)
    )
    payload = {
        "command": "plan",
        "ok": True,
        **_output_note(args, path, before),
        "dxf_version": wall_record.dxf_version,
        "entity_count": total,
        "layer_counts": layer_counts,
        "wall": {
            "centerline": {
                "start": [wall.centerline_start.x, wall.centerline_start.y],
                "end": [wall.centerline_end.x, wall.centerline_end.y],
            },
            "thickness_mm": wall.thickness_mm,
            "length_mm": wall_record.length,
            "include_axis": wall.include_axis,
            "cap_style": wall.cap_style,
            "entity_count": wall_record.entity_count,
        },
        "door": _door_summary(door),
        "alignment": {
            "wall_segment_passed": True,
            "hinge_offset_from_wall_mm": hinge_offset,
            "hinge_tolerance_mm": args.hinge_tolerance,
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"  {total} entities: {_format_counts(layer_counts)}"
        )
        for warning in door.warnings:
            print(f"  warning: {warning}")
        _print_written(
            "plan",
            path,
            before,
            f"wall {wall_record.length:g} mm x {wall.thickness_mm:g} mm "
            f"({wall_record.entity_count} entities)\n"
            f"  door width {door.width_mm:g} mm on the wall centreline "
            f"(hinge offset {hinge_offset:.3f} mm, tolerance {args.hinge_tolerance:g} mm)",
            total,
            layer_counts,
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# subcommand: window
# ---------------------------------------------------------------------------


def cmd_window(args: argparse.Namespace) -> int:
    # Exactly the cmd_door shape: build first (so a rejected command never
    # touches the target), then resolve/refuse, then one transaction write.
    # The overwrite contract is shared, not re-implemented, so ``window``
    # cannot become the hole the composition-level data-loss defect moved into.
    window = _build_window(args)
    doc = _new_document()
    record = write_window(doc, window, _window_layers(args))
    path = _resolve_output_path(args.out)
    before = _refuse_if_present(path, args.force, "window")

    def save(target: Path) -> tuple[str, ...]:
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(target)
        return record.handles()

    layer_counts = _snapshot_layer_counts(record)
    _write_output(
        path, before=before, save=save, required_layers=sorted(layer_counts)
    )
    payload = {
        "command": "window",
        "ok": True,
        **_output_note(args, path, before),
        "dxf_version": DXF_WRITE_VERSION,
        "layers": list(record.layers),
        "entity_count": len(record.handles()),
        "layer_counts": layer_counts,
        "handles": list(record.handles()),
        "window": _window_summary(
            window,
            wall_given=args.wall_start is not None,
            side_given=args.interior_side is not None,
        ),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for warning in window.warnings:
            print(f"  warning: {warning}")
        _print_written(
            "window",
            path,
            before,
            f"window width {window.width_mm:g} mm, thickness "
            f"{window.thickness_mm:g} mm, {window.divisions} division(s)\n"
            f"  width axis {window.width_axis_deg:g} deg, indoor side "
            f"{window.interior_side}\n"
            f"  centre ({window.center.x:g},{window.center.y:g}), jambs "
            f"({window.jamb_start.x:g},{window.jamb_start.y:g}) -> "
            f"({window.jamb_end.x:g},{window.jamb_end.y:g})",
            len(record.handles()),
            layer_counts,
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# subcommand: opening
# ---------------------------------------------------------------------------


def cmd_opening(args: argparse.Namespace) -> int:
    opening = _build_opening(args)
    doc = _new_document()
    record = write_opening(doc, opening, _opening_layers(args))
    path = _resolve_output_path(args.out)
    before = _refuse_if_present(path, args.force, "opening")

    def save(target: Path) -> tuple[str, ...]:
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(target)
        return record.handles()

    layer_counts = _snapshot_layer_counts(record)
    _write_output(
        path, before=before, save=save, required_layers=sorted(layer_counts)
    )
    payload = {
        "command": "opening",
        "ok": True,
        **_output_note(args, path, before),
        "dxf_version": DXF_WRITE_VERSION,
        "layers": list(record.layers),
        "entity_count": len(record.handles()),
        "layer_counts": layer_counts,
        "handles": list(record.handles()),
        "opening": _opening_summary(opening),
        # Reported in the result, not hidden: the opening's default layers are
        # a documented FALLBACK, not a resolved mapping.
        "layer_mapping_resolved": OPENING_LAYER_MAPPING_RESOLVED,
        "layer_mapping_note": OPENING_LAYER_MAPPING_NOTE,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for warning in opening.warnings:
            print(f"  warning: {warning}")
        print(f"  warning: {OPENING_LAYER_MAPPING_NOTE}")
        _print_written(
            "opening",
            path,
            before,
            f"opening width {opening.width_mm:g} mm, thickness "
            f"{opening.thickness_mm:g} mm, side {opening.side}\n"
            f"  on the reference line, offset {opening.offset_mm:g} mm\n"
            f"  centre ({opening.center.x:g},{opening.center.y:g}), edges "
            f"({opening.start_point.x:g},{opening.start_point.y:g}) -> "
            f"({opening.end_point.x:g},{opening.end_point.y:g})",
            len(record.handles()),
            layer_counts,
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# subcommand: hatch
# ---------------------------------------------------------------------------


def _quote_argv(value: Any) -> str:
    """Quote one value for the printed ``verify_hint`` command line.

    POSIX single-quote rules, deliberately, because the hint has to survive
    being copied on Windows too: inside single quotes a backslash is literal,
    so a Windows path is not mangled, and an annotation with spaces or an
    apostrophe is still one argument. A hint that has to be edited before it
    runs is not a hint.
    """
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _verify_hint(path: Path, *parts: str) -> str:
    return " ".join(["verify", "--in", _quote_argv(path), *parts])


def _recorded_content(record: Any) -> tuple[tuple[str, ...], dict[str, int]]:
    """(modelspace handles, layer counts) from ANY recorder's record.

    Shared on purpose. The five modules added here return four different
    record shapes -- ``handles()`` + ``snapshots``, a single ``handle`` field,
    a ``layer_counts()`` method, and a table-order record with no entities at
    all -- and a per-command re-implementation is how one subcommand ends up
    reporting a count the others do not.
    """
    handles: tuple[str, ...]
    if hasattr(record, "handles"):
        handles = tuple(record.handles())
    elif getattr(record, "handle", None):
        handles = (str(record.handle),)
    else:
        handles = ()
    counts: dict[str, int] = {}
    layer_counts = getattr(record, "layer_counts", None)
    if callable(layer_counts):
        counts = {str(k): int(v) for k, v in layer_counts().items()}
    elif isinstance(layer_counts, dict):
        counts = {str(k): int(v) for k, v in layer_counts.items()}
    elif hasattr(record, "snapshots"):
        for snapshot in record.snapshots:
            counts[snapshot.layer] = counts.get(snapshot.layer, 0) + 1
    elif getattr(record, "layer", None):
        counts[str(record.layer)] = max(1, len(handles))
    return handles, counts


def _build_hatch(args: argparse.Namespace) -> Any:
    values = list(args.boundary)
    if len(values) < 6 or len(values) % 2:
        raise CliError(
            f"--boundary needs at least three X Y pairs, got {len(values)} numbers"
        )
    boundary = [(values[i], values[i + 1]) for i in range(0, len(values), 2)]
    try:
        return make_hatch(
            boundary,
            layer=args.layer,
            name=args.name,
            scale=args.scale,
            angle_deg=args.angle_deg,
            pattern_name=args.pattern_name,
            allow_self_intersection=args.allow_self_intersection,
            min_area_mm2=args.min_area,
        )
    except (HatchValidationError, ValueError) as exc:
        raise CliError(f"hatch rejected: {exc}") from exc


def cmd_hatch(args: argparse.Namespace) -> int:
    hatch = _build_hatch(args)
    doc = _new_document()
    record = write_hatch(doc, hatch)
    path = _resolve_output_path(args.out)
    before = _refuse_if_present(path, args.force, "hatch")

    def save(target: Path) -> tuple[str, ...]:
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(target)
        return record.handles()

    handles, layer_counts = _recorded_content(record)
    _write_output(path, before=before, save=save, required_layers=sorted(layer_counts))
    payload = {
        "command": "hatch",
        "ok": True,
        **_output_note(args, path, before),
        "dxf_version": DXF_WRITE_VERSION,
        "layers": [record.layer],
        "entity_count": len(handles),
        "layer_counts": layer_counts,
        "handles": list(handles),
        "hatch": {
            "area_mm2": record.area_mm2,
            "perimeter_mm": record.perimeter_mm,
            "pattern": record.pattern.name,
            "scale": record.pattern.scale,
            "angle_deg": record.pattern.angle_deg,
            "self_intersecting": record.self_intersecting,
            "vertices": len(hatch.boundary),
        },
        # No HATCH entity is written -- the pattern contract is XDATA on a
        # closed boundary polyline. Reported so nobody has to read the module
        # to learn it, and so a HATCH appearing in this file is unmistakably
        # not ours.
        "entity_representation": "closed LWPOLYLINE + XDATA pattern contract",
        "layer_status": "UNRESOLVED: --layer is required because no hatch layer "
        "is observed in configs/architectural-layers.json",
        # A hatch layer usually classifies as UNKNOWN, which layer_semantics_mapped
        # FAILs by default. The verification command for this exact drawing is
        # printed, with the declaration the user has to pass -- stated, not
        # implied.
        "verify_hint": _verify_hint(
            path,
            "--only",
            "hatch",
            "--unresolved-layer",
            _quote_argv(record.layer),
            "--expect-hatch-area",
            f"{record.area_mm2:g}",
            "--expect-hatch-pattern",
            _quote_argv(record.pattern.name),
        ),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_written(
            "hatch",
            path,
            before,
            f"hatch area {record.area_mm2:g} mm2, perimeter "
            f"{record.perimeter_mm:g} mm, {len(hatch.boundary)} vertices\n"
            f"  pattern {record.pattern.name} scale {record.pattern.scale:g} "
            f"angle {record.pattern.angle_deg:g} deg, on layer {record.layer}\n"
            f"  written as a closed LWPOLYLINE with the pattern contract as "
            f"XDATA (no HATCH entity)\n"
            f"  verify with: {payload['verify_hint']}",
            len(handles),
            layer_counts,
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# subcommand: dim
# ---------------------------------------------------------------------------


def _build_dim(args: argparse.Namespace) -> Any:
    try:
        return make_dim(
            _point(args.p1, "--p1"),
            _point(args.p2, "--p2"),
            args.kind,
            offset_mm=args.offset_mm,
            text=args.text,
            decimals=args.decimals,
            layer=args.layer,
        )
    except (DimGeometryError, ValueError) as exc:
        raise CliError(f"dimension rejected: {exc}") from exc


def cmd_dim(args: argparse.Namespace) -> int:
    dim = _build_dim(args)
    doc = _new_document()
    # ensure_dim is the idempotent path and what dim.py says callers normally
    # want; --allow-duplicate is the deliberate way to place a second
    # dimension on the same geometry. Reporting `action` keeps the difference
    # visible instead of leaving it to be guessed.
    if args.allow_duplicate:
        record = write_dim(doc, dim, (args.layer, args.layer))
    else:
        record = ensure_dim(doc, dim, (args.layer, args.layer))
    path = _resolve_output_path(args.out)
    before = _refuse_if_present(path, args.force, "dim")

    def save(target: Path) -> tuple[str, ...]:
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(target)
        return (record.handle,)

    _write_output(path, before=before, save=save, required_layers=[record.layer])
    payload = {
        "command": "dim",
        "ok": True,
        **_output_note(args, path, before),
        "dxf_version": DXF_WRITE_VERSION,
        "layers": [record.layer],
        "entity_count": 1,
        "layer_counts": {record.layer: 1},
        "handles": [record.handle],
        "dim": {
            "kind": dim.kind.value,
            "p1": [dim.p1.x, dim.p1.y],
            "p2": [dim.p2.x, dim.p2.y],
            "measurement_mm": record.readback_measurement,
            "geometry_measurement_mm": dim.measured,
            "text": record.readback_text,
            "text_mode": record.text_mode.value,
            "line_location": [dim.line_location.x, dim.line_location.y],
            "block_name": record.block_name,
            "action": record.action,
            "verified": record.verified,
        },
        "verify_hint": _verify_hint(
            path,
            "--only",
            "dim",
            "--expect-dim-measurement",
            f"{record.readback_measurement:g}",
        ),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_written(
            "dim",
            path,
            before,
            f"{dim.kind.value} dimension, {record.readback_measurement:g} mm, "
            f"text {record.readback_text!r} ({record.text_mode.value})\n"
            f"  {record.action} on layer {record.layer}, block {record.block_name}\n"
            f"  verify with: {payload['verify_hint']}",
            1,
            {record.layer: 1},
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# subcommand: text
# ---------------------------------------------------------------------------


def _build_text(args: argparse.Namespace) -> Any:
    builder = make_mtext if args.mtext else make_text
    try:
        if args.mtext:
            return builder(
                args.content,
                _point(args.insert, "--insert"),
                layer=args.layer,
                height=args.height,
                rotation_deg=args.rotation_deg,
                style=args.style,
                width_mm=args.width_mm,
                attachment_point=args.attachment_point,
                role=args.role,
            )
        return builder(
            args.content,
            _point(args.insert, "--insert"),
            layer=args.layer,
            height=args.height,
            rotation_deg=args.rotation_deg,
            style=args.style,
            halign=args.halign,
            valign=args.valign,
            role=args.role,
        )
    except (TextValidationError, ValueError) as exc:
        raise CliError(f"text rejected: {exc}") from exc


def cmd_text(args: argparse.Namespace) -> int:
    geometry = _build_text(args)
    doc = _new_document()
    record = write_text(doc, geometry)
    path = _resolve_output_path(args.out)
    before = _refuse_if_present(path, args.force, "text")

    def save(target: Path) -> tuple[str, ...]:
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(target)
        return record.handles()

    handles, layer_counts = _recorded_content(record)
    _write_output(path, before=before, save=save, required_layers=sorted(layer_counts))
    payload = {
        "command": "text",
        "ok": True,
        **_output_note(args, path, before),
        "dxf_version": DXF_WRITE_VERSION,
        "layers": [record.layer],
        "entity_count": len(handles),
        "layer_counts": layer_counts,
        "handles": list(handles),
        "text": {
            "kind": geometry.kind,
            "content": geometry.content,
            "lines": len(geometry.lines),
            "insert": [geometry.insert.x, geometry.insert.y],
            "height": geometry.height,
            "rotation_deg": geometry.rotation_deg,
            "style": geometry.style,
            "role": geometry.role,
        },
        "roundtrip": {
            "intact": record.roundtrip.intact,
            "read_back_content": record.roundtrip.read_back_content,
            "failures": record.roundtrip.failures(),
        },
        "layer_status": "UNRESOLVED: --layer is required because no text layer "
        "is observed in configs/architectural-layers.json",
        "verify_hint": _verify_hint(
            path,
            "--only",
            "text",
            "--unresolved-layer",
            _quote_argv(record.layer),
            "--expect-text-content",
            _quote_argv(geometry.content),
        ),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_written(
            "text",
            path,
            before,
            f"{geometry.kind} {geometry.content!r} at "
            f"({geometry.insert.x:g},{geometry.insert.y:g}), height "
            f"{geometry.height:g} mm, rotation {geometry.rotation_deg:g} deg\n"
            f"  style {geometry.style}, layer {record.layer}, roundtrip "
            f"{'intact' if record.roundtrip.intact else 'DAMAGED: ' + str(record.roundtrip.failures())}\n"
            f"  verify with: {payload['verify_hint']}",
            len(handles),
            layer_counts,
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# subcommand: block
# ---------------------------------------------------------------------------


def _build_block(args: argparse.Namespace) -> tuple[Any, Any]:
    if not args.line and not args.arc:
        raise CliError("a block needs content: pass at least one --line or --arc")
    entities: list[Any] = []
    try:
        for values in args.line or []:
            if len(values) != 4:
                raise CliError("--line needs exactly four numbers: X1 Y1 X2 Y2")
            entities.append(
                line_entity(
                    (values[0], values[1]), (values[2], values[3]), layer=args.entity_layer
                )
            )
        for values in args.arc or []:
            if len(values) != 5:
                raise CliError(
                    "--arc needs exactly five numbers: CX CY RADIUS START_DEG END_DEG"
                )
            entities.append(
                arc_entity(
                    (values[0], values[1]),
                    values[2],
                    values[3],
                    values[4],
                    layer=args.entity_layer,
                )
            )
        definition = make_block_definition(args.name, entities, base_point=_point(args.base, "--base"))
        insert = make_block_insert(
            definition,
            _point(args.location, "--location"),
            rotation_deg=args.rotation_deg,
            scale=args.scale,
            layer=args.instance_layer,
            mode=BlockInsertMode(args.mode),
        )
    except BlockValidationError as exc:
        raise CliError(f"block rejected: {exc}") from exc
    return definition, insert


def cmd_block(args: argparse.Namespace) -> int:
    definition, insert = _build_block(args)
    doc = _new_document()
    definition_record = write_block_definition(doc, definition)
    insert_record = write_block_insert(doc, insert)
    path = _resolve_output_path(args.out)
    before = _refuse_if_present(path, args.force, "block")

    def save(target: Path) -> tuple[str, ...]:
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(target)
        # Only the INSERT record's handles: the block DEFINITION's entities
        # live in the block table, not the modelspace, and the transaction
        # verifier looks for modelspace handles. Reporting them would make
        # every block write fail its own read-back.
        return insert_record.handles()

    handles, layer_counts = _recorded_content(insert_record)
    _write_output(path, before=before, save=save, required_layers=sorted(layer_counts))
    payload = {
        "command": "block",
        "ok": True,
        **_output_note(args, path, before),
        "dxf_version": DXF_WRITE_VERSION,
        "layers": sorted(set(layer_counts)),
        "entity_count": len(handles),
        "layer_counts": layer_counts,
        "handles": list(handles),
        "block": {
            "name": definition.name,
            "mode": insert_record.mode.value,
            "base_point": [definition.base_point.x, definition.base_point.y],
            "location": [insert.location.x, insert.location.y],
            "rotation_deg": insert.rotation_deg,
            "scale": insert.scale,
            "definition_entities": definition_record.entity_count,
            "definition_length_mm": definition_record.measured_length_mm,
            "insert_layer": insert_record.layer,
            "entity_layer": args.entity_layer,
        },
        # The downstream visibility contract, reported rather than assumed.
        "visible_downstream": insert_record.visible_downstream,
        "contributed_segments": insert_record.contributed_segments,
        "contributed_length_mm": insert_record.contributed_length_mm,
        "hidden_segments": insert_record.hidden_segments,
        "hidden_length_mm": insert_record.hidden_length_mm,
        "downstream_contract": insert_record.downstream_contract,
        "verify_hint": _verify_hint(
            path,
            "--only",
            "block",
            "--expect-block-name",
            _quote_argv(definition.name),
            *(["--allow-hidden-insert"] if insert_record.mode is BlockInsertMode.INSERT else []),
        ),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        lines = [
            f"block {definition.name!r}: {definition_record.entity_count} entities, "
            f"{definition_record.measured_length_mm:g} mm, mode {insert_record.mode.value}",
            f"  at ({insert.location.x:g},{insert.location.y:g}), rotation "
            f"{insert.rotation_deg:g} deg, scale {insert.scale:g}",
            f"  contributes {insert_record.contributed_segments} segment(s) / "
            f"{insert_record.contributed_length_mm:g} mm downstream; hides "
            f"{insert_record.hidden_segments} / {insert_record.hidden_length_mm:g} mm",
            f"  verify with: {payload['verify_hint']}",
        ]
        _print_written(
            "block", path, before, "\n".join(lines), len(handles), layer_counts
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# subcommand: layers
# ---------------------------------------------------------------------------

#: ``--set NAME.KEY=VALUE`` keys, mapped to the LayerSpec attribute they set and
#: how the string is converted. An unknown key is a usage error, never a
#: silently ignored typo -- a mistyped ``--set WAL1.colour=7`` that changes
#: nothing is exactly the silent-success shape this CLI exists to prevent.
_LAYER_SET_KEYS: dict[str, tuple[str, str]] = {
    "color": ("color", "color"),
    "linetype": ("linetype", "linetype"),
    "lineweight": ("lineweight", "int"),
    "plot": ("plot", "bool"),
    "on": ("on", "bool"),
    "locked": ("locked", "bool"),
    "frozen": ("frozen", "bool"),
    "description": ("description", "text"),
}

#: Keys advertised by ``layers --help``. ``layer.py`` rejects ``description``
#: outright (LayerSpec.__post_init__: "description is unsupported"), so the
#: CLI must not promise it -- but the routing entry above is kept so
#: ``--set NAME.description=...`` still fails through the single, explicit
#: layer.py rejection (exit 2) instead of being silently reinterpreted as an
#: unknown key. Advertising it while refusing it was the reported defect.
_HELP_SET_KEYS: tuple[str, ...] = tuple(
    sorted(key for key in _LAYER_SET_KEYS if key != "description")
)


def _build_layer_specs(args: argparse.Namespace) -> list[Any]:
    names: list[str] = list(args.layer or [])
    values: dict[str, dict[str, Any]] = {name: {} for name in names}
    for item in args.set or []:
        if "=" not in item:
            raise CliError(f"--set needs NAME.KEY=VALUE, got {item!r}")
        target, _, raw = item.partition("=")
        name, _, key = target.partition(".")
        if key not in _LAYER_SET_KEYS:
            raise CliError(
                f"--set key {key!r} is not one of "
                f"{sorted(_LAYER_SET_KEYS)}"
            )
        if not name:
            raise CliError(f"--set needs a layer name before the '.', got {item!r}")
        attribute, kind = _LAYER_SET_KEYS[key]
        if kind == "int":
            try:
                parsed: Any = int(raw)
            except ValueError as exc:
                raise CliError(f"--set {name}.{key}={raw!r} is not an integer") from exc
        elif kind == "bool":
            lowered = raw.strip().lower()
            if lowered not in ("true", "false", "on", "off", "1", "0", "yes", "no"):
                raise CliError(
                    f"--set {name}.{key}={raw!r} is not a boolean "
                    "(true/false, on/off, yes/no, 1/0)"
                )
            parsed = lowered in ("true", "on", "1", "yes")
        elif kind == "color":
            parsed = raw if not raw.lstrip("-").isdigit() else int(raw)
        else:
            parsed = raw
        if name not in values:
            values[name] = {}
        values[name][attribute] = parsed
    ordered = list(dict.fromkeys([*names, *values]))
    if not ordered:
        raise CliError(
            "layers needs at least one --layer NAME or --set NAME.KEY=VALUE"
        )
    try:
        return [make_layer(name, **values.get(name, {})) for name in ordered]
    except LayerValidationError as exc:
        raise CliError(f"layer rejected: {exc}") from exc


def _assert_layers_written(target: Path, names: Sequence[str]) -> None:
    """Prove the LAYER table in ``target`` really holds every requested name.

    Raises :class:`VerificationFailed` (a ``TransactionError``), so the
    enclosing transaction rolls the write back and ``main`` reports exit 3.
    """
    ezdxf = _require_ezdxf()
    try:
        written = ezdxf.readfile(target)
    except (OSError, ezdxf.DXFError) as exc:  # pragma: no cover - unreadable output
        raise VerificationFailed(f"{target}: cannot be read back: {exc}") from exc
    present = {str(entry.dxf.name).upper() for entry in written.layers}
    missing = [name for name in names if name.upper() not in present]
    if missing:
        raise VerificationFailed(
            f"{target}: {len(missing)} of {len(names)} requested layers are absent "
            f"from the LAYER table after read-back: {missing}"
        )


def cmd_layers(args: argparse.Namespace) -> int:
    specs = _build_layer_specs(args)
    doc = _new_document()
    order = write_layers(
        doc,
        specs,
        allow_overwrite=args.allow_overwrite,
        protect_layer0=not args.no_protect_layer0,
    )
    path = _resolve_output_path(args.out)
    before = _refuse_if_present(path, args.force, "layers")

    def save(target: Path) -> tuple[str, ...]:
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(target)
        # A layers recording creates table entries, not modelspace entities, so
        # there is no handle for the transaction verifier to check -- and
        # required_layers cannot help, because that check asks which layers
        # CARRY ENTITIES. So the assertion is made here, inside the
        # transaction: read the file back and prove every requested layer is
        # in its table. Failing here rolls the write back and exits 3, rather
        # than committing a file that does not contain what was asked for.
        _assert_layers_written(target, [spec.name for spec in specs])
        return ()

    names = [spec.name for spec in specs]
    _write_output(path, before=before, save=save, required_layers=())
    payload = {
        "command": "layers",
        "ok": True,
        **_output_note(args, path, before),
        "dxf_version": DXF_WRITE_VERSION,
        "layers": names,
        "entity_count": 0,
        "layer_counts": {name: 0 for name in names},
        "handles": [],
        "layer_table": {
            "requested": list(order.requested),
            "appended": list(order.appended),
            "order": list(order.order),
            "records": [
                {
                    "name": item.name,
                    "created": item.created,
                    "semantic": str(item.semantic),
                    "applied": dict(item.applied),
                    "preserved": dict(item.preserved),
                    "unclassified": item.unclassified,
                }
                for item in order.records
            ],
        },
        "verify_hint": _verify_hint(
            path, "--only", "layers", *(["--expect-layers", *names] if names else [])
        ),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        lines = [
            f"layer table order: {', '.join(order.order)}",
            f"  created now: {', '.join(order.appended) or '(none)'}",
        ]
        for item in order.records:
            detail = f"applied {dict(item.applied)}" if item.applied else "no change requested"
            if item.unclassified:
                detail += " [UNRESOLVED: this name classifies as UNKNOWN]"
            lines.append(f"  {item.name}: {'created' if item.created else 'reused'}, {detail}")
        lines.append(f"  verify with: {payload['verify_hint']}")
        _print_written("layers", path, before, "\n".join(lines), 0, {name: 0 for name in names})
    return EXIT_OK


# ---------------------------------------------------------------------------
# subcommand: verify
# ---------------------------------------------------------------------------


def _read_modelspace(path: Path) -> tuple[Any, list[dict[str, Any]]]:
    """Open ``path`` and flatten the modelspace into plain dicts.

    Read-only: the document is opened and never saved. The per-entity decode
    is deliberately defensive -- a malformed entity yields ``None`` for the
    field that could not be read rather than aborting the whole report, and
    the CHECKS then decide whether an unreadable value is a defect. Silently
    dropping an entity from the list instead would let an entity count match
    by accident, which is the failure mode this project keeps re-learning.
    """
    ezdxf = _require_ezdxf()
    try:
        doc = ezdxf.readfile(path)
    except (OSError, ezdxf.DXFError) as exc:
        raise CliError(f"cannot read {path}: {exc}") from exc
    entities: list[dict[str, Any]] = []
    for entity in doc.modelspace():
        item: dict[str, Any] = {
            "type": entity.dxftype(),
            "layer": str(entity.dxf.layer),
            "handle": str(entity.dxf.handle),
        }
        if item["type"] == "LINE":
            item["start"] = _xy(entity.dxf.start)
            item["end"] = _xy(entity.dxf.end)
        elif item["type"] == "ARC":
            item["center"] = _xy(entity.dxf.center)
            item["radius"] = float(entity.dxf.radius)
            item["start_angle"] = float(entity.dxf.start_angle)
            item["end_angle"] = float(entity.dxf.end_angle)
        elif item["type"] == "LWPOLYLINE":
            # A hatch recording is a closed LWPOLYLINE carrying the pattern
            # contract as XDATA. read_hatch_metadata is the HATCH MODULE'S OWN
            # reader, so this is a real roundtrip through the module that wrote
            # the entity, not a re-implementation of it here.
            try:
                vertices = [_xy(vertex) for vertex in entity.get_points()]
            except (AttributeError, TypeError, ValueError):  # pragma: no cover
                vertices = []
            item["vertices"] = vertices
            item["closed"] = bool(getattr(entity, "closed", False))
            item["hatch"] = _safe_hatch_metadata(entity)
        elif item["type"] == "TEXT":
            item["insert"] = _xy(entity.dxf.insert)
            item["text"] = str(entity.dxf.text)
            item["height"] = float(entity.dxf.height)
            item["rotation_deg"] = float(getattr(entity.dxf, "rotation", 0.0))
        elif item["type"] == "MTEXT":
            item["insert"] = _xy(entity.dxf.insert)
            item["text"] = str(entity.plain_text())
            item["height"] = float(entity.dxf.char_height)
            item["rotation_deg"] = float(getattr(entity.dxf, "rotation", 0.0))
        elif item["type"] == "DIMENSION":
            item["block_name"] = str(entity.dxf.geometry)
            item["text"] = str(getattr(entity.dxf, "text", ""))
            item["measurement"] = _safe_dim_measurement(doc, entity)
            # The rendered string lives in the anonymous block, NOT in
            # dxf.text: dxf.text is the "<>" placeholder when the text was
            # delegated to the renderer. Reading the wrong one is how a
            # dimension passes a check that never looked at the drawing.
            item["rendered_text"] = dim_rendered_text(doc, entity)
        elif item["type"] == "INSERT":
            item["name"] = str(entity.dxf.name)
            item["insert"] = _xy(entity.dxf.insert)
        entities.append(item)
    return doc, entities


def _safe_hatch_metadata(entity: Any) -> dict[str, Any] | None:
    """The hatch module's own reader, or None if this polyline is not a hatch."""
    try:
        return read_hatch_metadata(entity)
    except Exception:  # noqa: BLE001 - a foreign polyline is not an error
        return None


def _safe_dim_measurement(doc: Any, entity: Any) -> float | None:
    """What the DIMENSION measures right now, or None if it cannot be read.

    None is carried into the report and turned into a FAIL by
    ``dim_measurement_is_readable``: an unmeasurable dimension in a drawing
    somebody asked to be verified is a defect, not a shrug.
    """
    try:
        value = entity.get_measurement()
    except Exception:  # noqa: BLE001 - a broken DIMSTYLE is reported, not raised
        return None
    return None if value is None else float(value)


def _unit_direction(start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return (1.0, 0.0)
    return (dx / length, dy / length)


def _length(start: tuple[float, float], end: tuple[float, float]) -> float:
    return math.hypot(end[0] - start[0], end[1] - start[1])


def _point_segment_distance(
    point: Point2D, start: Point2D, end: Point2D
) -> float:
    dx = end.x - start.x
    dy = end.y - start.y
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.dist((point.x, point.y), (start.x, start.y))
    parameter = ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_sq
    parameter = max(0.0, min(1.0, parameter))
    projection = Point2D(start.x + parameter * dx, start.y + parameter * dy)
    return math.dist((point.x, point.y), (projection.x, projection.y))


def _as_float_sequence(
    value: float | Sequence[float] | None,
) -> list[float]:
    """Normalise a repeatable expectation argument into a list of floats.

    ``--expect-dim-measurement`` is an ``action="append"`` flag, so argparse
    hands ``verify_drawing`` a list, but the function is also called directly
    with a single float. Both are accepted; ``None`` means "not requested".
    """
    if value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    return [float(item) for item in value]


def expectation_check_name(name: str, index: int) -> str:
    """Name for the ``index``-th instance of a repeatable expectation check.

    The first occurrence keeps the bare name so existing consumers of
    ``failed``/``passed`` are unaffected; later repeats are suffixed so a
    report naming several expectations still names them one by one.
    """
    return name if index == 0 else f"{name}_{index + 1}"


def _check(name: str, passed: bool, detail: str, **extra: Any) -> dict[str, Any]:
    check: dict[str, Any] = {
        "name": name,
        "status": "PASS" if passed else "FAIL",
        "detail": detail,
    }
    check.update(extra)
    return check


def _warn(name: str, detail: str, **extra: Any) -> dict[str, Any]:
    """A reported-but-not-blocking finding.

    WARN is deliberately a third status rather than a PASS: it is listed in the
    report, printed, and exposed as ``warnings`` in the JSON, but it never
    enters ``failed`` and never changes the exit code. Use it only for a state
    that is real and unresolved in the source data, not for a defect.
    """
    check: dict[str, Any] = {"name": name, "status": "WARN", "detail": detail}
    check.update(extra)
    return check


def _mid(item: dict[str, Any]) -> tuple[float, float]:
    return (
        (item["start"][0] + item["end"][0]) / 2.0,
        (item["start"][1] + item["end"][1]) / 2.0,
    )


def _signed_offset(
    point: tuple[float, float],
    origin: tuple[float, float],
    normal: tuple[float, float],
) -> float:
    return (point[0] - origin[0]) * normal[0] + (point[1] - origin[1]) * normal[1]


def _perp_deviation(direction: tuple[float, float], axis: tuple[float, float]) -> float:
    """|dot| of a unit direction with the axis: 0 when perpendicular to it."""
    return abs(direction[0] * axis[0] + direction[1] * axis[1])


def _analyse_window(
    window_entities: list[dict[str, Any]],
    tol: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Measure a window back from its own read-back entities.

    Partition derived from the drawing, not from the CLI's own options: the
    recorder puts the glazing line, both jamb edges and the casement arc on
    ``WIN``, the interior face line on ``WINELE`` (a different layer that
    classifies to the same WINDOW semantic) and the bars on ``WINBAR``
    (WINDOW_BAR). So the arc's layer names the window layer, and the layer that
    holds the only long line on the axis is the glazing line.

    The indoor-side check is the one that actually exercises the indoor
    decision: the interior face line is offset to one side of the glazing
    (centre)line, and the casement arc sweeps towards one side of the width
    axis. Those two must agree, or the drawing claims an indoor side its own
    geometry contradicts.
    """
    checks: list[dict[str, Any]] = []
    measured: dict[str, Any] = {}
    if not window_entities:
        checks.append(
            _check("window_present", False, "no window-semantic entities found; nothing to measure")
        )
        return checks, measured

    lines = [item for item in window_entities if item["type"] == "LINE"]
    arcs = [item for item in window_entities if item["type"] == "ARC"]
    bar_lines = [item for item in lines if classify_layer(item["layer"]) is LayerSemantic.WINDOW_BAR]
    face_lines = [item for item in lines if classify_layer(item["layer"]) is LayerSemantic.WINDOW]
    layers = sorted({item["layer"] for item in window_entities})
    measured["line_count"] = len(lines)
    measured["arc_count"] = len(arcs)
    measured["bar_line_count"] = len(bar_lines)
    measured["face_line_count"] = len(face_lines)
    measured["layers"] = layers
    checks.append(
        _check(
            "window_present",
            True,
            f"{len(lines)} lines and {len(arcs)} arcs on window layers {layers}",
        )
    )
    if len(arcs) != 1:
        checks.append(
            _check(
                "window_single_casement_arc",
                False,
                f"expected exactly 1 casement ARC on a window layer, found {len(arcs)}",
            )
        )
        return checks, measured
    checks.append(
        _check("window_single_casement_arc", True, "exactly 1 casement arc on a window layer")
    )

    arc = arcs[0]
    window_layer = arc["layer"]
    same_layer = [item for item in lines if item["layer"] == window_layer]
    other_face = [item for item in face_lines if item["layer"] != window_layer]
    measured["window_layer"] = window_layer
    measured["arc_radius_mm"] = arc["radius"]
    measured["arc_start_deg"] = arc["start_angle"]
    measured["arc_end_deg"] = arc["end_angle"]

    # The glazing line is the longest line on the window layer: the two jamb
    # edges are exactly as long as the wall thickness across the opening.
    if not same_layer:
        checks.append(
            _check("window_glazing_line_identified", False, f"no lines on window layer {window_layer}")
        )
        return checks, measured
    glazing = max(same_layer, key=lambda item: _length(item["start"], item["end"]))
    jambs = [item for item in same_layer if item is not glazing]
    glazing_dir = _unit_direction(glazing["start"], glazing["end"])
    glazing_mid = _mid(glazing)
    width = _length(glazing["start"], glazing["end"])
    normal = (-glazing_dir[1], glazing_dir[0])
    measured["glazing_line_length_mm"] = width
    measured["jamb_count"] = len(jambs)
    measured["width_axis_deg"] = math.degrees(math.atan2(glazing_dir[1], glazing_dir[0])) % 360.0
    if len(jambs) != 2:
        checks.append(
            _check(
                "window_glazing_line_identified",
                False,
                f"glazing line {width:g} mm on {window_layer} with {len(jambs)} jamb "
                f"edge(s); a window needs exactly 2",
            )
        )
        return checks, measured
    checks.append(
        _check(
            "window_glazing_line_identified",
            True,
            f"glazing line {width:g} mm and 2 jamb edges on {window_layer}",
        )
    )

    # 1. The opening width must run ALONG the width axis: each jamb edge is
    #    perpendicular to the glazing line.
    worst_perp = max(
        _perp_deviation(_unit_direction(item["start"], item["end"]), glazing_dir)
        for item in jambs
    )
    measured["jamb_perpendicular_deviation"] = worst_perp
    checks.append(
        _check(
            "window_jamb_edges_perpendicular_to_width_axis",
            worst_perp <= 1e-6,
            f"jamb edges deviate from perpendicular to the width axis by {worst_perp:g}",
        )
    )

    # 2. Each jamb edge spans the wall thickness; both must agree on it, and
    #    both must be centred on the centreline it crosses.
    jamb_lengths = sorted(_length(item["start"], item["end"]) for item in jambs)
    thickness = jamb_lengths[0]
    measured["jamb_lengths_mm"] = jamb_lengths
    measured["wall_thickness_mm"] = thickness
    checks.append(
        _check(
            "window_jamb_edges_span_equal_thickness",
            jamb_lengths[-1] - jamb_lengths[0] <= tol,
            f"jamb edge lengths {jamb_lengths} mm (a window across a wall must "
            f"cross the same thickness at both jambs)",
        )
    )
    jamb_across = [_signed_offset(_mid(item), glazing_mid, normal) for item in jambs]
    measured["jamb_centreline_offsets_mm"] = jamb_across
    checks.append(
        _check(
            "window_jamb_edges_centred_on_centreline",
            max(abs(value) for value in jamb_across) <= max(tol, 1e-6),
            f"jamb edge midpoints sit {[round(value, 6) for value in jamb_across]} mm "
            "from the glazing (centre)line; both must sit on it",
        )
    )

    # 3. The two jamb edges must bracket the glazing line, i.e. the measured
    #    opening width is the distance between the jamb midpoints.
    jamb_mids = [_mid(item) for item in jambs]
    opening_width = math.dist(jamb_mids[0], jamb_mids[1])
    measured["opening_width_mm"] = opening_width
    measured["opening_center"] = [
        (jamb_mids[0][0] + jamb_mids[1][0]) / 2.0,
        (jamb_mids[0][1] + jamb_mids[1][1]) / 2.0,
    ]
    checks.append(
        _check(
            "window_opening_width_matches_glazing_line",
            abs(opening_width - width) <= max(tol, 1e-6),
            f"jamb-to-jamb width {opening_width:g} mm vs glazing line {width:g} mm "
            f"(delta {abs(opening_width - width):g} mm)",
        )
    )

    # 3b. The casement arc is tied to the opening the way the door's swing arc
    #     is tied to its opening edges. MEASURED invariant (window.py builds
    #     the centre as jamb_start - outward*(thickness/2)): the arc centre lies
    #     exactly HALF A THICKNESS off the hinge-side jamb line, and its radius
    #     equals the opening width. The module docstring's "hinge at the indoor
    #     jamb" is not literally true of the centre point, so the check asserts
    #     the geometry that actually holds rather than the prose.
    hinge_side = min(jamb_mids, key=lambda mid: math.dist(mid, (arc["center"][0], arc["center"][1])))
    arc_to_hinge_jamb = math.dist(arc["center"], hinge_side)
    measured["arc_centre_to_hinge_jamb_mm"] = arc_to_hinge_jamb
    measured["arc_expected_hinge_offset_mm"] = thickness / 2.0
    checks.append(
        _check(
            "window_casement_arc_hinges_on_the_jamb",
            abs(arc_to_hinge_jamb - thickness / 2.0) <= max(tol, 1e-6),
            f"arc centre sits {arc_to_hinge_jamb:g} mm from the nearest jamb line, "
            f"which is {thickness / 2.0:g} mm = half the {thickness:g} mm wall "
            "thickness: the casement must hinge on the opening, not float",
        )
    )
    checks.append(
        _check(
            "window_casement_arc_radius_matches_opening",
            abs(arc["radius"] - opening_width) <= max(tol, 1e-6),
            f"arc radius {arc['radius']:g} mm vs opening width {opening_width:g} mm "
            f"(delta {abs(arc['radius'] - opening_width):g} mm)",
        )
    )

    # 4/5. The interior face line must exist, sit exactly half a thickness off
    #      the centreline, and do so on ONE side (it is a face, not a slab).
    if not other_face:
        checks.append(
            _check(
                "window_interior_face_line_present",
                False,
                "no interior face line on a window-semantic layer other than the "
                f"window layer {window_layer}",
            )
        )
    else:
        face = max(other_face, key=lambda item: _length(item["start"], item["end"]))
        face_mid = _mid(face)
        offset = _signed_offset(face_mid, glazing_mid, normal)
        measured["interior_face_offset_mm"] = offset
        measured["interior_face_length_mm"] = _length(face["start"], face["end"])
        measured["interior_side_measured"] = "left" if offset >= 0 else "right"
        checks.append(
            _check(
                "window_interior_face_line_present",
                True,
                f"interior face line on layer {face['layer']}, offset {offset:g} mm "
                "from the glazing (centre)line",
            )
        )
        checks.append(
            _check(
                "window_interior_face_offset_is_half_thickness",
                abs(abs(offset) - thickness / 2.0) <= max(tol, 1e-6),
                f"interior face sits {abs(offset):g} mm from the centreline vs "
                f"half thickness {thickness / 2.0:g} mm",
            )
        )
        # 6. The indoor determination itself: the face line's side and the
        #    casement arc's sweep side must agree. The arc starts on the width
        #    axis and sweeps by start->end; a CCW sweep bulges towards the left
        #    normal, a CW one towards the right.
        start_deg = arc["start_angle"] % 360.0
        end_deg = arc["end_angle"] % 360.0
        sweep = (end_deg - start_deg) % 360.0
        sweep_side = "left" if sweep <= 180.0 else "right"
        face_side = "left" if offset >= 0 else "right"
        measured["casement_sweep_deg"] = sweep
        measured["casement_sweep_side"] = sweep_side
        checks.append(
            _check(
                "window_interior_side_agrees_with_casement_sweep",
                sweep_side == face_side,
                f"interior face is on the {face_side} of the width axis but the "
                f"casement arc sweeps {sweep:g} deg to the {sweep_side}: the drawing "
                "claims an indoor side its own geometry contradicts",
            )
        )

    # 7. Bars: each must be perpendicular to the axis, inside the opening, and
    #    evenly spaced at width / (n + 1). Positions are measured from the
    #    glazing midpoint, so the opening spans [-width/2, +width/2].
    if not bar_lines:
        checks.append(
            _check(
                "window_bars_spaced_evenly_within_opening",
                True,
                "no window-bar entities: a window with 0 divisions has none",
            )
        )
        return checks, measured
    bar_mids = [_mid(item) for item in bar_lines]
    along = sorted(
        (mid[0] - glazing_mid[0]) * glazing_dir[0] + (mid[1] - glazing_mid[1]) * glazing_dir[1]
        for mid in bar_mids
    )
    divisions = len(along)
    expected_step = opening_width / (divisions + 1)
    steps = [along[i + 1] - along[i] for i in range(len(along) - 1)]
    measured["bar_positions_mm"] = along
    measured["bar_steps_mm"] = steps
    measured["expected_bar_step_mm"] = expected_step
    half_width = opening_width / 2.0
    inside = all(-half_width < value < half_width for value in along)
    worst_bar_perp = max(
        _perp_deviation(_unit_direction(item["start"], item["end"]), glazing_dir)
        for item in bar_lines
    )
    measured["bar_perpendicular_deviation"] = worst_bar_perp
    bar_across = [_signed_offset(_mid(item), glazing_mid, normal) for item in bar_lines]
    measured["bar_centreline_offsets_mm"] = bar_across
    checks.append(
        _check(
            "window_bars_perpendicular_to_width_axis",
            worst_bar_perp <= 1e-6,
            f"{len(bar_lines)} bar(s) deviate from perpendicular to the width axis "
            f"by {worst_bar_perp:g}",
        )
    )
    checks.append(
        _check(
            "window_bars_centred_on_centreline",
            max(abs(value) for value in bar_across) <= max(tol, 1e-6),
            f"bar midpoints sit {[round(value, 6) for value in bar_across]} mm from "
            "the glazing (centre)line; every bar must sit on it",
        )
    )
    checks.append(
        _check(
            "window_bars_within_opening_width",
            inside,
            f"bar positions {[round(value, 6) for value in along]} mm from the "
            f"glazing midpoint of a {opening_width:g} mm opening, which spans "
            f"+/-{half_width:g} mm",
        )
    )
    worst_step = max((abs(value - expected_step) for value in steps), default=0.0)
    measured["worst_bar_step_error_mm"] = worst_step
    checks.append(
        _check(
            "window_bars_spaced_evenly_within_opening",
            worst_step <= max(tol, 1e-6),
            f"{divisions} bar(s), steps {[round(value, 6) for value in steps]} mm vs "
            f"the even {expected_step:g} mm for {divisions} division(s) "
            f"(worst delta {worst_step:g} mm)",
        )
    )
    return checks, measured


def _analyse_opening(
    opening_entities: list[dict[str, Any]],
    tol: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Measure a wall opening back from its own read-back lines.

    The recorder writes two parallel boundary lines (the edges that cross the
    wall), one long face line a full thickness away on the far side, and three
    45-degree ticks. Which layer is which is taken from the drawing: the layer
    holding the two short parallel lines is the boundary layer, the other
    opening layer holds the face line and the ticks.

    The layer question is reported, not decided: this recorder's default
    layers classify to UNKNOWN on purpose
    (``LAYER_MAPPING_RESOLVED is False``), so ``verify`` warns and keeps
    checking the geometry. It does not treat an unresolved mapping as a broken
    drawing, and it does not accept it silently either.
    """
    checks: list[dict[str, Any]] = []
    measured: dict[str, Any] = {}
    if not opening_entities:
        checks.append(
            _check("opening_present", False, "no opening entities found; nothing to measure")
        )
        return checks, measured

    lines = [item for item in opening_entities if item["type"] == "LINE"]
    layers = sorted({item["layer"] for item in opening_entities})
    measured["line_count"] = len(lines)
    measured["layers"] = layers
    measured["layer_mapping_resolved"] = OPENING_LAYER_MAPPING_RESOLVED
    checks.append(
        _check("opening_present", True, f"{len(lines)} lines on opening layers {layers}")
    )
    if len(lines) < 3:
        checks.append(
            _check(
                "opening_boundary_edges_identified",
                False,
                f"expected 2 boundary edges, a face line and 3 ticks, found {len(lines)} line(s)",
            )
        )
        return checks, measured

    # Partition by geometry: the face line is the longest line on its layer.
    by_layer: dict[str, list[dict[str, Any]]] = {}
    for item in lines:
        by_layer.setdefault(item["layer"], []).append(item)
    boundary_layer = min(
        by_layer, key=lambda name: max(_length(i["start"], i["end"]) for i in by_layer[name])
    )
    boundary = by_layer[boundary_layer]
    face_layer = next(name for name in layers if name != boundary_layer)
    face = max(by_layer[face_layer], key=lambda item: _length(item["start"], item["end"]))
    ticks = [item for item in by_layer[face_layer] if item is not face]
    measured["boundary_layer"] = boundary_layer
    measured["face_layer"] = face_layer
    measured["tick_count"] = len(ticks)

    axis = _unit_direction(face["start"], face["end"])
    face_mid = _mid(face)
    normal = (-axis[1], axis[0])
    checks.append(
        _check(
            "opening_boundary_edges_identified",
            len(boundary) == 2 and len(ticks) == 3,
            f"2 boundary line(s) expected on {boundary_layer} (got {len(boundary)}), "
            f"face line + 3 ticks expected on {face_layer} (got 1 + {len(ticks)})",
        )
    )
    if len(boundary) != 2:
        return checks, measured

    boundary_lengths = sorted(_length(item["start"], item["end"]) for item in boundary)
    thickness = boundary_lengths[0]
    measured["boundary_lengths_mm"] = boundary_lengths
    measured["wall_thickness_mm"] = thickness
    checks.append(
        _check(
            "opening_boundary_spans_equal_wall_thickness",
            boundary_lengths[-1] - boundary_lengths[0] <= tol,
            f"boundary edge lengths {boundary_lengths} mm; both must cross the "
            "same wall thickness",
        )
    )

    # 1. The boundary must actually cross the wall: perpendicular to the face
    #    line, and as long as the gap from the boundary to the face line.
    worst_perp = max(
        _perp_deviation(_unit_direction(item["start"], item["end"]), axis) for item in boundary
    )
    measured["boundary_perpendicular_deviation"] = worst_perp
    checks.append(
        _check(
            "opening_boundary_perpendicular_to_face_line",
            worst_perp <= 1e-6,
            f"boundary edges deviate from perpendicular to the wall line by {worst_perp:g}",
        )
    )
    face_gap = abs(_signed_offset(face_mid, _mid(boundary[0]), normal))
    measured["boundary_to_face_distance_mm"] = face_gap
    checks.append(
        _check(
            "opening_boundary_crosses_wall_thickness",
            abs(face_gap - thickness) <= max(tol, 1e-6),
            f"face line sits {face_gap:g} mm from the boundary edge, which is "
            f"{thickness:g} mm long: the boundary must cross the whole thickness",
        )
    )

    boundary_mids = [_mid(item) for item in boundary]
    centre_origin = boundary_mids[0] if len(boundary_mids) == 2 else _mid(boundary[0])

    # 1b. Each boundary edge is centred on the centreline it crosses; the two
    #     are the same length, so the thickness is measurable from either.
    boundary_across = [_signed_offset(mid, centre_origin, normal) for mid in boundary_mids]
    measured["boundary_centreline_offsets_mm"] = boundary_across
    checks.append(
        _check(
            "opening_boundary_edges_centred_on_centreline",
            max(abs(value) for value in boundary_across) <= max(tol, 1e-6),
            f"boundary midpoints sit {[round(value, 6) for value in boundary_across]} "
            "mm from the centreline through them",
        )
    )

    # 2. Symmetry about the reference (centre) line. The centreline is the line
    #    through the two boundary midpoints, parallel to the face line and one
    #    full thickness away from it. Each boundary edge must be centred ON
    #    that line: its two ends equidistant either side of it.
    symmetry_error = 0.0
    for item in boundary:
        side = [_signed_offset(item[end], centre_origin, normal) for end in ("start", "end")]
        symmetry_error = max(symmetry_error, abs(side[0] + side[1]) / 2.0)
    measured["boundary_symmetry_error_mm"] = symmetry_error
    checks.append(
        _check(
            "opening_boundary_symmetric_about_wall_centreline",
            symmetry_error <= max(tol, 1e-6),
            f"worst midpoint offset from the centreline {symmetry_error:g} mm: both "
            "ends of a boundary edge must be the same distance either side of it",
        )
    )

    opening_width = math.dist(boundary_mids[0], boundary_mids[1])
    measured["opening_width_mm"] = opening_width
    measured["opening_center"] = [
        (boundary_mids[0][0] + boundary_mids[1][0]) / 2.0,
        (boundary_mids[0][1] + boundary_mids[1][1]) / 2.0,
    ]
    face_length = _length(face["start"], face["end"])
    measured["face_line_length_mm"] = face_length
    checks.append(
        _check(
            "opening_width_matches_face_line",
            abs(opening_width - face_length) <= max(tol, 1e-6),
            f"boundary-to-boundary width {opening_width:g} mm vs face line "
            f"{face_length:g} mm (delta {abs(opening_width - face_length):g} mm)",
        )
    )

    # 3. The ticks. Two of them are 45-degree jamb ticks: |along| == |across|.
    #    The third is the centre tick, which runs straight across the wall, so
    #    it is checked as perpendicular instead. Partitioning on geometry (the
    #    centre tick is the one parallel to the boundary lines) keeps this
    #    derived from the drawing rather than from the recorder's own naming.
    centre_ticks = [
        item
        for item in ticks
        if _perp_deviation(_unit_direction(item["start"], item["end"]), axis) <= 1e-6
    ]
    jamb_ticks = [item for item in ticks if item not in centre_ticks]
    measured["centre_tick_count"] = len(centre_ticks)
    measured["jamb_tick_count"] = len(jamb_ticks)
    worst_tick = 0.0
    for item in jamb_ticks:
        a = item["start"]
        b = item["end"]
        along_part = (b[0] - a[0]) * axis[0] + (b[1] - a[1]) * axis[1]
        across_part = (b[0] - a[0]) * normal[0] + (b[1] - a[1]) * normal[1]
        worst_tick = max(worst_tick, abs(abs(along_part) - abs(across_part)))
    measured["worst_tick_45_degree_error_mm"] = worst_tick
    checks.append(
        _check(
            "opening_ticks_are_45_degree",
            len(centre_ticks) == 1 and len(jamb_ticks) == 2 and worst_tick <= max(tol, 1e-6),
            f"{len(jamb_ticks)} jamb tick(s) and {len(centre_ticks)} centre tick(s); "
            f"worst |along|-|across| error on the jamb ticks {worst_tick:g} mm",
        )
    )
    if centre_ticks:
        # Measured: the recorder draws the centre tick as center -> center +
        # toward * (thickness / 2), i.e. HALF the wall thickness.
        tick = centre_ticks[0]
        across_part = _signed_offset(tick["end"], tick["start"], normal)
        measured["centre_tick_length_mm"] = abs(across_part)
        checks.append(
            _check(
                "opening_centre_tick_is_half_thickness",
                abs(abs(across_part) - thickness / 2.0) <= max(tol, 1e-6),
                f"centre tick runs {abs(across_part):g} mm across the wall vs half "
                f"the {thickness:g} mm thickness ({thickness / 2.0:g} mm)",
            )
        )

    # 4. The unresolved layer mapping: reported, never silently passed, never
    #    treated as a defect.
    unresolved = [name for name in layers if name in OPENING_UNRESOLVED_LAYERS]
    stray = [name for name in layers if name not in OPENING_UNRESOLVED_LAYERS]
    measured["unresolved_layers"] = unresolved
    checks.append(
        _check(
            "opening_layers_are_inert_temp_layers",
            not stray and bool(unresolved),
            f"opening layers {layers}: unresolved-fallback {unresolved}, "
            f"layers outside the documented TEMP- fallback {stray}",
        )
    )
    if unresolved:
        checks.append(
            _warn(
                "opening_layer_mapping_unresolved",
                OPENING_LAYER_MAPPING_NOTE,
                layers=unresolved,
                mapping={
                    name: str(classify_layer(name))
                    for name in unresolved
                },
            )
        )
    return checks, measured


def _analyse_wall(
    wall_lines: list[dict[str, Any]],
    centerlines: list[dict[str, Any]],
    tol: float,
    *,
    require_wall: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Measure the wall back from its own read-back lines.

    The wall recorder writes face lines (long, parallel to the wall) and cap
    lines (short, perpendicular). Nothing in the DXF records which is which, so
    the two are told apart geometrically: the wall axis is the direction shared
    by the longest lines, faces are the long lines on it, and the remaining
    wall lines are caps. The thickness is then the perpendicular distance
    between the two faces, and every cap must be exactly as long as that
    thickness -- an invariant that a hand-mangled drawing breaks.
    """
    checks: list[dict[str, Any]] = []
    measured: dict[str, Any] = {}
    if len(wall_lines) < 2:
        checks.append(
            _check(
                "wall_measurable",
                False,
                f"only {len(wall_lines)} wall-layer lines found; a wall needs at "
                "least two faces",
            )
        )
        return checks, measured

    longest_item = max(wall_lines, key=lambda item: _length(item["start"], item["end"]))
    ux, uy = _unit_direction(longest_item["start"], longest_item["end"])
    faces = []
    caps = []
    for item in wall_lines:
        direction = _unit_direction(item["start"], item["end"])
        parallel = math.isclose(abs(direction[0] * ux + direction[1] * uy), 1.0, abs_tol=1e-6)
        (faces if parallel else caps).append(item)

    if len(faces) < 2:
        checks.append(
            _check("wall_measurable", False, f"only {len(faces)} face line(s) found")
        )
        return checks, measured

    # thickness = perpendicular offset of the two faces from the first face
    first = faces[0]
    nx, ny = -uy, ux
    offsets = sorted(
        round(
            (item["start"][0] - first["start"][0]) * nx
            + (item["start"][1] - first["start"][1]) * ny,
            9,
        )
        for item in faces
    )
    thickness = offsets[-1] - offsets[0]
    axis_mid = (offsets[0] + offsets[-1]) / 2.0
    measured["face_count"] = len(faces)
    measured["cap_count"] = len(caps)
    measured["thickness_mm"] = thickness
    measured["wall_length_mm"] = _length(first["start"], first["end"])
    measured["face_offsets_mm"] = offsets

    checks.append(
        _check(
            "wall_thickness_positive",
            thickness > tol,
            f"measured wall thickness {thickness:g} mm between the two faces",
            measured_mm=thickness,
        )
    )
    face_lengths = [_length(item["start"], item["end"]) for item in faces]
    measured["face_lengths_mm"] = face_lengths
    checks.append(
        _check(
            "wall_faces_equal_length",
            max(face_lengths) - min(face_lengths) <= tol,
            f"face lengths {[round(value, 6) for value in face_lengths]} mm",
        )
    )
    # S2: these two used to live behind `if caps:` / `if centerlines:`, so a
    # wall that had lost its caps or its centreline verified clean while the
    # check count silently dropped from 19 to 5. "Not present" is a fact about
    # the drawing, so it is now reported as a fact -- a FAIL when a wall is
    # required, an explicitly-measured note otherwise. Never a silent skip.
    if caps:
        cap_lengths = [_length(item["start"], item["end"]) for item in caps]
        measured["cap_lengths_mm"] = cap_lengths
        worst = max(abs(value - thickness) for value in cap_lengths)
        checks.append(
            _check(
                "wall_cap_length_equals_thickness",
                worst <= tol,
                f"cap lengths {[round(value, 6) for value in cap_lengths]} mm vs "
                f"thickness {thickness:g} mm (worst delta {worst:g} mm)",
            )
        )
    else:
        measured["cap_count"] = 0
        checks.append(
            _check(
                "wall_caps_present",
                not require_wall,
                "no cap lines found on the wall layers: a wall recorder always "
                "writes one cap per end, so caps missing from a drawing means "
                "they were deleted or the drawing was not written by this "
                "recorder",
            )
        )
    if centerlines:
        center_offsets = []
        for item in centerlines:
            start = item["start"]
            center_offsets.append(
                round(
                    (start[0] - first["start"][0]) * nx
                    + (start[1] - first["start"][1]) * ny,
                    9,
                )
            )
        measured["centerline_offsets_mm"] = center_offsets
        measured["face_offset_mid_mm"] = axis_mid
        worst = max(abs(value - axis_mid) for value in center_offsets)
        checks.append(
            _check(
                "centerline_midway_between_faces",
                worst <= tol,
                f"centreline offsets {center_offsets} mm vs face mid {axis_mid:g} mm "
                f"(worst delta {worst:g} mm)",
            )
        )
    else:
        measured["centerline_count"] = 0
        # The centreline is optional in this recorder (--no-axis), so its
        # absence alone is not a defect; it is only a defect when the caller
        # scoped the drawing to the wall, where a wall with no centreline is a
        # wall nobody can measure against. Reported either way.
        checks.append(
            _check(
                "wall_centerline_reported",
                True,
                "no centreline line found (the recorder can be asked for "
                "--no-axis, so this is reported, not failed)",
            )
        )
    return checks, measured


def _analyse_door(
    door_entities: list[dict[str, Any]],
    tol: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Measure the opening width and centre back from the read-back door.

    Partition rule, derived from the drawing rather than from the CLI's own
    options: the door recorder puts the swing arc, the two opening edges and the
    leaf on ONE layer, and the frame face lines on a second door-semantic layer
    (``DOOR`` vs ``DOOR_ELE``). So the layer that carries the arc is the door
    layer; its lines are the leaf (the one touching the arc centre, which is the
    hinge) plus the two opening edges. The other door-semantic layer holds the
    frame lines, which are reported but not needed for the width. The opening
    width then has to equal the swing-arc radius -- a real invariant that a
    hand-mangled drawing breaks.
    """
    checks: list[dict[str, Any]] = []
    measured: dict[str, Any] = {}
    if not door_entities:
        checks.append(
            _check(
                "door_present",
                False,
                "no door-semantic entities found; nothing to measure",
            )
        )
        return checks, measured

    lines = [item for item in door_entities if item["type"] == "LINE"]
    arcs = [item for item in door_entities if item["type"] == "ARC"]
    layers = sorted({item["layer"] for item in door_entities})
    measured["line_count"] = len(lines)
    measured["arc_count"] = len(arcs)
    measured["layers"] = layers
    checks.append(
        _check(
            "door_present",
            True,
            f"{len(lines)} lines and {len(arcs)} arcs on door layers {layers}",
        )
    )
    if len(arcs) != 1:
        checks.append(
            _check(
                "door_single_swing_arc",
                False,
                f"expected exactly 1 swing ARC on a door layer, found {len(arcs)}",
            )
        )
        return checks, measured
    checks.append(
        _check("door_single_swing_arc", True, "exactly 1 swing arc on a door layer")
    )

    arc = arcs[0]
    center = arc["center"]
    radius = arc["radius"]
    door_layer = arc["layer"]
    measured["arc_center"] = [center[0], center[1]]
    measured["arc_radius_mm"] = radius
    measured["door_layer"] = door_layer
    measured["frame_layers"] = [name for name in layers if name != door_layer]

    snap = max(tol, 1e-6)
    door_layer_lines = [item for item in lines if item["layer"] == door_layer]
    frame_lines = [item for item in lines if item["layer"] != door_layer]
    measured["frame_line_count"] = len(frame_lines)

    leaf = None
    edges = []
    for item in door_layer_lines:
        ends = (Point2D(*item["start"]), Point2D(*item["end"]))
        if any(math.dist((p.x, p.y), center) <= snap for p in ends):
            leaf = item
        else:
            edges.append(item)
    measured["leaf_found"] = leaf is not None
    if leaf is None or len(edges) != 2:
        checks.append(
            _check(
                "door_opening_edges_identified",
                False,
                f"expected 2 opening edges plus a leaf at the arc centre on layer "
                f"{door_layer}, got {len(edges)} edges and leaf={leaf is not None}",
            )
        )
        return checks, measured
    checks.append(
        _check(
            "door_opening_edges_identified",
            True,
            f"2 opening edges and a leaf starting at the arc centre (hinge) on {door_layer}",
        )
    )

    width = _length(edges[0]["start"], edges[1]["start"])
    mid_a = (
        (edges[0]["start"][0] + edges[0]["end"][0]) / 2.0,
        (edges[0]["start"][1] + edges[0]["end"][1]) / 2.0,
    )
    mid_b = (
        (edges[1]["start"][0] + edges[1]["end"][0]) / 2.0,
        (edges[1]["start"][1] + edges[1]["end"][1]) / 2.0,
    )
    door_center = ((mid_a[0] + mid_b[0]) / 2.0, (mid_a[1] + mid_b[1]) / 2.0)
    measured["opening_width_mm"] = width
    measured["opening_center"] = [door_center[0], door_center[1]]
    measured["hinge"] = [center[0], center[1]]

    checks.append(
        _check(
            "door_opening_width_positive",
            width > tol,
            f"measured opening width {width:g} mm",
            measured_mm=width,
        )
    )
    checks.append(
        _check(
            "door_opening_width_matches_swing_arc",
            abs(width - radius) <= snap,
            f"opening width {width:g} mm vs swing-arc radius {radius:g} mm "
            f"(delta {abs(width - radius):g} mm)",
        )
    )
    # The hinge (arc centre) lies ON the hinge-side opening edge, at its
    # midpoint -- not at one of its endpoints -- so this is a point-to-segment
    # distance, not a point-to-endpoint distance.
    hinge_offset = min(
        _point_segment_distance(
            Point2D(*center), Point2D(*edge["start"]), Point2D(*edge["end"])
        )
        for edge in edges
    )
    measured["hinge_to_nearest_opening_edge_mm"] = hinge_offset
    checks.append(
        _check(
            "door_hinge_on_an_opening_edge",
            hinge_offset <= snap,
            f"arc centre ({center[0]:g},{center[1]:g}) sits {hinge_offset:g} mm from "
            "the nearest opening edge",
        )
    )
    hinge_to_center = math.dist(center, door_center)
    measured["hinge_to_center_mm"] = hinge_to_center
    checks.append(
        _check(
            "door_hinge_offset_is_half_width",
            abs(hinge_to_center - width / 2.0) <= snap,
            f"hinge-to-centre {hinge_to_center:g} mm vs half width {width / 2.0:g} mm",
        )
    )
    if frame_lines:
        edge_direction = _unit_direction(edges[0]["start"], edges[0]["end"])
        worst = 0.0
        for item in frame_lines:
            direction = _unit_direction(item["start"], item["end"])
            dot = abs(direction[0] * edge_direction[0] + direction[1] * edge_direction[1])
            worst = max(worst, 1.0 - dot)
        measured["frame_line_lengths_mm"] = [
            _length(item["start"], item["end"]) for item in frame_lines
        ]
        checks.append(
            _check(
                "door_frame_lines_parallel_to_opening_edges",
                worst <= 1e-6,
                f"{len(frame_lines)} frame line(s) on "
                f"{measured['frame_layers']}, worst direction deviation {worst:g}",
            )
        )
    return checks, measured


#: Every value ``--only`` accepts: one scope per recording module, plus
#: ``both`` (the historical wall+door default) and ``all``.
VERIFY_SCOPES: tuple[str, ...] = (
    "both",
    "wall",
    "door",
    "window",
    "opening",
    "hatch",
    "dim",
    "text",
    "block",
    "layers",
    "all",
)

#: LAYER table entries ezdxf creates in a fresh R2018 document. They are not
#: "layers the recorder made", so ``--only layers`` does not count them as
#: content and ``--expect-layers`` never demands them.
DEFAULT_LAYER_NAMES: tuple[str, ...] = ("0", "Defpoints")

#: Block table entries that are CAD machinery rather than recorded content.
DEFAULT_BLOCK_NAMES: frozenset[str] = frozenset(
    {"_CLOSEDFILLED", "_CLOSEDFILLED_HATCH", "_CLOSEDFILLED_CUSTOM"}
)


def _arc_length(radius: float, start_angle: float, end_angle: float) -> float:
    """Length of the CCW arc from ``start_angle`` to ``end_angle``, in degrees."""
    sweep = (end_angle - start_angle) % 360.0
    if sweep == 0.0:
        sweep = 360.0
    return abs(radius) * math.radians(sweep)


#: Returned by ``_block_geometry`` when an INSERT names a block that is not in
#: the BLOCK table. ``doc.blocks.get()`` answers a missing name with ``None``
#: rather than raising, so "absent" is a value the caller must handle, not an
#: exception path.
MISSING_BLOCK: tuple[int, float] | None = None


def _block_geometry(doc: Any, block_name: str) -> tuple[int, float] | None:
    """How much geometry a block definition holds, as (entities, length_mm).

    Measurement only -- this is what an INSERT keeps from a downstream
    segment extractor, so it is what the visibility check reports. It is a
    count and a length on purpose: the point is to name the cost of the
    INSERT, not to re-derive the geometry.

    Returns ``None`` when the block definition is absent. A drawing can hold an
    INSERT naming a block that was deleted, never written, or added by another
    tool; ``doc.blocks.get`` answers that with ``None`` and does not raise, so
    the absence has to be reported as a value. Iterating the ``None`` instead
    would abort the whole report -- losing every other check with it.
    """
    try:
        block = doc.blocks.get(block_name)
    except Exception:  # noqa: BLE001 - a malformed table is reported, not raised
        return MISSING_BLOCK
    if block is None:
        return MISSING_BLOCK
    count = 0
    length = 0.0
    for entity in block:
        kind = entity.dxftype()
        if kind == "LINE":
            count += 1
            length += _length(_xy(entity.dxf.start), _xy(entity.dxf.end))
        elif kind == "ARC":
            count += 1
            length += _arc_length(
                float(entity.dxf.radius),
                float(entity.dxf.start_angle),
                float(entity.dxf.end_angle),
            )
    return (count, length)


def _analyse_hatch(
    hatch_entities: list[dict[str, Any]], tol: float, require_hatch: bool
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A hatch is a closed boundary polyline plus a pattern contract.

    The contract is the module's own (XDATA read back by
    ``hatch.read_hatch_metadata``), so these checks ask the hatch module's own
    question of the file rather than re-deriving it.
    """
    checks: list[dict[str, Any]] = []
    measured: dict[str, Any] = {"count": len(hatch_entities)}
    if not hatch_entities:
        if require_hatch:
            checks.append(
                _check(
                    "hatch_present",
                    False,
                    "--only hatch was requested but no LWPOLYLINE carrying the "
                    "hatch pattern contract is in this drawing",
                    measured=0,
                )
            )
        else:
            measured["not_required_but_checked"] = True
        return checks, measured

    areas = []
    perimeters = []
    patterns = []
    for item in hatch_entities:
        meta = item.get("hatch") or {}
        areas.append(float(meta.get("area_mm2", 0.0)))
        perimeters.append(float(meta.get("perimeter_mm", 0.0)))
        pattern = meta.get("pattern") or {}
        patterns.append(str(pattern.get("name", "")))

    checks.append(
        _check(
            "hatch_boundary_is_closed",
            all(item.get("closed") for item in hatch_entities),
            "every hatch boundary polyline is closed"
            if all(item.get("closed") for item in hatch_entities)
            else "a hatch boundary polyline is not closed, so it encloses nothing",
            closed=[bool(item.get("closed")) for item in hatch_entities],
        )
    )
    vertices = [len(item.get("vertices") or ()) for item in hatch_entities]
    checks.append(
        _check(
            "hatch_vertex_count_at_least_three",
            all(count >= 3 for count in vertices),
            f"hatch boundary vertex counts: {vertices}"
            + ("" if all(count >= 3 for count in vertices) else " (a ring needs 3)"),
            vertex_counts=vertices,
        )
    )
    finite_areas = [area for area in areas if math.isfinite(area)]
    checks.append(
        _check(
            "hatch_area_positive",
            len(finite_areas) == len(areas) and all(area > tol for area in finite_areas),
            f"hatch areas in mm2: {areas}"
            + (
                ""
                if len(finite_areas) == len(areas) and all(area > tol for area in finite_areas)
                else " (a degenerate or non-finite boundary encloses nothing)"
            ),
            areas_mm2=areas,
        )
    )
    checks.append(
        _check(
            "hatch_pattern_name_present",
            all(name.strip() for name in patterns),
            f"hatch pattern names: {patterns}"
            + ("" if all(name.strip() for name in patterns) else " (an unnamed pattern is not a contract)"),
            pattern_names=patterns,
        )
    )
    measured["area_mm2"] = sum(areas)
    measured["perimeter_mm"] = sum(perimeters)
    measured["areas_mm2"] = areas
    measured["pattern_names"] = patterns
    measured["handles"] = [item["handle"] for item in hatch_entities]
    return checks, measured


def _numeric_dimension_text(value: str) -> tuple[float, float] | None:
    """(value, quantum) if ``value`` is a plain number, else None.

    The quantum comes from the string's own decimals, so the comparison
    tolerance is derived from what the drawing says rather than guessed.
    """
    text = value.strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    decimals = len(text.split(".")[1]) if "." in text else 0
    return (number, 0.5 * (10.0 ** -decimals))


def _analyse_dim(
    dim_entities: list[dict[str, Any]], tol: float, require_dim: bool
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A dimension must be measurable and its own text must not lie.

    ``rendered_text`` is read out of the anonymous block, which is what a
    human sees; ``dxf.text`` is the ``"<>`` placeholder whenever the renderer
    owns the string, so comparing that would compare a placeholder.
    """
    checks: list[dict[str, Any]] = []
    measured: dict[str, Any] = {"count": len(dim_entities)}
    if not dim_entities:
        if require_dim:
            checks.append(
                _check(
                    "dim_present",
                    False,
                    "--only dim was requested but this drawing contains no DIMENSION",
                    measured=0,
                )
            )
        else:
            measured["not_required_but_checked"] = True
        return checks, measured

    values = [item.get("measurement") for item in dim_entities]
    readable = [value for value in values if value is not None]
    checks.append(
        _check(
            "dim_measurement_is_readable",
            len(readable) == len(values),
            f"measured {len(readable)} of {len(values)} dimensions"
            + ("" if len(readable) == len(values) else " (an unreadable one cannot be verified)"),
            measured=readable,
        )
    )
    positive = [value for value in readable if value > 0.0]
    checks.append(
        _check(
            "dim_measurement_positive",
            len(positive) == len(readable) and bool(readable),
            f"measurements: {readable}"
            + (
                ""
                if len(positive) == len(readable) and readable
                else " (a zero-length dimension has no direction and cannot be read)"
            ),
            measured=readable,
        )
    )

    mismatches: list[str] = []
    injected: list[str] = []
    for item in dim_entities:
        rendered = str(item.get("rendered_text") or "")
        parsed = _numeric_dimension_text(rendered)
        actual = item.get("measurement")
        if parsed is None:
            # dim.py records caller-supplied strings verbatim (TextMode
            # INJECTED) and the "<>" placeholder is renderer-owned. Neither is
            # a number to check against, so it is reported, not failed.
            injected.append(rendered or str(item.get("text") or ""))
            continue
        if actual is None:
            continue
        value, quantum = parsed
        if abs(value - actual) > max(tol, quantum):
            mismatches.append(f"{item['handle']}: reads {rendered!r}, measures {actual:g}")
    checks.append(
        _check(
            "dim_text_matches_measurement",
            not mismatches,
            "every dimension's rendered text agrees with its measurement"
            if not mismatches
            else f"dimension text disagrees with the geometry: {'; '.join(mismatches)}",
            mismatches=mismatches,
        )
    )
    measured["measurements"] = readable
    measured["rendered_texts"] = [str(item.get("rendered_text") or "") for item in dim_entities]
    measured["injected_texts"] = injected
    measured["handles"] = [item["handle"] for item in dim_entities]
    return checks, measured


def _analyse_text(
    text_entities: list[dict[str, Any]], tol: float, require_text: bool
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A recorded annotation must carry content and a usable height."""
    checks: list[dict[str, Any]] = []
    measured: dict[str, Any] = {"count": len(text_entities)}
    if not text_entities:
        if require_text:
            checks.append(
                _check(
                    "text_present",
                    False,
                    "--only text was requested but this drawing contains no TEXT or MTEXT",
                    measured=0,
                )
            )
        else:
            measured["not_required_but_checked"] = True
        return checks, measured

    contents = [str(item.get("text") or "") for item in text_entities]
    checks.append(
        _check(
            "text_content_non_empty",
            all(content.strip() for content in contents),
            f"annotation content: {contents}"
            + ("" if all(content.strip() for content in contents) else " (empty text is not an annotation)"),
            contents=contents,
        )
    )
    heights = []
    for item in text_entities:
        try:
            heights.append(float(item.get("height", 0.0)))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            heights.append(float("nan"))
    usable = [height for height in heights if math.isfinite(height) and height > tol]
    checks.append(
        _check(
            "text_height_is_positive_finite",
            len(usable) == len(heights) and bool(heights),
            f"text heights: {heights}"
            + (
                ""
                if len(usable) == len(heights) and heights
                else " (a non-finite or zero height cannot be rendered)"
            ),
            heights=heights,
        )
    )
    measured["contents"] = contents
    measured["heights"] = heights
    measured["kinds"] = [item["type"] for item in text_entities]
    measured["handles"] = [item["handle"] for item in text_entities]
    return checks, measured


def _analyse_block(
    doc: Any,
    entities: list[dict[str, Any]],
    require_block: bool,
    allow_hidden_insert: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Block definitions, and the price of an INSERT.

    The visibility check replaces the old ``INSERT`` ban. An INSERT is
    legitimate output (``block --mode insert``); what is not legitimate is an
    INSERT nobody acknowledged, because it contributes nothing to any
    downstream measurement while still looking like content. So the check
    reports the hidden entity count and length and FAILS unless the caller
    says the concealment was intended.
    """
    checks: list[dict[str, Any]] = []
    block_names = [
        entry.name
        for entry in doc.blocks
        if not entry.name.startswith("*") and entry.name not in DEFAULT_BLOCK_NAMES
    ]
    inserts = [item for item in entities if item["type"] == "INSERT"]
    measured: dict[str, Any] = {
        "block_names": block_names,
        "insert_count": len(inserts),
    }
    if block_names or require_block:
        # Only emitted when it can say something: a scope that did not ask for
        # a block and found none reports its absence in `measured` rather than
        # inventing a FAIL for a drawing that was never asked to hold a block.
        checks.append(
            _check(
                "block_definition_present",
                bool(block_names),
                f"block definitions: {block_names}"
                + (
                    ""
                    if block_names
                    else " (--only block was requested but this drawing has no block)"
                ),
                block_names=block_names,
            )
        )
    else:
        measured["block_definition_not_required"] = True

    hidden: dict[str, dict[str, Any]] = {}
    # INSERTs whose block definition is not in the BLOCK table. Their cost
    # cannot be measured at all, which is a different failure from "measured
    # and found to be zero", so they are tracked by name and reported.
    dangling: list[str] = []
    total_hidden = 0
    total_length = 0.0
    for item in inserts:
        block_name = str(item.get("name", ""))
        geometry = _block_geometry(doc, block_name)
        if geometry is MISSING_BLOCK or geometry is None:
            if block_name not in dangling:
                dangling.append(block_name)
            hidden[block_name] = {
                "entities": None,
                "length_mm": None,
                "handle": item["handle"],
                "block_definition": "missing",
            }
            continue
        count, length = geometry
        hidden[block_name] = {
            "entities": count,
            "length_mm": length,
            "handle": item["handle"],
            "block_definition": "present",
        }
        total_hidden += count
        total_length += length
    measured["dangling_insert_block_names"] = dangling
    measured["hidden"] = hidden
    measured["hidden_entities"] = total_hidden
    measured["hidden_length_mm"] = total_length

    if not inserts:
        checks.append(
            _check(
                "insert_downstream_visibility",
                True,
                "no INSERT in the drawing: nothing is hidden from downstream "
                "measurement",
                hidden_entities=0,
                hidden_length_mm=0.0,
                dangling_block_names=[],
            )
        )
    else:
        # A dangling INSERT is not concealment the caller can acknowledge: the
        # cost is unmeasurable, not zero, so it fails whatever
        # --allow-hidden-insert says, and the missing block names are named
        # here instead of raising out of the whole report.
        measured_insert_detail = (
            f"INSERT hides {total_hidden} entities / {total_length:g} mm from "
            f"downstream measurement and contributes 0: {hidden}"
            + (
                f". INSERT also names block definition(s) that are not in the "
                f"BLOCK table: {dangling}; the geometry behind them cannot be "
                f"measured at all, so part of this cost is unknown"
                if dangling
                else ""
            )
        )
        if dangling and allow_hidden_insert:
            tail = (
                "; acknowledged with --allow-hidden-insert, but a dangling "
                "INSERT is not concealed geometry and stays unmeasurable"
            )
        elif dangling:
            tail = (
                f". Additionally {dangling} name block definition(s) that are "
                "not in the BLOCK table, which no acknowledgement can resolve. "
                f"{INSERT_VISIBILITY_NOTE}"
            )
        elif allow_hidden_insert:
            tail = "; acknowledged with --allow-hidden-insert"
        else:
            tail = f". {INSERT_VISIBILITY_NOTE}"
        checks.append(
            _check(
                "insert_downstream_visibility",
                allow_hidden_insert and not dangling,
                measured_insert_detail + tail,
                hidden_entities=total_hidden,
                hidden_length_mm=total_length,
                acknowledged=allow_hidden_insert,
                dangling_block_names=dangling,
                hidden=hidden,
            )
        )
    return checks, measured


def _analyse_layers(
    doc: Any, require_layers: bool, expect_layer_names: Sequence[str] | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The LAYER table, measured independently of what is drawn on it.

    A drawing can verify with zero modelspace entities and still be wrong
    about its layers -- that is the whole point of a ``layers`` recording --
    so this reads the table itself rather than inferring it from entities.
    """
    checks: list[dict[str, Any]] = []
    table = [(str(entry.dxf.name), int(entry.dxf.color)) for entry in doc.layers]
    non_default = [name for name, _color in table if name not in DEFAULT_LAYER_NAMES]
    measured: dict[str, Any] = {
        "layer_table": [name for name, _color in table],
        "non_default_layers": non_default,
        "colors": {name: color for name, color in table},
    }
    checks.append(
        _check(
            "layers_table_has_content",
            bool(non_default) or not require_layers,
            f"non-default layers: {non_default}"
            + ("" if non_default else " (--only layers was requested but the table has none)"),
            non_default_layers=non_default,
        )
    )
    if expect_layer_names is not None:
        wanted = [str(name) for name in expect_layer_names]
        present = {name.upper() for name, _color in table}
        missing = [name for name in wanted if name.upper() not in present]
        checks.append(
            _check(
                "layer_names_match_expectation",
                not missing,
                f"expected layers {wanted}, table holds {non_default}"
                + (f"; missing: {missing}" if missing else ""),
                expected=wanted,
                measured=[name for name, _color in table],
                missing=missing,
            )
        )
    return checks, measured


def verify_drawing(
    path: Path,
    *,
    tol: float = 1e-6,
    expect_entities: int | None = None,
    expect_wall_thickness: float | None = None,
    expect_door_width: float | None = None,
    expect_door_center: Sequence[float] | None = None,
    expect_window_width: float | None = None,
    expect_opening_width: float | None = None,
    expect_hatch_area: float | None = None,
    expect_hatch_pattern: str | None = None,
    expect_dim_measurement: float | Sequence[float] | None = None,
    expect_text_content: str | None = None,
    expect_block_name: str | None = None,
    expect_layer_names: Sequence[str] | None = None,
    require: str = "both",
    unresolved_layers: Sequence[str] = (),
    allow_hidden_insert: bool = False,
) -> dict[str, Any]:
    """Read ``path`` back and return a verification report.

    Pure read-only inspection: the drawing is opened, never saved.

    Nine ``--only`` scopes, one per recording module, plus per-module
    ``expect_*`` arguments. The five that used to have no verification path at
    all (hatch, dim, text, block, layer) each got a scope AND expectations,
    because a scope alone can only say "something is here", not "the thing I
    asked for is here".
    """
    _doc, entities = _read_modelspace(path)

    layer_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for item in entities:
        layer_counts[item["layer"]] = layer_counts.get(item["layer"], 0) + 1
        type_counts[item["type"]] = type_counts.get(item["type"], 0) + 1

    checks: list[dict[str, Any]] = []
    if require not in VERIFY_SCOPES:
        raise CliError(
            "--only must be one of " + ", ".join(sorted(VERIFY_SCOPES))
        )
    # 'both' keeps its measured meaning (wall + door) so an existing
    # wall/door drawing verifies exactly as before. 'all' is the strict
    # fail-closed superset. Either way, a window, opening, hatch, dimension,
    # text or block that is PRESENT is always checked: 'both' does not become
    # a way to skip them.
    require_wall = require in ("both", "wall", "all")
    require_door = require in ("both", "door", "all")
    require_window = require in ("window", "all")
    require_opening = require in ("opening", "all")
    require_hatch = require in ("hatch", "all")
    require_dim = require in ("dim", "all")
    require_text = require in ("text", "all")
    require_block = require in ("block", "all")
    require_layers = require in ("layers", "all")
    total = len(entities)
    checks.append(
        _check(
            "entity_count_reported",
            True,
            f"{total} entities in the modelspace",
            measured=total,
        )
    )
    if expect_entities is not None:
        checks.append(
            _check(
                "entity_count_matches_expectation",
                total == expect_entities,
                f"expected {expect_entities} entities, found {total}",
                expected=expect_entities,
                measured=total,
            )
        )

    forbidden = {
        name: type_counts.get(name, 0)
        for name in FORBIDDEN_DXF_TYPES
        if type_counts.get(name)
    }
    checks.append(
        _check(
            "no_hatch_entity",
            not forbidden,
            f"forbidden entity types present: {forbidden}"
            if forbidden
            else "no HATCH entity (the hatch recorder writes a boundary polyline "
            "plus a pattern contract, never a HATCH)",
            found=forbidden,
        )
    )

    semantics = {name: str(classify_layer(name)) for name in sorted(layer_counts)}
    # A layer that classifies as UNKNOWN is normally a FAIL -- but two
    # recorders REFUSE to invent a layer name (hatch.HATCH_LAYER_STATUS and
    # text.TEXT_LAYER_STATUS are both "UNRESOLVED") and push the decision to
    # the caller, and block declares the same. So the drawing may legitimately
    # contain an unclassified layer, exactly as the opening recorder's TEMP-
    # layers are legitimate. Those are declared per-drawing with
    # --unresolved-layer, are listed in the check's own detail, and any
    # UNDECLARED unknown layer is still a FAIL.
    declared = {str(name).upper() for name in unresolved_layers}
    unresolved_present = [
        name
        for name in semantics
        if name.upper() in declared or name in OPENING_UNRESOLVED_LAYERS
    ]
    unknown = [
        name
        for name, semantic in semantics.items()
        if semantic == LayerSemantic.UNKNOWN and name not in unresolved_present
    ]
    checks.append(
        _check(
            "layer_semantics_mapped",
            not unknown,
            f"layers mapping to {LayerSemantic.UNKNOWN}: {unknown}"
            if unknown
            else (
                f"mapping {semantics}"
                + (
                    f"; excluded from this check as documented unresolved "
                    f"fallbacks: {unresolved_present}"
                    if unresolved_present
                    else ""
                )
            ),
            mapping=semantics,
            excluded_unresolved=unresolved_present,
        )
    )

    wall_lines: list[dict[str, Any]] = []
    centerlines: list[dict[str, Any]] = []
    door_lines: list[dict[str, Any]] = []
    door_arcs: list[dict[str, Any]] = []
    door_entities: list[dict[str, Any]] = []
    window_entities: list[dict[str, Any]] = []
    opening_entities: list[dict[str, Any]] = []
    for item in entities:
        semantic = classify_layer(item["layer"])
        if semantic is LayerSemantic.WALL:
            if item["type"] == "LINE":
                wall_lines.append(item)
        elif semantic is LayerSemantic.CENTERLINE:
            if item["type"] == "LINE":
                centerlines.append(item)
        elif semantic is LayerSemantic.DOOR:
            if item["type"] == "LINE":
                door_lines.append(item)
            elif item["type"] == "ARC":
                door_arcs.append(item)
            door_entities.append(item)
        elif semantic in (LayerSemantic.WINDOW, LayerSemantic.WINDOW_BAR):
            window_entities.append(item)
        elif item["layer"] in OPENING_UNRESOLVED_LAYERS:
            opening_entities.append(item)

    # The five recorders that used to have no verification path. They are
    # collected by ENTITY TYPE, not by layer, because two of them refuse to
    # name a layer at all (see UNRESOLVED_LAYER_NOTE) and one of them
    # (dim) records on DIM, which no other recorder uses.
    hatch_entities = [
        item
        for item in entities
        if item["type"] == "LWPOLYLINE" and item.get("hatch")
    ]
    dim_entities = [item for item in entities if item["type"] == "DIMENSION"]
    text_entities = [item for item in entities if item["type"] in ("TEXT", "MTEXT")]

    wall_checks, wall_measured = _analyse_wall(
        wall_lines, centerlines, tol, require_wall=require_wall
    )
    if require_wall:
        checks.extend(wall_checks)
    else:
        # The caller scoped this drawing to the door, so a missing wall is not
        # a defect here. It is still measured and reported if present.
        wall_measured["not_required"] = True
    if expect_wall_thickness is not None:
        measured_thickness = wall_measured.get("thickness_mm")
        if measured_thickness is None:
            # S1: the user asked for this assertion. Not being able to measure
            # it is NOT the same as it holding. Creating no check and
            # reporting ok:true is the silent-success path this project keeps
            # shipping, so it becomes a visible FAIL instead.
            checks.append(
                _check(
                    "wall_thickness_matches_expectation",
                    False,
                    f"--expect-wall-thickness {expect_wall_thickness:g} was "
                    "requested but no wall thickness could be measured in this "
                    "drawing (no wall faces were found). Narrow the scope with "
                    "--only wall, or drop the expectation",
                    expected=expect_wall_thickness,
                    measured=None,
                )
            )
        else:
            checks.append(
                _check(
                    "wall_thickness_matches_expectation",
                    abs(measured_thickness - expect_wall_thickness) <= tol,
                    f"expected thickness {expect_wall_thickness:g} mm, measured "
                    f"{measured_thickness:g} mm",
                    expected=expect_wall_thickness,
                    measured=measured_thickness,
                )
            )

    door_checks, door_measured = _analyse_door(door_entities, tol)
    if require_door:
        checks.extend(door_checks)
    else:
        door_measured["not_required"] = True
    if expect_door_width is not None:
        measured_width = door_measured.get("opening_width_mm")
        if measured_width is None:
            checks.append(
                _check(
                    "door_width_matches_expectation",
                    False,
                    f"--expect-door-width {expect_door_width:g} was requested but "
                    "no door opening width could be measured in this drawing",
                    expected=expect_door_width,
                    measured=None,
                )
            )
        else:
            checks.append(
                _check(
                    "door_width_matches_expectation",
                    abs(measured_width - expect_door_width) <= tol,
                    f"expected width {expect_door_width:g} mm, measured "
                    f"{measured_width:g} mm",
                    expected=expect_door_width,
                    measured=measured_width,
                )
            )
    if expect_door_center is not None and "opening_center" in door_measured:
        want = (float(expect_door_center[0]), float(expect_door_center[1]))
        got = door_measured["opening_center"]
        checks.append(
            _check(
                "door_center_matches_expectation",
                math.dist(want, got) <= tol,
                f"expected centre ({want[0]:g},{want[1]:g}), measured ({got[0]:g},{got[1]:g})",
                expected=list(want),
                measured=list(got),
            )
        )
    elif expect_door_center is not None:
        checks.append(
            _check(
                "door_center_matches_expectation",
                False,
                f"--expect-door-center {expect_door_center[0]:g},{expect_door_center[1]:g} "
                "was requested but no door centre could be measured in this drawing",
                expected=[float(expect_door_center[0]), float(expect_door_center[1])],
                measured=None,
            )
        )

    # A window that is in the drawing is always checked, whatever --only says:
    # it would be a hole in the fail-closed contract to record a window and
    # then verify the drawing with the window unscanned.
    window_checks, window_measured = _analyse_window(window_entities, tol)
    if require_window or window_entities:
        checks.extend(window_checks)
    if not require_window:
        window_measured["not_required_but_checked"] = True
    if expect_window_width is not None:
        measured_window = window_measured.get("opening_width_mm")
        if measured_window is None:
            checks.append(
                _check(
                    "window_width_matches_expectation",
                    False,
                    f"--expect-window-width {expect_window_width:g} was requested "
                    "but no window opening width could be measured in this drawing",
                    expected=expect_window_width,
                    measured=None,
                )
            )
        else:
            checks.append(
                _check(
                    "window_width_matches_expectation",
                    abs(measured_window - expect_window_width) <= tol,
                    f"expected width {expect_window_width:g} mm, measured "
                    f"{measured_window:g} mm",
                    expected=expect_window_width,
                    measured=measured_window,
                )
            )

    # Same rule for the opening, plus its own unresolved-layer WARN.
    opening_checks, opening_measured = _analyse_opening(opening_entities, tol)
    if require_opening or opening_entities:
        checks.extend(opening_checks)
    if not require_opening:
        opening_measured["not_required_but_checked"] = True
    if expect_opening_width is not None:
        measured_opening = opening_measured.get("opening_width_mm")
        if measured_opening is None:
            checks.append(
                _check(
                    "opening_width_matches_expectation",
                    False,
                    f"--expect-opening-width {expect_opening_width:g} was requested "
                    "but no wall-opening width could be measured in this drawing",
                    expected=expect_opening_width,
                    measured=None,
                )
            )
        else:
            checks.append(
                _check(
                    "opening_width_matches_expectation",
                    abs(measured_opening - expect_opening_width) <= tol,
                    f"expected width {expect_opening_width:g} mm, measured "
                    f"{measured_opening:g} mm",
                    expected=expect_opening_width,
                    measured=measured_opening,
                )
            )

    # ---- the five recorders that had no verification path before this ------
    hatch_checks, hatch_measured = _analyse_hatch(hatch_entities, tol, require_hatch)
    checks.extend(hatch_checks)
    if expect_hatch_area is not None:
        measured_area = hatch_measured.get("area_mm2") if hatch_entities else None
        checks.append(
            _check(
                "hatch_area_matches_expectation",
                measured_area is not None and abs(measured_area - expect_hatch_area) <= tol,
                f"--expect-hatch-area {expect_hatch_area:g} was requested but "
                "no hatch boundary could be measured in this drawing"
                if measured_area is None
                else (
                    f"expected area {expect_hatch_area:g} mm2, measured "
                    f"{measured_area:g} mm2"
                ),
                expected=expect_hatch_area,
                measured=measured_area,
            )
        )
    if expect_hatch_pattern is not None:
        names = hatch_measured.get("pattern_names") or []
        checks.append(
            _check(
                "hatch_pattern_matches_expectation",
                expect_hatch_pattern in names,
                f"expected pattern {expect_hatch_pattern!r}, found {names}"
                + (
                    ""
                    if names
                    else " (no hatch pattern contract is present in this drawing)"
                ),
                expected=expect_hatch_pattern,
                measured=names,
            )
        )

    dim_checks, dim_measured = _analyse_dim(dim_entities, tol, require_dim)
    checks.extend(dim_checks)
    for index, expected in enumerate(_as_float_sequence(expect_dim_measurement)):
        values = [float(value) for value in (dim_measured.get("measurements") or [])]
        matched = next(
            (value for value in values if abs(value - expected) <= tol), None
        )
        checks.append(
            _check(
                expectation_check_name("dim_measurement_matches_expectation", index),
                matched is not None,
                f"--expect-dim-measurement {expected:g} was "
                "requested but no dimension measurement could be read from this "
                "drawing"
                if not values
                else (
                    f"expected measurement {expected:g} mm is present in this "
                    f"drawing as {matched:g} mm; measured {values}"
                )
                if matched is not None
                else (
                    f"expected measurement {expected:g} mm, no measured "
                    f"dimension matches it; measured {values}"
                ),
                expected=expected,
                measured=values,
                matched=matched,
            )
        )

    text_checks, text_measured = _analyse_text(text_entities, tol, require_text)
    checks.extend(text_checks)
    if expect_text_content is not None:
        contents = text_measured.get("contents") or []
        checks.append(
            _check(
                "text_content_matches_expectation",
                expect_text_content in contents,
                f"expected content {expect_text_content!r}, found {contents}"
                + (
                    ""
                    if contents
                    else " (no TEXT or MTEXT is present in this drawing)"
                ),
                expected=expect_text_content,
                measured=contents,
            )
        )

    block_checks, block_measured = _analyse_block(
        _doc, entities, require_block, allow_hidden_insert
    )
    checks.extend(block_checks)
    if expect_block_name is not None:
        names = block_measured.get("block_names") or []
        checks.append(
            _check(
                "block_name_matches_expectation",
                expect_block_name in names,
                f"expected block {expect_block_name!r}, found {names}"
                + ("" if names else " (this drawing defines no block)"),
                expected=expect_block_name,
                measured=names,
            )
        )

    layer_checks, layers_measured = _analyse_layers(
        _doc, require_layers, expect_layer_names
    )
    checks.extend(layer_checks)

    failed = [check["name"] for check in checks if check["status"] == "FAIL"]
    warned = [check["name"] for check in checks if check["status"] == "WARN"]
    return {
        "command": "verify",
        "ok": not failed,
        "in": str(path),
        "tol": tol,
        "require": require,
        "entity_count": total,
        "layer_counts": layer_counts,
        "type_counts": type_counts,
        "layer_semantics": semantics,
        "forbidden_entity_types": list(FORBIDDEN_DXF_TYPES),
        "wall": wall_measured,
        "door": door_measured,
        "window": window_measured,
        "opening": opening_measured,
        "hatch": hatch_measured,
        "dim": dim_measured,
        "text": text_measured,
        "block": block_measured,
        "layers": layers_measured,
        "unresolved_layers_declared": list(unresolved_layers),
        "unresolved_layer_note": UNRESOLVED_LAYER_NOTE if unresolved_layers else None,
        "insert_visibility_note": INSERT_VISIBILITY_NOTE if block_measured.get("insert_count") else None,
        "opening_layer_mapping_resolved": OPENING_LAYER_MAPPING_RESOLVED,
        "checks": checks,
        "failed": failed,
        # Reported, never blocking. See OPENING_LAYER_MAPPING_NOTE.
        "warnings": warned,
        "exit_code": EXIT_OK if not failed else EXIT_VERIFY_FAILED,
    }


def cmd_verify(args: argparse.Namespace) -> int:
    path = Path(args.input).expanduser()
    if not path.exists():
        raise CliError(f"no such drawing: {path}")
    report = verify_drawing(
        path,
        tol=args.tol,
        expect_entities=args.expect_entities,
        expect_wall_thickness=args.expect_wall_thickness,
        expect_door_width=args.expect_door_width,
        expect_door_center=args.expect_door_center,
        expect_window_width=args.expect_window_width,
        expect_opening_width=args.expect_opening_width,
        expect_hatch_area=args.expect_hatch_area,
        expect_hatch_pattern=args.expect_hatch_pattern,
        expect_dim_measurement=args.expect_dim_measurement,
        expect_text_content=args.expect_text_content,
        expect_block_name=args.expect_block_name,
        expect_layer_names=args.expect_layers,
        require=args.only,
        unresolved_layers=args.unresolved_layer or (),
        allow_hidden_insert=args.allow_hidden_insert,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"verify {path}")
        print(
            f"  {report['entity_count']} entities, "
            f"layers {_format_counts(report['layer_counts'])}"
        )
        for check in report["checks"]:
            marker = {"PASS": "ok  ", "FAIL": "FAIL", "WARN": "warn"}[check["status"]]
            print(f"  [{marker}] {check['name']}: {check['detail']}")
        if report["ok"]:
            print(f"RESULT: PASS ({len(report['checks'])} checks)")
        else:
            print(
                f"RESULT: FAIL ({len(report['failed'])} of "
                f"{len(report['checks'])} checks failed)"
            )
            print(f"  failed: {', '.join(report['failed'])}")
        if report["warnings"]:
            print(
                f"WARNING (does not affect the verdict): "
                f"{', '.join(report['warnings'])}"
            )
    return EXIT_OK if report["ok"] else EXIT_VERIFY_FAILED


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def _format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="all-in-cad-recorder",
        description=(
            "Record walls and doors into a DXF drawing and verify the result. "
            "Geometry, validation and DXF writing are done by the wall and door "
            "recorder modules; this CLI only turns arguments into their calls."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "exit codes: 0 success, 1 verification failed, 2 bad arguments, "
            "refused output path or I/O, 3 the write failed and was rolled back"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    wall_parser = subparsers.add_parser(
        "wall", help="record one straight wall from a centreline"
    )
    _add_wall_arguments(wall_parser)
    _add_out_arguments(wall_parser)
    _add_json_flag(wall_parser)
    wall_parser.set_defaults(func=cmd_wall)

    door_parser = subparsers.add_parser(
        "door", help="record one plan-view door from a hinge point or a centre point"
    )
    _add_door_arguments(door_parser, include_out=True)
    _add_json_flag(door_parser)
    door_parser.set_defaults(func=cmd_door)

    plan_parser = subparsers.add_parser(
        "plan", help="record a wall and a door on it into one drawing"
    )
    _add_wall_arguments(plan_parser)
    _add_door_arguments(plan_parser, include_out=False)
    plan_parser.add_argument(
        "--hinge-tolerance",
        type=float,
        default=DEFAULT_HINGE_TOLERANCE_MM,
        metavar="MM",
        help=(
            "how far the door hinge may sit from the wall centreline; exceeding it "
            f"fails the command (default: {DEFAULT_HINGE_TOLERANCE_MM:g})"
        ),
    )
    _add_out_arguments(plan_parser)
    _add_json_flag(plan_parser)
    plan_parser.set_defaults(func=cmd_plan)

    window_parser = subparsers.add_parser(
        "window", help="record one plan-view window symbol (LINE + ARC only)"
    )
    _add_window_arguments(window_parser)
    _add_out_arguments(window_parser)
    _add_json_flag(window_parser)
    window_parser.set_defaults(func=cmd_window)

    opening_parser = subparsers.add_parser(
        "opening", help="record one wall opening boundary + symbol (LINE only)"
    )
    _add_opening_arguments(opening_parser)
    _add_out_arguments(opening_parser)
    _add_json_flag(opening_parser)
    opening_parser.set_defaults(func=cmd_opening)

    hatch_parser = subparsers.add_parser(
        "hatch",
        help=(
            "record a hatch as a closed boundary polyline plus a pattern "
            "contract (no HATCH entity)"
        ),
    )
    _add_hatch_arguments(hatch_parser)
    _add_out_arguments(hatch_parser)
    _add_json_flag(hatch_parser)
    hatch_parser.set_defaults(func=cmd_hatch)

    dim_parser = subparsers.add_parser(
        "dim", help="record one dimension and read its measurement back"
    )
    _add_dim_arguments(dim_parser)
    _add_out_arguments(dim_parser)
    _add_json_flag(dim_parser)
    dim_parser.set_defaults(func=cmd_dim)

    text_parser = subparsers.add_parser(
        "text", help="record one TEXT or MTEXT annotation"
    )
    _add_text_arguments(text_parser)
    _add_out_arguments(text_parser)
    _add_json_flag(text_parser)
    text_parser.set_defaults(func=cmd_text)

    block_parser = subparsers.add_parser(
        "block", help="record a block definition and one instance of it"
    )
    _add_block_arguments(block_parser)
    _add_out_arguments(block_parser)
    _add_json_flag(block_parser)
    block_parser.set_defaults(func=cmd_block)

    layers_parser = subparsers.add_parser(
        "layers", help="record LAYER table entries, in order, with asserted attributes"
    )
    _add_layers_arguments(layers_parser)
    _add_out_arguments(layers_parser)
    _add_json_flag(layers_parser)
    layers_parser.set_defaults(func=cmd_layers)

    verify_parser = subparsers.add_parser(
        "verify", help="read a drawing back and check it (exit 1 on any failure)"
    )
    verify_parser.add_argument(
        "-i",
        "--in",
        dest="input",
        required=True,
        metavar="FILE.dxf",
        help="drawing to verify",
    )
    verify_parser.add_argument(
        "--tol", type=float, default=1e-6, metavar="MM", help="comparison tolerance (default: 1e-6)"
    )
    verify_parser.add_argument(
        "--expect-entities", type=int, default=None, metavar="N", help="required entity count"
    )
    verify_parser.add_argument(
        "--expect-wall-thickness",
        type=float,
        default=None,
        metavar="MM",
        help="required wall thickness",
    )
    verify_parser.add_argument(
        "--expect-door-width", type=float, default=None, metavar="MM", help="required opening width"
    )
    verify_parser.add_argument(
        "--expect-door-center",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        default=None,
        help="required opening centre",
    )
    verify_parser.add_argument(
        "--expect-window-width", type=float, default=None, metavar="MM", help="required window opening width"
    )
    verify_parser.add_argument(
        "--expect-opening-width", type=float, default=None, metavar="MM", help="required wall-opening width"
    )
    verify_parser.add_argument(
        "--expect-hatch-area",
        type=float,
        default=None,
        metavar="MM2",
        help="required total enclosed area of the hatch boundaries",
    )
    verify_parser.add_argument(
        "--expect-hatch-pattern",
        default=None,
        metavar="NAME",
        help="required hatch pattern name, e.g. ANSI31",
    )
    verify_parser.add_argument(
        "--expect-dim-measurement",
        type=float,
        action="append",
        default=None,
        metavar="MM",
        help=(
            "required measurement of some dimension in the drawing, matched "
            "against every dimension read back rather than only the first; "
            "repeat the flag to require several measurements, each of which is "
            "checked separately"
        ),
    )
    verify_parser.add_argument(
        "--expect-text-content",
        default=None,
        metavar="TEXT",
        help="required annotation content, matched exactly against the read-back text",
    )
    verify_parser.add_argument(
        "--expect-block-name",
        default=None,
        metavar="NAME",
        help="required block definition name in the block table",
    )
    verify_parser.add_argument(
        "--expect-layers",
        nargs="+",
        default=None,
        metavar="NAME",
        help=(
            "required LAYER table entries; every one must be present. Pass all "
            "the names after a single --expect-layers"
        ),
    )
    verify_parser.add_argument(
        "--unresolved-layer",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "declare a layer whose mapping is deliberately unresolved (hatch "
            "and text report LAYER_STATUS == UNRESOLVED and make the caller "
            "name the layer, which they then record and classify rather than "
            "refuse; block also reports UNRESOLVED but REFUSES any name that "
            "classifies as UNKNOWN, so it does not use this flag). "
            "Repeatable; declared layers are excluded "
            "from layer_semantics_mapped and listed in its detail. An "
            "undeclared unknown layer is still a FAIL"
        ),
    )
    verify_parser.add_argument(
        "--allow-hidden-insert",
        action="store_true",
        help=(
            "acknowledge that an INSERT in this drawing is intended. An INSERT "
            "hides its definition from every downstream measurement, so it "
            "fails insert_downstream_visibility until this is passed; the "
            "measured hidden entity count and length stay in the report"
        ),
    )
    verify_parser.add_argument(
        "--only",
        choices=VERIFY_SCOPES,
        default="both",
        help=(
            "what the drawing must contain. Default 'both' is fail-closed for "
            "wall and door: a wall that is not there, or a door that is not "
            "there, FAILS. One scope per recorder module -- wall, door, window, "
            "opening, hatch, dim, text, block, layers -- and 'all' requires "
            "every one of them. Anything that is PRESENT in the drawing is "
            "always checked, whatever this says"
        ),
    )
    _add_json_flag(verify_parser)
    verify_parser.set_defaults(func=cmd_verify)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if getattr(args, "hinge_tolerance", 1.0) < 0:
        print("error: --hinge-tolerance must be >= 0", file=sys.stderr)
        return EXIT_USAGE
    try:
        return int(args.func(args))
    except WriteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "command": args.command,
                        "ok": False,
                        "error": str(exc),
                        "exit_code": EXIT_WRITE_FAILED,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        return EXIT_WRITE_FAILED
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "command": args.command,
                        "ok": False,
                        "error": str(exc),
                        "exit_code": EXIT_USAGE,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(main())
