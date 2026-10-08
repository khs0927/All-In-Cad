"""Tests for the wall recorder.

Run (PYTHONHOME must be cleared on this host, see wall.py module docstring)::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\wall_test.py -q
"""

from __future__ import annotations

import io
import math
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:  # make `all_in_cad` importable without an install
    sys.path.insert(0, str(_SRC))

import ezdxf  # noqa: E402

from all_in_cad.readback import diff_snapshots  # noqa: E402
from all_in_cad.recorder import wall as wall_mod  # noqa: E402
from all_in_cad.recorder.wall import (  # noqa: E402
    DEFAULT_DOC_LAYER_CENTERS,
    DESIGN_SF_DETAIL_RATIOS,
    DESIGN_SF_OFFSET_RATIOS,
    DESIGN_SF_TRIM_RATIOS,
    OBSERVED_XICAD_LAYERS,
    OBSERVED_XICAD_LINE_OFFSETS_MM,
    LayerPlan,
    WallGeometry,
    WallRole,
    WallValidationError,
    layer_plan,
    make_wall,
    observed_layer_plan,
    write_wall,
)
from all_in_cad.semantic_layers import LayerSemantic, classify_layer  # noqa: E402
from all_in_cad.topology import Point2D, node_segments, segments_from_entities  # noqa: E402

TOL = 1e-6


@pytest.fixture
def doc() -> ezdxf.document.Drawing:
    return ezdxf.new(wall_mod.DXF_WRITE_VERSION)


def _edges_by_role(wall: WallGeometry) -> dict[WallRole, list[tuple[Point2D, Point2D]]]:
    out: dict[WallRole, list[tuple[Point2D, Point2D]]] = {}
    for role, start, end in wall.edges():
        out.setdefault(role, []).append((start, end))
    return out


# --- horizontal wall, thickness 200 ---------------------------------------


def test_horizontal_wall_thickness_200_offsets_exactly() -> None:
    wall = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0)
    roles = _edges_by_role(wall)

    assert wall.length == pytest.approx(12000.0)
    assert wall.half_thickness == pytest.approx(100.0)

    neg = roles[WallRole.FACE_NEG][0]
    pos = roles[WallRole.FACE_POS][0]
    assert (neg[0].x, neg[0].y) == (0.0, -100.0)
    assert (neg[1].x, neg[1].y) == (12000.0, -100.0)
    assert (pos[0].x, pos[0].y) == (0.0, 100.0)
    assert (pos[1].x, pos[1].y) == (12000.0, 100.0)

    # the two faces are exactly one thickness apart
    assert pos[0].y - neg[0].y == pytest.approx(200.0)


def test_horizontal_wall_caps_and_entity_count() -> None:
    wall = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0)
    roles = _edges_by_role(wall)

    assert set(roles) == {
        WallRole.FACE_NEG,
        WallRole.FACE_POS,
        WallRole.CAP_START,
        WallRole.CAP_END,
    }
    assert len(wall.edges()) == 4

    start_cap = roles[WallRole.CAP_START][0]
    assert (start_cap[0].x, start_cap[0].y) == (0.0, -100.0)
    assert (start_cap[1].x, start_cap[1].y) == (0.0, 100.0)
    end_cap = roles[WallRole.CAP_END][0]
    assert (end_cap[0].x, end_cap[0].y) == (12000.0, -100.0)
    assert (end_cap[1].x, end_cap[1].y) == (12000.0, 100.0)


# --- vertical wall (no axis-alignment assumption) --------------------------


def test_vertical_wall_thickness_200() -> None:
    wall = make_wall((0.0, 0.0), (0.0, 12000.0), 200.0)
    roles = _edges_by_role(wall)

    neg = roles[WallRole.FACE_NEG][0]
    pos = roles[WallRole.FACE_POS][0]
    # unit direction is +Y, so the left-hand normal is -X and FACE_NEG (at
    # -half along the normal) therefore lands on X = +100
    assert (neg[0].x, neg[0].y) == (100.0, 0.0)
    assert (neg[1].x, neg[1].y) == (100.0, 12000.0)
    assert (pos[0].x, pos[0].y) == (-100.0, 0.0)
    assert (pos[1].x, pos[1].y) == (-100.0, 12000.0)
    assert wall.length == pytest.approx(12000.0)


