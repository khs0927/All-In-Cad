"""Window (mullion window) recorder: plan-view window symbol as LINE + ARC only.

No INSERT, no HATCH, by the same decision the door recorder made. Every entity
is an explicit edge so this repository's topology/verification code can consume
it without expanding blocks.

=====================================================================
OBSERVED PROMPT SEQUENCE (runtime capture, high confidence)
=====================================================================
    xiWin2 : '>> 설정(S)/ 창문 폭 입력 <1500:'
             -> '한 쪽 점 지정 (실내측 점) <_nea>: '
             -> '동일 선상의 창문폭 방향 점지정 <_nea>: '

    xiWallOpening : '>> 설정(S)/ 개구부 폭 입력 <...>'
             -> '상대쪽 벽체 선 지정 <_per>: '
             -> '동일 선상의 개구부폭 방향 점지정 <_nea>: '

    Restored DCL labels (decrypted): WinDiv_edt '창 등분 갯수',
    WinBarLay_edt '창틀 켜', WinDivWd '양개문 시작길이',
    Offset_edt '시작점에서 개구부 띄울 거리',
    EachWall_rdo '각각의 벽선으로', Fin1Wall_rdo '첫번째 벽선 따라'.

    CONFIRMED VALUES: door width 900 mm, window width 1500 mm. Every other
    default (divisions, bar layout, casement start length, offset, per-wall vs
    first-wall mode) was NEVER read, so this module ships them as explicitly
    labelled design constants, never as measured defaults.

=====================================================================
THE INDOOR POINT IS LOAD-BEARING [ESTIMATION]
=====================================================================
OBSERVED: the first pick is labelled '실내측 점' (indoor-side point). So it is
not a neutral datum: it decides which side of the wall is indoor. This module
makes that concrete. Given an optional ``wall_segment`` (read-only context,
same pattern as ``door.make_door``), the indoor side is derived as the sign of
``dot(indoor_point - closest_point_on_wall, n)`` where ``n`` is the left-hand
normal of the width axis.

[DESIGN] How the indoor side reaches the drawing:
  * the two jamb LINEs are symmetric, so the side does not affect them;
  * the elevation / interior face LINE (layer WINELE) is drawn on the INDOOR
    face only, so the side decides which face line exists;
  * the opening-direction ARC (layer WIN) sweeps from the indoor jamb towards
    the interior, so an inward-opening casement and an outward-opening one are
    different geometry rather than the same symbol relabelled.
Without a wall segment the side cannot be measured, so ``interior_side`` is an
explicit parameter (default 'left') and a warning is recorded.

=====================================================================
[DESIGN] ENTITY COMPOSITION -- NOT OBSERVED. The original window command's
entity output was never captured; no frame count, bar layout or casement
geometry below has any observational support.
=====================================================================
    role                dxftype  layer    count            meaning
    ------------------  -------  -------  ---------------  -------------------
    jamb_start          LINE     WIN      1                opening edge, -w/2
    jamb_end            LINE     WIN      1                opening edge, +w/2
    glazing             LINE     WIN      1                glazing line on the
                                                     wall centreline
    casement_arc        ARC      WIN      1                opening direction,
                                                     hinge at the indoor jamb
    interior_face       LINE     WINELE   1                indoor face line only
    mullion             LINE     WINBAR   ``divisions``    '창틀 켜' bars

Layers: ``WIN``, ``WINBAR`` and ``WINELE`` are [OBSERVED] entries of
``configs/architectural-layers.json`` (window, window_bar, window), so no new
layer name is coined. The three-way split of the composition across those
layers is a design choice, not an observation.

=====================================================================
ENVIRONMENT NOTE (host trap, observed on this host by running it)
=====================================================================
Aside injects ``PYTHONHOME`` on this Windows host, which hides the venv's
standard library and makes ``import ezdxf`` fail with
``ModuleNotFoundError: No module named 'annotationlib'``. Always run with::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\window_test.py

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
    "DEFAULT_DIVISIONS",
    "DEFAULT_LAYERS",
    "DEFAULT_THICKNESS_MM",
    "DEFAULT_WINDOW_WIDTH_MM",
    "DXF_READ_FLOOR_VERSION",
    "DXF_VERSION",
    "DXF_VERSION_NAME",
    "WindowEntitySpec",
    "WindowGeometry",
    "WindowGeometryError",
    "WindowRecord",
    "make_window",
    "readback_snapshots",
    "write_window",
]

DXF_VERSION = "AC1032"
DXF_VERSION_NAME = "R2018"
DXF_READ_FLOOR_VERSION = "AC1015"

#: [OBSERVED] Window layers already in configs/architectural-layers.json.
DEFAULT_LAYERS: tuple[str, str, str] = ("WIN", "WINBAR", "WINELE")

#: [OBSERVED] The prompt default for the window width is 1500 mm.
DEFAULT_WINDOW_WIDTH_MM = 1500.0

#: [DESIGN, NOT OBSERVED] Restored DCL label WinDiv_edt is '창 등분 갯수' but no
#: default was ever read. 1 division == two panes is the simplest value that
#: still exercises the mullion path.
DEFAULT_DIVISIONS = 1

#: [DESIGN, NOT OBSERVED] The window spans a wall of unknown thickness here; the
#: door recorder made the same documented choice with 100 mm.
DEFAULT_THICKNESS_MM = 100.0

InteriorSide = Literal["left", "right"]
LayerSlot = Literal["window", "bar", "element"]


class WindowGeometryError(ValueError):
    """Raised when a window request cannot become valid LINE/ARC geometry."""


@dataclass(frozen=True, slots=True)
class WindowEntitySpec:
    """One planned entity, independent of ezdxf. ``dxftype`` is LINE or ARC."""

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
class WindowGeometry:
    """Resolved plan geometry for one window, before it touches a document."""

    anchor: Point2D
    indoor_point: Point2D
    direction_point: Point2D
    center: Point2D
    jamb_start: Point2D
    jamb_end: Point2D
    width_axis_deg: float
    interior_side: InteriorSide
    width_mm: float
    thickness_mm: float
    divisions: int
    entities: tuple[WindowEntitySpec, ...]
    warnings: tuple[str, ...] = ()

    @property
    def handle_hint(self) -> str:
        """Stable textual identity for the window, used for warnings and tests."""
        return (
            f"window@{self.center.x:.3f},{self.center.y:.3f}"
            f"/w{self.width_mm:.3f}/i{self.interior_side}"
        )

    def specs(self) -> tuple[WindowEntitySpec, ...]:
        return self.entities

    def specs_for_slot(self, slot: LayerSlot) -> tuple[WindowEntitySpec, ...]:
        return tuple(spec for spec in self.entities if spec.layer_slot == slot)

    def spec(self, role: str) -> WindowEntitySpec:
        return next(spec for spec in self.entities if spec.role == role)


@dataclass(frozen=True, slots=True)
class WindowRecord:
    """What was actually written, with readback evidence per entity."""

    document_id: str
    geometry: WindowGeometry
    layers: tuple[str, str, str]
    entity_handles: tuple[str, ...]
    snapshots: tuple[EntitySnapshot, ...]

    def handles(self) -> tuple[str, ...]:
        return self.entity_handles


def make_window(
    indoor_point: tuple[float, float] | Point2D,
    direction_point: tuple[float, float] | Point2D,
    width_mm: float = DEFAULT_WINDOW_WIDTH_MM,
    thickness_mm: float = DEFAULT_THICKNESS_MM,
    divisions: int = DEFAULT_DIVISIONS,
    *,
    interior_side: InteriorSide | None = None,
    wall_segment: tuple[tuple[float, float], tuple[float, float]] | None = None,
    indoor_tolerance: float = 1.0,
    casement_deg: float = 90.0,
) -> WindowGeometry:
    """Build window geometry from the observed two-pick prompt order.

    ``indoor_point`` is the [OBSERVED] '실내측 점' pick, ``direction_point`` the
    [OBSERVED] '창문폭 방향 점지정' pick on the same line. The width axis ``u`` is
    the unit vector indoor -> direction, and the window is centred on the indoor
    point's projection onto the width axis.

    ``wall_segment`` is optional read-only context (same pattern as
    ``door.make_door``). With it, ``interior_side`` is measured from the pick;
    without it, the side falls back to ``interior_side`` or 'left' plus a
    warning, because nothing observable decides it.
    """
    indoor = _as_point(indoor_point, "indoor_point")
    direction = _as_point(direction_point, "direction_point")
    width = _positive(width_mm, "width_mm")
    thickness = _positive(thickness_mm, "thickness_mm")
    sweep = _sweep(casement_deg)
    if indoor_tolerance < 0:
        raise WindowGeometryError("indoor_tolerance must be non-negative")
    if isinstance(divisions, bool) or not isinstance(divisions, int):
        raise WindowGeometryError(f"divisions must be an int, got {divisions!r}")
    if divisions < 1:
        raise WindowGeometryError(f"divisions must be >= 1, got {divisions}")
    if interior_side is not None and interior_side not in ("left", "right"):
        raise WindowGeometryError(
            f"interior_side must be 'left', 'right' or None, got {interior_side!r}"
        )

    warnings: list[str] = []

    span = math.dist((indoor.x, indoor.y), (direction.x, direction.y))
    if span <= 0:
        raise WindowGeometryError(
            "indoor_point and direction_point must differ; a zero-length width "
            "axis cannot bound a window"
        )
    axis_deg = math.degrees(math.atan2(direction.y - indoor.y, direction.x - indoor.x))
    axis = _unit(axis_deg)
    normal = Point2D(-axis.y, axis.x)

    # Indoor side: measured against the wall when the caller supplied one.
    if wall_segment is not None:
        wall_start = _as_point(wall_segment[0], "wall_segment[0]")
        wall_end = _as_point(wall_segment[1], "wall_segment[1]")
        wall_length = math.dist((wall_start.x, wall_start.y), (wall_end.x, wall_end.y))
        if wall_length <= 0:
            raise WindowGeometryError("wall_segment endpoints must differ")
        offset = signed_segment_offset(indoor, wall_start, wall_end, normal)
        if abs(offset) > indoor_tolerance:
            warnings.append(
                f"indoor_point {indoor.x:.3f},{indoor.y:.3f} is {abs(offset):.3f} mm "
                f"off the wall centreline (tolerance {indoor_tolerance:.3f} mm); the "
                f"indoor side is read from the pick, not from the wall"
            )
        side: InteriorSide = "left" if offset >= 0 else "right"
    else:
        side = interior_side or "left"
        if interior_side is None:
            warnings.append(
                "no wall_segment and no explicit interior_side: the indoor side "
                "is not observable, defaulting to 'left'"
            )
    if interior_side is not None and wall_segment is None:
        side = interior_side

    inward = normal if side == "left" else Point2D(-normal.x, -normal.y)
    outward = Point2D(-inward.x, -inward.y)

    def along(point: Point2D, distance: float) -> Point2D:
        return Point2D(point.x + axis.x * distance, point.y + axis.y * distance)

    def across(point: Point2D, distance: float) -> Point2D:
        return Point2D(point.x + inward.x * distance, point.y + inward.y * distance)

    # The indoor pick is the width-axis origin; the window is centred on it.
    center = along(indoor, 0.0)
    jamb_start = along(center, -width / 2.0)
    jamb_end = along(center, width / 2.0)
    half = thickness / 2.0

    def span_thickness(point: Point2D) -> tuple[Point2D, Point2D]:
        return (
            Point2D(point.x - inward.x * half, point.y - inward.y * half),
            Point2D(point.x + inward.x * half, point.y + inward.y * half),
        )

    jamb_start_ends = span_thickness(jamb_start)
    jamb_end_ends = span_thickness(jamb_end)

    entities: list[WindowEntitySpec] = [
        WindowEntitySpec(
            "jamb_start", "LINE", "window", jamb_start_ends[0], jamb_start_ends[1]
        ),
        WindowEntitySpec(
            "jamb_end", "LINE", "window", jamb_end_ends[0], jamb_end_ends[1]
        ),
        # Glazing line sits on the width axis, i.e. the wall centreline.
        WindowEntitySpec("glazing", "LINE", "window", jamb_start, jamb_end),
        # Opening direction: hinge at the indoor jamb, sweeping towards indoors
        # by casement_deg from the closed (in-plane) position.
        WindowEntitySpec(
            "casement_arc",
            "ARC",
            "window",
            center=Point2D(
                jamb_start.x - outward.x * half,
                jamb_start.y - outward.y * half,
            ),
            radius=width,
            start_deg=normalize_deg(axis_deg),
            end_deg=normalize_deg(
                axis_deg + (sweep if side == "left" else -sweep)
            ),
        ),
        # Indoor face line only, on the side the pick selected.
        WindowEntitySpec(
            "interior_face",
            "LINE",
            "element",
            across(jamb_start, half),
            across(jamb_end, half),
        ),
    ]

    for index in range(1, divisions + 1):
        distance = -width / 2.0 + width * index / (divisions + 1)
        bar = along(center, distance)
        bar_ends = span_thickness(bar)
        entities.append(
            WindowEntitySpec(
                f"mullion_{index}", "LINE", "bar", bar_ends[0], bar_ends[1]
            )
        )

    return WindowGeometry(
        anchor=indoor,
        indoor_point=indoor,
        direction_point=direction,
        center=center,
        jamb_start=jamb_start,
        jamb_end=jamb_end,
        width_axis_deg=normalize_deg(axis_deg),
        interior_side=side,
        width_mm=width,
        thickness_mm=thickness,
        divisions=divisions,
        entities=tuple(entities),
        warnings=tuple(warnings),
    )


def write_window(
    doc: Any,
    window: WindowGeometry,
    layers: Sequence[str] = DEFAULT_LAYERS,
) -> WindowRecord:
    """Emit the window into ``doc`` and return a readback record per entity."""
    resolved = tuple(layers)
    if len(resolved) != 3:
        raise WindowGeometryError(
            "layers must be exactly (window_layer, bar_layer, element_layer)"
        )
    window_layer, bar_layer, element_layer = resolved
    if not all(str(name).strip() for name in resolved):
        raise WindowGeometryError("layer names must be non-empty")

    document_id = _document_id(doc)
    for name in resolved:
        if not doc.layers.has_entry(name):
            doc.layers.add(name)

    slot_to_layer = {
        "window": window_layer,
        "bar": bar_layer,
        "element": element_layer,
    }
    msp = doc.modelspace()
    handles: list[str] = []
    for spec in window.entities:
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

    return WindowRecord(
        document_id=document_id,
        geometry=window,
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


def signed_segment_offset(
    point: Point2D,
    start: Point2D,
    end: Point2D,
    normal: Point2D,
) -> float:
    """Signed distance of ``point`` from segment ``start..end`` along ``normal``.

    Uses the *infinite* line through the segment: an indoor pick may sit beyond
    the wall's end and still be on the correct side, which is what decides
    indoor/outdoor. Collapses to a plain point distance for a zero-length
    segment so the caller still gets a number instead of a ZeroDivisionError.
    """
    dx, dy = end.x - start.x, end.y - start.y
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return math.dist((point.x, point.y), (start.x, start.y))
    parameter = ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_sq
    closest = Point2D(start.x + parameter * dx, start.y + parameter * dy)
    return (point.x - closest.x) * normal.x + (point.y - closest.y) * normal.y


def normalize_deg(value: float) -> float:
    """Normalise an angle in degrees into [0, 360)."""
    result = math.fmod(value, 360.0)
    if result < 0:
        result += 360.0
    if result == 360.0 or result == -0.0:
        result = 0.0
    return result


def _sweep(casement_deg: float) -> float:
    value = _finite(casement_deg, "casement_deg")
    if not 0.0 < value < 360.0:
        raise WindowGeometryError(
            "casement_deg must satisfy 0 < casement_deg < 360: a sweep of 0 or "
            f"360 would make a zero-length arc (got {casement_deg!r})"
        )
    return value


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
    if point is None:
        raise WindowGeometryError("internal: point was None")
    return (point.x, point.y)


def _document_id(doc: Any) -> str:
    for attribute in ("doc_id", "filename"):
        value = getattr(doc, attribute, None)
        if isinstance(value, str) and value:
            return value
    return "in-memory"


def _positive(value: float, name: str) -> float:
    result = _finite(value, name)
    if result <= 0:
        raise WindowGeometryError(f"{name} must be > 0 (got {value!r})")
    return result


def _finite(value: float, name: str) -> float:
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise WindowGeometryError(f"{name} must be a finite number (got {value!r})")
    return number


def _as_point(value: tuple[float, float] | Point2D, name: str) -> Point2D:
    if isinstance(value, Point2D):
        return Point2D(_finite(value.x, f"{name}.x"), _finite(value.y, f"{name}.y"))
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise WindowGeometryError(f"{name} must be an (x, y) pair (got {value!r})")
    return Point2D(_finite(value[0], f"{name}.x"), _finite(value[1], f"{name}.y"))


def _unit(deg: float) -> Point2D:
    radians = math.radians(deg)
    return Point2D(math.cos(radians), math.sin(radians))
