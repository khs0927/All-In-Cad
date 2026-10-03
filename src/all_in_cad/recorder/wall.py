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
LAYER NAMING: TWO NAME SYSTEMS. OBSERVED AND DESIGN ARE NOT THE SAME
=====================================================================
[OBSERVED] Entity dump of a wall actually drawn in ZWCAD by the running
module (centreline (0,0)->(12000,0), all entities were LINE, all units mm).
Y is the offset from the centreline:

    layer "0" : LINE Y=0                                  1 entity
    layer "C" : LINE Y=-100, LINE Y=+100                  2 entities
    layer "S" : LINE Y=-120, LINE Y=+250, LINE Y=+280     3 entities
    layer "F" : LINE Y=+200                               1 entity

That is 1 + 2 + 3 + 1 = **7 entities**, and the same 7 offsets are exposed as
:data:`OBSERVED_XICAD_LINE_OFFSETS_MM`. Thickness 200 -> the two faces at
+/-100 and the centreline at 0 is [OBSERVED] and is reproduced exactly by
``make_wall``.

[DESIGN] **"9 entities".** Not supported by the dump. 9 only works if two
end-cap lines are quietly added to the tally, and the dump contains no caps:
layer "C" is exactly the two faces at -100/+100. The number 9 is therefore a
[DESIGN] claim and this module does not make it. See
:data:`OBSERVED_XICAD_LINE_OFFSETS_MM` for the count that was actually seen.

[DESIGN] **The roles of "S" and "F" ("trim", "detail", "soffit").** The dump
proves the four coordinates and nothing else. Neither the drawing nor the
recovered binary says what those lines are -- -120 is 20 mm outside the wall
face, +250/+280 are 150/180 mm outside it, which is consistent with a ceiling
cornice, a soffit, a floor mark, or nothing in particular. Only
``C -> the two faces`` is consistent with the dump; everything else here is a
documented guess. :data:`classify_layer` confirms the gap: ``"C"``/``"S"``/
``"F"``/``"0"`` all classify to ``LayerSemantic.UNKNOWN`` [OBSERVED].

[DESIGN] **Cap lines.** The dump has none. ``make_wall`` emits two by default
(``cap_style="line"``) because a bounded wall needs ends, but that is this
module's choice, not a reproduction of the dump. Note that the default output
is 5 entities, not 7 and not 9, because it does not emit the S/F lines unless
ratios are passed.

The project convention (``src/all_in_cad/semantic_layers.py`` and
``configs/architectural-layers.json``) names wall layers
``WAL1``/``WAL2``/``WAL3`` (all classifying to ``LayerSemantic.WALL``) and
centreline layers ``CEN``/``CEN1`` (classifying to ``LayerSemantic.CENTERLINE``).
There is no ``C``/``S``/``F`` entry in that table at all, so -- exactly as in
``hatch.py`` (``KNOWN_HATCH_LAYERS = ()``) and ``text.py``
(``KNOWN_TEXT_LAYERS = ()``) -- the XiCAD-to-project mapping is NOT resolved by
observation. Unlike ``dim.py``'s ``LAYER_MAPPING_RESOLVED = True`` (where
``DIM`` exists in the config), nothing here resolves ``C``/``S``/``F``.

The observed layer names are exposed verbatim as
:data:`OBSERVED_XICAD_LAYERS` and can be selected at runtime, while the DEFAULT
output uses this repository's WAL1/WAL2/WAL3 convention. No new layer names are
invented by this module. Read :func:`observed_layer_plan` before trusting it: it
is a [DESIGN] reproduction aid, not an observed configuration.

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
    "DESIGN_SF_DETAIL_RATIOS",
    "DESIGN_SF_OFFSET_RATIOS",
    "DESIGN_SF_TRIM_RATIOS",
    "OBSERVED_XICAD_LAYERS",
    "OBSERVED_XICAD_LINE_OFFSETS_MM",
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

#: The literal layer strings seen in the ZWCAD dump [OBSERVED]. The *mapping* of
#: those strings onto project roles is NOT observed -- see the module docstring.
#: PROJECT:  what this repository calls the same role.
OBSERVED_XICAD_LAYERS: dict[str, str] = {
    # [OBSERVED] layer name and the count of lines on it; the role below is
    # [DESIGN] unless marked.
    "0": DEFAULT_AXIS_LAYER,  # OBSERVED 1 line, Y=0; centreline role is consistent
    "C": "WAL1",  # OBSERVED 2 lines, Y=-100/+100; "the two faces" is consistent
    "S": "WAL2",  # OBSERVED 3 lines, Y=-120/+250/+280; role [DESIGN], unnamed
    "F": "WAL3",  # OBSERVED 1 line, Y=+200; role [DESIGN], unnamed
}

#: The dump itself, as measured: layer name -> Y offsets from the centreline
#: (mm) of a 200 mm wall. [OBSERVED] -- these are the numbers, nothing more.
#: Total entity count is ``sum(len(v) for v in ...)`` = 1+2+3+1 = **7**.
OBSERVED_XICAD_LINE_OFFSETS_MM: dict[str, tuple[float, ...]] = {
    "0": (0.0,),
    "C": (-100.0, 100.0),
    "S": (-120.0, 250.0, 280.0),
    "F": (200.0,),
}

#: [DESIGN] How many of the observed S/F lines this module routes to
#: :attr:`LayerPlan.trim` (which lands on the "S"/``WAL2`` layer). The dump
#: proves 3 lines on "S"; that the 3 lines are a single trim-like feature is a
#: guess, and it is a guess that has to be *split out* by the caller because
#: ``trim_ratios`` and ``detail_ratios`` are separate parameters.
DESIGN_SF_TRIM_RATIOS: tuple[float, ...] = (-1.2, 2.5, 2.8)

#: [DESIGN] The remaining observed line (Y=+200 -> +2.0 half-thicknesses) routed
#: to :attr:`LayerPlan.detail` on the "F"/``WAL3`` layer.
DESIGN_SF_DETAIL_RATIOS: tuple[float, ...] = (2.0,)

#: [DESIGN] The four observed S/F offsets expressed in half-thicknesses of the
#: 200 mm wall (half = 100 mm): -1.2, 2.5, 2.8, 2.0. Kept only as the rescaling
#: helper it is. Passing this whole tuple as ``detail_ratios`` sends ALL FOUR
#: lines to the detail role and therefore does NOT reproduce the observed
#: layer split -- that is why the two constants above exist. Set
#: ``trim_ratios=()`` and ``detail_ratios=()`` to omit these lines entirely.
DESIGN_SF_OFFSET_RATIOS: tuple[float, ...] = (
    *DESIGN_SF_TRIM_RATIOS,
    *DESIGN_SF_DETAIL_RATIOS,
)


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
    """[DESIGN] A layer plan that *resembles* the observed XiCAD assignment.

    [OBSERVED] part: layer "C" carries the two faces, "0" the centreline, and
    three lines on "S" / one on "F" exist at all. [DESIGN] part: the role names
    ("trim", "detail") and -- importantly -- ``cap`` is placed on "C", so calling
    this reproduces 4 lines on layer "C" where the dump had exactly 2. Do not
    read the output of this function as a reproduction of the observation; the
    observed line offsets are :data:`OBSERVED_XICAD_LINE_OFFSETS_MM`.
    """
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