def test_wall_is_rotationally_consistent() -> None:
    """A 90 degree rotation of the input rotates every output point too."""
    def rotate(point: Point2D) -> tuple[float, float]:
        return (-point.y, point.x)

    base = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0)
    rotated = make_wall(rotate(Point2D(0.0, 0.0)), rotate(Point2D(12000.0, 0.0)), 200.0)

    base_points = [(s.x, s.y) for _r, s, _e in base.edges()]
    rotated_points = [(s.x, s.y) for _r, s, _e in rotated.edges()]
    assert rotated_points == [(-y, x) for (x, y) in base_points]


# --- non axis-aligned centerline -------------------------------------------


def test_oblique_centerline_offset_is_perpendicular() -> None:
    wall = make_wall((1000.0, 2000.0), (4000.0, 5000.0), 240.0)
    ux, uy = wall.unit_direction
    nx, ny = wall.unit_normal

    assert ux == pytest.approx(3000.0 / math.hypot(3000.0, 3000.0))
    assert uy == pytest.approx(3000.0 / math.hypot(3000.0, 3000.0))
    assert wall.length == pytest.approx(math.hypot(3000.0, 3000.0))

    roles = _edges_by_role(wall)
    for role, sign in ((WallRole.FACE_NEG, -1.0), (WallRole.FACE_POS, 1.0)):
        start, end = roles[role][0]
        assert start.y == pytest.approx(2000.0 + sign * 120.0 * ny)
        assert start.x == pytest.approx(1000.0 + sign * 120.0 * nx)
        assert end.y == pytest.approx(5000.0 + sign * 120.0 * ny)
        assert end.x == pytest.approx(4000.0 + sign * 120.0 * nx)


def test_obtuse_centerline_keeps_faces_parallel_and_apart() -> None:
    wall = make_wall((-5000.0, 0.0), (3000.0, 4000.0), 175.0)
    edges = {role: (start, end) for role, start, end in wall.edges()}
    ns, ne = edges[WallRole.FACE_NEG]
    ps, pe = edges[WallRole.FACE_POS]
    def dist(a: Point2D, b: Point2D) -> float:
        return math.dist((a.x, a.y), (b.x, b.y))

    assert dist(ps, ns) == pytest.approx(175.0)
    assert dist(ns, ps) == pytest.approx(175.0)
    assert dist(pe, ne) == pytest.approx(175.0)
    assert dist(ps, pe) == pytest.approx(wall.length)
    # cap lines stay perpendicular to the centreline
    cs, ce = edges[WallRole.CAP_START]
    assert dist(cs, ce) == pytest.approx(175.0)
    ce2, _unused = edges[WallRole.CAP_END]
    assert dist(cs, ce2) == pytest.approx(wall.length)


# --- arbitrary thickness ---------------------------------------------------


@pytest.mark.parametrize("thickness", [1.0, 17.5, 99.0, 200.0, 250.5, 400.0])
def test_arbitrary_thickness_thirds_and_halves(thickness: float) -> None:
    wall = make_wall((0.0, 0.0), (5000.0, 0.0), thickness)
    roles = _edges_by_role(wall)
    assert roles[WallRole.FACE_NEG][0][0].y == pytest.approx(-thickness / 2.0)
    assert roles[WallRole.FACE_POS][0][0].y == pytest.approx(thickness / 2.0)
    assert wall.half_thickness == pytest.approx(thickness / 2.0)


# --- rejection of degenerate input ----------------------------------------


@pytest.mark.parametrize("thickness", [0.0, -1.0, -200.0])
def test_non_positive_thickness_rejected(thickness: float) -> None:
    with pytest.raises(WallValidationError):
        make_wall((0.0, 0.0), (12000.0, 0.0), thickness)


def test_zero_length_centerline_rejected() -> None:
    with pytest.raises(WallValidationError):
        make_wall((100.0, 100.0), (100.0, 100.0), 200.0)


def test_nan_thickness_rejected() -> None:
    with pytest.raises(WallValidationError):
        make_wall((0.0, 0.0), (12000.0, 0.0), float("nan"))


def test_non_finite_point_rejected() -> None:
    with pytest.raises(WallValidationError):
        make_wall((float("inf"), 0.0), (12000.0, 0.0), 200.0)


def test_bad_cap_style_rejected() -> None:
    with pytest.raises(WallValidationError):
        make_wall((0.0, 0.0), (12000.0, 0.0), 200.0, cap_style="blob")


# --- layer assignment ------------------------------------------------------


