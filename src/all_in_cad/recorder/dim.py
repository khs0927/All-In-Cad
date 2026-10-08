"""Dimension recorder: writes DIMENSION entities by CONSTRUCTION, never by command.

=====================================================================
WHY THIS MODULE GENERATES DIMENSIONS AND NEVER EXECUTES DIMLINEAR
=====================================================================
[OBSERVED, session history] In ZWCAD, sending ``DIMLINEAR`` left the command in a
live state, where it swallowed the next input -- especially coordinates -- and
door placement then failed repeatedly. Worse, the *diagnostic tool built to
investigate that failure was itself contaminated*: entity deltas picked up the
user's concurrent work, so a wrong verdict was produced, and the undo that
followed erased the user's own drawing.

Three distinct hazards, and this module is structured against all three:

1. A dimension command left active consumes the NEXT input. Coordinate entry is
   exactly what it consumes, so a "transmit failed" can be a probe being eaten
   rather than a rejected send. A diagnostic that reports "the command blocks all
   input" is an over-generalisation from one contaminated observation.
2. Entity deltas are only trustworthy when the document is otherwise quiescent.
   Concurrent user work pollutes them.
3. Consequently a failure verdict that changes the document is not a measurement,
   it is a side effect -- and undo cannot distinguish the two.

THE STRUCTURAL ANSWER: this repository has no COM, no keystroke synthesis and no
prompt-driven execution at all. Therefore a dimension here is not "run", it is
"built". ``make_dim`` computes pure 2D geometry, and ``write_dim``/``ensure_dim``
construct ``DIMENSION`` entities through the ezdxf data model. There is no code
path in this module that can transmit a command, open a prompt, or consume
console input, so hazards 1-3 are unreachable rather than merely avoided. The
deliberate cost is stated below.

COST OF GENERATING RATHER THAN EXECUTING [DESIGN]
The generated dimension is an ezdxf/ACI-authored entity, not the output of the
vendor's dimension engine. It will not reproduce vendor-specific conveniences
(measured-unit scaling rules, dynamic dragging, gap-jump behaviour, style
inheritance from the active dimstyle beyond what is set explicitly). The upside
is that the result is deterministic, inspectable, and reproducible in a test --
none of which was true of the command-driven attempt. [UNRESOLVED] Whether the
original runtime's dimension output matches this entity composition is UNKNOWN
and was never observed; nothing here should be read as a claim about it.

=====================================================================
UNITS: MILLIMETRES  [DESIGN, grounded in OBSERVED repository data]
=====================================================================
There is no dimension-specific observation, so mm is chosen from what the
repository has actually measured elsewhere:

  * wall thickness 200            (observed, wall.py: faces at +/-100)
  * door width 900                (observed, opening.py: CONFIRMED 900 mm)
  * window width 1500             (observed, opening.py: CONFIRMED 1500 mm)
  * door width presets 30/60/90/120/150/180 (observed DCL, door.py)

All of these are millimetre magnitudes, and the presets are the only numbers in
the repository that are NOT mm -- they are the dialog's cm entry. So mm is the
drawing unit and the preset set is a UI convenience, not a competing unit. Every
length in this module is therefore millimetres, and :data:`DEFAULT_UNIT` records
that as code rather than leaving it to a reader's assumption.

A dimension is nevertheless written WITHOUT a unit suffix by default
(``unit_suffix=""``). [DESIGN] A suffix is display policy, not measurement: the
measurement stays the bare number, and the drawing carries its unit in
``$INSUNITS`` / the dimstyle. Callers who want "3000 mm" pass
``unit_suffix="mm"``.

=====================================================================
TEXT POLICY: EXPLICIT COMPUTED TEXT IS THE DEFAULT, NOT A CAD "<>"
=====================================================================
Auto-measured text (the DXF ``"<>"`` placeholder) and explicitly injected text
are both supported. The default is EXPLICIT, computed by this module. That is not
a stylistic preference -- it is forced by a measured defect:

[OBSERVED, measured on this host, ezdxf 1.4.4] Letting the renderer produce the
text silently DROPS THE DECIMAL SEPARATOR. Measured, with the stock ``EZDXF``
dimstyle (``dimdec=2``, ``dimdsep=ord('.')``):

    3000.0 mm -> "300000"      1234.5 mm -> "123450"
    900.0 mm  -> "90000"       200.0 mm  -> "20000"

So a 1234.5 mm measurement is written into the drawing as the string ``"123450"``,
which is wrong by a factor of 100 and indistinguishable from a 123450 mm
measurement to any downstream reader. Changing ``dimdsep`` to a comma does not
help; the separator is lost either way. This was reproduced four times with four
different values before being treated as a property of this host's renderer.

A dimension whose text cannot be trusted to match its geometry is precisely the
"silent success" this repository treats as its worst failure mode, so the module
does not delegate text. It formats the number itself (:func:`format_measurement`),
writes that string, and then READS IT BACK and compares it to the geometry. A
mismatch raises :class:`DimVerifyError` instead of returning a record.

The two supported modes, and what each means:

  * ``text=None`` (DEFAULT) -- text_mode="measured". This module measures the
    geometry, formats it with the requested precision, and injects that string
    explicitly. It is "automatic" from the caller's point of view and is
    self-verifying.
  * ``text="..."`` -- text_mode="injected". The caller owns the string verbatim
    (a tolerance callout, a room name, a note). The measured value is still
    computed and still recorded on the record, so an injected string that
    contradicts the geometry is visible in the record rather than hidden.
  * ``text="<>"`` -- text_mode="auto_delegated". Hands formatting back to the
    renderer. Supported for completeness, but given the separator loss measured
    above the readback check is deliberately STRICTER here: the module compares
    the rendered block text against the value it measured and raises if they
    differ. On this host that means this mode raises rather than writing a wrong
    number. It is exposed, not defaulted, and the failure is loud.

=====================================================================
ARCHITECTURE: ENSURE (idempotent, target-bound), NOT WRITE-AND-HOPE
=====================================================================
A CAD dimension is not an object. It is a REFERENCE that measures an entity
identified by handle. Two architectures are possible:

  (A) WRITE -- emit a fresh DIMENSION on every call. The dimension is a value:
      the number is frozen into the text at write time. Re-running the recorder
      after the wall moves leaves the old number in the drawing forever, and
      nothing in the document records that it has gone stale. Re-running also
      DUPLICATES the dimension. Both failures are silent.

  (B) ENSURE -- the dimension is a binding: a stable key derived from the target
      entity handle, the dimension kind and the layer. Calling again finds the
      existing binding, RE-MEASURES THE LIVE TARGET, updates the text if the
      number moved, and re-renders in place. Calling again with nothing changed
      is a no-op that returns the same handle.

THIS MODULE CHOOSES (B), and the choice is load-bearing:

  * It is the only one of the two that is correct after the target changes. A
    dimension's whole purpose is to be true of its subject; WRITE cannot be,
    because it has no way to notice the subject moved.
  * It matches what a dimension IS in the file format. The DIMENSION entity
    stores defpoints and an association, not an intrinsic length. Reproducing
    that structure is more faithful, not less.
  * It is the only choice compatible with the "generate, never execute" rule
    above: a construct-then-verify loop is safe to re-run, whereas a command that
    is still waiting for a point is not. Re-runnability is a safety property
    here, not a convenience.

HOW THE CHOICE BREAKS, AND THE SIGNAL IT MAKES  [this is the pinned behaviour]
The ENSURE architecture can fail in three ways, and each one is surfaced as a
loud, testable signal rather than a quiet pass. This is the part that matters
most, because an architecture whose failure mode is silence would be a net
negative in this repository.

  BREAK 1 -- THE BINDING IS LOST (association XDATA missing or corrupt).
    A second ensure then cannot find its binding and CREATES A SECOND DIMENSION
    for the same target. The document is wrong: two dimensions, one of which
    will go stale. SIGNAL: the record reports the write as ``created`` rather
    than ``updated``, and the entity count for that target grows. This is
    detectable by any caller by comparing a re-ensure result against the first
    result's handle and the target's dimension count. Pinned by
    ``test_ensure_twice_is_idempotent_and_reuses_the_handle`` and
    ``test_lost_association_is_visible_as_a_second_dimension``.

  BREAK 2 -- THE TEXT GOES STALE (target geometry edited without re-ensuring).
    This is the failure WRITE cannot even express, and the reason ENSURE was
    chosen. SIGNAL: the stored text no longer equals the measured value. The
    module exposes this as a first-class check, :func:`find_stale_dimensions`,
    which re-measures every bound dimension and returns the ones whose text
    disagrees. Pinned by
    ``test_editing_the_target_makes_the_dimension_stale_and_find_stale_sees_it``.

  BREAK 3 -- THE RENDERER DISAGREES WITH US (the decimal-separator loss above).
    The written text is read back out of the anonymous block and compared against
    the value measured from the geometry. SIGNAL: :class:`DimVerifyError`, raised
    from inside the write, before any record is returned. It is never a warning
    field on a record that a caller might ignore. Pinned by
    ``test_rendered_text_is_read_back_and_compared_to_the_measurement`` and
    ``test_delegated_auto_text_raises_instead_of_writing_a_wrong_number``.

If every one of those three tests were deleted, this module would still pass a
naive "did a DIMENSION get written" test while being quietly wrong. They are the
reason the architecture is defensible; see dim_test.py, which asserts the
signals rather than the entity count.

=====================================================================
LAYERS: DIM EXISTS AND IS USED. NO NEW NAME IS INVENTED.
=====================================================================
[OBSERVED, config] ``configs/architectural-layers.json`` and
``semantic_layers.py`` both contain ``"DIM": "dimension"`` (and ``"DIMLE"``).
So a dimension key is RESOLVED, unlike the opening recorder's ``TEMP-`` layer:
``LAYER_MAPPING_RESOLVED = True`` and the default is the existing name ``DIM``.

[UNRESOLVED] ``DIMLE`` also classifies to ``dimension`` but nothing observed here
distinguishes what it is for. This module does not guess: it writes every
dimension to ``DIM`` and leaves ``DIMLE`` unused, rather than inventing a
role for it. Callers may pass ``layers=("DIMLE",)`` explicitly.

This is the opposite decision from the opening recorder's, and the difference is
deliberate. There, no measured layer existed and the substitute (WAL2/WAL3) was
measured to inject ~1891 mm of phantom wall length. Here the layer IS in the
config, so a substitute is not needed and using a real one is correct. The rule
is the same in both cases: use the observed name if it exists, and if it does
not, do not invent a role for an unobserved layer.

=====================================================================
ENVIRONMENT NOTE (host trap, observed on this host by running it)
=====================================================================
Aside injects ``PYTHONHOME`` on this Windows host, which hides the venv's
standard library and makes ``import ezdxf`` fail with
``ModuleNotFoundError: No module named 'annotationlib'``. Always run with::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\dim_test.py

[OBSERVED, ezdxf 1.4.4] Re-rendering an EXISTING dimension allocates a NEW
anonymous block (``*D1`` -> ``*D2``) and leaves the old one in the file. The
ensure path therefore deletes the superseded block explicitly; without that,
repeated ensures leak one orphan block per call into the drawing.

DXF version: written as R2018 (AC1032). [DESIGN] The R2000 (AC1015) read floor
is a consumer-side contract, so every entity emitted here is R2000-legal.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from ..readback import EntitySnapshot
from ..topology import Point2D

__all__ = [
    "ASSOC_XDATA_APPID",
    "DEFAULT_DECIMALS",
    "DEFAULT_DIMSTYLE",
    "DEFAULT_LAYERS",
    "DEFAULT_UNIT",
    "DXF_READ_FLOOR_VERSION",
    "DXF_VERSION",
    "DXF_VERSION_NAME",
    "LAYER_MAPPING_RESOLVED",
    "DimGeometry",
    "DimGeometryError",
    "DimKind",
    "DimRecord",
    "DimVerifyError",
    "StaleDimension",
    "TextMode",
    "ensure_dim",
    "find_stale_dimensions",
    "format_measurement",
    "make_dim",
    "rendered_text",
    "write_dim",
]

DXF_VERSION = "AC1032"
DXF_VERSION_NAME = "R2018"
DXF_READ_FLOOR_VERSION = "AC1015"

#: [DESIGN] The drawing unit is millimetres. See the UNITS section of the
#: module docstring for the observed magnitudes this is grounded in.
DEFAULT_UNIT = "mm"

#: [OBSERVED, ezdxf 1.4.4] ``EZDXF`` is the dimstyle ezdxf creates with
#: ``setup=True``; it is a real, existing style, not one invented here.
DEFAULT_DIMSTYLE = "EZDXF"

#: [DESIGN] Two decimals matches the ``dimdec=2`` of the stock EZDXF style and
#: the mm magnitudes in this repository (200, 900, 1500), where a hundredth of a
#: millimetre is far below any real construction tolerance.
DEFAULT_DECIMALS = 2

#: [OBSERVED, config] ``DIM`` exists in configs/architectural-layers.json and
#: maps to semantic ``dimension``. No new name is coined. See module docstring.
LAYER_MAPPING_RESOLVED = True
DEFAULT_LAYERS: tuple[str, str] = ("DIM", "DIM")

#: XDATA app id under which the ENSURE binding (target handle -> dim handle) is
#: stored on the DIMENSION entity. [OBSERVED, ezdxf 1.4.4] XDATA survives a
#: DXF write/read roundtrip; ``set_app_data`` on a Dimension does not (it
#: rejects the payload this module needs), which is why XDATA is used.
ASSOC_XDATA_APPID = "AICD_ASSOC"

#: Association version, so a future format change is detectable rather than
#: silently misread.
ASSOC_VERSION = 1


class DimKind(StrEnum):
    """The four dimension shapes this recorder generates.

    [DESIGN] None of these was observed in the original runtime.
    """

    #: Measures |dx|. Dimension line runs along X, placed below the points.
    LINEAR_HORIZONTAL = "linear_horizontal"
    #: Measures |dy|. Dimension line runs along Y, placed right of the points.
    LINEAR_VERTICAL = "linear_vertical"
    #: Measures the true distance along the segment, dimension line parallel
    #: to it. This is the one that suits an oblique wall.
    ALIGNED = "aligned"
    #: Measures |dx| (or |dy|, see ``measure``) but draws the dimension line
    #: rotated by ``angle``. This is AutoCAD's rotated DIMLINEAR: the number is
    #: a projection, the line is not axis-parallel. It is deliberately NOT the
    #: same as ALIGNED, which measures the true length instead.
    ORTHOGONAL = "orthogonal"


class TextMode(StrEnum):
    """How the dimension string was produced. See the TEXT POLICY section."""

    #: Default: this module measured the geometry and injected the string.
    MEASURED = "measured"
    #: The caller supplied the string verbatim.
    INJECTED = "injected"
    #: The DXF ``"<>"`` placeholder: formatting delegated to the renderer.
    #: Readback is strict here and raises on this host -- see module docstring.
    AUTO_DELEGATED = "auto_delegated"


class DimGeometryError(ValueError):
    """Raised for a dimension request that cannot become valid geometry."""


class DimVerifyError(RuntimeError):
    """Raised when the text written into the file disagrees with the geometry.

    This is the loud signal behind ENSURE break 3. It is raised from inside the
    write, never reported as a field on a record a caller could ignore.
    """


def format_measurement(
    value: float,
    decimals: int = DEFAULT_DECIMALS,
    unit_suffix: str = "",
) -> str:
    """Format a measured millimetre value as a dimension string.

    [DESIGN] Rounding is ROUND_HALF_UP, i.e. away from zero on a tie. Python's
    builtin ``round`` is ROUND_HALF_EVEN, which would render 0.5 as "0" and 1.5
    as "2" -- asymmetric and surprising in a drawing. Half-up matches what a
    reader expects from a measured length, and it is applied through ``Decimal``
    so binary float representation cannot flip a tie (0.145 is not exactly
    0.145, and a naive ``round(0.145, 2)`` disagrees with ``round(0.15, 2)``).
    """
    if not math.isfinite(value):
        raise DimGeometryError(f"measurement must be finite, got {value!r}")
    if decimals < 0:
        raise DimGeometryError(f"decimals must be >= 0, got {decimals}")
    quantum = Decimal(1).scaleb(-decimals)
    rounded = Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP)
    return f"{rounded:f}{unit_suffix}"


@dataclass(frozen=True, slots=True)
class DimGeometry:
    """Resolved 2D geometry for one dimension, before it touches a document."""

    p1: Point2D
    p2: Point2D
    kind: DimKind
    #: Line location: the point the dimension line is drawn through.
    line_location: Point2D
    #: Absolute measured value in millimetres. Always >= 0; a reversed
    #: right-to-left pair measures the same positive value as left-to-right.
    measured: float
    angle_deg: float
    text: str | None
    text_mode: TextMode
    decimals: int
    unit_suffix: str
    #: Text midpoint override, applied through set_location. None = centred.
    text_offset: Point2D | None
    #: True to draw architectural tick marks instead of arrowheads.
    use_tick: bool
    tick_size: float
    arrow_size: float
    layer: str
    #: Handle of the entity this dimension measures, when known. This is what
    #: makes the dimension a reference rather than a frozen value.
    target_handle: str | None = None

    @property
    def key(self) -> str:
        """Stable identity for the ENSURE binding.

        Built from the TARGET HANDLE when there is one, because a handle is the
        only identity that survives the target moving. Without a target handle
        the measured extent is used instead, which is a weaker key: it changes
        when the geometry changes, so such a dimension is treated as a new one
        rather than updated. That asymmetry is deliberate and documented.
        """
        identity = self.target_handle or (
            f"pts:{self.p1.x:.6f},{self.p1.y:.6f}:{self.p2.x:.6f},{self.p2.y:.6f}"
        )
        return f"{identity}|{self.kind.value}|{self.layer}"

    def assoc_payload(self) -> str:
        """Serialise the ENSURE binding into its XDATA string."""
        return json.dumps(
            {
                "v": ASSOC_VERSION,
                "target": self.target_handle,
                "kind": self.kind.value,
                "layer": self.layer,
                "measured": self.measured,
                "text": self.text,
                "mode": self.text_mode.value,
                "decimals": self.decimals,
                "unit_suffix": self.unit_suffix,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def expected_text(self) -> str:
        """The string this module believes should be in the drawing.

        For MEASURED this is the formatted value. For INJECTED it is the
        caller's string, which is deliberately NOT the measured number -- the
        disagreement is then visible rather than silently overwritten.
        """
        if self.text_mode is TextMode.AUTO_DELEGATED:
            return "<>"
        if self.text is not None:
            return self.text
        return format_measurement(self.measured, self.decimals, self.unit_suffix)


@dataclass(frozen=True, slots=True)
class DimRecord:
    """What was actually written, plus the evidence that it is correct."""

    document_id: str
    geometry: DimGeometry
    handle: str
    block_name: str
    layer: str
    #: "created" or "updated" -- the ENSURE outcome. A second ensure that
    #: creates instead of updating is BREAK 1 and is visible here.
    action: str
    #: The value read back OUT of the written dimension entity.
    readback_measurement: float
    #: The text read back OUT of the anonymous block (i.e. the drawing's own
    #: rendered text), which is what a human or a downstream tool would read.
    readback_text: str
    text_mode: TextMode
    #: True when the text was verified against the measurement in this call.
    verified: bool

    @property
    def created(self) -> bool:
        return self.action == "created"

    @property
    def updated(self) -> bool:
        return self.action == "updated"

    def snapshot(self) -> EntitySnapshot:
        return EntitySnapshot(
            document_id=self.document_id,
            handle=self.handle,
            entity_type="DIMENSION",
            layer=self.layer,
            geometry={
                "defpoint": [
                    self.geometry.line_location.x,
                    self.geometry.line_location.y,
                ],
                "defpoint2": [self.geometry.p1.x, self.geometry.p1.y],
                "defpoint3": [self.geometry.p2.x, self.geometry.p2.y],
                "measurement": self.readback_measurement,
            },
            properties={
                "text": self.readback_text,
                "kind": self.geometry.kind.value,
                "text_mode": self.text_mode.value,
                "assoc": ASSOC_XDATA_APPID,
            },
        )


@dataclass(frozen=True, slots=True)
class StaleDimension:
    """A bound dimension whose text no longer matches its live target."""

    handle: str
    key: str
    stored_text: str
    measured_now: float
    expected_text: str


def make_dim(
    p1: tuple[float, float] | Point2D,
    p2: tuple[float, float] | Point2D,
    kind: DimKind = DimKind.LINEAR_HORIZONTAL,
    *,
    offset_mm: float = 800.0,
    text: str | None = None,
    decimals: int = DEFAULT_DECIMALS,
    unit_suffix: str = "",
    text_offset: tuple[float, float] | Point2D | None = None,
    angle_deg: float | None = None,
    measure: str = "x",
    use_tick: bool = False,
    tick_size: float = 1.0,
    arrow_size: float | None = None,
    layer: str = DEFAULT_LAYERS[0],
    target_handle: str | None = None,
) -> DimGeometry:
    """Build pure dimension geometry. No ezdxf, no document, no command.

    ``offset_mm`` is the distance of the dimension line from the measured
    points, on the side chosen per kind. It is a positive magnitude; the sign is
    decided by the kind, so ``offset_mm=0`` puts the dimension line on top of the
    geometry (a legitimate but usually unreadable choice, and not rejected).

    ``text=None`` (default) measures and injects -- see the TEXT POLICY section.
    ``text="<>"`` delegates formatting to the renderer. Any other string is
    injected verbatim and recorded as :attr:`TextMode.INJECTED`.

    ``kind=ORTHOGONAL`` measures a projection (``measure="x"`` or ``"y"``) while
    drawing the dimension line at ``angle_deg``; that is the whole difference
    from ALIGNED, which measures true length.

    Zero-length is rejected for every kind, including a pure projection of zero
    (a vertical pair measured horizontally measures 0 and would render a
    meaningless dimension).
    """
    start = _as_point(p1, "p1")
    end = _as_point(p2, "p2")
    kind = DimKind(kind)
    offset = _finite(offset_mm, "offset_mm")
    decimals = int(decimals)
    if decimals < 0:
        raise DimGeometryError(f"decimals must be >= 0, got {decimals}")
    if not layer.strip():
        raise DimGeometryError("layer must be non-empty")
    if offset < 0:
        raise DimGeometryError(f"offset_mm must be >= 0, got {offset_mm}")

    dx = end.x - start.x
    dy = end.y - start.y
    distance = math.hypot(dx, dy)
    if distance <= 0:
        raise DimGeometryError(
            "the two points coincide; a zero-length dimension has no direction "
            "and would render an unreadable value"
        )

    if kind is DimKind.LINEAR_HORIZONTAL:
        measured = abs(dx)
        if measured <= 0:
            raise DimGeometryError(
                "horizontal dimension of a vertical pair measures 0; use "
                "LINEAR_VERTICAL or ALIGNED"
            )
        mid_y = (start.y + end.y) / 2.0
        location = Point2D((start.x + end.x) / 2.0, mid_y - offset)
        angle = 0.0
    elif kind is DimKind.LINEAR_VERTICAL:
        measured = abs(dy)
        if measured <= 0:
            raise DimGeometryError(
                "vertical dimension of a horizontal pair measures 0; use "
                "LINEAR_HORIZONTAL or ALIGNED"
            )
        mid_x = (start.x + end.x) / 2.0
        location = Point2D(mid_x + offset, (start.y + end.y) / 2.0)
        angle = 90.0
    elif kind is DimKind.ALIGNED:
        measured = distance
        # Dimension line parallel to the segment, offset along its left normal.
        ux, uy = dx / distance, dy / distance
        nx, ny = -uy, ux
        location = Point2D(
            (start.x + end.x) / 2.0 + nx * offset,
            (start.y + end.y) / 2.0 + ny * offset,
        )
        angle = math.degrees(math.atan2(dy, dx))
    elif kind is DimKind.ORTHOGONAL:
        if measure not in ("x", "y"):
            raise DimGeometryError(
                f"measure must be 'x' or 'y' for ORTHOGONAL, got {measure!r}"
            )
        measured = abs(dx) if measure == "x" else abs(dy)
        if measured <= 0:
            raise DimGeometryError(
                f"orthogonal dimension measured on {measure!r} of this pair is 0"
            )
        resolved_angle = 0.0 if angle_deg is None else _finite(angle_deg, "angle_deg")
        radians = math.radians(resolved_angle)
        # Line location is pushed along the DIMENSION LINE direction (rotated),
        # not along the measured axis -- that is what makes it orthogonal.
        location = Point2D(
            (start.x + end.x) / 2.0 + math.cos(radians) * offset,
            (start.y + end.y) / 2.0 + math.sin(radians) * offset,
        )
        angle = resolved_angle
    else:  # pragma: no cover - StrEnum exhaustiveness
        raise DimGeometryError(f"unhandled kind {kind!r}")

    mode, resolved_text = _resolve_text(text, measured, decimals, unit_suffix)

    return DimGeometry(
        p1=start,
        p2=end,
        kind=kind,
        line_location=location,
        measured=measured,
        angle_deg=angle % 360.0,
        text=resolved_text,
        text_mode=mode,
        decimals=decimals,
        unit_suffix=unit_suffix,
        text_offset=(
            None
            if text_offset is None
            else _as_point(text_offset, "text_offset")
        ),
        use_tick=bool(use_tick),
        tick_size=_finite(tick_size, "tick_size"),
        arrow_size=None if arrow_size is None else _finite(arrow_size, "arrow_size"),
        layer=layer,
        target_handle=(
            None if target_handle is None else str(target_handle).upper()
        ),
    )


def write_dim(
    doc: Any,
    dim: DimGeometry,
    layers: Sequence[str] = DEFAULT_LAYERS,
) -> DimRecord:
    """Create the dimension and return a record carrying the readback evidence.

    This is the one-shot path. It always CREATES. :func:`ensure_dim` is the
    idempotent path and is what callers normally want; this exists so a caller
    can deliberately place a second dimension on the same geometry.

    The dimension entity goes on ``layers[0]``; ``layers[1]`` names the layer for
    the drawn geometry block, which is recorded but not separately written,
    because ezdxf owns the anonymous block. Pass the same name twice (the
    default) to keep both on one layer.
    """
    return _emit(doc, dim, layers, action="created", existing=None)


def ensure_dim(
    doc: Any,
    dim: DimGeometry,
    layers: Sequence[str] = DEFAULT_LAYERS,
) -> DimRecord:
    """Idempotently make ``dim`` true in ``doc`` and return the evidence.

    If a DIMENSION already carries this binding (:attr:`DimGeometry.key`), it is
    RE-MEASURED against the geometry in ``dim`` and re-rendered in place, and the
    record says ``action="updated"``. Otherwise one is created.

    A second call with unchanged geometry returns the SAME handle and reports
    ``updated``. If instead a second dimension appears, the association was lost
    -- that is BREAK 1, and it is visible in ``action`` and in the handle
    changing, not hidden behind a successful return.
    """
    existing = _find_bound_dimension(doc, dim.key)
    return _emit(
        doc,
        dim,
        layers,
        action="updated" if existing is not None else "created",
        existing=existing,
    )


def find_stale_dimensions(doc: Any) -> list[StaleDimension]:
    """Re-measure every bound dimension and report the ones whose text lies.

    This is the detector for ENSURE break 2: the target was edited and nobody
    re-ensured. A dimension that has drifted is exactly the case where
    write-and-forget silently leaves a wrong number in a drawing, so the module
    can name it instead of trusting it.

    Only dimensions carrying this module's association are considered; a
    dimension written by hand is never judged by these rules.
    """
    stale: list[StaleDimension] = []
    for entity, payload in _iter_bound_dimensions(doc):
        current = _measure_like(entity, payload["kind"])
        decimals = int(payload.get("decimals", DEFAULT_DECIMALS))
        suffix = str(payload.get("unit_suffix", ""))
        expected = (
            str(payload.get("text"))
            if payload.get("mode") == TextMode.INJECTED.value
            else format_measurement(current, decimals, suffix)
        )
        stored = _entity_text(entity)
        if stored != expected:
            stale.append(
                StaleDimension(
                    handle=str(entity.dxf.handle),
                    key=str(payload.get("key", "")),
                    stored_text=stored,
                    measured_now=current,
                    expected_text=expected,
                )
            )
    return stale


def rendered_text(doc: Any, entity: Any) -> str:
    """Read the dimension's own rendered text out of its anonymous block.

    This is the number a human or a downstream reader actually sees. It is
    deliberately NOT read from ``entity.dxf.text``: when the text was delegated
    to the renderer, ``dxf.text`` stays the ``"<>`` placeholder while the real
    string lives in the block. Reading the wrong one is how a dimension passes a
    check that never looked at the drawing.
    """
    block_name = str(entity.dxf.geometry)
    block = doc.blocks.get(block_name)
    for item in block:
        if item.dxftype() == "MTEXT":
            return str(item.plain_text())
        if item.dxftype() == "TEXT":
            return str(item.dxf.text)
    return ""


# --- internals ----------------------------------------------------------------


def _emit(
    doc: Any,
    dim: DimGeometry,
    layers: Sequence[str],
    *,
    action: str,
    existing: Any,
) -> DimRecord:
    ezdxf = _require_ezdxf()
    resolved = tuple(str(name) for name in layers)
    if len(resolved) != 2:
        raise DimGeometryError(
            "layers must be exactly (dimension_layer, block_layer)"
        )
    if not all(name.strip() for name in resolved):
        raise DimGeometryError("layer names must be non-empty")

    document_id = _document_id(doc)
    entity_layer, block_layer = resolved
    for name in (entity_layer, block_layer):
        if not doc.layers.has_entry(name):
            doc.layers.add(name)

    if existing is None:
        override = _new_override(doc, dim, entity_layer)
        old_block: str | None = None
    else:
        # ENSURE update: rewrite the defpoints on the existing entity, then wrap
        # it in a fresh override and re-render. Keeping the handle is the point:
        # every existing reference to this dimension stays valid.
        existing.dxf.defpoint = (dim.line_location.x, dim.line_location.y, 0.0)
        existing.dxf.defpoint2 = (dim.p1.x, dim.p1.y, 0.0)
        existing.dxf.defpoint3 = (dim.p2.x, dim.p2.y, 0.0)
        if existing.dxf.layer != entity_layer:
            existing.dxf.layer = entity_layer
        old_block = str(existing.dxf.geometry)
        override = ezdxf.entities.DimStyleOverride(existing)
        # [OBSERVED, ezdxf 1.4.4] Wrapping an existing DIMENSION inherits its
        # already-committed dxf.text, so re-rendering would keep the OLD string
        # even though the defpoints moved. The text must be re-applied
        # explicitly, or an update silently writes a stale number -- the exact
        # failure the readback check exists to catch, and it did.
        if dim.text_mode is not TextMode.AUTO_DELEGATED:
            override.set_text(dim.text or "")
        if dim.use_tick:
            override.set_tick(size=dim.tick_size)
        elif dim.arrow_size is not None:
            override.set_arrows(size=dim.arrow_size)
        if dim.text_offset is not None:
            override.set_location(
                (
                    (dim.p1.x + dim.p2.x) / 2.0 + dim.text_offset.x,
                    (dim.p1.y + dim.p2.y) / 2.0 + dim.text_offset.y,
                )
            )
    override.render()
    entity = override.dimension

    new_block = str(entity.dxf.geometry)
    if old_block and old_block != new_block:
        # [OBSERVED, ezdxf 1.4.4] re-render allocates a new anonymous block and
        # leaves the old one behind. Drop it, or every ensure leaks a block.
        try:
            doc.blocks.delete_block(old_block, safe=False)
        except Exception:  # pragma: no cover - block already gone
            pass

    entity.set_xdata(
        ASSOC_XDATA_APPID,
        [(1000, json.dumps({**json.loads(dim.assoc_payload()), "key": dim.key}))],
    )

    readback_measurement = float(entity.get_measurement())
    readback_text = rendered_text(doc, entity)

    verified = _verify(dim, readback_measurement, readback_text)

    return DimRecord(
        document_id=document_id,
        geometry=dim,
        handle=str(entity.dxf.handle),
        block_name=new_block,
        layer=str(entity.dxf.layer),
        action=action,
        readback_measurement=readback_measurement,
        readback_text=readback_text,
        text_mode=dim.text_mode,
        verified=verified,
    )


def _new_override(doc: Any, dim: DimGeometry, layer: str) -> Any:
    """Build a not-yet-rendered DimStyleOverride for a brand new dimension.

    Uses only the ezdxf data model. No command is transmitted and no prompt is
    opened -- that is the structural guarantee described in the module docstring.
    """
    msp = doc.modelspace()
    attribs = {"layer": layer}
    p1 = (dim.p1.x, dim.p1.y)
    p2 = (dim.p2.x, dim.p2.y)
    base = (dim.line_location.x, dim.line_location.y)

    if dim.kind is DimKind.ALIGNED:
        offset = math.dist(
            (base[0], base[1]),
            ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0),
        )
        override = msp.add_aligned_dim(
            p1=p1, p2=p2, distance=offset, dimstyle=DEFAULT_DIMSTYLE, dxfattribs=attribs
        )
    else:
        override = msp.add_linear_dim(
            base=base,
            p1=p1,
            p2=p2,
            angle=dim.angle_deg,
            dimstyle=DEFAULT_DIMSTYLE,
            dxfattribs=attribs,
        )

    if dim.use_tick:
        override.set_tick(size=dim.tick_size)
    elif dim.arrow_size is not None:
        override.set_arrows(size=dim.arrow_size)

    if dim.text_mode is not TextMode.AUTO_DELEGATED:
        override.set_text(dim.text or "")
    # AUTO_DELEGATED: leave the default "<>" so the renderer formats it.

    if dim.text_offset is not None:
        mid = (
            (p1[0] + p2[0]) / 2.0 + dim.text_offset.x,
            (p1[1] + p2[1]) / 2.0 + dim.text_offset.y,
        )
        override.set_location((mid[0], mid[1]))

    return override


def _verify(dim: DimGeometry, measured: float, text: str) -> bool:
    """Compare what the drawing now says against what the geometry is.

    Raises rather than returning False for a disagreement. The one tolerated
    case is a half-ulp float difference in the measurement, because the renderer
    reports a value derived by its own arithmetic.
    """
    if not math.isclose(measured, dim.measured, rel_tol=0.0, abs_tol=1e-6):
        raise DimVerifyError(
            f"written dimension measures {measured} but the geometry measures "
            f"{dim.measured}; the defpoints and the measurement disagree"
        )
    if dim.text_mode is TextMode.AUTO_DELEGATED:
        # Deliberately strict. On this host the renderer drops the decimal
        # separator, so the delegated string will not match the measurement.
        expected = format_measurement(dim.measured, dim.decimals, dim.unit_suffix)
        if text != expected:
            raise DimVerifyError(
                f"renderer-produced text {text!r} does not match the measured "
                f"value {expected!r}; delegating formatting to the renderer is "
                f"not safe on this host -- omit text= to have this module format "
                f"it instead"
            )
        return True
    if text != dim.expected_text:
        raise DimVerifyError(
            f"written text {text!r} does not match the expected text "
            f"{dim.expected_text!r}"
        )
    return True


def _resolve_text(
    text: str | None,
    measured: float,
    decimals: int,
    unit_suffix: str,
) -> tuple[TextMode, str | None]:
    """Decide the text mode. Default is MEASURED; see the TEXT POLICY section."""
    if text is None:
        return (
            TextMode.MEASURED,
            format_measurement(measured, decimals, unit_suffix),
        )
    if text == "<>":
        return TextMode.AUTO_DELEGATED, text
    if not str(text).strip():
        raise DimGeometryError(
            "text must be a non-empty string, None, or the '<>' placeholder"
        )
    return TextMode.INJECTED, str(text)


def _find_bound_dimension(doc: Any, key: str) -> Any:
    for entity, payload in _iter_bound_dimensions(doc):
        if payload.get("key") == key:
            return entity
    return None


def _iter_bound_dimensions(doc: Any):
    """Yield ``(entity, payload)`` for DIMENSIONs carrying this module's binding."""
    for entity in doc.modelspace().query("DIMENSION"):
        try:
            tags = entity.get_xdata(ASSOC_XDATA_APPID)
        except Exception:
            continue
        if not tags:
            continue
        try:
            payload = json.loads(tags[0].value)
        except (ValueError, IndexError, TypeError):
            # A corrupt association is not the same as a missing one. It is
            # BREAK 1 in disguise, and it surfaces as "not found" so that
            # ensure_dim creates -- visibly, via action="created".
            continue
        if isinstance(payload, dict):
            yield entity, payload


