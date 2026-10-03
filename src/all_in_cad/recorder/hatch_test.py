"""Tests for the hatch recorder.

Run (PYTHONHOME must be cleared on this host, see hatch.py module docstring)::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest \
        C:\\Users\\khs09\\all-in-cad\\src\\all_in_cad\\recorder\\hatch_test.py

The roundtrip section is deliberately not a "did ezdxf reload it" test. It
feeds the reloaded file back through THIS repository's own analyzer
(``extraction_runtime._normalize_ezdxf_entity`` -> ``topology.segments_from_entities``
-> ``node_segments``). An earlier audit found that no recorder test fed its own
output into its own verify path, and that gap is what let a CRITICAL bug pass;
so the boundary/pattern contract is checked through the real consumer chain
here, and a control case proves the chain would actually notice if a region
stopped contributing.
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

from all_in_cad.extraction_runtime import _normalize_ezdxf_entity  # noqa: E402
from all_in_cad.readback import EntitySnapshot, diff_snapshots  # noqa: E402
from all_in_cad.recorder import hatch as hatch_mod  # noqa: E402
from all_in_cad.recorder.hatch import (  # noqa: E402
    HATCH_APPID,
    HATCH_LAYER_STATUS,
    KNOWN_HATCH_LAYERS,
    MIN_AREA_MM2,
    UNRESOLVED_XRECORD_API,
    HatchPattern,
    HatchRole,
    HatchValidationError,
    make_hatch,
    read_hatch_metadata,
    write_hatch,
)
from all_in_cad.semantic_layers import LayerSemantic, classify_layer  # noqa: E402
from all_in_cad.topology import node_segments, segments_from_entities  # noqa: E402

TOL = 1e-6

# A layer the project convention has no entry for. Deliberately NOT a plausible
# substitute like "HAT1": see HATCH_LAYER_STATUS.
TEST_LAYER = "TEST-HATCH"


@pytest.fixture
def doc() -> ezdxf.document.Drawing:
    return ezdxf.new(hatch_mod.DXF_WRITE_VERSION)


def _square(size: float = 100.0, height: float = 50.0) -> list[tuple[float, float]]:
    return [(0.0, 0.0), (size, 0.0), (size, height), (0.0, height)]


# --- closed boundaries ------------------------------------------------------


def test_closed_rectangle_area_matches_analytic_value() -> None:
    hatch = make_hatch(_square(100.0, 50.0), layer=TEST_LAYER, pattern_name="ANSI31")

    assert hatch.area_mm2() == pytest.approx(5000.0)
    assert hatch.perimeter_mm() == pytest.approx(300.0)
    assert len(hatch.boundary_edges()) == 4
    # the ring is implicitly closed: the wrap-around edge exists
    last = hatch.boundary_edges()[-1]
    assert (last.start.x, last.start.y) == (0.0, 50.0)
    assert (last.end.x, last.end.y) == (0.0, 0.0)


def test_orientation_changes_sign_but_not_area() -> None:
    ccw = make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31")
    cw = make_hatch(list(reversed(_square())), layer=TEST_LAYER, pattern_name="ANSI31")

    assert ccw.signed_area_mm2() == pytest.approx(5000.0)
    assert cw.signed_area_mm2() == pytest.approx(-5000.0)
    assert cw.area_mm2() == pytest.approx(ccw.area_mm2())


def test_concave_l_shape_area() -> None:
    # 100x100 square with the top-right 50x50 corner removed -> 7500
    concave = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (50.0, 50.0), (50.0, 100.0), (0.0, 100.0)]
    hatch = make_hatch(concave, layer=TEST_LAYER, pattern_name="ANSI31")
    assert hatch.area_mm2() == pytest.approx(7500.0)
    assert not hatch.self_intersecting


def test_triangle_area() -> None:
    hatch = make_hatch([(0.0, 0.0), (100.0, 0.0), (0.0, 100.0)], layer=TEST_LAYER, pattern_name="ANSI31")
    assert hatch.area_mm2() == pytest.approx(5000.0)


@pytest.mark.parametrize("bad", [[], [(0.0, 0.0)], [(0.0, 0.0), (10.0, 0.0)]])
def test_fewer_than_three_vertices_rejected(bad: list) -> None:
    with pytest.raises(HatchValidationError, match="at least 3 vertices"):
        make_hatch(bad, layer=TEST_LAYER, pattern_name="ANSI31")


# --- degenerate boundaries (area 0) ----------------------------------------


def test_collinear_zero_area_boundary_rejected() -> None:
    with pytest.raises(HatchValidationError, match="degenerate boundary"):
        make_hatch([(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)], layer=TEST_LAYER, pattern_name="ANSI31")




def test_repeated_point_zero_area_rejected() -> None:
    # three identical points: the zero-length-edge rule fires before the area
    # rule, which is the more precise diagnosis
    with pytest.raises(HatchValidationError, match="zero length"):
        make_hatch([(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)], layer=TEST_LAYER, pattern_name="ANSI31")


def test_collinear_distinct_points_reach_the_area_rule() -> None:
    """Zero-length edges are gone, so this one must fail on area instead."""
    with pytest.raises(HatchValidationError, match="degenerate boundary"):
        make_hatch([(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)], layer=TEST_LAYER, pattern_name="ANSI31")


def test_sliver_below_min_area_rejected() -> None:
    # 1000 x 0.0005 mm = 0.5 mm^2, below the 1.0 mm^2 default threshold
    sliver = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 0.0005), (0.0, 0.0005)]
    with pytest.raises(HatchValidationError, match="degenerate boundary"):
        make_hatch(sliver, layer=TEST_LAYER, pattern_name="ANSI31")


def test_min_area_threshold_is_honoured_when_lowered() -> None:
    sliver = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 0.0005), (0.0, 0.0005)]
    hatch = make_hatch(sliver, layer=TEST_LAYER, pattern_name="ANSI31", min_area_mm2=0.1)
    assert hatch.area_mm2() == pytest.approx(0.5)


def test_repeated_consecutive_vertex_rejected_as_zero_length_edge() -> None:
    with pytest.raises(HatchValidationError, match="zero length"):
        make_hatch(
            [(0.0, 0.0), (0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)],
            layer=TEST_LAYER,
            pattern_name="ANSI31",
        )


def test_repeated_non_adjacent_vertex_rejected() -> None:
    with pytest.raises(HatchValidationError, match="repeats vertex"):
        make_hatch(
            [(0.0, 0.0), (100.0, 0.0), (0.0, 0.0), (100.0, 50.0), (0.0, 50.0)],
            layer=TEST_LAYER,
            pattern_name="ANSI31",
        )


def test_closing_point_must_not_be_repeated() -> None:
    """Passing the first point again is a zero-length edge, not a closure."""
    ring = _square() + [(0.0, 0.0)]
    with pytest.raises(HatchValidationError, match="zero length"):
        make_hatch(ring, layer=TEST_LAYER, pattern_name="ANSI31")


def test_non_finite_vertex_rejected() -> None:
    with pytest.raises(HatchValidationError, match="finite"):
        make_hatch([(0.0, 0.0), (math.inf, 0.0), (100.0, 50.0)], layer=TEST_LAYER, pattern_name="ANSI31")


# --- self-intersection: rule is REJECT BY DEFAULT ---------------------------


def test_self_intersecting_bowtie_rejected_by_default() -> None:
    bowtie = [(0.0, 0.0), (100.0, 100.0), (100.0, 0.0), (0.0, 100.0)]
    with pytest.raises(HatchValidationError, match="self-intersecting boundary rejected"):
        make_hatch(bowtie, layer=TEST_LAYER, pattern_name="ANSI31")


def test_self_intersecting_ring_allowed_only_with_explicit_opt_in() -> None:
    spiral = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (20.0, 100.0),
              (20.0, 20.0), (80.0, 20.0), (80.0, 80.0), (40.0, 80.0)]
    with pytest.raises(HatchValidationError, match="self-intersecting boundary rejected"):
        make_hatch(spiral, layer=TEST_LAYER, pattern_name="ANSI31")

    hatch = make_hatch(
        spiral, layer=TEST_LAYER, pattern_name="ANSI31", allow_self_intersection=True
    )
    assert hatch.self_intersecting is True
    assert hatch.allow_self_intersection is True
    # the flag is carried into the contract so a consumer can refuse it
    assert hatch.contract()["self_intersecting"] is True


def test_opted_in_bowtie_still_rejected_when_area_collapses_to_zero() -> None:
    """A bow-tie's two lobes cancel under the shoelace (nonzero) rule. The
    opt-in clears the self-intersection rule but cannot conjure area."""
    bowtie = [(0.0, 0.0), (100.0, 100.0), (100.0, 0.0), (0.0, 100.0)]
    with pytest.raises(HatchValidationError, match="degenerate boundary"):
        make_hatch(
            bowtie, layer=TEST_LAYER, pattern_name="ANSI31", allow_self_intersection=True
        )


def test_opted_in_nonzero_area_self_intersection_is_written_and_flagged(doc) -> None:
    """A self-crossing spiral: it encloses a real non-zero area, so it is only
    accepted because the opt-in was given explicitly, and it stays flagged."""
    spiral = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (20.0, 100.0),
              (20.0, 20.0), (80.0, 20.0), (80.0, 80.0), (40.0, 80.0)]
    hatch = make_hatch(
        spiral, layer=TEST_LAYER, pattern_name="ANSI31", allow_self_intersection=True
    )
    assert hatch.self_intersecting is True
    assert hatch.area_mm2() > 0.0
    record = write_hatch(doc, hatch)
    assert record.self_intersecting is True
    assert record.contract["self_intersecting"] is True


def test_self_intersection_detector_uses_shared_noding_helper() -> None:
    """The rule is expressed with topology.node_segments, so hatch and
    topology cannot disagree about what 'crossing' means."""
    assert hatch_mod.node_segments is node_segments
    simple = make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31")
    assert simple.self_intersecting is False
    # a simple ring nodes to exactly its own edge count
    assert len(node_segments(simple.boundary_edges())) == len(simple.boundary_edges())


# --- pattern validation -----------------------------------------------------


@pytest.mark.parametrize("scale", [0.0, -1.0, -0.001])
def test_non_positive_pattern_scale_rejected(scale: float) -> None:
    with pytest.raises(HatchValidationError, match="scale must be finite and > 0"):
        make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31", scale=scale)


@pytest.mark.parametrize("scale", [math.nan, math.inf, -math.inf])
def test_non_finite_pattern_scale_rejected(scale: float) -> None:
    with pytest.raises(HatchValidationError, match="scale must be finite and > 0"):
        make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31", scale=scale)


def test_non_finite_pattern_angle_rejected() -> None:
    with pytest.raises(HatchValidationError, match="angle_deg must be finite"):
        make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31", angle_deg=math.nan)


@pytest.mark.parametrize("name", ["", "   ", None, 7])
def test_empty_or_non_string_pattern_name_rejected(name) -> None:
    with pytest.raises(HatchValidationError, match="pattern name must be a non-empty string"):
        make_hatch(_square(), layer=TEST_LAYER, pattern_name=name)


def test_pattern_name_is_not_validated_against_an_invented_table() -> None:
    """No pattern table was observed in this repository, so an unknown but
    well-formed name is passed through rather than rejected against a list
    this module would have had to invent."""
    hatch = make_hatch(_square(), layer=TEST_LAYER, pattern_name="SOME_VENDOR_PATTERN_9000")
    assert hatch.pattern.name == "SOME_VENDOR_PATTERN_9000"


def test_pattern_object_and_triple_are_equivalent() -> None:
    by_object = make_hatch(_square(), HatchPattern("ANSI31", 5.0, 45.0), layer=TEST_LAYER)
    by_triple = make_hatch(
        _square(), layer=TEST_LAYER, pattern_name="ANSI31", scale=5.0, angle_deg=45.0
    )
    assert by_object.pattern == by_triple.pattern
    assert by_object.contract() == by_triple.contract()


# --- layer: UNRESOLVED, and nothing invented --------------------------------


def test_measured_project_convention_has_no_hatch_layer() -> None:
    """The reason `layer` is required. If a hatch layer is ever added to the
    project convention, this test fails and points at the code to update."""
    assert KNOWN_HATCH_LAYERS == (), (
        "a project-convention hatch layer appeared; update HATCH_LAYER_STATUS, "
        "KNOWN_HATCH_LAYERS and give make_hatch a default"
    )
    assert HATCH_LAYER_STATUS == "UNRESOLVED"
    assert not hasattr(LayerSemantic, "HATCH")
    assert classify_layer(TEST_LAYER) is LayerSemantic.UNKNOWN


def test_layer_is_required_with_no_invented_default() -> None:
    with pytest.raises(TypeError):
        make_hatch(_square(), pattern_name="ANSI31")  # type: ignore[call-arg]


def test_empty_layer_rejected() -> None:
    with pytest.raises(HatchValidationError, match="layer must be a non-empty string"):
        make_hatch(_square(), layer="  ", pattern_name="ANSI31")


def test_record_reports_layer_semantic_as_unknown_rather_than_asserting_one(doc) -> None:
    hatch = make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31")
    record = write_hatch(doc, hatch)
    assert record.layer == TEST_LAYER
    assert record.layer_semantic is LayerSemantic.UNKNOWN
    assert record.layer_counts() == {TEST_LAYER: 1}
    assert TEST_LAYER in [layer.dxf.name for layer in doc.layers]


def test_no_invented_hatch_layer_constant_is_exported() -> None:
    assert "HAT1" not in dir(hatch_mod)
    assert "HATCH1" not in dir(hatch_mod)


# --- writing ----------------------------------------------------------------


def test_writes_one_closed_lwpolyline_and_no_hatch_entity(doc) -> None:
    hatch = make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31")
    record = write_hatch(doc, hatch)

    assert record.entity_count == 1
    assert record.entities[0][0] is HatchRole.BOUNDARY
    assert record.entities[0][2] == "LWPOLYLINE"
    assert record.dxf_version == "R2018"

    entities = list(doc.modelspace())
    assert len(entities) == 1
    assert entities[0].dxftype() == "LWPOLYLINE"
    assert entities[0].closed is True
    # the design decision: NO HATCH entity is ever written
    assert {e.dxftype() for e in entities} == {"LWPOLYLINE"}
    assert list(doc.query("HATCH")) == []


def test_written_hatch_satisfies_the_standing_no_hatch_rule(doc) -> None:
    """Guard the cli.py standing rule without modifying cli.py.

    cli.py declares ``FORBIDDEN_DXF_TYPES = ("INSERT", "HATCH")`` and its
    ``verify`` command fails a drawing containing either. This test re-reads
    that constant (read-only) and asserts the hatch recorder never trips it.
    If someone relaxes the rule and wants real HATCH entities, THIS is the
    test that should be revisited first.
    """
    from all_in_cad.recorder import cli as cli_mod

    assert "HATCH" in cli_mod.FORBIDDEN_DXF_TYPES

    hatch = make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31")
    write_hatch(doc, hatch)
    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    reloaded = ezdxf.read(stream)

    type_counts: dict[str, int] = {}
    for entity in reloaded.modelspace():
        name = entity.dxftype()
        type_counts[name] = type_counts.get(name, 0) + 1

    forbidden = {
        name: type_counts[name]
        for name in cli_mod.FORBIDDEN_DXF_TYPES
        if type_counts.get(name)
    }
    assert forbidden == {}, f"hatch output must not trip no_insert_or_hatch: {forbidden}"


def test_registered_appid_is_created(doc) -> None:
    hatch = make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31")
    write_hatch(doc, hatch)
    assert HATCH_APPID in [app.dxf.name for app in doc.appids]


def test_writes_r2018_and_rejects_other_versions() -> None:
    assert ezdxf.new("R2018").dxfversion == "AC1032"
    assert hatch_mod.DXF_WRITE_VERSION == "R2018"
    assert hatch_mod.DXF_MIN_READ_VERSION == "R2000"
    with pytest.raises(ValueError):
        write_hatch(ezdxf.new("R2010"), make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31"))


def test_unresolved_xrecord_api_is_documented() -> None:
    assert "1.4.4" in UNRESOLVED_XRECORD_API
    assert "XDATA" in UNRESOLVED_XRECORD_API


# --- ROUNDTRIP 1: ezdxf save/reload preserves geometry + contract -----------


def test_roundtrip_preserves_geometry_and_layer(doc) -> None:
    hatch = make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31")
    record = write_hatch(doc, hatch)

    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    reloaded = ezdxf.read(stream)
    assert reloaded.dxfversion == "AC1032"
    assert reloaded.acad_release == "R2018"

    entities = list(reloaded.modelspace())
    assert len(entities) == 1
    entity = entities[0]
    assert entity.dxftype() == "LWPOLYLINE"
    assert entity.closed is True
    assert entity.dxf.layer == TEST_LAYER
    assert entity.dxf.handle.upper() == record.handles()[0].upper()
    assert [tuple(p) for p in entity.get_points("xy")] == _square()


def test_roundtrip_preserves_pattern_metadata(doc) -> None:
    hatch = make_hatch(
        _square(120.5, 40.25), layer=TEST_LAYER, pattern_name="ANSI31",
        scale=7.5, angle_deg=45.0, name="SLAB_A",
    )
    write_hatch(doc, hatch)

    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    reloaded = ezdxf.read(stream)
    entity = list(reloaded.modelspace())[0]

    contract = read_hatch_metadata(entity)
    assert contract is not None, "pattern contract must survive save/reload"
    assert contract["pattern"] == {"name": "ANSI31", "scale": 7.5, "angle_deg": 45.0}
    assert contract["name"] == "SLAB_A"
    assert contract["layer"] == TEST_LAYER
    assert contract["closed"] is True
    assert contract["self_intersecting"] is False
    assert contract["vertex_count"] == 4
    assert contract["vertices"] == [[0.0, 0.0], [120.5, 0.0], [120.5, 40.25], [0.0, 40.25]]


def test_roundtrip_preserves_measured_area_and_perimeter(doc) -> None:
    hatch = make_hatch(_square(100.0, 50.0), layer=TEST_LAYER, pattern_name="ANSI31")
    write_hatch(doc, hatch)

    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    reloaded = ezdxf.read(stream)
    contract = read_hatch_metadata(list(reloaded.modelspace())[0])

    assert contract is not None
    assert contract["area_mm2"] == pytest.approx(5000.0, abs=TOL)
    assert contract["perimeter_mm"] == pytest.approx(300.0, abs=TOL)


def test_roundtrip_reads_back_as_none_for_a_plain_polyline(doc) -> None:
    """A polyline with no contract is reported as such, not as a broken one."""
    doc.modelspace().add_lwpolyline(_square(), close=True)
    assert read_hatch_metadata(list(doc.modelspace())[0]) is None


def test_record_snapshots_roundtrip_digest_stable(doc) -> None:
    hatch = make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31")
    before = write_hatch(doc, hatch).snapshots()

    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    reloaded = ezdxf.read(stream)

    after: list[EntitySnapshot] = []
    for entity in reloaded.modelspace():
        geometry, properties = _normalize_ezdxf_entity(entity)
        after.append(
            EntitySnapshot(
                document_id=before[0].document_id,
                handle=entity.dxf.handle,
                entity_type=entity.dxftype(),
                layer=entity.dxf.layer,
                geometry=geometry,
                properties=properties,
            )
        )

    diff = diff_snapshots(
        before, after, document_id=before[0].document_id,
        before_revision=0, after_revision=1,
    )
    assert diff.changed is False, "recorded snapshot must match the reloaded entity"
    assert all(item.after_digest is not None for item in diff.entities)


def test_second_hatch_appears_as_added_in_diff(doc) -> None:
    first = write_hatch(doc, make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31"))
    before = first.snapshots()
    second = write_hatch(
        doc, make_hatch(_square(80.0, 80.0), layer=TEST_LAYER, pattern_name="ANSI31")
    )
    after = before + second.snapshots()

    diff = diff_snapshots(
        before, after, document_id=before[0].document_id,
        before_revision=0, after_revision=1,
    )
    assert diff.changed is True
    assert len([i for i in diff.entities if i.kind == "added"]) == second.entity_count


# --- ROUNDTRIP 2: through THIS repository's own analyzer --------------------
# This is the test the earlier audit found missing: recorder output must be fed
# back through the real verify chain, not just reloaded with ezdxf.


def _snapshots_from_entities(entities) -> list[EntitySnapshot]:
    out: list[EntitySnapshot] = []
    for entity in entities:
        geometry, properties = _normalize_ezdxf_entity(entity)
        out.append(
            EntitySnapshot(
                document_id="roundtrip",
                handle=entity.dxf.handle,
                entity_type=entity.dxftype(),
                layer=entity.dxf.layer,
                geometry=geometry,
                properties=properties,
            )
        )
    return out


def test_reloaded_hatch_normalizes_to_closed_polyline_geometry(doc) -> None:
    hatch = make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31")
    write_hatch(doc, hatch)

    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    reloaded = ezdxf.read(stream)

    snapshot = _snapshots_from_entities(reloaded.modelspace())[0]
    assert snapshot.entity_type == "LWPOLYLINE"
    assert snapshot.geometry["closed"] is True
    assert len(snapshot.geometry["points"]) == 4


def test_reloaded_hatch_boundary_contributes_topology_segments(doc) -> None:
    """The payoff of option (b): the region is visible to the verify chain."""
    hatch = make_hatch(_square(100.0, 50.0), layer=TEST_LAYER, pattern_name="ANSI31")
    write_hatch(doc, hatch)

    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    reloaded = ezdxf.read(stream)
    snapshots = _snapshots_from_entities(reloaded.modelspace())

    segments = segments_from_entities(snapshots)
    assert len(segments) == 4, "a closed quad must yield its four edges"
    assert len(node_segments(segments)) == 4

    # and the ring is geometrically consistent with what was recorded
    total = sum(
        s.start.x * s.end.y - s.end.x * s.start.y for s in segments
    ) / 2.0
    assert abs(total) == pytest.approx(5000.0, abs=1e-6)


def test_control_hatch_entity_would_contribute_nothing(doc) -> None:
    """Control case for the design decision in the module docstring.

    A real HATCH with the same boundary normalizes to EMPTY geometry here and
    yields 0 segments. If a future change gives the normalizer a HATCH branch
    this test fails, and that is the correct moment to revisit option (a).
    """
    probe = ezdxf.new("R2018")
    entity = probe.modelspace().add_hatch(color=7)
    entity.paths.add_polyline_path([(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)],
                                  is_closed=True)

    geometry, properties = _normalize_ezdxf_entity(entity)
    assert geometry == {}, "normalizer has no HATCH branch: geometry is empty"
    assert properties == {}

    snapshot = EntitySnapshot(
        document_id="control", handle=entity.dxf.handle, entity_type="HATCH",
        layer=entity.dxf.layer, geometry=geometry, properties=properties,
    )
    assert len(segments_from_entities([snapshot])) == 0

    # whereas the option (b) boundary written by this module does contribute
    hatch = make_hatch(_square(100.0, 50.0), layer=TEST_LAYER, pattern_name="ANSI31")
    write_hatch(doc, hatch)
    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    reloaded = ezdxf.read(stream)
    assert len(segments_from_entities(_snapshots_from_entities(reloaded.modelspace()))) == 4


def test_reloaded_hatch_boundary_nodes_against_a_wall(doc) -> None:
    """The boundary is real geometry: it nodes together with a wall's faces,
    which is the kind of check a verification pass would actually perform."""
    from all_in_cad.recorder.wall import make_wall, write_wall

    wall = make_wall((0.0, 250.0), (400.0, 250.0), 200.0)
    write_wall(doc, wall)
    hatch = make_hatch(_square(100.0, 50.0), layer=TEST_LAYER, pattern_name="ANSI31")
    write_hatch(doc, hatch)

    stream = io.StringIO()
    doc.write(stream)
    stream.seek(0)
    reloaded = ezdxf.read(stream)
    segments = segments_from_entities(_snapshots_from_entities(reloaded.modelspace()))
    assert len(segments) == 8  # 4 wall + 4 hatch
    noded = node_segments(segments)
    assert noded, "wall and hatch boundaries must node into non-degenerate segments"


def test_contract_metadata_is_readable_independently_of_geometry(doc) -> None:
    """Downstream pattern consumers read the contract, not the vertices."""
    hatch = make_hatch(
        _square(), layer=TEST_LAYER, pattern_name="ANSI31", scale=2.0, angle_deg=30.0
    )
    record = write_hatch(doc, hatch)
    entity = list(doc.modelspace())[0]

    contract = read_hatch_metadata(entity)
    assert contract is not None
    assert contract["pattern"]["name"] == "ANSI31"
    assert contract["pattern"]["scale"] == 2.0
    assert contract["pattern"]["angle_deg"] == 30.0
    # the record and the written contract agree
    assert contract["area_mm2"] == pytest.approx(record.area_mm2)
    assert contract["layer"] == record.layer
    assert contract["layer_semantic"] == str(record.layer_semantic)


def test_handles_and_layer_lookup_on_record(doc) -> None:
    hatch = make_hatch(_square(), layer=TEST_LAYER, pattern_name="ANSI31")
    record = write_hatch(doc, hatch)
    handle = record.handles()[0]
    assert record.role_of(handle) is HatchRole.BOUNDARY
    assert record.layer_of(handle) == TEST_LAYER
    with pytest.raises(KeyError):
        record.layer_of("DEADBEEF")