def test_default_layer_assignment_uses_project_convention(doc) -> None:
    wall = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0)
    record = write_wall(doc, wall)

    layers = [(role, layer) for role, _h, _t, layer, _s, _e in record.entities]
    assert layers == [
        (WallRole.FACE_NEG, "WAL1"),
        (WallRole.FACE_POS, "WAL1"),
        (WallRole.CAP_START, "WAL2"),
        (WallRole.CAP_END, "WAL2"),
    ]
    assert record.layer_counts() == {"WAL1": 2, "WAL2": 2}
    for _role, _handle, _type, layer, _s, _e in record.entities:
        assert classify_layer(layer) is LayerSemantic.WALL


def test_axis_line_uses_existing_centreline_layer(doc) -> None:
    wall = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0, include_axis=True)
    record = write_wall(doc, wall)

    assert record.entity_count == 5
    assert record.role_of(record.handles()[0]) is WallRole.AXIS
    axis_handle = record.handles()[0]
    assert record.layer_of(axis_handle) == "CEN1"
    assert classify_layer("CEN1") is LayerSemantic.CENTERLINE


def test_custom_doc_layer_centers_are_honoured(doc) -> None:
    wall = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0)
    record = write_wall(doc, wall, doc_layer_centers=("WAL1", "WAL3", "WAL2"))
    assert record.layer_counts() == {"WAL1": 2, "WAL3": 2}
    # only layers actually carrying entities are created
    table = [layer.dxf.name for layer in doc.layers]
    assert "WAL1" in table and "WAL3" in table
    assert "WAL2" not in table  # unused here, so deliberately not added


def test_documented_observed_xiCAD_mapping_is_available() -> None:
    plan = observed_layer_plan()
    assert plan.face == OBSERVED_XICAD_LAYERS["C"] == "WAL1"
    assert plan.trim == OBSERVED_XICAD_LAYERS["S"] == "WAL2"
    assert plan.detail == OBSERVED_XICAD_LAYERS["F"] == "WAL3"
    assert plan.axis == OBSERVED_XICAD_LAYERS["0"] == "CEN1"
    # The observed ZWCAD names C/S/F/0 are NOT project layer names; the two
    # naming systems are deliberately kept distinct and documented as such.
    for observed in ("C", "S", "F", "0"):
        assert classify_layer(observed) is LayerSemantic.UNKNOWN


def test_layer_plan_requires_three_names() -> None:
    with pytest.raises(ValueError):
        layer_plan(("WAL1", "WAL2"))


# --- observed dump: the numbers, and the count they actually add up to ----
#
# The literal ``trim_ratios=(-1.2, 2.5, 2.8)`` / ``detail_ratios=(2.0,)`` that
# used to sit in this file asserted three things at once and could only prove
# one of them: that the Y offsets are right (observed), that three of them go
# to one layer and one to another (a design choice), and that a wall has "9"
# entities (never supported by the dump -- the dump totals 7). The three claims
# are now tested separately, and the failing one is named.


def test_observed_dump_line_offsets_are_exactly_as_recorded() -> None:
    """[OBSERVED] The dump's layer names, line counts and Y offsets."""
    assert OBSERVED_XICAD_LINE_OFFSETS_MM == {
        "0": (0.0,),
        "C": (-100.0, 100.0),
        "S": (-120.0, 250.0, 280.0),
        "F": (200.0,),
    }
    assert {name: len(y) for name, y in OBSERVED_XICAD_LINE_OFFSETS_MM.items()} == {
        "0": 1,
        "C": 2,
        "S": 3,
        "F": 1,
    }


def test_observed_dump_totals_seven_entities_and_the_dump_has_no_caps() -> None:
    """"9 entities" is [DESIGN]: the dump sums to 7 and lists no cap lines."""
    observed_total = sum(len(y) for y in OBSERVED_XICAD_LINE_OFFSETS_MM.values())
    assert observed_total == 7  # 1 centreline + 2 faces + 3 S + 1 F
    assert observed_total != 9
    # "C" is exactly the two faces: no cap line was observed on any layer.
    assert OBSERVED_XICAD_LINE_OFFSETS_MM["C"] == (-100.0, 100.0)
    # ...which is also why the module default does not match the dump.
    default_wall = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0, include_axis=True)
    assert len(default_wall.edges()) == 5  # axis + 2 faces + 2 caps [DESIGN]


