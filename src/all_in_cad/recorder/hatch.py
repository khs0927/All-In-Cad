"""Hatch recorder: records a filled region as a **closed boundary polyline plus
a pattern-metadata contract**, deliberately NOT as a ``HATCH`` entity.

This is the fourth module in the recorder family (``wall``/``door``/``window``/
``opening``); it follows the same two-function shape:

    make_xxx(...)      -> XxxGeometry   (pure geometry, no ezdxf)
    write_xxx(doc, g)  -> XxxRecord     (entity recording, ezdxf only)

=====================================================================
ENVIRONMENT NOTE (observed on this host, same as wall.py)
=====================================================================
ezdxf 1.4.4 lives in ``C:\\Users\\khs09\\all-in-cad\\.venv``. Aside injects
``PYTHONHOME``/``PYTHONPATH`` into the process environment, which hides the
venv interpreter's standard library and makes ``import ezdxf`` fail with
``ModuleNotFoundError: No module named 'annotationlib'``. Clear both first::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\hatch_test.py

=====================================================================
THE DESIGN DECISION: WHY (b) AND NOT (a)  -- and the measured evidence
=====================================================================
The parent task posed a choice:

  (a) write a real ``HATCH`` entity and document that this repository's
      normalizer has no HATCH branch, so the hatch contributes 0 to the
      verification chain;
  (b) express the region as a boundary polyline plus a "apply pattern X here"
      metadata contract, stored outside the geometry.

**This module implements (b).** The evidence is OBSERVED, not assumed, and it
was measured on this host against the actual normalizer in
``src/all_in_cad/extraction_runtime.py`` (``_normalize_ezdxf_entity``):

    * ``_normalize_ezdxf_entity`` dispatches on ``entity.dxftype().upper()``
      and has branches for LINE, LWPOLYLINE, CIRCLE, ARC, TEXT/MTEXT, INSERT
      and DIMENSION. There is **no HATCH branch**. A HATCH therefore falls
      through to the default and normalizes to ``geometry={}`` and
      ``properties={}``.
    * Measured: a HATCH carrying a closed 4-vertex polyline path normalizes to
      ``geometry`` with ZERO keys, and ``segments_from_entities`` returns
      **0 segments** for it. The same shape written as a closed LWPOLYLINE
      normalizes to ``{"points": [...], "closed": True}`` and returns
      **4 segments**, which ``node_segments`` noding keeps at 4.

So the consequence of (a) is not a cosmetic one. A HATCH region is invisible
to every topology/verification consumer in this repository: it has no
``start``/``end`` and no ``points``, which are the only two geometry shapes
``topology.segments_from_entities`` knows how to read. Choosing (a) would
write a region that no later stage can measure, area-check, or verify, and
"the normalizer will grow a HATCH branch someday" is not a contract -- it is a
hope, and the whole point of this recorder family is that recorder output is
verifiable today.

There is a THIRD, independent piece of evidence against (a), found in
``src/all_in_cad/recorder/cli.py`` (read-only, not modified by this module)::

    FORBIDDEN_DXF_TYPES = ("INSERT", "HATCH")          # line 280

and the ``verify`` command appends a check named ``no_insert_or_hatch`` which
FAILS the drawing when any entity of those types is present. So option (a)
would not merely contribute zero to the verification chain -- it would make
``verify`` fail outright. The same constant also explains, independently of
the normalizer, why wall/door/window/opening are all LINE/ARC: the recorder
family is under an explicit standing rule not to emit HATCH or INSERT. Option
(b) honours that rule, and
``test_written_hatch_satisfies_the_standing_no_hatch_rule`` keeps it honest.

Option (b) keeps the region fully inside the chain that already works:
    boundary LWPOLYLINE (closed=True)
        -> normalizes to {"points", "closed"}
        -> segments_from_entities yields one Segment2D per edge
        -> node_segments can node it against walls/openings
        -> area is recomputable by the same shoelace this module validates
and carries the *pattern intent* (name, scale, angle) as metadata that a
renderer/consumer can act on.

**HOW (b) IS USED DOWNSTREAM -- the contract**
    1. GEOMETRY CONSUMERS (already work, no code change):
       ``topology.segments_from_entities`` reads the LWPOLYLINE and gets the
       closed ring; this is the same path a wall uses. Any area/containment
       check written against wall faces works against a hatch boundary too.
    2. PATTERN CONSUMERS (read the metadata, new code required):
       the writer stores the pattern as XDATA under the app id
       :data:`HATCH_APPID` on the boundary polyline itself, so the pattern
       travels *with* the entity and needs no side table or handle lookup.
       :func:`read_hatch_metadata` is the reader; a renderer (or a future
       normalizer HATCH branch) converts the boundary + pattern into a real
       HATCH at the moment the pattern is actually rendered.
    3. WHY XDATA AND NOT AN XRECORD -- see :data:`UNRESOLVED_XRECORD_API`.
       Measured on ezdxf 1.4.4: ``rootdict.add_xrecord(key)`` accepts no tags
       argument, the returned ``XRecord`` has neither ``set_tags`` nor an
       ``edit()`` context manager, and ``XRecord.load(Tags.from_text(...))``
       raises ``AttributeError: 'Tags' object has no attribute 'appdata'``.
       XDATA by contrast roundtrips cleanly through save/reload (covered by
       ``test_roundtrip_preserves_pattern_metadata``). Rather than ship an
       XRECORD path that cannot be written on the pinned ezdxf, the contract
       uses XDATA and records the XRECORD gap as UNRESOLVED.

What (b) gives up, stated plainly: a downstream consumer that only understands
``HATCH`` will not see a filled region from this module. That is a real cost
and it is the accepted cost of having the region be measurable inside this
repository. A caller that genuinely needs a fillable HATCH for a CAD operator
should not use this module; nothing here pretends otherwise.

=====================================================================
LAYER NAME: UNRESOLVED, AND DELIBERATELY NOT INVENTED
=====================================================================
MEASURED, on this host, at authoring time:
  * ``src/all_in_cad/semantic_layers.py``: ``LayerSemantic`` has no HATCH
    member (members are COLUMN, WALL, ELEVATOR, DOOR, WINDOW, WINDOW_BAR,
    STAIR, DIMENSION, CENTERLINE, UNKNOWN). The ``_EXACT`` table has no hatch
    key, and ``_PATTERNS`` has no hatch pattern.
  * ``configs/architectural-layers.json``: the ``exact`` table likewise has no
    hatch key.
  * Therefore ``classify_layer`` returns ``LayerSemantic.UNKNOWN`` for every
    hatch-shaped candidate name.

The earlier opening-recorder work already showed what happens when a missing
convention gets quietly substituted: a silent data corruption, because the
substitute name classifies as something real and is then treated as
authoritative. So **this module invents no layer name.**
:data:`KNOWN_HATCH_LAYERS` is the MEASURED set of project-convention hatch
layers and it is **empty**. :data:`HATCH_LAYER_STATUS` records the open item.

Consequence for the API: ``make_hatch`` takes ``layer`` as a REQUIRED keyword
with no default. There is no safe default to supply, so the module forces the
caller to make the decision explicitly rather than inheriting a guess. The
boundary is written to exactly the layer given, the layer table entry is
created if missing, and the record reports ``classify_layer(layer)`` so a
reader can see that the semantic is currently UNKNOWN rather than having the
module assert one.

=====================================================================
BOUNDARY ACCEPTANCE RULES (all [DESIGN]; nothing about hatching is observed)
=====================================================================
A boundary is accepted only if ALL of the following hold:
  * at least 3 vertices, all finite, and the ring is implicitly closed;
  * no zero-length edge (consecutive duplicate vertices) -- a repeated vertex
    makes the ring's turning ambiguous and is rejected rather than snapped;
  * no repeated non-adjacent vertex;
  * |area| >= ``min_area_mm2`` (default 1.0 mm^2) -- degenerate, collinear or
    sliver rings are rejected. Default threshold, not an observation;
  * SELF-INTERSECTION: **rejected by default.** A self-intersecting ring does
    not bound one region (the interior depends on the fill rule chosen), so
    the "one pattern over one region" contract cannot be honoured. The check
    reuses ``topology.node_segments``: a simple ring noding to exactly its
    input segment count proves no crossing, while a crossing ring yields MORE
    segments (measured: 4 segments in -> 6 out for a bow-tie). The check is
    deliberately expressed in terms of the shared noding helper rather than a
    private intersection routine so the hatch and the topology code cannot
    disagree about what "crossing" means.
    ``allow_self_intersection=True`` is an explicit opt-in escape hatch. When
    set, the area is the absolute shoelace area (which is the NONZERO-rule
    area; a bow-tie's two lobes cancel to 0 and are rejected as degenerate),
    and the record is flagged ``self_intersecting=True`` so a consumer can
    refuse it. The flag is the point: the option exists, and it is loud.

Pattern validation: name must be a non-empty string, scale must be finite and
strictly positive, angle must be finite.
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
    from ..topology import Point2D, Segment2D, node_segments
except ImportError:  # pragma: no cover - direct/flat execution fallback
    from all_in_cad.readback import EntitySnapshot
    from all_in_cad.semantic_layers import LayerSemantic, classify_layer
    from all_in_cad.topology import Point2D, Segment2D, node_segments

__all__ = [
    "DXF_MIN_READ_VERSION",
    "DXF_WRITE_VERSION",
    "HATCH_APPID",
    "HATCH_LAYER_STATUS",
    "KNOWN_HATCH_LAYERS",
    "MIN_AREA_MM2",
    "UNRESOLVED_XRECORD_API",
    "HatchGeometry",
    "HatchPattern",
    "HatchRecord",
    "HatchRole",
    "HatchValidationError",
    "make_hatch",
    "read_hatch_metadata",
    "write_hatch",
]

#: DXF version written by this recorder and the oldest version it reads.
DXF_WRITE_VERSION = "R2018"  # AC1032
DXF_MIN_READ_VERSION = "R2000"  # AC1015

class HatchRole(StrEnum):
    """Role of a written hatch entity (independent of the layer name).

    Only the boundary is written; the pattern is metadata, not an entity.
    """

    BOUNDARY = "boundary"

#: Default minimum enclosed area, in mm^2. [DESIGN] a chosen threshold, not
#: an observed CAD tolerance.
MIN_AREA_MM2 = 1.0

#: App id under which the pattern contract is stored as XDATA on the boundary.
HATCH_APPID = "ALLINCAD_HATCH"

#: [UNRESOLVED] There is no hatch layer in the project convention. MEASURED:
#: ``semantic_layers.LayerSemantic`` has no HATCH member, ``_EXACT`` has no
#: hatch key, ``_PATTERNS`` has no hatch pattern, and the ``exact`` table in
#: ``configs/architectural-layers.json`` has no hatch key. The measured set of
#: project-convention hatch layer names is therefore EMPTY. No substitute name
#: is invented here; ``make_hatch`` requires an explicit ``layer``.
HATCH_LAYER_STATUS = "UNRESOLVED"
KNOWN_HATCH_LAYERS: tuple[str, ...] = ()

#: [UNRESOLVED] XRECORD was evaluated as the metadata carrier and REJECTED for
#: the pinned ezdxf 1.4.4: ``rootdict.add_xrecord(key)`` takes no tags, the
#: ``XRecord`` entity exposes neither ``set_tags`` nor ``edit()``, and
#: ``XRecord.load(Tags.from_text(text))`` fails with
#: ``AttributeError: 'Tags' object has no attribute 'appdata'``. XDATA is used
#: instead because it roundtrips (see the roundtrip test). Revisit when
#: ezdxf is upgraded.
UNRESOLVED_XRECORD_API = (
    "ezdxf 1.4.4 XRecord has no writable tag API; XDATA is used instead. "
    "Re-evaluate when ezdxf is upgraded past 1.4.4."
)


class HatchValidationError(ValueError):
    """Raised for a boundary or pattern that cannot be recorded as given."""


@dataclass(frozen=True, slots=True)
class HatchPattern:
    """The pattern intent for a region. [DESIGN] -- nothing here is observed.

    ``name`` is a hatch pattern name in the AutoCAD/ezdxf spelling (for
    example ``"ANSI31"``). This module does NOT validate the name against a
    pattern table: no such table was observed in this repository, and
    inventing a closed set of valid names would reject patterns a real CAD
    installation may legitimately have. An empty or non-string name is
    rejected; a plausible-but-unknown name is passed through unchanged, so a
    consumer can resolve it against the real pattern table.
    """

    name: str
    scale: float = 1.0
    angle_deg: float = 0.0

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise HatchValidationError("pattern name must be a non-empty string")
        if not math.isfinite(self.scale) or self.scale <= 0:
            raise HatchValidationError(
                f"pattern scale must be finite and > 0, got {self.scale!r}"
            )
        if not math.isfinite(self.angle_deg):
            raise HatchValidationError(
                f"pattern angle_deg must be finite, got {self.angle_deg!r}"
            )

    def as_fields(self) -> dict[str, Any]:
        return {
            "pattern.name": self.name,
            "pattern.scale": float(self.scale),
            "pattern.angle_deg": float(self.angle_deg),
        }


@dataclass(frozen=True, slots=True)
class HatchGeometry:
    """Pure 2D description of one hatch region, in millimetres.

    ``boundary`` is the implicit closed ring; the last vertex is NOT repeated
    as the first (that would create a zero-length closing edge).
    """

    boundary: tuple[Point2D, ...]
    pattern: HatchPattern
    layer: str
    allow_self_intersection: bool = False
    min_area_mm2: float = MIN_AREA_MM2
    name: str = ""

    # -- validation ---------------------------------------------------------

    def validate(self) -> None:
        if len(self.boundary) < 3:
            raise HatchValidationError(
                f"a closed boundary needs at least 3 vertices, got {len(self.boundary)}"
            )
        for index, point in enumerate(self.boundary):
            if not (math.isfinite(point.x) and math.isfinite(point.y)):
                raise HatchValidationError(f"boundary[{index}] must be finite")

        if not isinstance(self.layer, str) or not self.layer.strip():
            # See HATCH_LAYER_STATUS: there is no project hatch layer to
            # default to, so an empty layer is always a caller mistake.
            raise HatchValidationError("layer must be a non-empty string")

        if not math.isfinite(self.min_area_mm2) or self.min_area_mm2 < 0:
            raise HatchValidationError(
                f"min_area_mm2 must be finite and >= 0, got {self.min_area_mm2!r}"
            )

        self.pattern.validate()

        for edge_index, edge in enumerate(self.boundary_edges()):
            if math.dist(
                (edge.start.x, edge.start.y), (edge.end.x, edge.end.y)
            ) <= 0.0:
                raise HatchValidationError(
                    f"boundary edge {edge_index} has zero length: "
                    f"{edge.start} -> {edge.end}; repeated consecutive vertices "
                    "are rejected rather than snapped"
                )

        seen: dict[tuple[float, float], int] = {}
        for index, point in enumerate(self.boundary):
            key = (point.x, point.y)
            if key in seen:
                raise HatchValidationError(
                    f"boundary vertex {index} repeats vertex {seen[key]}: {key}"
                )
            seen[key] = index

        # Self-intersection is diagnosed BEFORE the area check. A bow-tie is
        # both self-intersecting AND zero-area, and "your boundary crosses
        # itself" is the more specific and more actionable diagnosis, so it
        # must not be masked by the generic degenerate-area message.
        if self.is_self_intersecting() and not self.allow_self_intersection:
            raise HatchValidationError(
                "self-intersecting boundary rejected: it does not bound a single "
                "region, so the 'one pattern over one region' contract cannot be "
                "honoured. Pass allow_self_intersection=True to override; the "
                "record will be flagged self_intersecting=True."
            )

        area = abs(self.signed_area_mm2())
        if area < self.min_area_mm2:
            raise HatchValidationError(
                f"degenerate boundary: enclosed area {area} mm^2 is below "
                f"min_area_mm2={self.min_area_mm2}"
            )

    # -- geometry -----------------------------------------------------------

    def boundary_edges(self) -> list[Segment2D]:
        """Closed ring as segments, including the wrap-around last->first."""
        points = self.boundary
        return [
            Segment2D("boundary", points[i], points[(i + 1) % len(points)])
            for i in range(len(points))
        ]

    def signed_area_mm2(self) -> float:
        """Shoelace (signed) area. Positive for counter-clockwise rings.

    [DESIGN] The shoelace formula is implemented here because
    ``topology.py`` has no polygon-area helper to reuse (checked: its public
        API is Point2D, Segment2D, NodedSegment, segments_from_entities and
        node_segments). It is deliberately kept local and covered by tests
        against analytically known areas rather than being assumed.
        """
        points = self.boundary
        total = 0.0
        for index in range(len(points)):
            current = points[index]
            following = points[(index + 1) % len(points)]
            total += current.x * following.y - following.x * current.y
        return total / 2.0

    def area_mm2(self) -> float:
        return abs(self.signed_area_mm2())

    def perimeter_mm(self) -> float:
        return sum(
            math.dist((s.start.x, s.start.y), (s.end.x, s.end.y))
            for s in self.boundary_edges()
        )

    def is_self_intersecting(self) -> bool:
        """True when the ring crosses itself.

        Reuses :func:`topology.node_segments` so the hatch and the rest of the
        repository share one definition of "crossing". A simple ring nodes to
        exactly its own segment count; a crossing ring yields strictly more
        (MEASURED: 4 in -> 6 out for a bow-tie).
        """
        edges = self.boundary_edges()
        return len(node_segments(edges)) > len(edges)

    @property
    def self_intersecting(self) -> bool:
        return self.is_self_intersecting()

    # -- contract payload ---------------------------------------------------

    def contract(self) -> dict[str, Any]:
        """The full (b) contract: boundary ring + pattern intent.

        This dict is the whole point of option (b) and is what gets written as
        XDATA. It is deliberately flat and JSON-serialisable so a consumer in
        another process or language can read it without this module.
        """
        return {
            "name": self.name,
            "pattern": {
                "name": self.pattern.name,
                "scale": self.pattern.scale,
                "angle_deg": self.pattern.angle_deg,
            },
            "layer": self.layer,
            "closed": True,
            "vertices": [[p.x, p.y] for p in self.boundary],
            "area_mm2": self.area_mm2(),
            "perimeter_mm": self.perimeter_mm(),
            "self_intersecting": self.is_self_intersecting(),
            "layer_semantic": str(classify_layer(self.layer)),
        }

    def contract_xdata(self) -> list[tuple[int, Any]]:
        """The XDATA tag list for this contract. Symmetric with the reader."""
        return _contract_to_xdata(self.contract())


def _contract_to_xdata(contract: dict[str, Any]) -> list[tuple[int, Any]]:
    """Flatten a contract dict to XDATA tags.

    Encoding is a flat sequence of ``key=<value>`` strings under group code
    1000, plus one ``(1070, n)`` count ahead of the vertex block. 1000 is used
    for every field (including the numbers) on purpose: a single code makes the
    format trivially parseable by a consumer in another language, and it
    removes the pairing problem that comes from mixing 1000 and 1040 tags and
    having to zip them back together positionally. Floats round-trip through
    ``repr()`` exactly, so no precision is lost.
    """
    fields: dict[str, Any] = {
        "contract_version": "1",
        "name": contract["name"],
        "layer": contract["layer"],
        "layer_semantic": contract["layer_semantic"],
        "closed": str(contract["closed"]),
        "area_mm2": repr(float(contract["area_mm2"])),
        "perimeter_mm": repr(float(contract["perimeter_mm"])),
        "self_intersecting": str(contract["self_intersecting"]),
    }
    for axis, value in contract["pattern"].items():
        fields[f"pattern.{axis}"] = (
            value if isinstance(value, str) else repr(float(value))
        )

    tags: list[tuple[int, Any]] = [(1000, f"{key}={value}") for key, value in fields.items()]
    tags.append((1000, f"vertex_count={len(contract['vertices'])}"))
    for x, y in contract["vertices"]:
        tags.append((1000, f"v={float(x)!r},{float(y)!r}"))
    return tags


def read_hatch_metadata(entity: Any) -> dict[str, Any] | None:
    """Read back the (b) contract written by :func:`write_hatch`.

    Returns ``None`` when ``entity`` carries no contract. This is the
    downstream entry point described in the module docstring: a renderer, or a
    future normalizer HATCH branch, converts this dict into a real HATCH at the
    moment the pattern is actually drawn.
    """
    try:
        tags = entity.get_xdata(HATCH_APPID)
    except Exception:  # pragma: no cover - ezdxf raises for an unknown appid
        return None
    if not tags:
        return None

    contract: dict[str, Any] = {}
    vertices: list[list[float]] = []
    pattern: dict[str, Any] = {}
    for _code, raw in tags:
        text = str(raw)
        if "=" not in text:
            continue
        key, _, value = text.partition("=")
        if key == "v":
            x_text, _, y_text = value.partition(",")
            vertices.append([float(x_text), float(y_text)])
        elif key.startswith("pattern."):
            axis = key.split(".", 1)[1]
            pattern[axis] = value if axis == "name" else float(value)
        elif key in ("closed", "self_intersecting"):
            contract[key] = value == "True"
        elif key in ("area_mm2", "perimeter_mm"):
            contract[key] = float(value)
        elif key == "vertex_count":
            contract[key] = int(value)
        else:
            contract[key] = value

    contract["pattern"] = pattern
    contract["vertices"] = vertices
    return contract


def make_hatch(
    boundary: Iterable[Sequence[float] | Point2D],
    pattern: HatchPattern | None = None,
    *,
    layer: str,
    name: str = "",
    scale: float = 1.0,
    angle_deg: float = 0.0,
    pattern_name: str = "",
    allow_self_intersection: bool = False,
    min_area_mm2: float = MIN_AREA_MM2,
) -> HatchGeometry:
    """Build a :class:`HatchGeometry`.

    ``layer`` is REQUIRED and has no default: see :data:`HATCH_LAYER_STATUS`.
    There is no project-convention hatch layer to inherit, and inventing one
    produced silent data corruption in the earlier opening work, so the
    decision is pushed to the caller.

    Either pass a ready ``pattern=`` or the ``pattern_name``/``scale``/
    ``angle_deg`` triple. ``boundary`` is an implicitly closed ring of at least
    three points; do NOT repeat the first point at the end (that is a
    zero-length edge and is rejected).
    """
    points = tuple(
        _as_point(value, f"boundary[{i}]") for i, value in enumerate(boundary)
    )
    if pattern is None:
        # pattern_name is passed through WITHOUT str() coercion on purpose: a
        # None or non-string name must reach HatchPattern.validate and be
        # rejected there, not be silently turned into the string "None".
        pattern = HatchPattern(
            name=pattern_name, scale=float(scale), angle_deg=float(angle_deg)
        )
    elif not isinstance(pattern, HatchPattern):  # pragma: no cover - defensive
        raise HatchValidationError(
            f"pattern must be a HatchPattern, got {type(pattern).__name__}"
        )

    geometry = HatchGeometry(
        boundary=points,
        pattern=pattern,
        layer=str(layer),
        allow_self_intersection=bool(allow_self_intersection),
        min_area_mm2=float(min_area_mm2),
        name=str(name),
    )
    # validate() runs FIRST: it rejects non-finite vertices, and the
    # self-intersection probe feeds vertices to topology.node_segments, which
    # cannot cope with an infinity. validate() also owns the self-intersection
    # opt-in, so there is a single validation entry point.
    geometry.validate()
    return geometry


@dataclass(frozen=True, slots=True)
class HatchRecord:
    """Result of :func:`write_hatch`: what was actually written, by handle."""

    document_id: str
    dxf_version: str
    layer: str
    layer_semantic: LayerSemantic
    pattern: HatchPattern
    area_mm2: float
    perimeter_mm: float
    self_intersecting: bool
    contract: dict[str, Any]
    entities: tuple[tuple[HatchRole, str, str, str], ...]

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    def handles(self) -> tuple[str, ...]:
        return tuple(item[1] for item in self.entities)

    def layer_of(self, handle: str) -> str:
        for _role, entity_handle, _type, layer in self.entities:
            if entity_handle.upper() == handle.upper():
                return layer
        raise KeyError(handle)

    def role_of(self, handle: str) -> HatchRole:
        for role, entity_handle, _type, _layer in self.entities:
            if entity_handle.upper() == handle.upper():
                return role
        raise KeyError(handle)

    def layer_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _role, _handle, _type, layer in self.entities:
            counts[layer] = counts.get(layer, 0) + 1
        return counts

    def snapshots(self) -> list[EntitySnapshot]:
        """Convert the record into readback snapshots (for digest/diff).

        The snapshot mirrors the shape ``extraction_runtime._normalize_ezdxf_entity``
        actually produces for a closed LWPOLYLINE -- ``points`` as
        ``[x, y, start_width, end_width, bulge]`` 5-tuples -- rather than an
        idealised 2-tuple. Matching the normalizer exactly is the point of
        option (b): it is what lets a reloaded hatch be compared digest-for-
        digest against this record without a shim. The pattern contract is
        deliberately NOT in ``properties`` either, because the normalizer
        puts nothing there; it travels as XDATA and is read with
        :func:`read_hatch_metadata`.
        """
        return [
            EntitySnapshot(
                document_id=self.document_id,
                handle=handle,
                entity_type=entity_type,
                layer=layer,
                geometry={
                    "points": [
                        [vertex[0], vertex[1], 0.0, 0.0, 0.0]
                        for vertex in self.contract["vertices"]
                    ],
                    "closed": True,
                },
            )
            for _role, handle, entity_type, layer in self.entities
        ]


def write_hatch(doc: Any, hatch: HatchGeometry) -> HatchRecord:
    """Write ``hatch`` into an ezdxf document as ONE closed LWPOLYLINE plus
    the pattern contract as XDATA on that same polyline.

    No HATCH entity is written -- see the module docstring for the measurement
    behind that choice. ``doc`` is an ``ezdxf.document.Drawing`` created for
    :data:`DXF_WRITE_VERSION` (AC1032, R2018). A missing layer table entry is
    created. Returns a :class:`HatchRecord` describing exactly what was
    written.
    """
    _require_ezdxf()
    doc_version = str(getattr(doc, "dxfversion", "") or "")
    doc_release = str(getattr(doc, "acad_release", "") or "")
    if _release_of(doc_version) != DXF_WRITE_VERSION or (
        doc_release and doc_release != DXF_WRITE_VERSION
    ):
        raise ValueError(
            f"this recorder writes {DXF_WRITE_VERSION} (AC1032) only, got {doc_version}"
        )
    if not _is_r2000_or_newer(_release_of(doc_version)):  # pragma: no cover - defensive
        raise ValueError(f"read lower bound is {DXF_MIN_READ_VERSION} (AC1015); got {doc_version}")

    hatch.validate()
    if hatch.is_self_intersecting() and not hatch.allow_self_intersection:
        raise HatchValidationError(
            "refusing to write a self-intersecting boundary that was not "
            "explicitly allowed"
        )

    msp = doc.modelspace()
    if hatch.layer not in doc.layers:
        doc.layers.add(hatch.layer)
    if HATCH_APPID not in doc.appids:
        doc.appids.add(HATCH_APPID)

    entity = msp.add_lwpolyline(
        [(p.x, p.y) for p in hatch.boundary],
        format="xy",
        close=True,
        dxfattribs={"layer": hatch.layer},
    )
    entity.set_xdata(HATCH_APPID, _contract_to_xdata(hatch.contract()))

    contract = hatch.contract()
    return HatchRecord(
        document_id=_document_id(doc),
        dxf_version=doc_release or _release_of(doc_version),
        layer=hatch.layer,
        layer_semantic=classify_layer(hatch.layer),
        pattern=hatch.pattern,
        area_mm2=float(contract["area_mm2"]),
        perimeter_mm=float(contract["perimeter_mm"]),
        self_intersecting=bool(contract["self_intersecting"]),
        contract=contract,
        entities=((HatchRole.BOUNDARY, entity.dxf.handle, "LWPOLYLINE", hatch.layer),),
    )


def _as_point(value: Sequence[float] | Point2D, name: str) -> Point2D:
    if isinstance(value, Point2D):
        return value
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        x, y = value[0], value[1]
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return Point2D(float(x), float(y))
    raise HatchValidationError(f"{name} must be a 2D point, got {value!r}")


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
            "ezdxf is required by the hatch recorder. On this host run the project "
            "venv with a cleared PYTHONHOME, otherwise the standard library is "
            "hidden and this import fails with 'No module named annotationlib'."
        ) from exc
    return ezdxf