def _measure_like(entity: Any, kind: str) -> float:
    """Measure an existing DIMENSION's own defpoints, for staleness checks."""
    start = entity.dxf.defpoint2
    end = entity.dxf.defpoint3
    dx = abs(float(end.x) - float(start.x))
    dy = abs(float(end.y) - float(start.y))
    if kind == DimKind.LINEAR_VERTICAL.value:
        return dy
    if kind == DimKind.ALIGNED.value:
        return math.hypot(dx, dy)
    return dx


def _entity_text(entity: Any) -> str:
    text = str(entity.dxf.text)
    return "" if text == "<>" else text


def _as_point(value: tuple[float, float] | Point2D, name: str) -> Point2D:
    if isinstance(value, Point2D):
        return Point2D(_finite(value.x, f"{name}.x"), _finite(value.y, f"{name}.y"))
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise DimGeometryError(f"{name} must be an (x, y) pair (got {value!r})")
    return Point2D(_finite(value[0], f"{name}.x"), _finite(value[1], f"{name}.y"))


def _finite(value: float, name: str) -> float:
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise DimGeometryError(f"{name} must be a finite number (got {value!r})")
    return number


def _document_id(doc: Any) -> str:
    for attribute in ("doc_id", "filename", "filepath"):
        value = getattr(doc, attribute, None)
        if isinstance(value, str) and value:
            return value
    return "in-memory"


def _require_ezdxf() -> Any:
    try:
        import ezdxf  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - env guard
        raise ModuleNotFoundError(
            "ezdxf is required by the dimension recorder. On this host run the "
            "project venv with a cleared PYTHONHOME, otherwise the standard "
            "library is hidden and this import fails with 'No module named "
            "annotationlib'."
        ) from exc
    return ezdxf