def test_observed_sf_offsets_are_reproduced_by_the_design_ratio_split() -> None:
    """[OBSERVED] part: the four S/F Y offsets are reproduced exactly.

    [DESIGN] part: which of them becomes trim and which becomes detail, and
    therefore which layer each lands on.
    """
    wall = make_wall(
        (0.0, 0.0),
        (12000.0, 0.0),
        200.0,
        layers=observed_layer_plan(),
        trim_ratios=DESIGN_SF_TRIM_RATIOS,
        detail_ratios=DESIGN_SF_DETAIL_RATIOS,
    )
    ys_by_role = {
        role: sorted({start.y for r, start, _end in wall.edges() if r is role})
        for role in (WallRole.TRIM, WallRole.DETAIL)
    }
    # observed: three lines on "S" at -120/+250/+280, one on "F" at +200
    assert ys_by_role[WallRole.TRIM] == [-120.0, 250.0, 280.0]
    assert ys_by_role[WallRole.DETAIL] == [200.0]


def test_combined_ratio_tuple_cannot_reproduce_the_observed_layer_split() -> None:
    """[DESIGN] Why the single ratio tuple is not a reproduction of anything.

    ``DESIGN_SF_OFFSET_RATIOS`` holds the same four numbers, but ``detail_ratios``
    routes every one of them to the detail role, so passing it whole puts 4
    lines on "F"/``WAL3`` and none on "S"/``WAL2`` -- the opposite of the dump.
    This test exists so the constant cannot quietly be relabelled "observed"
    again without a test failing.
    """
    wall = make_wall(
        (0.0, 0.0),
        (12000.0, 0.0),
        200.0,
        layers=observed_layer_plan(),
        detail_ratios=DESIGN_SF_OFFSET_RATIOS,
    )
    assert sorted({start.y for _r, start, _e in wall.edges() if _r is WallRole.DETAIL}) == [
        -120.0,
        200.0,
        250.0,
        280.0,
    ]
    assert not [e for e in wall.edges() if e[0] is WallRole.TRIM]


def test_observed_layer_plan_places_caps_on_C_which_the_dump_does_not_show() -> None:
    """[DESIGN] The known deviation of :func:`observed_layer_plan`, pinned.

    Layer "C" is observed as exactly two lines, but the plan puts the two cap
    lines there too, so writing with it yields 4 lines on ``WAL1``. Pinned so
    the deviation stays visible instead of being mistaken for reproduction.
    """
    plan = observed_layer_plan()
    assert plan.cap == plan.face == "WAL1"
    wall = make_wall(
        (0.0, 0.0), (12000.0, 0.0), 200.0, layers=plan, trim_ratios=DESIGN_SF_TRIM_RATIOS
    )
    wal1_lines = [e for e in wall.edges() if plan.for_role(e[0]) == "WAL1"]
    assert len(wal1_lines) == 4  # 2 observed faces + 2 unobserved caps
    assert len(OBSERVED_XICAD_LINE_OFFSETS_MM["C"]) == 2


# --- roundtrip: write, save, reload, re-read with ezdxf ---------------------


def test_roundtrip_entity_count_and_coordinates(doc) -> None:
    wall = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0, include_axis=True)
    record = write_wall(doc, wall)
    assert record.entity_count == 5
    assert record.dxf_version == "R2018"

    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    reloaded = ezdxf.read(stream)

    assert reloaded.dxfversion == "AC1032"
    assert reloaded.acad_release == "R2018"

    entities = list(reloaded.modelspace())
    assert len(entities) == record.entity_count
    assert {e.dxftype() for e in entities} == {"LINE"}  # LINE only, no INSERT/HATCH

    coords = sorted(
        (round(e.dxf.start.x, 9), round(e.dxf.start.y, 9),
         round(e.dxf.end.x, 9), round(e.dxf.end.y, 9))
        for e in entities
    )
    assert coords == [
        (0.0, -100.0, 0.0, 100.0),  # start cap
        (0.0, -100.0, 12000.0, -100.0),  # face at -half
        (0.0, 0.0, 12000.0, 0.0),  # axis
        (0.0, 100.0, 12000.0, 100.0),  # face at +half
        (12000.0, -100.0, 12000.0, 100.0),  # end cap
    ]

    layers = sorted(e.dxf.layer for e in entities)
    assert layers == ["CEN1", "WAL1", "WAL1", "WAL2", "WAL2"]


def test_roundtrip_matches_record_handles_layers_and_snapshots(doc) -> None:
    wall = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0)
    record = write_wall(doc, wall)

    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    reloaded = ezdxf.read(stream)

    by_handle = {e.dxf.handle: e for e in reloaded.modelspace()}
    for _role, handle, entity_type, layer, start, end in record.entities:
        entity = by_handle[handle.upper()]
        assert entity.dxftype() == entity_type == "LINE"
        assert entity.dxf.layer == layer
        assert (entity.dxf.start.x, entity.dxf.start.y) == pytest.approx(
            (start.x, start.y), abs=TOL
        )
        assert (entity.dxf.end.x, entity.dxf.end.y) == pytest.approx((end.x, end.y), abs=TOL)

    # the recorded snapshots still describe the reloaded entities
    snapshots = record.snapshots()
    assert len(snapshots) == record.entity_count
    assert {s.handle for s in snapshots} == {h.upper() for h in by_handle}


