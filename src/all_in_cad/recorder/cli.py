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
``verify``  read a drawing back and check the things that must still be true

Every subcommand requires ``--out`` (the DXF to write) except ``verify``, which
reads the drawing named by ``--in``. ``--json`` switches stdout to a single
machine-readable JSON object.

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

* ``wall`` / ``door`` / ``plan`` / ``window`` / ``opening`` CREATE by default.
  If ``--out`` already exists, the command refuses with exit code 2 and does
  not touch the file.
* ``--force`` is the only way to replace an existing drawing, and the same flag
  means the same thing in every subcommand. It is one shared
  ``_add_out_arguments``, not five.
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
        capture_state,
        dxf_verifier,
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
FORBIDDEN_DXF_TYPES = ("INSERT", "HATCH")

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
# subcommand: verify
# ---------------------------------------------------------------------------


def _read_modelspace(path: Path) -> tuple[Any, list[dict[str, Any]]]:
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
        entities.append(item)
    return doc, entities


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
    require: str = "both",
) -> dict[str, Any]:
    """Read ``path`` back and return a verification report.

    Pure read-only inspection: the drawing is opened, never saved.
    """
    _doc, entities = _read_modelspace(path)

    layer_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for item in entities:
        layer_counts[item["layer"]] = layer_counts.get(item["layer"], 0) + 1
        type_counts[item["type"]] = type_counts.get(item["type"], 0) + 1

    checks: list[dict[str, Any]] = []
    if require not in ("both", "wall", "door", "window", "opening", "all"):
        raise CliError(
            "--only must be 'both', 'wall', 'door', 'window', 'opening' or 'all'"
        )
    # 'both' keeps its measured meaning (wall + door) so an existing
    # wall/door drawing verifies exactly as before. 'all' is the strict
    # fail-closed superset. Either way, a window or opening that is PRESENT is
    # always checked: 'both' does not become a way to skip them.
    require_wall = require in ("both", "wall", "all")
    require_door = require in ("both", "door", "all")
    require_window = require in ("window", "all")
    require_opening = require in ("opening", "all")
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
            "no_insert_or_hatch",
            not forbidden,
            f"forbidden entity types present: {forbidden}"
            if forbidden
            else "no INSERT and no HATCH",
            found=forbidden,
        )
    )

    semantics = {name: str(classify_layer(name)) for name in sorted(layer_counts)}
    # The opening recorder's TEMP- layers resolve to UNKNOWN by design
    # (LAYER_MAPPING_RESOLVED is False), so they are excluded here and
    # reported by _analyse_opening as a WARN instead. Excluding only these
    # exact names keeps a genuinely unknown layer (ZZZ_MYSTERY) a FAIL.
    unresolved_present = [name for name in semantics if name in OPENING_UNRESOLVED_LAYERS]
    unknown = [
        name
        for name, semantic in semantics.items()
        if semantic == LayerSemantic.UNKNOWN and name not in OPENING_UNRESOLVED_LAYERS
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
        "wall": wall_measured,
        "door": door_measured,
        "window": window_measured,
        "opening": opening_measured,
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
        require=args.only,
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
        "--only",
        choices=("both", "wall", "door", "window", "opening", "all"),
        default="both",
        help=(
            "what the drawing must contain. Default 'both' is fail-closed for "
            "wall and door: a wall that is not there, or a door that is not "
            "there, FAILS. 'window' and 'opening' make those mandatory, and "
            "'all' requires all four. A window or opening that is PRESENT in "
            "the drawing is always checked, whatever this says"
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
