"""Wall-opening recorder: an opening expressed as a SYMBOL, not as a cut.

No INSERT, no HATCH, no boolean wall subtraction -- see WHY BELOW. Every entity
is an explicit LINE so this repository's topology/verification code can consume
it without expanding blocks.

=====================================================================
OBSERVED PROMPT SEQUENCE (runtime capture, high confidence)
=====================================================================
    xiWallOpening : '>> 설정(S)/ 개구부 폭 입력 <...>'
                    -> '상대쪽 벽체 선 지정 <_per>: '
                    -> '동일 선상의 개구부폭 방향 점지정 <_nea>: '

    Restored DCL labels (decrypted): Offset_edt '시작점에서 개구부 띄울거리',
    EachWall_rdo '각각의 벽선으로', Fin1Wall_rdo '첫번째 벽선 따라'.

    CONFIRMED VALUES: door width 900 mm, window width 1500 mm. The opening
    width default was never read, so :data:`DEFAULT_OPENING_WIDTH_MM` is a
    documented design constant, NOT a measurement. The offset default
    (Offset_edt) is likewise unresolved: it is exposed as ``offset_mm=0.0``,
    meaning "no gap from the start point", and is labelled unresolved in code.

=====================================================================
WHY THIS IS A SYMBOL AND NOT A PHYSICAL CUT [DESIGN]
=====================================================================
An opening in a wall is topologically a subtraction: the wall's two face lines
must be interrupted and two new edges must appear where the opening starts and
ends. This recorder deliberately does NOT do that, for three reasons:

1. [OBSERVED] Every wall this repository has seen drawn is LINE-only, with no
   block INSERT and no HATCH, and the normaliser does not expand INSERT, so a
   block contributes zero topology segments. A subtraction-based symbol would
   need either a block or a boolean, i.e. exactly the constructs that carry no
   usable topology here.
2. This recorder has no wall model. It receives one reference wall line and a
   width, and nothing about the rest of the wall run, the wall thickness in the
   model, the room polygon, or the join order. A cut needs the whole wall, so
   doing one here would require inventing state the caller does not pass -- an
   estimate presented as a cut.
3. The measured fact a downstream tool needs from an opening is its two boundary
   positions along the wall. Two boundary LINEs plus a symbol carry exactly that
   fact, and they stay readable: every endpoint is a real segment endpoint.

So the opening is recorded as: the two boundary edges across the wall
thickness, a symbol line on the far face, two 45-degree ticks at the jambs, and
a centre tick. The subtraction itself is the host's job, not the recorder's.

=====================================================================
[DESIGN] ENTITY COMPOSITION -- NOT OBSERVED
=====================================================================
    role            dxftype  layer                meaning
    --------------  -------  -------------------  -------------------------
    edge_start      LINE     TEMP-OPENING-BND      boundary at -width/2
    edge_end        LINE     TEMP-OPENING-BND      boundary at +width/2
    face_line       LINE     TEMP-OPENING-SYM      face line past the reference
    tick_start      LINE     TEMP-OPENING-SYM      45-degree jamb tick
    tick_end        LINE     TEMP-OPENING-SYM      45-degree jamb tick
    center_tick     LINE     TEMP-OPENING-SYM      centre mark

The ``TEMP-`` prefix is load-bearing, not cosmetic: it marks a value that
records an observation gap so a later reader cannot mistake it for a decided
layer assignment.

LAYERS ARE UNRESOLVED (미확정), AND THE SUBSTITUTE IS DELIBERATELY INERT
=========================================================================
[OBSERVED, config] ``configs/architectural-layers.json`` has no opening entry --
only WAL1/WAL2/WAL3, DOOR, DOOR_ELE, WIN, WINBAR, WINELE, COL, ELE, STAIR, DIM,
CEN. [OBSERVED, XiCAD] ``WO``/``xiWallOpening`` exists as its own command, but no
drawing with an opening in it has ever been observed, so there is **no measured
layer for an opening**. :data:`LAYER_MAPPING_RESOLVED` therefore stays ``False``:
an unobserved layer cannot be resolved by reasoning about it.

Why the substitute is NOT a real wall layer [MEASURED, see
``opening_test.py::test_wall_layers_would_leak_six_phantom_wall_segments``]:
``semantic_layers.classify_layer`` maps WAL1/2/3 to ``LayerSemantic.WALL``, and
``topology.segments_from_entities`` / ``semantic_graph`` then feed every LINE on
them into wall reasoning. A prior revision used ``("WAL2", "WAL3")`` here. Writing
one opening that way adds **six phantom wall segments and ~1891 mm of phantom
wall length** to any room or wall-length computation, and still yields **zero**
``infer_opening_hosts()`` relations, because that function only accepts entities
whose semantic is DOOR or WINDOW (``architecture.py:62-64``). So the old
substitute was not a neutral placeholder: it was a silent corruption with no
downstream benefit.

Why the substitute is also NOT the DOOR family: routing the opening onto
``DOOR``/``DOOR_ELE`` does make ``infer_opening_hosts()`` return six relations
(also measured), but those relations assert *doors* -- a semantic claim the
recorder cannot support, since ``xiWallOpening`` is type-agnostic. That would
inject six false positives into the persisted ``opening_hosts`` table. A wrong
claim is worse than an inert value.

So the default is a self-evident temporary name that
``classify_layer()`` resolves to ``LayerSemantic.UNKNOWN``. The config policy
(``unknown_layers: preserve_and_flag``, ``never_delete_unknown: true``) then does
exactly the right thing with it: the geometry is preserved, flagged, and read by
nobody. The failure mode is "no opening seen", which is honest. Callers may
override ``layers=`` once the real layer is observed; ``opening_test.py`` asserts
the config really has no opening key, so the unresolved label cannot rot
silently.

=====================================================================
ENVIRONMENT NOTE (host trap, observed on this host by running it)
=====================================================================
Aside injects ``PYTHONHOME`` on this Windows host, which hides the venv's
standard library and makes ``import ezdxf`` fail with
``ModuleNotFoundError: No module named 'annotationlib'``. Always run with::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\opening_test.py

DXF version: written as R2018 (AC1032). [DESIGN] The R2000 (AC1015) read floor
is a consumer-side contract, so every entity emitted here is R2000-legal and
the file is written at the newer version on purpose.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from ..readback import EntitySnapshot
from ..topology import Point2D

__all__ = [
    "DEFAULT_LAYERS",
    "DEFAULT_OPENING_WIDTH_MM",
    "DEFAULT_THICKNESS_MM",
    "DXF_READ_FLOOR_VERSION",
    "DXF_VERSION",
    "DXF_VERSION_NAME",
    "LAYER_MAPPING_RESOLVED",
    "OpeningEntitySpec",
    "OpeningGeometry",
    "OpeningGeometryError",
    "OpeningRecord",
    "make_opening",
    "readback_snapshots",
    "write_opening",
]

DXF_VERSION = "AC1032"
DXF_VERSION_NAME = "R2018"
DXF_READ_FLOOR_VERSION = "AC1015"

#: False because configs/architectural-layers.json has no opening entry. See the
#: module docstring; the constant exists so callers can detect the fallback.
LAYER_MAPPING_RESOLVED = False

#: [UNRESOLVED] (boundary layer, symbol layer). Deliberately inert: these names
#: carry the ``TEMP-`` marker, and ``classify_layer()`` resolves both to
#: ``LayerSemantic.UNKNOWN``, so an unobserved opening never enters wall or
#: opening reasoning. See the module docstring for the measured cost of the
#: WAL2/WAL3 substitute this replaced.
DEFAULT_LAYERS: tuple[str, str] = ("TEMP-OPENING-BND", "TEMP-OPENING-SYM")

#: [DESIGN, NOT OBSERVED] The opening width default was never read from the DCL.
DEFAULT_OPENING_WIDTH_MM = 1500.0

#: [DESIGN, NOT OBSERVED] Wall thickness across the opening, same documented
#: 100 mm choice the door and window recorders made.
DEFAULT_THICKNESS_MM = 100.0

Side = Literal["left", "right"]
LayerSlot = Literal["boundary", "symbol"]


class OpeningGeometryError(ValueError):
    """Raised when an opening request cannot become valid LINE geometry."""


@dataclass(frozen=True, slots=True)
class OpeningEntitySpec:
    """One planned entity, independent of ezdxf. ``dxftype`` is LINE only."""

    role: str
    dxftype: Literal["LINE"]
    layer_slot: LayerSlot
    start: Point2D
    end: Point2D


@dataclass(frozen=True, slots=True)
class OpeningGeometry:
    """Resolved plan geometry for one wall opening, before ezdxf."""

    reference_start: Point2D
    reference_end: Point2D
    direction_point: Point2D | None
    origin: Point2D
    start_point: Point2D
    end_point: Point2D
    center: Point2D
    width_axis_deg: float
    side: Side
    width_mm: float
    thickness_mm: float
    offset_mm: float
    entities: tuple[OpeningEntitySpec, ...]
    warnings: tuple[str, ...] = ()

    @property
    def handle_hint(self) -> str:
        """Stable textual identity for the opening, used for warnings and tests."""
        return (
            f"opening@{self.center.x:.3f},{self.center.y:.3f}"
            f"/w{self.width_mm:.3f}/s{self.side}"
        )

    def specs(self) -> tuple[OpeningEntitySpec, ...]:
        return self.entities

    def specs_for_slot(self, slot: LayerSlot) -> tuple[OpeningEntitySpec, ...]:
        return tuple(spec for spec in self.entities if spec.layer_slot == slot)

    def spec(self, role: str) -> OpeningEntitySpec:
        return next(spec for spec in self.entities if spec.role == role)


@dataclass(frozen=True, slots=True)
class OpeningRecord:
    """What was actually written, with readback evidence per entity."""

    document_id: str
    geometry: OpeningGeometry
    layers: tuple[str, str]
    entity_handles: tuple[str, ...]
    snapshots: tuple[EntitySnapshot, ...]

    def handles(self) -> tuple[str, ...]:
        return self.entity_handles


def make_opening(
    reference_line: tuple[tuple[float, float], tuple[float, float]],
    width_mm: float = DEFAULT_OPENING_WIDTH_MM,
    thickness_mm: float = DEFAULT_THICKNESS_MM,
    *,
    start_point: tuple[float, float] | Point2D | None = None,
    direction_point: tuple[float, float] | Point2D | None = None,
    side: Side = "left",
    offset_mm: float = 0.0,
    collinear_tolerance: float = 1.0,
) -> OpeningGeometry:
    """Build opening geometry from the observed two-pick prompt order.

    ``reference_line`` is the [OBSERVED] '상대쪽 벽체 선' pick, i.e. the
    opposite wall line. The opening is placed on that line: the wall line's own
    direction is the width axis ``u``, and the opening is centred ``offset_mm``
    past ``start_point`` measured along ``u``.

    ``start_point`` defaults to the reference line's start point, and
    ``offset_mm`` is the gap between that start point and the opening's first
    edge, which is what the restored DCL label Offset_edt ('시작점에서 개구부
    띄울거리') describes. ``offset_mm=0.0`` therefore keeps the [UNRESOLVED]
    Offset_edt default honest: no gap is invented.

    ``direction_point`` is the [OBSERVED] '동일 선상의 개구부폭 방향 점지정'
    pick. Because the prompt says it lies on the same line, the line's own
    direction already determines the axis, so a direction point that is not
    collinear is a *warning* and never changes the axis.
    """
    ref_start = _as_point(reference_line[0], "reference_line[0]")
    ref_end = _as_point(reference_line[1], "reference_line[1]")
    width = _positive(width_mm, "width_mm")
    thickness = _positive(thickness_mm, "thickness_mm")
    offset = _non_negative(offset_mm, "offset_mm")
    if side not in ("left", "right"):
        raise OpeningGeometryError(f"side must be 'left' or 'right', got {side!r}")
    if collinear_tolerance < 0:
        raise OpeningGeometryError("collinear_tolerance must be non-negative")

    warnings: list[str] = []

    line_length = math.dist((ref_start.x, ref_start.y), (ref_end.x, ref_end.y))
    if line_length <= 0:
        raise OpeningGeometryError(
            "reference_line endpoints must differ; a zero-length wall line "
            "cannot bound an opening"
        )
    axis_deg = math.degrees(
        math.atan2(ref_end.y - ref_start.y, ref_end.x - ref_start.x)
    )
    axis = _unit(axis_deg)
    normal = Point2D(-axis.y, axis.x)

    origin = (
        _as_point(start_point, "start_point")
        if start_point is not None
        else ref_start
    )

    resolved_direction: Point2D | None = None
    if direction_point is not None:
        resolved_direction = _as_point(direction_point, "direction_point")
        if math.dist(
            (origin.x, origin.y), (resolved_direction.x, resolved_direction.y)
        ) <= 0:
            raise OpeningGeometryError(
                "direction_point must differ from the opening start point"
            )
        cross = abs(
            (resolved_direction.x - origin.x) * normal.x
            + (resolved_direction.y - origin.y) * normal.y
        )
        if cross > collinear_tolerance:
            warnings.append(
                f"direction_point is {cross:.3f} mm off the reference line "
                f"(tolerance {collinear_tolerance:.3f} mm); the prompt says it "
                f"lies on the same line, so the wall line's own direction wins"
            )

    def along(point: Point2D, distance: float) -> Point2D:
        return Point2D(point.x + axis.x * distance, point.y + axis.y * distance)

    sign = 1.0 if side == "left" else -1.0
    # 'left' draws the symbol towards +normal, 'right' towards -normal. The
    # along-wall positions are identical in both cases, so side is a pure
    # mirror across the reference line.
    toward = Point2D(normal.x * sign, normal.y * sign)
    start = along(origin, offset)
    end = along(origin, offset + width)
    center = along(origin, offset + width / 2.0)
    half = thickness / 2.0

    def to_face(point: Point2D) -> Point2D:
        """Move a point to the far face, i.e. across the opening thickness."""
        return Point2D(
            point.x + toward.x * thickness,
            point.y + toward.y * thickness,
        )

    def across(point: Point2D) -> tuple[Point2D, Point2D]:
        return (
            Point2D(point.x - toward.x * half, point.y - toward.y * half),
            Point2D(point.x + toward.x * half, point.y + toward.y * half),
        )

    start_ends = across(start)
    end_ends = across(end)

    def tick(point: Point2D, step: float) -> tuple[Point2D, Point2D]:
        """45-degree jamb tick: from the jamb itself, one step along the wall
        and one step across, so both components are equal for any wall angle.
        """
        return (
            point,
            Point2D(
                point.x + axis.x * sign * step + toward.x * step,
                point.y + axis.y * sign * step + toward.y * step,
            ),
        )

    step = thickness / 2.0
    tick_start = tick(start, step)
    tick_end = tick(end, step)

    entities: tuple[OpeningEntitySpec, ...] = (
        OpeningEntitySpec("edge_start", "LINE", "boundary", start_ends[0], start_ends[1]),
        OpeningEntitySpec("edge_end", "LINE", "boundary", end_ends[0], end_ends[1]),
        OpeningEntitySpec(
            "face_line", "LINE", "symbol", to_face(start), to_face(end)
        ),
        OpeningEntitySpec(
            "tick_start", "LINE", "symbol", tick_start[0], tick_start[1]
        ),
        OpeningEntitySpec("tick_end", "LINE", "symbol", tick_end[0], tick_end[1]),
        OpeningEntitySpec(
            "center_tick",
            "LINE",
            "symbol",
            center,
            Point2D(center.x + toward.x * step, center.y + toward.y * step),
        ),
    )

    return OpeningGeometry(
        reference_start=ref_start,
        reference_end=ref_end,
        direction_point=resolved_direction,
        origin=origin,
        start_point=start,
        end_point=end,
        center=center,
        width_axis_deg=normalize_deg(axis_deg),
        side=side,
        width_mm=width,
        thickness_mm=thickness,
        offset_mm=offset,
        entities=entities,
        warnings=tuple(warnings),
    )


def write_opening(
    doc: Any,
    opening: OpeningGeometry,
    layers: Sequence[str] = DEFAULT_LAYERS,
) -> OpeningRecord:
    """Emit the opening symbol into ``doc`` and return a readback record."""
    resolved = tuple(layers)
    if len(resolved) != 2:
        raise OpeningGeometryError(
            "layers must be exactly (boundary_layer, symbol_layer)"
        )
    boundary_layer, symbol_layer = resolved
    if not all(str(name).strip() for name in resolved):
        raise OpeningGeometryError("layer names must be non-empty")

    document_id = _document_id(doc)
    for name in resolved:
        if not doc.layers.has_entry(name):
            doc.layers.add(name)

    slot_to_layer = {"boundary": boundary_layer, "symbol": symbol_layer}
    msp = doc.modelspace()
    handles: list[str] = []
    for spec in opening.entities:
        entity = msp.add_line(
            (spec.start.x, spec.start.y, 0.0),
            (spec.end.x, spec.end.y, 0.0),
            dxfattribs={"layer": slot_to_layer[spec.layer_slot]},
        )
        handles.append(str(entity.dxf.handle))

    return OpeningRecord(
        document_id=document_id,
        geometry=opening,
        layers=resolved,
        entity_handles=tuple(handles),
        snapshots=tuple(readback_snapshots(doc, handles, document_id)),
    )


def readback_snapshots(
    doc: Any,
    handles: Sequence[str],
    document_id: str,
) -> list[EntitySnapshot]:
    """Build readback.EntitySnapshot values straight from the live document."""
    by_handle = {
        str(entity.dxf.handle).upper(): entity for entity in doc.modelspace()
    }
    snapshots: list[EntitySnapshot] = []
    for handle in handles:
        entity = by_handle[str(handle).upper()]
        start = entity.dxf.start
        end = entity.dxf.end
        snapshots.append(
            EntitySnapshot(
                document_id=document_id,
                handle=str(entity.dxf.handle),
                entity_type=entity.dxftype(),
                layer=str(entity.dxf.layer),
                geometry={
                    "start": [start.x, start.y],
                    "end": [end.x, end.y],
                },
            )
        )
    return snapshots


def normalize_deg(value: float) -> float:
    """Normalise an angle in degrees into [0, 360)."""
    result = math.fmod(value, 360.0)
    if result < 0:
        result += 360.0
    if result == 360.0 or result == -0.0:
        result = 0.0
    return result


def _positive(value: float, name: str) -> float:
    result = _finite(value, name)
    if result <= 0:
        raise OpeningGeometryError(f"{name} must be > 0 (got {value!r})")
    return result


def _non_negative(value: float, name: str) -> float:
    result = _finite(value, name)
    if result < 0:
        raise OpeningGeometryError(f"{name} must be >= 0 (got {value!r})")
    return result


def _finite(value: float, name: str) -> float:
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise OpeningGeometryError(
            f"{name} must be a finite number (got {value!r})"
        )
    return number


def _document_id(doc: Any) -> str:
    for attribute in ("doc_id", "filename"):
        value = getattr(doc, attribute, None)
        if isinstance(value, str) and value:
            return value
    return "in-memory"


def _as_point(value: tuple[float, float] | Point2D, name: str) -> Point2D:
    if isinstance(value, Point2D):
        return Point2D(_finite(value.x, f"{name}.x"), _finite(value.y, f"{name}.y"))
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise OpeningGeometryError(f"{name} must be an (x, y) pair (got {value!r})")
    return Point2D(_finite(value[0], f"{name}.x"), _finite(value[1], f"{name}.y"))


def _unit(deg: float) -> Point2D:
    radians = math.radians(deg)
    return Point2D(math.cos(radians), math.sin(radians))
