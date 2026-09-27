"""Wall recorder: writes wall geometry to DXF R2018 using only LINE (and, where a
rounded joint is genuinely required, ARC) entities.

No INSERT, no HATCH. Every wall is expressed as explicit edges so the result is
readable by downstream topology/verification code in this repository.

=====================================================================
ENVIRONMENT NOTE (observed on this host, verified by running it)
=====================================================================
Python lives in ``C:\\Users\\khs09\\all-in-cad\\.venv`` and ezdxf 1.4.4 is
already installed there. Aside injects ``PYTHONHOME=C:\\Users\\khs09\\.aside\\
runtime\\python\\runtime`` into the process environment, which hides the
standard library of the venv interpreter and makes ``import ezdxf`` fail with
``ModuleNotFoundError: No module named 'annotationlib'``. Therefore always
clear both variables before running this module's tests::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\wall_test.py -q

This is a host quirk of the launcher, not a defect in this module.

=====================================================================
LAYER NAMING: THE OBSERVED XiCAD ZWCAD LAYERS AND THE PROJECT CONVENTION
ARE TWO DIFFERENT NAME SYSTEMS -- DO NOT CONFUSE THEM
=====================================================================
Observed (entity dump of a wall actually drawn in ZWCAD by the running module,
centreline (0,0)->(12000,0), all entities were LINE, all units mm):

    layer "0" : centreline LINE (0,0)->(12000,0)
    layer "C" : LINE Y=-100, LINE Y=+100        (two faces, thickness 200)
    layer "S" : LINE Y=-120, LINE Y=+250, LINE Y=+280
    layer "F" : LINE Y=+200

This repository's convention (``src/all_in_cad/semantic_layers.py`` and
``configs/architectural-layers.json``) instead names wall layers
``WAL1``/``WAL2``/``WAL3`` (all classifying to ``LayerSemantic.WALL``) and
centreline layers ``CEN``/``CEN1`` (classifying to ``LayerSemantic.CENTERLINE``).
There is no ``C``/``S``/``F`` entry in that table at all.

So: the observed ZWCAD layers and this project's layer convention are DIFFERENT
NAMING SYSTEMS for the same roles. Nothing is renamed away here: the observed
mapping is exposed verbatim as :data:`OBSERVED_XICAD_LAYERS` and can be selected
at runtime, while the DEFAULT output uses this repository's WAL1/WAL2/WAL3
convention as instructed. No new layer names are invented by this module.

Layer selection is ``doc_layer_centers=("WAL1", "WAL2", "WAL3")``: face lines,
cap lines, and detail lines respectively. The optional axis line defaults to
``CEN1`` (an existing name in the project convention) unless overridden.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

try:  # package-relative import (normal case)
    from ..readback import EntitySnapshot
    from ..semantic_layers import LayerSemantic, classify_layer
    from ..topology import Point2D
except ImportError:  # pragma: no cover - direct/flat execution fallback
    from all_in_cad.readback import EntitySnapshot
    from all_in_cad.semantic_layers import LayerSemantic, classify_layer
    from all_in_cad.topology import Point2D

__all__ = [
    "DEFAULT_DOC_LAYER_CENTERS",
    "OBSERVED_XICAD_LAYERS",
    "LayerPlan",
    "WallGeometry",
    "WallRecord",
    "WallRole",
    "WallValidationError",
    "make_wall",
    "write_wall",
    "layer_plan",
    "observed_layer_plan",
]

#: DXF version written by this recorder and the oldest version it reads.
DXF_WRITE_VERSION = "R2018"  # AC1032
DXF_MIN_READ_VERSION = "R2000"  # AC1015

#: Project layer convention (see module docstring). Index 0/1/2 are the
#: ``doc_layer_centers`` default: face, cap, detail.
DEFAULT_DOC_LAYER_CENTERS: tuple[str, str, str] = ("WAL1", "WAL2", "WAL3")

#: Existing project layer name for centrelines (``LayerSemantic.CENTERLINE``).
DEFAULT_AXIS_LAYER = "CEN1"

#: Observed XiCAD/ZWCAD module layers -> project-convention layer names.
#: OBSERVED: the drawing dumped from ZWCAD used these literal layer strings.
#: PROJECT:  what this repository calls the same role.
OBSERVED_XICAD_LAYERS: dict[str, str] = {
    "0": DEFAULT_AXIS_LAYER,  # OBSERVED centreline on layer "0"
    "C": "WAL1",  # OBSERVED the two wall faces
    "S": "WAL2",  # OBSERVED three trim/soffit lines
    "F": "WAL3",  # OBSERVED one face/detail line
}

#: Estimated ratio of the observed detail lines to the half-thickness.
#: ESTIMATION (not an observation): the observed detail lines sat at absolute
#: offsets -120, +250, +280, +200 mm from the centreline of a 200 mm wall
#: (half = 100 mm), i.e. -1.2, 2.5, 2.8, 2.0 half-thicknesses. The dump proves
#: the coordinates; it does NOT prove the semantic meaning of the S/F layers,
#: so the ratios are a documented guess used only to rescale the same shape to
#: other thicknesses. Set ``detail_ratios=()`` to omit them.
OBSERVED_DETAIL_RATIOS: tuple[float, ...] = (-1.2, 2.5, 2.8, 2.0)


class WallRole(StrEnum):
    """Role of a written wall entity (independent of the layer name)."""

    AXIS = "axis"
    FACE_NEG = "face_neg"
    FACE_POS = "face_pos"
    CAP_START = "cap_start"
    CAP_END = "cap_end"
    TRIM = "trim"
    DETAIL = "detail"


class WallValidationError(ValueError):
    """Raised for a wall that cannot be represented (degenerate geometry)."""


@dataclass(frozen=True, slots=True)
class LayerPlan:
    """Layer name per role. Names come from the project convention by default."""

    face: str = "WAL1"
    cap: str = "WAL2"
    trim: str = "WAL2"
    detail: str = "WAL3"
    axis: str = DEFAULT_AXIS_LAYER

    def for_role(self, role: WallRole) -> str:
        return {
            WallRole.FACE_NEG: self.face,
            WallRole.FACE_POS: self.face,
            WallRole.CAP_START: self.cap,
            WallRole.CAP_END: self.cap,
            WallRole.TRIM: self.trim,
            WallRole.DETAIL: self.detail,
            WallRole.AXIS: self.axis,
        }[role]

    def semantic_of(self, role: WallRole) -> LayerSemantic:
        return classify_layer(self.for_role(role))


def layer_plan(
    doc_layer_centers: Sequence[str] = DEFAULT_DOC_LAYER_CENTERS,
    *,
    axis_layer: str = DEFAULT_AXIS_LAYER,
) -> LayerPlan:
    """Build a :class:`LayerPlan` from the ``doc_layer_centers`` triple."""
    if len(doc_layer_centers) != 3:
        raise ValueError("doc_layer_centers must contain exactly three layer names")
    face, cap, detail = (str(name) for name in doc_layer_centers)
    for name in (face, cap, detail, axis_layer):
        if not name.strip():
            raise ValueError("layer names must be non-empty")
    return LayerPlan(face=face, cap=cap, trim=cap, detail=detail, axis=axis_layer)


def observed_layer_plan() -> LayerPlan:
    """Layer plan that reproduces the OBSERVED XiCAD layer assignment."""
    return LayerPlan(
        face=OBSERVED_XICAD_LAYERS["C"],
        cap=OBSERVED_XICAD_LAYERS["C"],
        trim=OBSERVED_XICAD_LAYERS["S"],
        detail=OBSERVED_XICAD_LAYERS["F"],
        axis=OBSERVED_XICAD_LAYERS["0"],
    )


@dataclass(frozen=True, slots=True)
class WallGeometry:
    """Pure 2D geometry of one wall, in millimetres, ready to be written."""

    centerline_start: Point2D
    centerline_end: Point2D
    thickness_mm: float
    layers: LayerPlan = field(default_factory=LayerPlan)
    include_axis: bool = False
    cap_style: str = "line"
    detail_ratios: tuple[float, ...] = ()
    trim_ratios: tuple[float, ...] = ()

    @property
    def half_thickness(self) -> float:
        return self.thickness_mm / 2.0

    @property
    def length(self) -> float:
        return math.dist(
            (self.centerline_start.x, self.centerline_start.y),
            (self.centerline_end.x, self.centerline_end.y),
        )

    @property
    def unit_direction(self) -> tuple[float, float]:
        length = self.length
        return (
            (self.centerline_end.x - self.centerline_start.x) / length,
            (self.centerline_end.y - self.centerline_start.y) / length,
        )

    @property
    def unit_normal(self) -> tuple[float, float]:
        """Left-hand normal of the centreline direction: ``(-uy, ux)``.

        FACE_NEG sits at ``-half`` along this normal and FACE_POS at ``+half``,
        so for a +X wall FACE_NEG is at Y=-half and for a +Y wall FACE_NEG is at
        X=+half. The pairing is deterministic; only the side label depends on
        the direction, and reversing the centreline swaps the two labels.
        """
        ux, uy = self.unit_direction
        return (-uy, ux)

    def validate(self) -> None:
        for name, point in (
            ("centerline_start", self.centerline_start),
            ("centerline_end", self.centerline_end),
        ):
            if not (math.isfinite(point.x) and math.isfinite(point.y)):
                raise WallValidationError(f"{name} must be finite")
        if not math.isfinite(self.thickness_mm):
            raise WallValidationError("thickness_mm must be finite")
        if self.thickness_mm <= 0:
            raise WallValidationError(
                f"thickness_mm must be positive, got {self.thickness_mm}"
            )
        if self.length <= 0:
            raise WallValidationError(
                "centerline length must be positive; a zero-length centerline "
                "cannot bound a wall"
            )
        if self.cap_style not in ("line", "miter", "none"):
            raise WallValidationError(
                f"cap_style must be 'line', 'miter' or 'none', got {self.cap_style!r}"
            )

    def edges(self) -> list[tuple[WallRole, Point2D, Point2D]]:
        """Return ``(role, start, end)`` triples in draw order.

        Only LINE geometry is emitted. A butt/miter end of a straight wall is
        fully described by two points, so ARC is not required for this wall
        shape; ARC would only be needed for a genuinely curved centreline,
        which is out of scope for this recorder and is therefore NOT guessed at.
        """
        self.validate()
        nx, ny = self.unit_normal
        ux, uy = self.unit_direction
        half = self.half_thickness
        start = self.centerline_start
        end = self.centerline_end

        def offset(point: Point2D, distance: float) -> Point2D:
            return Point2D(point.x + nx * distance, point.y + ny * distance)

        def along(point: Point2D, distance: float) -> Point2D:
            return Point2D(point.x + ux * distance, point.y + uy * distance)

        out: list[tuple[WallRole, Point2D, Point2D]] = []

        if self.include_axis:
            out.append((WallRole.AXIS, start, end))

        out.append((WallRole.FACE_NEG, offset(start, -half), offset(end, -half)))
        out.append((WallRole.FACE_POS, offset(start, half), offset(end, half)))

        if self.cap_style == "line":
            out.append((WallRole.CAP_START, offset(start, -half), offset(start, half)))
            out.append((WallRole.CAP_END, offset(end, -half), offset(end, half)))
        elif self.cap_style == "miter":
            # 45 degree bevel at each end, overhanging by the half-thickness.
            # This is the join two walls meeting at a right angle would need.
            for role, point in ((WallRole.CAP_START, start), (WallRole.CAP_END, end)):
                # start cap overhangs backwards, end cap overhangs forwards
                sign = -1.0 if role is WallRole.CAP_START else 1.0
                out.append(
                    (
                        role,
                        offset(along(point, sign * half), -half),
                        offset(along(point, sign * half), half),
                    )
                )

        for ratio in self.trim_ratios:
            out.append(
                (WallRole.TRIM, offset(start, ratio * half), offset(end, ratio * half))
            )
        for ratio in self.detail_ratios:
            out.append(
                (WallRole.DETAIL, offset(start, ratio * half), offset(end, ratio * half))
            )
        return out


@dataclass(frozen=True, slots=True)
class WallRecord:
    """Result of :func:`write_wall`: what was actually written, by handle."""

    document_id: str
    dxf_version: str
    centerline_start: Point2D
    centerline_end: Point2D
    thickness_mm: float
    length: float
    entities: tuple[tuple[WallRole, str, str, str, Point2D, Point2D], ...]

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    def handles(self) -> tuple[str, ...]:
        return tuple(item[1] for item in self.entities)

    def layer_of(self, handle: str) -> str:
        for _role, entity_handle, _type, layer, _s, _e in self.entities:
            if entity_handle.upper() == handle.upper():
                return layer
        raise KeyError(handle)

    def role_of(self, handle: str) -> WallRole:
        for role, entity_handle, _type, _layer, _s, _e in self.entities:
            if entity_handle.upper() == handle.upper():
                return role
        raise KeyError(handle)

    def layer_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _role, _handle, _type, layer, _s, _e in self.entities:
            counts[layer] = counts.get(layer, 0) + 1
        return counts

    def snapshots(self) -> list[EntitySnapshot]:
        """Convert the record into readback snapshots (for digest/diff)."""
        return [
            EntitySnapshot(
                document_id=self.document_id,
                handle=handle,
                entity_type=entity_type,
                layer=layer,
                geometry={
                    "start": [start.x, start.y],
                    "end": [end.x, end.y],
                },
            )
            for _role, handle, entity_type, layer, start, end in self.entities
        ]


def make_wall(
    centerline_start: Sequence[float] | Point2D,
    centerline_end: Sequence[float] | Point2D,
    thickness_mm: float,
    layers: LayerPlan | None = None,
    *,
    include_axis: bool = False,
    cap_style: str = "line",
    detail_ratios: Iterable[float] = (),
    trim_ratios: Iterable[float] = (),
) -> WallGeometry:
    """Build a :class:`WallGeometry` for a straight wall.

    ``thickness_mm`` may be any positive value; the two faces land exactly at
    ``+/- thickness_mm / 2`` from the centreline (thickness 200 -> +/- 100).
    The centreline may be axis-aligned or not; offsets are taken along the
    centreline normal, so a Y-direction (vertical) wall works the same way.
    """
    plan = layers if layers is not None else LayerPlan()
    geometry = WallGeometry(
        centerline_start=_as_point(centerline_start, "centerline_start"),
        centerline_end=_as_point(centerline_end, "centerline_end"),
        thickness_mm=float(thickness_mm),
        layers=plan,
        include_axis=bool(include_axis),
        cap_style=str(cap_style),
        detail_ratios=tuple(float(value) for value in detail_ratios),
        trim_ratios=tuple(float(value) for value in trim_ratios),
    )
    geometry.validate()
    return geometry


def write_wall(
    doc: Any,
    wall: WallGeometry,
    doc_layer_centers: Sequence[str] = DEFAULT_DOC_LAYER_CENTERS,
) -> WallRecord:
    """Write ``wall`` into an ezdxf document as LINE entities only.

    ``doc`` is an ``ezdxf.document.Drawing`` created for
    :data:`DXF_WRITE_VERSION` (AC1032, R2018). Missing layer table entries are
    created. Returns a :class:`WallRecord` describing exactly what was written.
    """
    ezdxf = _require_ezdxf()
    # ezdxf reports Drawing.dxfversion as the AC code ("AC1032") and
    # Drawing.acad_release as the release name ("R2018"). Accept either form.
    doc_version = str(getattr(doc, "dxfversion", "") or "")
    doc_release = str(getattr(doc, "acad_release", "") or "")
    if _release_of(doc_version) != DXF_WRITE_VERSION or (
        doc_release and doc_release != DXF_WRITE_VERSION
    ):
        raise ValueError(
            f"this recorder writes {DXF_WRITE_VERSION} (AC1032) only, got {doc_version}"
        )
    if not _is_r2000_or_newer(_release_of(doc_version)):  # pragma: no cover - defensive
        raise ValueError(f"read lower bound is R2000 (AC1015); got {doc_version}")

    plan = wall.layers
    if plan == LayerPlan() and tuple(doc_layer_centers) != DEFAULT_DOC_LAYER_CENTERS:
        plan = layer_plan(doc_layer_centers)

    msp = doc.modelspace()
    layer_names: list[str] = []
    for _role, _s, _e in wall.edges():
        name = plan.for_role(_role)
        if name not in layer_names:
            layer_names.append(name)
    for name in layer_names:
        if name not in doc.layers:
            doc.layers.add(name)

    written: list[tuple[WallRole, str, str, str, Point2D, Point2D]] = []
    for role, start, end in wall.edges():
        layer = plan.for_role(role)
        entity = msp.add_line(
            (start.x, start.y, 0.0),
            (end.x, end.y, 0.0),
            dxfattribs={"layer": layer},
        )
        written.append((role, entity.dxf.handle, "LINE", layer, start, end))

    start = wall.centerline_start
    end = wall.centerline_end
    return WallRecord(
        document_id=_document_id(doc),
        dxf_version=doc_release or _release_of(doc_version),
        centerline_start=start,
        centerline_end=end,
        thickness_mm=wall.thickness_mm,
        length=wall.length,
        entities=tuple(written),
    )


def _as_point(value: Sequence[float] | Point2D, name: str) -> Point2D:
    if isinstance(value, Point2D):
        return value
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        x, y = value[0], value[1]
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return Point2D(float(x), float(y))
    raise WallValidationError(f"{name} must be a 2D point, got {value!r}")


def _document_id(doc: Any) -> str:
    for attr in ("filename", "filepath"):
        value = getattr(doc, attr, "") or ""
        if value:
            return str(value)
    return "in-memory"


#: ezdxf AC code -> release name, for the versions this recorder cares about.
_DXF_CODES: dict[str, str] = {
    "AC1009": "R12",
    "AC1012": "R13",
    "AC1014": "R14",
    "AC1015": "R2000",
    "AC1018": "R2004",
    "AC1021": "R2007",
    "AC1024": "R2010",
    "AC1027": "R2013",
    "AC1032": "R2018",
}


def _release_of(version: str) -> str:
    """Accept ``AC1032`` or ``R2018`` and return the release name."""
    if version in _DXF_CODES:
        return _DXF_CODES[version]
    if version.startswith("R"):
        return version
    return version


def _is_r2000_or_newer(version: str) -> bool:
    return version in ("R2000", "R2004", "R2007", "R2010", "R2013", "R2018")


def _require_ezdxf() -> Any:
    try:
        import ezdxf  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - env guard
        raise ModuleNotFoundError(
            "ezdxf is required by the wall recorder. On this host run the project "
            "venv with a cleared PYTHONHOME, otherwise the standard library is "
            "hidden and this import fails with 'No module named annotationlib'."
        ) from exc
    return ezdxf
