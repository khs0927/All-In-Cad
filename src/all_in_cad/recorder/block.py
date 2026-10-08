"""Block definition + block insertion recorder.

This is the fifth module in the recorder family (``wall``/``door``/``window``/
``opening``/``hatch``) and follows the same two-function shape:

    make_xxx(...)      -> XxxGeometry   (pure geometry, no ezdxf)
    write_xxx(doc, g)  -> XxxRecord     (entity recording, ezdxf only)

=====================================================================
ENVIRONMENT NOTE (observed on this host, same as wall.py / hatch.py)
=====================================================================
ezdxf 1.4.4 lives in ``C:\\Users\\khs09\\all-in-cad\\.venv``. Aside injects
``PYTHONHOME``/``PYTHONPATH`` into the process environment, which hides the
venv interpreter's standard library and makes ``import ezdxf`` fail with
``ModuleNotFoundError: No module named 'annotationlib'``. Clear both first::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\block_test.py

=====================================================================
THE (a) / (b) / (c) DECISION, AND THE MEASUREMENT BEHIND IT
=====================================================================
The parent task posed three options for a block recorder:

  (a) define and insert a real ``INSERT`` entity (standard CAD practice);
  (b) flatten the block content and write LINE/ARC directly;
  (c) provide both, declare a default, and show with data which one passes
      which verification.

**This module implements (c), with (b) as the DEFAULT.**

The reason is not taste. It was measured on this host against the actual
normalizer (``extraction_runtime._normalize_ezdxf_entity``) and the actual
topology chain (``topology.segments_from_entities`` /
``topology.node_segments``), and the numbers are these [MEASURED]:

    (a) INSERT of a block whose content is 4 LINE edges, 6000 mm total:
            msp entities        = 1        (the INSERT)
            segments_from_...   = 0
            total segment length= 0.0 mm
    (b) the same 4 edges flattened as LINE on layer WAL1:
            msp entities        = 4
            segments_from_...   = 4
            total segment length= 6000.0 mm

``_normalize_ezdxf_entity`` has an INSERT branch, but it normalizes to
``geometry={"insert": [x, y, z]}`` and ``properties={"block_name": ...}`` only.
``segments_from_entities`` reads exactly two geometry shapes -- ``start``/``end``
and ``points`` -- so an INSERT has neither and yields nothing. Nothing about the
block content is traversed: ``doc.modelspace()`` for that drawing contains the
single INSERT and no LINEs. The block's own edges are in
``doc.blocks["OPEN1"]``, which no part of the verification chain visits.

So (a) means: geometry that is correct in a CAD application and **invisible to
this repository's normalizer, topology and every downstream measurement**. The
earlier audit already measured what substituting a substitute layer into an
opening does to an upper-level calculation (6 phantom wall segments and a fake
1891 mm length injected), which is the same failure mode: content that the
consumer cannot see is content the consumer will guess about.

(b) means: the block shape enters the chain through exactly the same keys as a
wall -- ``start``/``end`` on LINE, noding and intersection identical.

What (b) gives up, stated plainly: the block *reference* semantics. A caller who
inserts the same opening 40 times gets 40 independent copies of the geometry in
the file, cannot edit them as one unit in CAD, and loses the block table. That is
a real cost. It is the accepted cost of having the shape be measurable today.

=====================================================================
HOW "(a) IS INVISIBLE" IS EXPRESSED AS A CONTRACT, NOT AS AN ERROR
=====================================================================
The brevity rule for this repository: a quiet failure is the expensive one.
So (a) is NOT a silent mode and NOT a warning. It is a first-class mode whose
invisibility is *reported as a measured fact* by the record:

    record.mode                    == "insert"
    record.visible_downstream      is False
    record.contributed_segments    == 0
    record.contributed_length_mm   == 0.0
    record.hidden_segments         == <N>       <- what the INSERT conceals
    record.hidden_length_mm        == <L> mm    <- what the INSERT conceals
    record.downstream_contract     -> the full dict, incl. the exact
                                       normalizer geometry keys the entity will
                                       carry, so a caller can predict it

``hidden_segments`` / ``hidden_length_mm`` are computed by running the block
content through the *same* :func:`all_in_cad.topology.segments_from_entities`
the rest of the repository uses. So even in (a) mode the caller always learns
the real content size -- it is simply attributed to the definition rather than
to the modelspace. A caller that wants the contract enforced rather than
reported passes ``require_downstream_visible=True`` to
:func:`write_block_insert`, which then raises :class:`BlockValidationError`.
Both behaviours are available; neither is silent.

=====================================================================
ENTITY KINDS ALLOWED INSIDE A BLOCK DEFINITION (all [MEASURED])
=====================================================================
Contributions below are the actual output of ``segments_from_entities`` for one
entity of that kind, through the real normalizer, on this host.

ALLOWED
  LINE   1 segment. ``geometry={"start","end"}``. The only fully-supported
         primitive. This is the backbone of every recorder in the family.
  ARC    0 segments -- and this is the honest catch. ``_normalize_ezdxf_entity``
         gives an ARC ``{"center","radius","start_angle","end_angle"}``, which
         ``segments_from_entities`` cannot turn into a segment. So a rounded
         opening corner is geometrically faithful in the file and contributes
         nothing to the length/segment chain, exactly like an INSERT.
         ARC is nevertheless ALLOWED, and the reason is consistency, not
         ignorance: ``wall.py``, ``door.py`` and ``window.py`` already emit ARC
         for genuinely curved geometry, and blocking it here would invent a rule
         the completed modules do not follow. The cost is not hidden: every ARC
         contributes to :data:`BlockEntityKind` accounting and the definition
         record reports ``arc_count`` and ``arc_unmeasured_length_mm`` so the
         gap is visible in the record instead of assumed away.

REJECTED, each with its measured reason
  LWPOLYLINE  REJECTED. It is the trap, because it is the one non-LINE kind that
         DOES reach the chain: a straight closed 3-vertex LWPOLYLINE measured
         3 segments. But the normalizer reads it with ``get_points("xyseb")``
         and ``segments_from_entities`` keeps only ``[x, y]`` -- the **bulge is
         dropped**. MEASURED: a 2-vertex polyline with bulge 0.5 on a 10 mm
         chord reports length 10.0 mm; the true arc is a semicircle of radius
         5, i.e. 15.7 mm. That is a ~36% silent under-measurement of a curved
         edge, produced by geometry that looks completely correct in CAD. A
         straight LWPOLYLINE is also expressible as the LINEs it stands for, so
         accepting it would add a silent-failure mode and buy nothing.
  HATCH    REJECTED. No normalizer branch; falls through to ``geometry={}``
         (measured 0 segments). Same conclusion and same measurement as
         ``hatch.py``, which already rejects writing a real HATCH for this
         reason.
  SPLINE   REJECTED. No normalizer branch; ``geometry={}`` (measured 0).
  ELLIPSE  REJECTED. No normalizer branch; ``geometry={}`` (measured 0).
  CIRCLE   REJECTED. It DOES normalize (``{"center","radius"}``) but produces
         0 segments, and unlike ARC it has no precedent in this recorder
         family; a circle is a full-circle arc, which is separately refused
         (see ``_validate_arc``).

Nested blocks are refused as well: a block inside a block is an INSERT inside
the block, which is (a) hidden one level deeper.

=====================================================================
BASE POINT, ROTATION, SCALE -- AND WHY SCALE MUST BE UNIFORM
=====================================================================
The base point is the block origin. An instance maps a local point ``p`` to::

    world = location + R(rotation) * (scale * (p - base_point))

The same formula is used by BOTH modes, so ``mode="flatten"`` and
``mode="insert"`` produce identical world coordinates for the same instance --
which is the roundtrip test's strongest assertion, and the cheapest proof that
(a) is not quietly different from (b).

Scale is deliberately single-valued. A non-uniform scale turns an ARC into an
ELLIPSE under the transform, and ELLIPSE is rejected for the table above; more
importantly it would be a shape the module then could not record honestly. So
``scale_x != scale_y`` is refused rather than approximated.

=====================================================================
LAYER NAMES: NO NEW NAME IS INVENTED
=====================================================================
Blocks in this project are openings, doors and windows, so the block content is
written to the SAME existing convention layers as the standalone recorders:
``WAL1``/``WAL2``/``WAL3``, ``DOOR``/``DOOR_ELE``, ``WIN``/``WINBAR``/``WINELE``
(``configs/architectural-layers.json`` and ``semantic_layers.py``). The block
module invents no ``BLK*``/``OPEN*`` layer. A layer that ``classify_layer``
maps to ``LayerSemantic.UNKNOWN`` is refused, so a wrong name fails loudly
instead of becoming silent corruption.

OBSERVED, and deliberately kept separate from the convention: in the XiCAD
prototype, ``OPEN1`` (18) and ``OPEN2`` (10) blocks were observed inserted on
layer ``"0"``. That is the only block-related observation in existence, it does
not resolve which layer an opening block belongs to, and it is exposed verbatim
as :data:`OBSERVED_XICAD_BLOCK_LAYER` rather than being promoted to a default.
See :data:`OPENING_BLOCK_LAYER_STATUS`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

try:  # package-relative import (normal case)
    from ..readback import EntitySnapshot
    from ..semantic_layers import LayerSemantic, classify_layer
    from ..topology import Point2D, Segment2D, node_segments, segments_from_entities
except ImportError:  # pragma: no cover - direct/flat execution fallback
    from all_in_cad.readback import EntitySnapshot
    from all_in_cad.semantic_layers import LayerSemantic, classify_layer
    from all_in_cad.topology import (
        Point2D,
        Segment2D,
        node_segments,
        segments_from_entities,
    )

__all__ = [
    "DXF_MIN_READ_VERSION",
    "DXF_WRITE_VERSION",
    "OBSERVED_XICAD_BLOCK_LAYER",
    "OPENING_BLOCK_LAYER_STATUS",
    "CONVENTION_BLOCK_LAYERS",
    "ALLOWED_BLOCK_ENTITY_KINDS",
    "REJECTED_BLOCK_ENTITY_KINDS",
    "REJECTION_REASONS",
    "MEASURED_SEGMENT_CONTRIBUTION",
    "BlockEntity",
    "BlockEntityKind",
    "BlockDefinition",
    "BlockDefinitionRecord",
    "BlockInsert",
    "BlockInsertMode",
    "BlockInsertRecord",
    "BlockValidationError",
    "arc_entity",
    "line_entity",
    "line_entity_count",
    "make_block_definition",
    "make_block_insert",
    "transform_point",
    "write_block_definition",
    "write_block_insert",
]

#: DXF version written by this recorder and the oldest version it reads.
DXF_WRITE_VERSION = "R2018"  # AC1032
DXF_MIN_READ_VERSION = "R2000"  # AC1015

#: Existing project-convention layers a block definition may be written to.
#: These are the names already in ``configs/architectural-layers.json`` /
#: ``semantic_layers.py``. No ``BLK*`` or ``OPEN*`` layer is created.
CONVENTION_BLOCK_LAYERS: tuple[str, ...] = (
    "WAL1",
    "WAL2",
    "WAL3",
    "DOOR",
    "DOOR_ELE",
    "WIN",
    "WINBAR",
    "WINELE",
)

#: [OBSERVED] In the XiCAD prototype (1711-file survey) ``OPEN1`` appeared 18
#: times and ``OPEN2`` 10 times, all inserted on layer ``"0"``. This is the
#: ONLY block-related observation available. It is kept verbatim and is NOT
#: promoted to a default, because the same survey did not close the U-1
#: question of which layer an opening block belongs to.
OBSERVED_XICAD_BLOCK_LAYER = "0"

#: The U-1 question (which project layer an opening block belongs to) is not
#: closed by the layer-0 observation. This module therefore requires the caller
#: to name a convention layer and refuses ``"0"`` unless the caller opts in
#: explicitly, rather than guessing.
OPENING_BLOCK_LAYER_STATUS = "UNRESOLVED"


class BlockValidationError(ValueError):
    """Raised for a block that cannot be represented as given."""


class BlockEntityKind(StrEnum):
    """Entity kinds permitted inside a block definition."""

    LINE = "LINE"
    ARC = "ARC"


class BlockInsertMode(StrEnum):
    """How an instance reaches the drawing.

    FLATTEN is the default and the only mode whose geometry is measurable by
    this repository's normalizer. INSERT is opt-in and self-reports that it is
    not.
    """

    FLATTEN = "flatten"
    INSERT = "insert"


#: Kinds a block definition may contain.
ALLOWED_BLOCK_ENTITY_KINDS: frozenset[str] = frozenset(
    {BlockEntityKind.LINE, BlockEntityKind.ARC}
)

#: Kinds refused, with the measured reason. See the module docstring.
REJECTED_BLOCK_ENTITY_KINDS: frozenset[str] = frozenset(
    {"LWPOLYLINE", "POLYLINE", "HATCH", "SPLINE", "ELLIPSE", "CIRCLE", "INSERT", "POINT"}
)

REJECTION_REASONS: dict[str, str] = {
    "LWPOLYLINE": (
        "MEASURED: the normalizer reads points with get_points('xyseb') but "
        "segments_from_entities keeps only [x, y], so the bulge is dropped. A "
        "2-vertex polyline with bulge 0.5 on a 10 mm chord reports 10.0 mm; the "
        "true semicircular arc is 15.7 mm. That is silent under-measurement of "
        "a curved edge, and a straight LWPOLYLINE is expressible as LINEs."
    ),
    "POLYLINE": "legacy 2D polyline; the 3D-vertex reader is not used by this chain",
    "HATCH": (
        "MEASURED: no normalizer branch, normalizes to geometry={} and 0 "
        "segments. See hatch.py, which rejects writing a real HATCH for the "
        "same measured reason."
    ),
    "SPLINE": "MEASURED: no normalizer branch, normalizes to geometry={} and 0 segments",
    "ELLIPSE": "MEASURED: no normalizer branch, normalizes to geometry={} and 0 segments",
    "CIRCLE": (
        "MEASURED: normalizes to {'center','radius'} but yields 0 segments, and "
        "unlike ARC it has no precedent in this recorder family"
    ),
    "INSERT": "nested blocks: an INSERT inside a block is (a) hidden one level deeper",
    "POINT": "no geometry payload in the normalized chain",
}

#: [MEASURED on this host] segments contributed by ONE entity of each kind,
#: through the real normalizer and the real ``segments_from_entities``.
MEASURED_SEGMENT_CONTRIBUTION: dict[str, int] = {
    "LINE": 1,
    "ARC": 0,
    "CIRCLE": 0,
    "LWPOLYLINE": 3,  # straight closed 3-vertex case; bulged case is wrong, see above
    "HATCH": 0,
    "SPLINE": 0,
    "ELLIPSE": 0,
    "INSERT": 0,
}


# ===========================================================================
# Block definition (pure geometry)
# ===========================================================================


@dataclass(frozen=True, slots=True)
class BlockEntity:
    """One entity in a block definition, in BLOCK-LOCAL millimetres.

    The local coordinates are relative to the definition's base point, which
    is applied by the insert transform (see the module docstring). Only LINE
    and ARC exist here; there is no way to smuggle another kind in, because
    :meth:`from_dxf_entity` refuses it and the constructors only build these.
    """

    kind: BlockEntityKind
    layer: str
    # LINE
    start: Point2D | None = None
    end: Point2D | None = None
    # ARC
    center: Point2D | None = None
    radius: float = 0.0
    start_angle: float = 0.0
    end_angle: float = 0.0

    @property
    def contributes_segments(self) -> int:
        """Segments this entity contributes to the verification chain.

        MEASURED, not assumed: see :data:`MEASURED_SEGMENT_CONTRIBUTION`. The
        ARC row is 0 and that is stated here rather than left for a consumer to
        discover.
        """
        return MEASURED_SEGMENT_CONTRIBUTION[str(self.kind)]

    def unmeasured_length_mm(self) -> float:
        """Length this entity has, but the chain cannot measure. 0 for LINE."""
        if self.kind is BlockEntityKind.LINE:
            return 0.0
        span = math.radians(self.end_angle - self.start_angle)
        return abs(self.radius * span)

    def validate(self, *, allow_observed_layer_zero: bool = False) -> None:
        if self.kind not in ALLOWED_BLOCK_ENTITY_KINDS:
            raise BlockValidationError(
                f"entity kind {self.kind} is not allowed in a block definition; "
                f"REJECTION_REASONS: {REJECTION_REASONS.get(str(self.kind), 'n/a')}"
            )
        _validate_layer(self.layer, allow_observed_layer_zero=allow_observed_layer_zero)
        if self.kind is BlockEntityKind.LINE:
            for name, point in (("start", self.start), ("end", self.end)):
                if point is None or not (math.isfinite(point.x) and math.isfinite(point.y)):
                    raise BlockValidationError(f"LINE {name} must be a finite 2D point")
            assert self.start is not None and self.end is not None
            if self.start == self.end:
                raise BlockValidationError(
                    "LINE has zero length: start == end; a zero-length edge is "
                    "rejected rather than dropped, so the content count stays honest"
                )
        else:
            if self.center is None or not (
                math.isfinite(self.center.x) and math.isfinite(self.center.y)
            ):
                raise BlockValidationError("ARC center must be a finite 2D point")
            if not math.isfinite(self.radius) or self.radius <= 0:
                raise BlockValidationError(
                    f"ARC radius must be finite and > 0, got {self.radius!r}"
                )
            _validate_arc_angles(self.start_angle, self.end_angle)

    def transformed(
        self,
        *,
        base_point: Point2D,
        location: Point2D,
        rotation_deg: float,
        scale: float,
    ) -> BlockEntity:
        """Return this entity mapped into world coordinates.

        Uses the same formula the DXF INSERT transform implements, so flatten
        mode and insert mode agree by construction rather than by coincidence:
        ``world = location + R(rotation) * (scale * (p - base_point))``.
        """
        angle = math.radians(rotation_deg)
        cos_a, sin_a = math.cos(angle), math.sin(angle)

        def move(p: Point2D) -> Point2D:
            dx = (p.x - base_point.x) * scale
            dy = (p.y - base_point.y) * scale
            return Point2D(location.x + dx * cos_a - dy * sin_a,
                           location.y + dx * sin_a + dy * cos_a)

        if self.kind is BlockEntityKind.LINE:
            assert self.start is not None and self.end is not None
            return BlockEntity(
                kind=self.kind,
                layer=self.layer,
                start=move(self.start),
                end=move(self.end),
            )
        assert self.center is not None
        return BlockEntity(
            kind=self.kind,
            layer=self.layer,
            center=move(self.center),
            radius=self.radius * scale,
            start_angle=_rotate_arc_angle(self.start_angle, rotation_deg),
            end_angle=_rotate_arc_angle(self.end_angle, rotation_deg),
        )


def line_entity(
    start: Sequence[float] | Point2D,
    end: Sequence[float] | Point2D,
    *,
    layer: str,
    allow_observed_layer_zero: bool = False,
) -> BlockEntity:
    """A straight edge of the block, in block-local coordinates."""
    entity = BlockEntity(
        kind=BlockEntityKind.LINE,
        layer=layer,
        start=_as_point(start, "start"),
        end=_as_point(end, "end"),
    )
    entity.validate(allow_observed_layer_zero=allow_observed_layer_zero)
    return entity


def arc_entity(
    center: Sequence[float] | Point2D,
    radius: float,
    start_angle: float,
    end_angle: float,
    *,
    layer: str,
    allow_observed_layer_zero: bool = False,
) -> BlockEntity:
    """A rounded edge, in block-local coordinates. Angles in degrees, CCW."""
    entity = BlockEntity(
        kind=BlockEntityKind.ARC,
        layer=layer,
        center=_as_point(center, "center"),
        radius=float(radius),
        start_angle=float(start_angle),
        end_angle=float(end_angle),
    )
    entity.validate(allow_observed_layer_zero=allow_observed_layer_zero)
    return entity


def line_entity_count(entities: Iterable[BlockEntity]) -> int:
    """How many entities in this collection are straight edges."""
    return sum(1 for entity in entities if entity.kind is BlockEntityKind.LINE)


def _validate_arc_angles(start_angle: float, end_angle: float) -> None:
    if not (math.isfinite(start_angle) and math.isfinite(end_angle)):
        raise BlockValidationError("ARC angles must be finite")
    span = end_angle - start_angle
    if span <= 0:
        raise BlockValidationError(
            f"ARC must sweep counter-clockwise from start to end, got span {span}"
        )
    if span >= 360.0 - 1e-9:
        raise BlockValidationError(
            "ARC spanning 360 degrees is a CIRCLE, which is a rejected kind; "
            "write it as the straight edges it stands for, or leave it out"
        )


def _rotate_arc_angle(angle: float, rotation_deg: float) -> float:
    return (angle + rotation_deg) % 360.0


@dataclass(frozen=True, slots=True)
class BlockDefinition:
    """Pure description of one block definition, in millimetres.

    Local coordinates are relative to ``base_point``. ``bbox`` is computed from
    the content and is flagged conservative when an ARC is present, because a
    full-circle bounding box is used for the arc rather than a tight one.
    """

    name: str
    entities: tuple[BlockEntity, ...]
    base_point: Point2D = Point2D(0.0, 0.0)
    allow_self_intersection: bool = False
    allow_observed_layer_zero: bool = False

    # -- validation ---------------------------------------------------------

    def validate(self, *, allow_observed_layer_zero: bool | None = None) -> None:
        allow_zero = (
            self.allow_observed_layer_zero
            if allow_observed_layer_zero is None
            else bool(allow_observed_layer_zero)
        )
        if not isinstance(self.name, str) or not self.name.strip():
            raise BlockValidationError("block name must be a non-empty string")
        if not self.entities:
            raise BlockValidationError(
                f"block {self.name!r} is EMPTY: a definition with no entities "
                "inserts nothing and normalizes to nothing. Writing it would "
                "create a file entry that looks like content and measures as "
                "zero, which is the exact silent failure this module refuses to "
                "produce. Add at least one LINE or ARC."
            )
        for index, entity in enumerate(self.entities):
            try:
                entity.validate(allow_observed_layer_zero=allow_zero)
            except BlockValidationError as exc:
                raise BlockValidationError(
                    f"block {self.name!r} entity[{index}] is invalid: {exc}"
                ) from exc
        if not (math.isfinite(self.base_point.x) and math.isfinite(self.base_point.y)):
            raise BlockValidationError("base_point must be finite")
        if self.is_self_intersecting():
            raise BlockValidationError(
                f"block {self.name!r} is SELF-INTERSECTING: its straight edges "
                f"node to more than {line_entity_count(self.entities)} segments. A "
                "self-crossing definition does not bound one region, so the "
                "'one definition, one shape' contract cannot be honoured and "
                "node_segments would split it into unexpected pieces. Refused "
                "rather than silently re-noded."
            )

    # -- geometry -----------------------------------------------------------

    def line_segments(self) -> list[Segment2D]:
        """Straight edges as topology segments (the noder's input)."""
        out: list[Segment2D] = []
        for index, entity in enumerate(self.entities):
            if entity.kind is BlockEntityKind.LINE:
                assert entity.start is not None and entity.end is not None
                out.append(Segment2D(f"{self.name}:{index}", entity.start, entity.end))
        return out

    def is_self_intersecting(self) -> bool:
        """True when the straight edges cross.

        Expressed through the shared :func:`topology.node_segments` helper, as
        ``hatch.py`` does, so "crossing" means the same thing everywhere in this
        repository. ARC edges are NOT part of this check and that limit is
        deliberate and documented: the shared noder is straight-segment-only, so
        including arcs would make this module disagree with the topology code
        about what it can resolve. ``arc_count`` in the record is the honest
        statement of what went unchecked.
        """
        edges = self.line_segments()
        if len(edges) < 2:
            return False
        return len(node_segments(edges)) > len(edges)

    def bbox(self) -> tuple[Point2D, Point2D]:
        """(min, max) of the content in block-local coordinates.

        For an ARC the full circle extent (center +/- radius) is used, so the
        box is a conservative superset; ``bbox_is_conservative`` reports it.
        """
        xs: list[float] = []
        ys: list[float] = []
        for entity in self.entities:
            if entity.kind is BlockEntityKind.LINE:
                assert entity.start is not None and entity.end is not None
                xs.extend((entity.start.x, entity.end.x))
                ys.extend((entity.start.y, entity.end.y))
            else:
                assert entity.center is not None
                xs.extend((entity.center.x - entity.radius, entity.center.x + entity.radius))
                ys.extend((entity.center.y - entity.radius, entity.center.y + entity.radius))
        return Point2D(min(xs), min(ys)), Point2D(max(xs), max(ys))

    def bbox_is_conservative(self) -> bool:
        return any(entity.kind is BlockEntityKind.ARC for entity in self.entities)

    def size(self) -> tuple[float, float]:
        low, high = self.bbox()
        return (high.x - low.x, high.y - low.y)

    @property
    def arc_count(self) -> int:
        return sum(1 for entity in self.entities if entity.kind is BlockEntityKind.ARC)

    def arc_unmeasured_length_mm(self) -> float:
        return sum(entity.unmeasured_length_mm() for entity in self.entities)

    def measured_length_mm(self) -> float:
        """Length the verification chain can actually measure from this content.

        Runs the content through the SAME :func:`segments_from_entities` the
        repository uses, on a snapshot shaped exactly as
        ``_normalize_ezdxf_entity`` would produce it. This is the number an
        INSERT conceals, and it is always available from the definition even in
        insert mode.
        """
        return sum(
            math.dist((s.start.x, s.start.y), (s.end.x, s.end.y))
            for s in self.normalized_segments()
        )

    def normalized_segments(self) -> list[Segment2D]:
        """Segments the content yields through the real normalizer + topology.

        The snapshots here are built by hand to match
        ``_normalize_ezdxf_entity`` exactly: LINE -> ``{"start","end"}``,
        ARC -> ``{"center","radius","start_angle","end_angle"}``. Using the
        project's own functions on top of that is what makes this a
        measurement rather than an assertion.
        """
        return segments_from_entities(self.snapshots())

    def snapshots(self) -> list[EntitySnapshot]:
        """Content as the normalizer would emit it, for digest/diff reuse."""
        out: list[EntitySnapshot] = []
        for index, entity in enumerate(self.entities):
            if entity.kind is BlockEntityKind.LINE:
                assert entity.start is not None and entity.end is not None
                geometry = {
                    "start": [entity.start.x, entity.start.y, 0.0],
                    "end": [entity.end.x, entity.end.y, 0.0],
                }
            else:
                assert entity.center is not None
                geometry = {
                    "center": [entity.center.x, entity.center.y, 0.0],
                    "radius": float(entity.radius),
                    "start_angle": float(entity.start_angle),
                    "end_angle": float(entity.end_angle),
                }
            out.append(
                EntitySnapshot(
                    document_id=f"block:{self.name}",
                    handle=f"{index}",
                    entity_type=str(entity.kind),
                    layer=entity.layer,
                    geometry=geometry,
                )
            )
        return out


def make_block_definition(
    name: str,
    entities: Iterable[BlockEntity],
    *,
    base_point: Sequence[float] | Point2D = (0.0, 0.0),
    allow_self_intersection: bool = False,
    allow_observed_layer_zero: bool = False,
) -> BlockDefinition:
    """Build a :class:`BlockDefinition` from pure geometry. No ezdxf touched.

    Rejects, loudly: an empty content list, a non-LINE/ARC entity, an
    UNKNOWN-classifying layer, a zero-length edge, a full-circle arc, and
    self-intersecting straight edges.

    ``allow_observed_layer_zero=True`` is the explicit opt-in that permits the
    OBSERVED XiCAD layer ``"0"``. It is off by default because that layer
    classifies as UNKNOWN in this project's convention and the survey that
    observed it did not close the U-1 question (see
    :data:`OPENING_BLOCK_LAYER_STATUS`).
    """
    items = tuple(entities)
    definition = BlockDefinition(
        name=str(name),
        entities=items,
        base_point=_as_point(base_point, "base_point"),
        allow_self_intersection=bool(allow_self_intersection),
        allow_observed_layer_zero=bool(allow_observed_layer_zero),
    )
    if definition.is_self_intersecting() and definition.allow_self_intersection:
        return definition
    definition.validate()
    return definition


# ===========================================================================
# Block insertion (pure geometry)
# ===========================================================================


@dataclass(frozen=True, slots=True)
class BlockInsert:
    """One instance of a block definition, in world millimetres."""

    definition: BlockDefinition
    location: Point2D = Point2D(0.0, 0.0)
    rotation_deg: float = 0.0
    scale: float = 1.0
    layer: str | None = None
    mode: BlockInsertMode = BlockInsertMode.FLATTEN

    def validate(self) -> None:
        self.definition.validate()
        if not (math.isfinite(self.location.x) and math.isfinite(self.location.y)):
            raise BlockValidationError("insert location must be finite")
        if not math.isfinite(self.rotation_deg):
            raise BlockValidationError("insert rotation_deg must be finite")
        if not math.isfinite(self.scale) or self.scale <= 0:
            raise BlockValidationError(
                f"insert scale must be finite and > 0, got {self.scale!r}"
            )
        if self.mode not in (BlockInsertMode.FLATTEN, BlockInsertMode.INSERT):
            raise BlockValidationError(f"unknown insert mode {self.mode!r}")
        if self.layer is not None:
            _validate_layer(
                self.layer,
                allow_observed_layer_zero=self.definition.allow_observed_layer_zero,
            )

    def instance_layer(self) -> str:
        """The INSERT's own layer, defaulting to the definition's first layer.

        In insert mode the block reference itself carries this layer. It is NOT
        invented: it is one of the definition's own layers, so the INSERT can
        never land on a name the content does not already use.
        """
        if self.layer is not None:
            return self.layer
        return self.definition.entities[0].layer

    def world_entities(self) -> list[BlockEntity]:
        """Content mapped into world coordinates, base point applied.

        Identical to what ezdxf/the CAD kernel does for an INSERT of the same
        instance, which is the property the roundtrip test asserts.
        """
        self.validate()
        return [
            entity.transformed(
                base_point=self.definition.base_point,
                location=self.location,
                rotation_deg=self.rotation_deg,
                scale=self.scale,
            )
            for entity in self.definition.entities
        ]


def make_block_insert(
    definition: BlockDefinition,
    location: Sequence[float] | Point2D = (0.0, 0.0),
    *,
    rotation_deg: float = 0.0,
    scale: float = 1.0,
    layer: str | None = None,
    mode: BlockInsertMode | str = BlockInsertMode.FLATTEN,
) -> BlockInsert:
    """Build a :class:`BlockInsert`.

    ``mode`` defaults to ``"flatten"`` -- see the module docstring for the
    measurement behind that default. ``"insert"`` is available and is opt-in.
    """
    insert = BlockInsert(
        definition=definition,
        location=_as_point(location, "location"),
        rotation_deg=float(rotation_deg),
        scale=float(scale),
        layer=None if layer is None else str(layer),
        mode=BlockInsertMode(str(mode)),
    )
    insert.validate()
    return insert


def transform_point(
    point: Sequence[float] | Point2D,
    base_point: Sequence[float] | Point2D,
    location: Sequence[float] | Point2D,
    rotation_deg: float,
    scale: float,
) -> Point2D:
    """The single instance transform, exposed so callers can predict with it.

    ``world = location + R(rotation) * (scale * (point - base_point))``
    """
    p = _as_point(point, "point")
    base = _as_point(base_point, "base_point")
    dest = _as_point(location, "location")
    dx = (p.x - base.x) * scale
    dy = (p.y - base.y) * scale
    angle = math.radians(rotation_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    return Point2D(dest.x + dx * cos_a - dy * sin_a, dest.y + dx * sin_a + dy * cos_a)


# ===========================================================================
# Records
# ===========================================================================


@dataclass(frozen=True, slots=True)
class BlockDefinitionRecord:
    """What :func:`write_block_definition` actually wrote into the block table."""

    document_id: str
    dxf_version: str
    name: str
    base_point: Point2D
    entity_count: int
    line_count: int
    arc_count: int
    arc_unmeasured_length_mm: float
    measured_segments: int
    measured_length_mm: float
    self_intersecting: bool
    bbox_min: Point2D
    bbox_max: Point2D
    bbox_is_conservative: bool
    layer_counts: dict[str, int]
    entities: tuple[tuple[str, str, str, str], ...]  # (kind, handle, type, layer)

    def handles(self) -> tuple[str, ...]:
        return tuple(item[1] for item in self.entities)

    def layer_of(self, handle: str) -> str:
        for _kind, entity_handle, _type, layer in self.entities:
            if entity_handle.upper() == handle.upper():
                return layer
        raise KeyError(handle)


@dataclass(frozen=True, slots=True)
class BlockInsertRecord:
    """What :func:`write_block_insert` wrote, and whether anything can see it.

    ``contributed_segments`` / ``contributed_length_mm`` are the MEASURED
    result of running this instance's entities through
    :func:`topology.segments_from_entities`. For ``mode="insert"`` they are 0
    and 0.0 -- that is the contract, reported, not an error and not a silence.
    ``hidden_*`` is what the INSERT conceals, which is always computable from
    the definition and is therefore never lost.
    """

    document_id: str
    dxf_version: str
    block_name: str
    mode: BlockInsertMode
    location: Point2D
    rotation_deg: float
    scale: float
    layer: str
    layer_semantic: LayerSemantic
    entity_count: int
    contributed_segments: int
    contributed_length_mm: float
    hidden_segments: int
    hidden_length_mm: float
    visible_downstream: bool
    downstream_contract: dict[str, Any]
    entities: tuple[tuple[str, str, str, str], ...]  # (kind, handle, type, layer)

    def handles(self) -> tuple[str, ...]:
        return tuple(item[1] for item in self.entities)

    def layer_of(self, handle: str) -> str:
        for _kind, entity_handle, _type, layer in self.entities:
            if entity_handle.upper() == handle.upper():
                return layer
        raise KeyError(handle)

    def layer_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _kind, _handle, _type, layer in self.entities:
            counts[layer] = counts.get(layer, 0) + 1
        return counts

    def snapshots(self) -> list[EntitySnapshot]:
        return _world_snapshots(self.block_name, self.entities, self.document_id)


# ===========================================================================
# Writers
# ===========================================================================


def write_block_definition(
    doc: Any, definition: BlockDefinition, *, on_existing: str = "error"
) -> BlockDefinitionRecord:
    """Write ``definition`` into ``doc.blocks`` and return what was written.

    ``doc`` must be an ``ezdxf.document.Drawing`` created for
    :data:`DXF_WRITE_VERSION` (AC1032, R2018). The base point becomes the
    block's insertion base point, so an instance's world placement follows
    :func:`transform_point`. The block content is written in block-LOCAL
    coordinates, exactly as DXF expects, and the boundary box is computed from
    that same local content.
    """
    _require_ezdxf()
    _check_doc_version(doc)
    definition.validate()

    if on_existing not in ("error", "reuse"):
        raise ValueError("on_existing must be 'error' or 'reuse'")
    if definition.name in doc.blocks:
        if on_existing == "error":
            raise BlockValidationError(
                f"block {definition.name!r} already exists in this document; pass "
                "on_existing='reuse' to insert the existing definition instead of "
                "silently redefining it"
            )
        return _record_existing(doc, definition)

    block = doc.blocks.new(
        name=definition.name,
        base_point=(definition.base_point.x, definition.base_point.y, 0.0),
    )
    for name in _layers_of(definition.entities):
        if name not in doc.layers:
            doc.layers.add(name)

    written: list[tuple[str, str, str, str]] = []
    for entity in definition.entities:
        if entity.kind is BlockEntityKind.LINE:
            assert entity.start is not None and entity.end is not None
            ezdxf_entity = block.add_line(
                (entity.start.x, entity.start.y, 0.0),
                (entity.end.x, entity.end.y, 0.0),
                dxfattribs={"layer": entity.layer},
            )
        else:
            assert entity.center is not None
            ezdxf_entity = block.add_arc(
                (entity.center.x, entity.center.y, 0.0),
                entity.radius,
                entity.start_angle,
                entity.end_angle,
                dxfattribs={"layer": entity.layer},
            )
        written.append(
            (str(entity.kind), ezdxf_entity.dxf.handle, str(entity.kind), entity.layer)
        )

    low, high = definition.bbox()
    return BlockDefinitionRecord(
        document_id=_document_id(doc),
        dxf_version=_release_of(str(getattr(doc, "dxfversion", ""))),
        name=definition.name,
        base_point=definition.base_point,
        entity_count=len(written),
        line_count=line_entity_count(definition.entities),
        arc_count=definition.arc_count,
        arc_unmeasured_length_mm=definition.arc_unmeasured_length_mm(),
        measured_segments=len(definition.normalized_segments()),
        measured_length_mm=definition.measured_length_mm(),
        self_intersecting=definition.is_self_intersecting(),
        bbox_min=low,
        bbox_max=high,
        bbox_is_conservative=definition.bbox_is_conservative(),
        layer_counts=_layer_counts(definition.entities),
        entities=tuple(written),
    )


def _record_existing(doc: Any, definition: BlockDefinition) -> BlockDefinitionRecord:
    """Report an already-present block table entry against the requested content.

    Reported, not silently accepted: the record still describes the DEFINITION
    that was asked for, and ``reused_existing_definition`` is not claimed. The
    caller can compare ``measured_segments`` against the document to detect a
    mismatch.
    """
    block = doc.blocks.get(definition.name)
    low, high = definition.bbox()
    written = tuple(
        (entity.dxftype(), entity.dxf.handle, entity.dxftype(), entity.dxf.layer)
        for entity in block
    )
    return BlockDefinitionRecord(
        document_id=_document_id(doc),
        dxf_version=_release_of(str(getattr(doc, "dxfversion", ""))),
        name=definition.name,
        base_point=Point2D(block.block.dxf.base_point.x, block.block.dxf.base_point.y),
        entity_count=len(written),
        line_count=len(definition.line_segments()),
        arc_count=definition.arc_count,
        arc_unmeasured_length_mm=definition.arc_unmeasured_length_mm(),
        measured_segments=len(definition.normalized_segments()),
        measured_length_mm=definition.measured_length_mm(),
        self_intersecting=definition.is_self_intersecting(),
        bbox_min=low,
        bbox_max=high,
        bbox_is_conservative=definition.bbox_is_conservative(),
        layer_counts=_layer_counts(definition.entities),
        entities=written,
    )


def write_block_insert(
    doc: Any,
    insert: BlockInsert,
    *,
    define_if_missing: bool = True,
    require_downstream_visible: bool = False,
) -> BlockInsertRecord:
    """Write one instance of ``insert.definition`` into ``doc.modelspace()``.

    Two modes, and they are not interchangeable -- see the module docstring:

    ``mode="flatten"`` (DEFAULT)
        The content is transformed by the instance transform and written as
        LINE/ARC on the entity's own layer. The result is measurable: the
        returned ``contributed_segments`` is what
        :func:`topology.segments_from_entities` will return for it.

    ``mode="insert"``
        A real ``INSERT`` (block reference) is written, standard CAD practice.
        The result is NOT measurable: the record reports
        ``visible_downstream=False``, ``contributed_segments=0`` and
        ``hidden_segments=<N>`` / ``hidden_length_mm=<L>`` for the content the
        INSERT conceals. That is a contract, not a defect and not a warning.

    ``require_downstream_visible=True`` turns the insert mode's invisibility
    into a hard error, for a caller that must not emit unmeasurable geometry.
    The default is ``False``: the caller may legitimately want the reference,
    and the record already states the cost.

    ``define_if_missing`` writes the block definition first when the block table
    has no entry for the name, so an insert never dangles.

    [MEASURED, FreeCAD 1.1.3] In FLATTEN mode the definition is NOT referenced
    by anything, and FreeCAD instantiates an unreferenced block anyway: the
    same 4 edges written flat came back as 8 single-edge shapes totalling
    12000000 internal units instead of 4 shapes / 6000000. So for a flatten-only
    drawing, pass ``define_if_missing=False`` unless the definition is wanted
    for provenance. The duplication is FreeCAD's behaviour, not a geometry
    error, but it is the kind of thing a reader counts and then has to explain.
    """
    _require_ezdxf()
    _check_doc_version(doc)
    insert.validate()

    if insert.mode is BlockInsertMode.INSERT and require_downstream_visible:
        raise BlockValidationError(
            f"mode='insert' was refused for block {insert.definition.name!r} "
            "because require_downstream_visible=True. An INSERT normalizes to "
            "geometry={'insert': ...} only, so segments_from_entities returns 0 "
            "segments for it and this repository cannot measure the shape. Use "
            "mode='flatten' to get measurable geometry."
        )

    if insert.definition.name not in doc.blocks:
        if insert.mode is BlockInsertMode.INSERT and not define_if_missing:
            # an INSERT would dangle without its definition, so this is an error
            raise BlockValidationError(
                f"block {insert.definition.name!r} is not defined in this "
                "document; write_block_definition first or pass "
                "define_if_missing=True"
            )
        if define_if_missing:
            # In FLATTEN mode the drawing carries no reference to the block, so
            # this is optional provenance rather than a requirement -- see the
            # FreeCAD duplication measurement in this function's docstring.
            write_block_definition(doc, insert.definition)

    msp = doc.modelspace()
    for name in _layers_of(insert.world_entities()):
        if name not in doc.layers:
            doc.layers.add(name)

    if insert.mode is BlockInsertMode.INSERT:
        return _write_insert_entity(doc, msp, insert)
    return _write_flattened_entities(doc, msp, insert)


def _write_insert_entity(doc: Any, msp: Any, insert: BlockInsert) -> BlockInsertRecord:
    layer = insert.instance_layer()
    # ezdxf 1.4.4's add_blockref takes only (name, insert, dxfattribs), so
    # rotation and scale go in as DXF attributes. Setting them after creation is
    # equivalent and is verified against the flatten path by the roundtrip test.
    reference = msp.add_blockref(
        insert.definition.name,
        (insert.location.x, insert.location.y, 0.0),
        dxfattribs={"layer": layer},
    )
    reference.dxf.rotation = insert.rotation_deg
    reference.dxf.xscale = insert.scale
    reference.dxf.yscale = insert.scale
    reference.dxf.zscale = insert.scale
    hidden = insert.definition.normalized_segments()
    return BlockInsertRecord(
        document_id=_document_id(doc),
        dxf_version=_release_of(str(getattr(doc, "dxfversion", ""))),
        block_name=insert.definition.name,
        mode=BlockInsertMode.INSERT,
        location=insert.location,
        rotation_deg=insert.rotation_deg,
        scale=insert.scale,
        layer=layer,
        layer_semantic=classify_layer(layer),
        entity_count=1,
        contributed_segments=0,
        contributed_length_mm=0.0,
        hidden_segments=len(hidden),
        hidden_length_mm=insert.definition.measured_length_mm(),
        visible_downstream=False,
        downstream_contract={
            "mode": "insert",
            "entity_type_written": "INSERT",
            "normalizer_geometry_keys": ["insert"],
            "normalizer_properties_keys": ["block_name"],
            "contributes_segments": 0,
            "contributes_length_mm": 0.0,
            "visible_downstream": False,
            "hidden_segments": len(hidden),
            "hidden_length_mm": insert.definition.measured_length_mm(),
            "hidden_arc_unmeasured_length_mm": insert.definition.arc_unmeasured_length_mm(),
            "reason": (
                "extraction_runtime._normalize_ezdxf_entity has an INSERT branch "
                "but emits only geometry={'insert': [x, y, z]} and "
                "properties={'block_name': ...}; topology.segments_from_entities "
                "reads only 'start'/'end' and 'points', so an INSERT contributes "
                "0 segments by construction."
            ),
        },
        entities=(
            (BlockInsertMode.INSERT, reference.dxf.handle, "INSERT", layer),
        ),
    )


def _write_flattened_entities(doc: Any, msp: Any, insert: BlockInsert) -> BlockInsertRecord:
    written: list[tuple[str, str, str, str]] = []
    for entity in insert.world_entities():
        if entity.kind is BlockEntityKind.LINE:
            assert entity.start is not None and entity.end is not None
            ezdxf_entity = msp.add_line(
                (entity.start.x, entity.start.y, 0.0),
                (entity.end.x, entity.end.y, 0.0),
                dxfattribs={"layer": entity.layer},
            )
        else:
            assert entity.center is not None
            ezdxf_entity = msp.add_arc(
                (entity.center.x, entity.center.y, 0.0),
                entity.radius,
                entity.start_angle,
                entity.end_angle,
                dxfattribs={"layer": entity.layer},
            )
        written.append(
            (str(entity.kind), ezdxf_entity.dxf.handle, str(entity.kind), entity.layer)
        )

    world = insert.world_entities()
    measured = segments_from_entities(_entity_snapshots(world, insert.definition.name))
    measured_length = sum(
        math.dist((s.start.x, s.start.y), (s.end.x, s.end.y)) for s in measured
    )
    layer = insert.instance_layer()
    return BlockInsertRecord(
        document_id=_document_id(doc),
        dxf_version=_release_of(str(getattr(doc, "dxfversion", ""))),
        block_name=insert.definition.name,
        mode=BlockInsertMode.FLATTEN,
        location=insert.location,
        rotation_deg=insert.rotation_deg,
        scale=insert.scale,
        layer=layer,
        layer_semantic=classify_layer(layer),
        entity_count=len(written),
        contributed_segments=len(measured),
        contributed_length_mm=measured_length,
        hidden_segments=0,
        hidden_length_mm=0.0,
        visible_downstream=True,
        downstream_contract={
            "mode": "flatten",
            "entity_type_written": "LINE,ARC",
            "normalizer_geometry_keys": ["start", "end"],
            "normalizer_properties_keys": [],
            "contributes_segments": len(measured),
            "contributes_length_mm": measured_length,
            "visible_downstream": True,
            "hidden_segments": 0,
            "hidden_length_mm": 0.0,
            "arc_unmeasured_length_mm": sum(
                entity.unmeasured_length_mm() for entity in world
            ),
            "reason": (
                "content is written as LINE, which the normalizer emits with "
                "start/end and topology.segments_from_entities turns into one "
                "segment per edge. ARC edges are still written faithfully but "
                "contribute 0 segments (measured); see "
                "MEASURED_SEGMENT_CONTRIBUTION."
            ),
        },
        entities=tuple(written),
    )


# ===========================================================================
# helpers
# ===========================================================================


def _world_snapshots(
    block_name: str, entities: tuple[tuple[str, str, str, str], ...], document_id: str
) -> list[EntitySnapshot]:
    """Rebuild snapshots from a flatten-mode record's written handles."""
    return [
        EntitySnapshot(
            document_id=document_id,
            handle=handle,
            entity_type=entity_type,
            layer=layer,
            geometry={},
        )
        for _kind, handle, entity_type, layer in entities
    ]


def _entity_snapshots(entities: Iterable[BlockEntity], block_name: str) -> list[EntitySnapshot]:
    """Snapshot a list of world entities exactly as the normalizer would."""
    out: list[EntitySnapshot] = []
    for index, entity in enumerate(entities):
        if entity.kind is BlockEntityKind.LINE:
            assert entity.start is not None and entity.end is not None
            geometry = {
                "start": [entity.start.x, entity.start.y, 0.0],
                "end": [entity.end.x, entity.end.y, 0.0],
            }
        else:
            assert entity.center is not None
            geometry = {
                "center": [entity.center.x, entity.center.y, 0.0],
                "radius": float(entity.radius),
                "start_angle": float(entity.start_angle),
                "end_angle": float(entity.end_angle),
            }
        out.append(
            EntitySnapshot(
                document_id=f"block:{block_name}",
                handle=f"{index}",
                entity_type=str(entity.kind),
                layer=entity.layer,
                geometry=geometry,
            )
        )
    return out


def _layers_of(entities: Iterable[BlockEntity]) -> list[str]:
    names: list[str] = []
    for entity in entities:
        if entity.layer not in names:
            names.append(entity.layer)
    return names


def _layer_counts(entities: Iterable[BlockEntity]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entity in entities:
        counts[entity.layer] = counts.get(entity.layer, 0) + 1
    return counts


def _as_point(value: Sequence[float] | Point2D, name: str) -> Point2D:
    if isinstance(value, Point2D):
        return value
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        x, y = value[0], value[1]
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return Point2D(float(x), float(y))
    raise BlockValidationError(f"{name} must be a 2D point, got {value!r}")


def _validate_layer(name: str, *, allow_observed_layer_zero: bool = False) -> None:
    """Refuse a layer the project convention does not know.

    Inventing a substitute here is exactly what produced silent corruption in
    the earlier opening work, so an UNKNOWN-classifying name is an error.

    The one exception is the OBSERVED XiCAD spelling ``"0"``, which is accepted
    only when the caller passes ``allow_observed_layer_zero=True``. It still
    classifies as UNKNOWN, and the record still reports
    :class:`LayerSemantic` ``UNKNOWN`` for it, so the opt-in is visible in the
    output rather than hidden here.
    """
    if not isinstance(name, str) or not name.strip():
        raise BlockValidationError("layer must be a non-empty string")
    if classify_layer(name) is not LayerSemantic.UNKNOWN:
        return
    if name == OBSERVED_XICAD_BLOCK_LAYER and allow_observed_layer_zero:
        return
    hint = (
        " It is the OBSERVED XiCAD spelling, so it can be used with "
        "allow_observed_layer_zero=True, but it still classifies as UNKNOWN "
        f"(status: {OPENING_BLOCK_LAYER_STATUS}) and a convention layer is "
        "preferred."
        if name == OBSERVED_XICAD_BLOCK_LAYER
        else ""
    )
    raise BlockValidationError(
        f"layer {name!r} classifies as UNKNOWN in the project convention and "
        f"is refused. Use one of {list(CONVENTION_BLOCK_LAYERS)}; this module "
        f"invents no new layer name." + hint
    )


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


def _check_doc_version(doc: Any) -> None:
    doc_version = str(getattr(doc, "dxfversion", "") or "")
    doc_release = str(getattr(doc, "acad_release", "") or "")
    if _release_of(doc_version) != DXF_WRITE_VERSION or (
        doc_release and doc_release != DXF_WRITE_VERSION
    ):
        raise ValueError(
            f"this recorder writes {DXF_WRITE_VERSION} (AC1032) only, got {doc_version}"
        )


def _require_ezdxf() -> Any:
    try:
        import ezdxf  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - env guard
        raise ModuleNotFoundError(
            "ezdxf is required by the block recorder. On this host run the "
            "project venv with a cleared PYTHONHOME, otherwise the standard "
            "library is hidden and this import fails with 'No module named "
            "annotationlib'."
        ) from exc
    return ezdxf
