"""Door recorder: writes a plan-view door symbol into a DXF as LINE + ARC only.

DESIGN (설계) -- this module invents the entity composition below. Nothing here is
an observation of what the original CAD runtime writes. [UNOBSERVED] The original
door command's output (block INSERT vs. line/arc combo vs. hatch) was never
captured, and the hypothesis that ``DOOR_ELE`` / ``OnewayDoor`` are block names
has neither support nor refutation. This recorder therefore makes NO block
INSERT and NO HATCH, by explicit decision of the task.

[OBSERVED] Layer names ``DOOR`` and ``DOOR_ELE`` already exist in
``configs/architectural-layers.json`` and both map to semantic ``door`` in
``semantic_layers.py``. ``DOOR_ELE`` corresponds to the restored DCL item
``DoorEle_rdo '문틀 입면선'`` (door-frame face line). No new layer name is coined.

[DESIGN] Composition, 6 entities, all LINE/ARC:

    role                 dxftype  layer      meaning
    -------------------  -------  ---------  ------------------------------------
    opening_edge_hinge   LINE     DOOR       opening edge line at the hinge end
    opening_edge_latch   LINE     DOOR       opening edge line at the latch end
    leaf                 LINE     DOOR       door leaf, drawn in the open position
    swing_arc            ARC      DOOR       opening direction, hinge-centred, r=width
    frame_face_hinge     LINE     DOOR_ELE   door-frame face line, hinge side
    frame_face_latch     LINE     DOOR_ELE   door-frame face line, latch side

Why this combination: a plan door symbol needs (a) the two opening edges so the
opening is measurable against the wall, (b) a leaf line so the leaf position is
a real segment any downstream tool can intersect, and (c) one arc so the swing
direction and swing angle are readable without executing CAD. HATCH is rejected
because a swing symbol is an outline, not a region, and HATCH introduces
boundary/associativity state that has no meaning for a door. INSERT is rejected
because the task requires inline geometry and because a block reference cannot
express "leaf drawn at swing angle" without per-instance transforms.

Wall orientation: the wall runs along the width axis ``u`` (default +X). The
door assembly is laid out on the wall centreline ``y = center_y``. ``n`` is the
left-hand normal of ``u`` and spans the thickness.

ANGLE POLICY (결정): every angle is normalised mod 360 into [0, 360). The ARC
sweep must satisfy 0 < swing < 360 exactly; a sweep of 0 or 360 is degenerate
(the arc would have zero length) and is rejected with ValueError rather than
silently dropped. Because a normalised swing in (0, 360) is never a multiple of
360, ``arc_end_deg`` can never equal ``arc_start_deg`` after normalisation, so
ezdxf never receives a zero-length arc.

ENVIRONMENT NOTE (호스트 함정): Aside injects ``PYTHONHOME`` on this Windows
host, which hides the standard library and makes ``import ezdxf`` fail with
``ModuleNotFoundError: No module named 'annotationlib'``. Always run with::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest ...

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
    "DEFAULT_FRAME_WIDTH_MM",
    "DEFAULT_LAYERS",
    "DEFAULT_THICKNESS_MM",
    "DXF_READ_FLOOR_VERSION",
    "DXF_VERSION",
    "DXF_VERSION_NAME",
    "DoorEntitySpec",
    "DoorGeometry",
    "DoorRecord",
    "WIDTH_PRESET_MM",
    "make_door",
    "make_door_centered",
    "readback_snapshots",
    "write_door",
]

DXF_VERSION = "AC1032"
DXF_VERSION_NAME = "R2018"
DXF_READ_FLOOR_VERSION = "AC1015"

DEFAULT_LAYERS: tuple[str, str] = ("DOOR", "DOOR_ELE")

# [OBSERVED] DCL width presets.
WIDTH_PRESET_MM: tuple[int, ...] = (30, 60, 90, 120, 150, 180)

# [DESIGN, NOT OBSERVED] The restored DCL dialog gives the *names* of
# DoorThk_edt (문두께) and BarWidth_edt (틀 폭) but no default values were ever
# read. These two numbers are my chosen defaults, not measurements.
DEFAULT_THICKNESS_MM = 100.0
DEFAULT_FRAME_WIDTH_MM = 0.0

Side = Literal["left", "right"]
LayerSlot = Literal["door", "frame"]


class DoorGeometryError(ValueError):
    """Raised when a door request cannot be turned into valid LINE/ARC geometry."""


@dataclass(frozen=True, slots=True)
class DoorEntitySpec:
    """One planned entity, independent of ezdxf. ``dxftype`` is LINE or ARC only."""

    role: str
    dxftype: Literal["LINE", "ARC"]
    layer_slot: LayerSlot
    start: Point2D | None = None
    end: Point2D | None = None
    center: Point2D | None = None
    radius: float | None = None
    start_deg: float | None = None
    end_deg: float | None = None


@dataclass(frozen=True, slots=True)
class DoorGeometry:
    """Fully resolved plan geometry for one door, before it touches a document."""

    center: Point2D
    hinge: Point2D
    latch: Point2D
    leaf_tip: Point2D
    width_axis_deg: float
    closed_deg: float
    arc_start_deg: float
    arc_end_deg: float
    swing_deg: float
    width_mm: float
    thickness_mm: float
    frame_width_mm: float
    side: Side
    center_on_wall: bool
    entities: tuple[DoorEntitySpec, ...]
    warnings: tuple[str, ...] = ()

    @property
    def handle_hint(self) -> str:
        """Stable textual identity for the door, used for warnings and tests."""
        return (
            f"door@{self.center.x:.3f},{self.center.y:.3f}"
            f"/w{self.width_mm:.3f}/s{self.side}"
        )

    def specs(self) -> tuple[DoorEntitySpec, ...]:
        return self.entities

    def specs_for_slot(self, slot: LayerSlot) -> tuple[DoorEntitySpec, ...]:
        return tuple(spec for spec in self.entities if spec.layer_slot == slot)


@dataclass(frozen=True, slots=True)
class DoorRecord:
    """What was actually written, with the readback evidence for each entity."""

    document_id: str
    geometry: DoorGeometry
    layers: tuple[str, str]
    entity_handles: tuple[str, ...]
    snapshots: tuple[EntitySnapshot, ...]

    def handles(self) -> tuple[str, ...]:
        return self.entity_handles


def make_door(
    hinge_point: tuple[float, float] | Point2D,
    width_mm: float,
    thickness_mm: float = DEFAULT_THICKNESS_MM,
    swing_deg: float = 90.0,
    side: Side = "left",
    center_on_wall: bool = True,
    *,
    width_axis_deg: float = 0.0,
    wall_segment: tuple[tuple[float, float], tuple[float, float]] | None = None,
    hinge_tolerance: float = 1.0,
    frame_width_mm: float = DEFAULT_FRAME_WIDTH_MM,
) -> DoorGeometry:
    """Build door geometry from a hinge point.

    ``side="left"`` puts the hinge on the low-X end of the width axis, so the
    closed leaf points along +u. ``side="right"`` mirrors that.

    ``wall_segment`` is optional and read-only context: when supplied, its
    direction becomes the width axis and the hinge is checked against it. An
    off-wall hinge is a *warning*, never an error, because the recorder has no
    wall model of its own.
    """
    hinge = _as_point(hinge_point, "hinge_point")
    width = _positive(width_mm, "width_mm")
    thickness = _positive(thickness_mm, "thickness_mm")
    frame_width = _non_negative(frame_width_mm, "frame_width_mm")
    swing = _sweep(swing_deg)
    if side not in ("left", "right"):
        raise DoorGeometryError(f"side must be 'left' or 'right', got {side!r}")
    if hinge_tolerance < 0:
        raise DoorGeometryError("hinge_tolerance must be non-negative")

    warnings: list[str] = []

    if wall_segment is not None:
        wall_start = _as_point(wall_segment[0], "wall_segment[0]")
        wall_end = _as_point(wall_segment[1], "wall_segment[1]")
        wall_length = math.dist((wall_start.x, wall_start.y), (wall_end.x, wall_end.y))
        if wall_length <= 0:
            raise DoorGeometryError("wall_segment endpoints must differ")
        axis_deg = math.degrees(
            math.atan2(wall_end.y - wall_start.y, wall_end.x - wall_start.x)
        )
        distance = _point_segment_distance(hinge, wall_start, wall_end)
        if distance > hinge_tolerance:
            warnings.append(
                f"hinge {hinge.x:.3f},{hinge.y:.3f} is {distance:.3f} mm from the "
                f"wall centreline (tolerance {hinge_tolerance:.3f} mm)"
            )
    else:
        axis_deg = width_axis_deg

    axis = _unit(axis_deg)
    normal = Point2D(-axis.y, axis.x)

    # closed direction: the leaf direction when the door is shut.
    closed = axis if side == "left" else Point2D(-axis.x, -axis.y)
    closed_deg = normalize_deg(math.degrees(math.atan2(closed.y, closed.x)))

    # ``closed`` runs from the hinge towards the latch, so the latch is a full
    # width away from the hinge and the centre sits halfway between them.
    latch = Point2D(hinge.x + closed.x * width, hinge.y + closed.y * width)
    center = Point2D(
        (hinge.x + latch.x) / 2.0, (hinge.y + latch.y) / 2.0
    )

    # Leaf drawn in the OPEN position: rotate the closed direction by the swing.
    leaf_dir = _rotate(closed, swing)
    leaf_tip = Point2D(
        hinge.x + leaf_dir.x * width, hinge.y + leaf_dir.y * width
    )

    # Thickness span across the opening. center_on_wall=True straddles the wall
    # centreline; False is my design alternative, flush to the +normal face.
    if center_on_wall:
        half = thickness / 2.0
        lower, upper = -half, half
    else:
        lower, upper = 0.0, thickness

    def across(point: Point2D) -> tuple[Point2D, Point2D]:
        return (
            Point2D(point.x + normal.x * lower, point.y + normal.y * lower),
            Point2D(point.x + normal.x * upper, point.y + normal.y * upper),
        )

    hinge_edge, latch_edge = across(hinge), across(latch)
    frame_hinge_point = Point2D(
        hinge.x - closed.x * frame_width, hinge.y - closed.y * frame_width
    )
    frame_latch_point = Point2D(
        latch.x + closed.x * frame_width, latch.y + closed.y * frame_width
    )
    frame_hinge, frame_latch = across(frame_hinge_point), across(frame_latch_point)

    entities: tuple[DoorEntitySpec, ...] = (
        DoorEntitySpec("opening_edge_hinge", "LINE", "door", hinge_edge[0], hinge_edge[1]),
        DoorEntitySpec("opening_edge_latch", "LINE", "door", latch_edge[0], latch_edge[1]),
        DoorEntitySpec("leaf", "LINE", "door", hinge, leaf_tip),
        DoorEntitySpec(
            "swing_arc",
            "ARC",
            "door",
            center=hinge,
            radius=width,
            start_deg=closed_deg,
            end_deg=normalize_deg(closed_deg + swing),
        ),
        DoorEntitySpec(
            "frame_face_hinge", "LINE", "frame", frame_hinge[0], frame_hinge[1]
        ),
        DoorEntitySpec(
            "frame_face_latch", "LINE", "frame", frame_latch[0], frame_latch[1]
        ),
    )

    return DoorGeometry(
        center=center,
        hinge=hinge,
        latch=latch,
        leaf_tip=leaf_tip,
        width_axis_deg=normalize_deg(axis_deg),
        closed_deg=closed_deg,
        arc_start_deg=closed_deg,
        arc_end_deg=normalize_deg(closed_deg + swing),
        swing_deg=swing,
        width_mm=width,
        thickness_mm=thickness,
        frame_width_mm=frame_width,
        side=side,
        center_on_wall=center_on_wall,
        entities=entities,
        warnings=tuple(warnings),
    )


def make_door_centered(
    center_x: float,
    center_y: float,
    width_mm: float,
    thickness_mm: float = DEFAULT_THICKNESS_MM,
    swing_deg: float = 90.0,
    side: Side = "left",
    center_on_wall: bool = True,
    *,
    width_axis_deg: float = 0.0,
    wall_segment: tuple[tuple[float, float], tuple[float, float]] | None = None,
    hinge_tolerance: float = 1.0,
    frame_width_mm: float = DEFAULT_FRAME_WIDTH_MM,
) -> DoorGeometry:
    """Centre-based entry point. The hinge sits exactly ``width_mm / 2`` from the centre.

    Goal case: ``make_door_centered(6000, 0, 900)`` puts the hinge at 5550,0 and
    the latch at 6450,0, so the door spans X 5550..6450 and its centre is 6000.
    """
    width = _positive(width_mm, "width_mm")
    cx = _finite(center_x, "center_x")
    cy = _finite(center_y, "center_y")

    if wall_segment is not None:
        wall_start = _as_point(wall_segment[0], "wall_segment[0]")
        wall_end = _as_point(wall_segment[1], "wall_segment[1]")
        if math.dist((wall_start.x, wall_start.y), (wall_end.x, wall_end.y)) <= 0:
            raise DoorGeometryError("wall_segment endpoints must differ")
        axis_deg = math.degrees(
            math.atan2(wall_end.y - wall_start.y, wall_end.x - wall_start.x)
        )
    else:
        axis_deg = width_axis_deg

    if side not in ("left", "right"):
        raise DoorGeometryError(f"side must be 'left' or 'right', got {side!r}")

    axis = _unit(axis_deg)
    offset = width / 2.0
    sign = -1.0 if side == "left" else 1.0
    hinge = Point2D(cx + sign * axis.x * offset, cy + sign * axis.y * offset)

    return make_door(
        hinge,
        width,
        thickness_mm,
        swing_deg,
        side,
        center_on_wall,
        width_axis_deg=axis_deg,
        wall_segment=wall_segment,
        hinge_tolerance=hinge_tolerance,
        frame_width_mm=frame_width_mm,
    )


def write_door(
    doc: Any,
    door: DoorGeometry,
    layers: Sequence[str] = DEFAULT_LAYERS,
) -> DoorRecord:
    """Emit the door into ``doc`` and return a readback record for every entity."""
    if len(tuple(layers)) != 2:
        raise DoorGeometryError("layers must be exactly (door_layer, frame_layer)")
    door_layer, frame_layer = layers[0], layers[1]

    document_id = _document_id(doc)
    for name in (door_layer, frame_layer):
        if not doc.layers.has_entry(name):
            doc.layers.add(name)

    slot_to_layer = {"door": door_layer, "frame": frame_layer}
    msp = doc.modelspace()
    handles: list[str] = []
    for spec in door.entities:
        attribs = {"layer": slot_to_layer[spec.layer_slot]}
        if spec.dxftype == "LINE":
            entity = msp.add_line(
                _xy(spec.start), _xy(spec.end), dxfattribs=attribs
            )
        else:
            assert spec.center is not None and spec.radius is not None
            assert spec.start_deg is not None and spec.end_deg is not None
            entity = msp.add_arc(
                center=_xy(spec.center),
                radius=spec.radius,
                start_angle=spec.start_deg,
                end_angle=spec.end_deg,
                dxfattribs=attribs,
            )
        handles.append(str(entity.dxf.handle))

    return DoorRecord(
        document_id=document_id,
        geometry=door,
        layers=(door_layer, frame_layer),
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
        snapshots.append(
            EntitySnapshot(
                document_id=document_id,
                handle=str(entity.dxf.handle),
                entity_type=entity.dxftype(),
                layer=str(entity.dxf.layer),
                geometry=_entity_geometry(entity),
            )
        )
    return snapshots


def _entity_geometry(entity: Any) -> dict[str, Any]:
    if entity.dxftype() == "LINE":
        start = entity.dxf.start
        end = entity.dxf.end
        return {"start": [start.x, start.y], "end": [end.x, end.y]}
    center = entity.dxf.center
    return {
        "center": [center.x, center.y],
        "radius": float(entity.dxf.radius),
        "start_angle": float(entity.dxf.start_angle),
        "end_angle": float(entity.dxf.end_angle),
    }


def _xy(point: Point2D | None) -> tuple[float, float]:
    """ezdxf only accepts plain coordinate pairs, not our Point2D dataclass."""
    if point is None:
        raise DoorGeometryError("internal: point was None")
    return (point.x, point.y)


def _document_id(doc: Any) -> str:
    for attribute in ("doc_id", "filename"):
        value = getattr(doc, attribute, None)
        if isinstance(value, str) and value:
            return value
    return "in-memory"


def normalize_deg(value: float) -> float:
    """Normalise an angle in degrees into [0, 360)."""
    result = math.fmod(value, 360.0)
    if result < 0:
        result += 360.0
    if result == 360.0 or result == -0.0:
        result = 0.0
    return result


def _sweep(swing_deg: float) -> float:
    value = _finite(swing_deg, "swing_deg")
    if not 0.0 < value < 360.0:
        raise DoorGeometryError(
            "swing_deg must satisfy 0 < swing_deg < 360: a sweep of 0 or 360 "
            f"would make a zero-length arc (got {swing_deg!r})"
        )
    return value


def _positive(value: float, name: str) -> float:
    result = _finite(value, name)
    if result <= 0:
        raise DoorGeometryError(f"{name} must be > 0 (got {value!r})")
    return result


def _non_negative(value: float, name: str) -> float:
    result = _finite(value, name)
    if result < 0:
        raise DoorGeometryError(f"{name} must be >= 0 (got {value!r})")
    return result


def _finite(value: float, name: str) -> float:
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise DoorGeometryError(f"{name} must be a finite number (got {value!r})")
    return number


def _as_point(value: tuple[float, float] | Point2D, name: str) -> Point2D:
    if isinstance(value, Point2D):
        return Point2D(_finite(value.x, f"{name}.x"), _finite(value.y, f"{name}.y"))
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise DoorGeometryError(f"{name} must be an (x, y) pair (got {value!r})")
    return Point2D(_finite(value[0], f"{name}.x"), _finite(value[1], f"{name}.y"))


def _unit(deg: float) -> Point2D:
    radians = math.radians(deg)
    return Point2D(math.cos(radians), math.sin(radians))


def _rotate(direction: Point2D, deg: float) -> Point2D:
    radians = math.radians(deg)
    cos, sin = math.cos(radians), math.sin(radians)
    return Point2D(
        direction.x * cos - direction.y * sin,
        direction.x * sin + direction.y * cos,
    )


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