def test_roundtrip_snapshots_are_digest_stable(doc) -> None:
    wall = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0)
    before = write_wall(doc, wall).snapshots()

    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    reloaded = ezdxf.read(stream)
    after = [
        type(before[0])(
            document_id=before[0].document_id,
            handle=e.dxf.handle,
            entity_type=e.dxftype(),
            layer=e.dxf.layer,
            geometry={
                "start": [e.dxf.start.x, e.dxf.start.y],
                "end": [e.dxf.end.x, e.dxf.end.y],
            },
        )
        for e in reloaded.modelspace()
    ]

    diff = diff_snapshots(
        before, after, document_id=before[0].document_id,
        before_revision=0, after_revision=1,
    )
    assert diff.changed is False
    assert all(item.after_digest is not None for item in diff.entities)


def test_second_wall_shows_up_as_added_in_diff(doc) -> None:
    first = write_wall(doc, make_wall((0.0, 0.0), (12000.0, 0.0), 200.0))
    before = first.snapshots()
    second = write_wall(doc, make_wall((0.0, 5000.0), (12000.0, 5000.0), 200.0))
    after = before + second.snapshots()

    diff = diff_snapshots(
        before, after, document_id=before[0].document_id,
        before_revision=0, after_revision=1,
    )
    assert diff.changed is True
    added = [i for i in diff.entities if i.kind == "added"]
    assert len(added) == second.entity_count
    assert len(before) == 4 and len(after) == 8


def test_two_faces_are_detected_as_a_wall_pair(doc) -> None:
    """Topology/WALL_PAIR style pairing: the two faces are parallel and
    `thickness` apart, and they classify as LayerSemantic.WALL."""
    wall = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0)
    record = write_wall(doc, wall)
    snapshots = record.snapshots()

    segments = segments_from_entities(
        snapshots, semantics={LayerSemantic.WALL}
    )
    assert len(segments) == 4  # two faces plus two caps
    face_segments = [
        s for s in segments
        if s.start.y == s.end.y and s.start.y in (-100.0, 100.0)
    ]
    assert len(face_segments) == 2
    left, right = sorted(face_segments, key=lambda s: s.start.y)
    nx, ny = wall.unit_normal
    gap = abs((right.start.x - left.start.x) * nx + (right.start.y - left.start.y) * ny)
    assert gap == pytest.approx(200.0)

    noded = node_segments(segments)
    assert noded, "wall faces should node into non-degenerate segments"


# --- document level --------------------------------------------------------


def test_writes_r2018_and_rejects_other_versions() -> None:
    r2018 = ezdxf.new("R2018")
    assert r2018.dxfversion == "AC1032"  # ezdxf reports the AC code
    assert r2018.acad_release == "R2018"
    assert wall_mod.DXF_WRITE_VERSION == "R2018"
    assert wall_mod.DXF_MIN_READ_VERSION == "R2000"

    wrong = ezdxf.new("R2010")
    assert wrong.dxfversion == "AC1024"
    with pytest.raises(ValueError):
        write_wall(wrong, make_wall((0.0, 0.0), (12000.0, 0.0), 200.0))


def test_miter_cap_extends_beyond_the_centerline() -> None:
    wall = make_wall((0.0, 0.0), (12000.0, 0.0), 200.0, cap_style="miter")
    roles = _edges_by_role(wall)
    start_cap = roles[WallRole.CAP_START][0]
    assert (start_cap[0].x, start_cap[0].y) == pytest.approx((-100.0, -100.0))
    assert (start_cap[1].x, start_cap[1].y) == pytest.approx((-100.0, 100.0))
    end_cap = roles[WallRole.CAP_END][0]
    assert (end_cap[0].x, end_cap[0].y) == pytest.approx((12100.0, -100.0))
    assert (end_cap[1].x, end_cap[1].y) == pytest.approx((12100.0, 100.0))


def test_default_layers_constant_is_the_project_convention() -> None:
    assert DEFAULT_DOC_LAYER_CENTERS == ("WAL1", "WAL2", "WAL3")
    assert layer_plan() == LayerPlan(face="WAL1", cap="WAL2", trim="WAL2", detail="WAL3")
