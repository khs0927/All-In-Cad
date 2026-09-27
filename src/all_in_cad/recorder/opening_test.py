"""Tests for the wall-opening recorder (wall.py / door.py conventions followed).

ENVIRONMENT NOTE (host trap): this host injects PYTHONHOME, which hides the
standard library and breaks ``import ezdxf`` with
``ModuleNotFoundError: No module named 'annotationlib'``. Run with::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest ...
"""

from __future__ import annotations

import json
import math
import pathlib

import ezdxf
import pytest

from all_in_cad.readback import DiffKind, diff_snapshots
from all_in_cad.recorder.opening import (
    DEFAULT_LAYERS,
    DEFAULT_OPENING_WIDTH_MM,
    DEFAULT_THICKNESS_MM,
    DXF_VERSION,
    DXF_VERSION_NAME,
    LAYER_MAPPING_RESOLVED,
    OpeningGeometryError,
    make_opening,
    normalize_deg,
    readback_snapshots,
    write_opening,
)
from all_in_cad.semantic_layers import LayerSemantic, classify_layer

LAYER_CONFIG = pathlib.Path(__file__).resolve().parents[3] / "configs" / "architectural-layers.json"

HORIZONTAL = ((0, 0), (12000, 0))
VERTICAL = ((0, 0), (0, 12000))
OBLIQUE = ((0, 0), (6000, 3000))


def _new_doc() -> "ezdxf.document.Drawing":
    return ezdxf.new(DXF_VERSION, setup=True)


def _spec(opening, role):
    return next(spec for spec in opening.entities if spec.role == role)


def _roles(opening):
    return [spec.role for spec in opening.entities]


# --- (1) defaults --------------------------------------------------------------


def test_default_width_is_an_unresolved_design_constant() -> None:
    # Only door 900 and window 1500 were ever confirmed; the opening width was
    # not, so this number is documented as a design choice.
    assert DEFAULT_OPENING_WIDTH_MM == 1500.0


def test_default_offset_is_zero_so_no_gap_is_invented() -> None:
    opening = make_opening(HORIZONTAL)
    assert opening.offset_mm == 0.0
    assert opening.start_point.x == pytest.approx(0.0)
    assert opening.center.x == pytest.approx(750.0)


def test_default_composition_is_six_lines() -> None:
    opening = make_opening(HORIZONTAL)
    assert len(opening.entities) == 6
    assert _roles(opening) == [
        "edge_start",
        "edge_end",
        "face_line",
        "tick_start",
        "tick_end",
        "center_tick",
    ]


def test_every_entity_is_a_line() -> None:
    opening = make_opening(HORIZONTAL)
    assert {spec.dxftype for spec in opening.entities} == {"LINE"}


def test_no_insert_and_no_hatch_in_the_composition() -> None:
    opening = make_opening(HORIZONTAL)
    assert not any("INSERT" in spec.role.upper() for spec in opening.entities)
    assert not any("HATCH" in spec.role.upper() for spec in opening.entities)


def test_handle_hint_is_stable_text() -> None:
    hint = make_opening(HORIZONTAL).handle_hint
    assert hint == make_opening(HORIZONTAL).handle_hint
    assert hint.startswith("opening@750.000,0.000/w1500.000")


# --- (2) layers are unresolved, on purpose ------------------------------------


def test_layer_mapping_is_flagged_unresolved() -> None:
    assert LAYER_MAPPING_RESOLVED is False


def test_the_layer_config_really_has_no_opening_key() -> None:
    table = json.loads(LAYER_CONFIG.read_text(encoding="utf-8"))["exact"]
    assert not any("OPEN" in name.upper() for name in table), (
        "configs/architectural-layers.json gained an opening layer; the "
        "'unresolved' label in opening.py must be revisited"
    )


def test_layer_mapping_stays_unresolved_because_nothing_was_observed() -> None:
    # The reason is stated as a fact, not a mood: no drawing containing an
    # opening has been observed, so there is no measured layer to resolve to.
    # DOOR and WIN are *candidates* from the semantic convention, not evidence.
    assert LAYER_MAPPING_RESOLVED is False
    assert not any(
        classify_layer(name) in (LayerSemantic.DOOR, LayerSemantic.WINDOW)
        for name in DEFAULT_LAYERS
    )


def test_default_layers_are_temporary_and_self_evident() -> None:
    # The names must advertise their own provisional status so a later reader
    # cannot mistake them for a decided layer assignment.
    assert DEFAULT_LAYERS == ("TEMP-OPENING-BND", "TEMP-OPENING-SYM")
    for name in DEFAULT_LAYERS:
        assert name.startswith("TEMP-")


def test_default_layers_are_absent_from_the_layer_config() -> None:
    # A TEMP name must NOT be a configured semantic layer. If someone adds it
    # to configs/architectural-layers.json, LAYER_MAPPING_RESOLVED has to be
    # revisited and these tests are what force that review.
    table = json.loads(LAYER_CONFIG.read_text(encoding="utf-8"))["exact"]
    for name in DEFAULT_LAYERS:
        assert name not in table


def test_default_layers_classify_as_unknown_not_wall() -> None:
    # The critical property: an unresolved opening must be inert. If these
    # classified as WALL the six LINEs would become phantom wall segments.
    assert classify_layer("TEMP-OPENING-BND") is LayerSemantic.UNKNOWN
    assert classify_layer("TEMP-OPENING-SYM") is LayerSemantic.UNKNOWN


# --- (2b) the substitute's risk is pinned by an executable oracle --------------


def _wall_semantics_probe(layers):
    """Write a wall plus one opening on ``layers`` and report what downstream
    wall/opening reasoning would see. This is the oracle for the docstring's
    measured claims, so they cannot rot into folklore.
    """
    from all_in_cad.architecture import infer_opening_hosts
    from all_in_cad.readback import EntitySnapshot
    from all_in_cad.topology import segments_from_entities

    doc = _new_doc()
    msp = doc.modelspace()
    msp.add_line((0, 0), (12000, 0), dxfattribs={"layer": "WAL1"})
    record = write_opening(doc, make_opening(HORIZONTAL), layers=layers)
    handles = set(record.handles())
    snaps = list(record.snapshots)
    for entity in msp:
        if str(entity.dxf.handle) in handles:
            continue
        snaps.append(
            EntitySnapshot(
                document_id="probe",
                handle=str(entity.dxf.handle),
                entity_type=entity.dxftype(),
                layer=str(entity.dxf.layer),
                geometry={
                    "start": [entity.dxf.start.x, entity.dxf.start.y],
                    "end": [entity.dxf.end.x, entity.dxf.end.y],
                },
            )
        )
    walls = segments_from_entities(snaps, semantics={LayerSemantic.WALL})
    total = sum(
        math.dist((s.start.x, s.start.y), (s.end.x, s.end.y)) for s in walls
    )
    hosts = infer_opening_hosts(snaps, max_distance=500.0)
    return len(walls), total, len(hosts)


def test_wall_layers_would_leak_six_phantom_wall_segments() -> None:
    # Justifies dropping the WAL2/WAL3 substitute. A single opening written on
    # real wall layers would nearly double the wall length seen downstream.
    count, total, hosts = _wall_semantics_probe(("WAL2", "WAL3"))
    assert count == 7, "6 phantom wall segments leaked into wall reasoning"
    assert total > 12000.0 + 1000.0, f"phantom wall length: {total} mm"
    assert hosts == 0, "and it still produced no opening-host relation"


def test_door_layers_would_fabricate_six_false_openings() -> None:
    # Justifies not routing the substitute through the DOOR family either:
    # it satisfies infer_opening_hosts, but only by claiming "door", which
    # xiWallOpening is type-agnostic and cannot support.
    _count, _total, hosts = _wall_semantics_probe(("DOOR", "DOOR_ELE"))
    assert hosts == 6


def test_the_chosen_substitute_is_inert_in_both_directions() -> None:
    count, total, hosts = _wall_semantics_probe(DEFAULT_LAYERS)
    assert count == 1, "only the real wall line may be seen as a wall"
    assert total == pytest.approx(12000.0)
    assert hosts == 0, "and no false opening relation is fabricated"


def test_slot_split_is_two_boundaries_and_four_symbols() -> None:
    opening = make_opening(HORIZONTAL)
    assert [spec.layer_slot for spec in opening.entities] == [
        "boundary",
        "boundary",
        "symbol",
        "symbol",
        "symbol",
        "symbol",
    ]
    assert len(opening.specs_for_slot("boundary")) == 2
    assert len(opening.specs_for_slot("symbol")) == 4


# --- (3) boundary geometry along the wall -------------------------------------


def test_boundaries_are_half_a_width_from_the_centre() -> None:
    opening = make_opening(HORIZONTAL, 2000)
    assert opening.start_point.x == pytest.approx(0.0)
    assert opening.end_point.x == pytest.approx(2000.0)
    assert opening.center.x == pytest.approx(1000.0)


def test_boundary_edges_span_the_thickness() -> None:
    spec = make_opening(HORIZONTAL).spec("edge_start")
    span = math.dist((spec.start.x, spec.start.y), (spec.end.x, spec.end.y))
    assert span == pytest.approx(DEFAULT_THICKNESS_MM)


def test_boundaries_are_perpendicular_to_the_wall_line() -> None:
    spec = make_opening(HORIZONTAL).spec("edge_end")
    # wall runs along +X, so the boundary is a vertical segment
    assert spec.start.x == pytest.approx(spec.end.x)


def test_face_line_is_parallel_to_the_wall_and_one_thickness_away() -> None:
    spec = make_opening(HORIZONTAL).spec("face_line")
    assert spec.start.y == pytest.approx(DEFAULT_THICKNESS_MM)
    assert spec.end.y == pytest.approx(DEFAULT_THICKNESS_MM)
    assert spec.end.x - spec.start.x == pytest.approx(1500.0)


def test_face_line_is_aligned_with_the_opening_edges() -> None:
    opening = make_opening(HORIZONTAL, 2000)
    face = opening.spec("face_line")
    assert face.start.x == pytest.approx(opening.start_point.x)
    assert face.end.x == pytest.approx(opening.end_point.x)


def test_offset_shifts_the_opening_along_the_wall() -> None:
    flush = make_opening(HORIZONTAL)
    pushed = make_opening(HORIZONTAL, offset_mm=300.0)
    assert pushed.center.x - flush.center.x == pytest.approx(300.0)


def test_offset_is_measured_from_the_start_point_to_the_first_edge() -> None:
    opening = make_opening(HORIZONTAL, 2000, offset_mm=300.0)
    assert opening.start_point.x == pytest.approx(300.0)
    assert opening.end_point.x == pytest.approx(2300.0)


def test_explicit_start_point_replaces_the_line_start() -> None:
    opening = make_opening(HORIZONTAL, start_point=(4000, 0))
    assert opening.start_point.x == pytest.approx(4000.0)
    assert opening.center.x == pytest.approx(4750.0)
    assert opening.origin.x == pytest.approx(4000.0)


def test_start_point_off_the_line_is_accepted_without_warning() -> None:
    opening = make_opening(HORIZONTAL, start_point=(4000, 250))
    assert opening.origin.y == pytest.approx(250.0)
    assert opening.warnings == ()


def test_ticks_are_at_45_degrees_to_the_wall() -> None:
    spec = make_opening(HORIZONTAL).spec("tick_start")
    run = spec.end.x - spec.start.x
    rise = spec.end.y - spec.start.y
    assert abs(rise) == pytest.approx(abs(run))


def test_center_tick_is_centred_on_the_opening() -> None:
    spec = make_opening(HORIZONTAL, 2000).spec("center_tick")
    assert spec.start.x == pytest.approx(1000.0)
    assert spec.end.x == pytest.approx(1000.0)


def test_ticks_sit_at_the_two_jambs() -> None:
    opening = make_opening(HORIZONTAL, 2000)
    start_tick = opening.spec("tick_start")
    end_tick = opening.spec("tick_end")
    assert start_tick.start.x == pytest.approx(0.0)
    assert end_tick.start.x == pytest.approx(2000.0)


# --- (4) axis-aligned and oblique wall symmetry --------------------------------


def test_horizontal_wall_produces_horizontal_boundaries() -> None:
    opening = make_opening(HORIZONTAL)
    assert opening.width_axis_deg == pytest.approx(0.0)
    assert opening.start_point.y == pytest.approx(0.0)
    assert opening.end_point.y == pytest.approx(0.0)


def test_vertical_wall_produces_horizontal_boundaries_in_the_other_axis() -> None:
    opening = make_opening(VERTICAL)
    assert opening.width_axis_deg == pytest.approx(90.0)
    assert opening.start_point.x == pytest.approx(0.0)
    assert opening.end_point.x == pytest.approx(0.0)
    assert opening.end_point.y - opening.start_point.y == pytest.approx(1500.0)


def test_oblique_wall_keeps_the_width_in_euclidean_terms() -> None:
    opening = make_opening(OBLIQUE, 2000)
    assert math.dist(
        (opening.start_point.x, opening.start_point.y),
        (opening.end_point.x, opening.end_point.y),
    ) == pytest.approx(2000.0)
    assert opening.width_axis_deg == pytest.approx(26.5650511, abs=1e-6)


def test_horizontal_and_vertical_lines_give_mirror_symbols() -> None:
    horizontal = make_opening(HORIZONTAL).spec("face_line")
    vertical = make_opening(VERTICAL).spec("face_line")
    # both move towards their own +normal, which is +Y and -X respectively
    assert horizontal.start.y == pytest.approx(100.0)
    assert vertical.start.x == pytest.approx(-100.0)


def test_side_right_mirrors_the_symbol_across_the_line() -> None:
    left = make_opening(HORIZONTAL, side="left")
    right = make_opening(HORIZONTAL, side="right")
    assert left.spec("face_line").start.y == pytest.approx(100.0)
    assert right.spec("face_line").start.y == pytest.approx(-100.0)


def test_side_right_leaves_the_boundary_positions_unchanged() -> None:
    left = make_opening(HORIZONTAL, side="left")
    right = make_opening(HORIZONTAL, side="right")
    assert right.start_point.x == pytest.approx(left.start_point.x)
    assert right.end_point.x == pytest.approx(left.end_point.x)


def test_side_right_keeps_the_boundaries_on_the_wall_line() -> None:
    spec = make_opening(HORIZONTAL, side="right").spec("edge_start")
    assert (spec.start.x + spec.end.x) / 2.0 == pytest.approx(0.0)
    assert sorted((spec.start.y, spec.end.y)) == pytest.approx([-50.0, 50.0])


def test_reversing_the_reference_line_keeps_the_width_but_mirrors_the_offset() -> None:
    forward = make_opening(HORIZONTAL, start_point=(4000, 0))
    reverse = make_opening(((12000, 0), (0, 0)), start_point=(4000, 0))
    assert forward.width_axis_deg == pytest.approx(0.0)
    assert reverse.width_axis_deg == pytest.approx(180.0)
    # from the same pick, +X runs forward into the opening while -X runs away
    assert forward.end_point.x == pytest.approx(5500.0)
    assert reverse.end_point.x == pytest.approx(2500.0)
    assert forward.end_point.x - forward.start_point.x == pytest.approx(1500.0)
    assert abs(reverse.end_point.x - reverse.start_point.x) == pytest.approx(1500.0)


def test_offset_from_a_reversed_line_runs_the_other_way() -> None:
    forward = make_opening(HORIZONTAL, offset_mm=500.0)
    reverse = make_opening(((12000, 0), (0, 0)), offset_mm=500.0)
    assert forward.start_point.x == pytest.approx(500.0)
    # the reversed line starts at X=12000 and runs towards -X
    assert reverse.start_point.x == pytest.approx(11500.0)


def test_width_axis_is_normalised_into_zero_360() -> None:
    assert normalize_deg(-45.0) == pytest.approx(315.0)
    assert normalize_deg(450.0) == pytest.approx(90.0)


# --- (5) the second prompt pick -------------------------------------------------


def test_a_collinear_direction_point_produces_no_warning() -> None:
    opening = make_opening(HORIZONTAL, direction_point=(6000, 0))
    assert opening.warnings == ()
    assert opening.direction_point == opening.direction_point


def test_an_off_line_direction_point_warns_and_the_line_still_wins() -> None:
    opening = make_opening(HORIZONTAL, direction_point=(6000, 400))
    assert any("off the reference line" in item for item in opening.warnings)
    assert opening.width_axis_deg == pytest.approx(0.0)


def test_a_direction_point_equal_to_the_start_is_rejected() -> None:
    with pytest.raises(OpeningGeometryError, match="must differ"):
        make_opening(HORIZONTAL, direction_point=(0, 0))


# --- (6) bad input is rejected ---------------------------------------------------


def test_zero_width_is_rejected() -> None:
    with pytest.raises(OpeningGeometryError, match="width_mm must be > 0"):
        make_opening(HORIZONTAL, 0)


def test_negative_width_is_rejected() -> None:
    with pytest.raises(OpeningGeometryError, match="width_mm must be > 0"):
        make_opening(HORIZONTAL, -900)


def test_zero_thickness_is_rejected() -> None:
    with pytest.raises(OpeningGeometryError, match="thickness_mm must be > 0"):
        make_opening(HORIZONTAL, thickness_mm=0)


def test_zero_length_reference_line_is_rejected() -> None:
    with pytest.raises(OpeningGeometryError, match="endpoints must differ"):
        make_opening(((0, 0), (0, 0)))


def test_negative_offset_is_rejected() -> None:
    with pytest.raises(OpeningGeometryError, match="offset_mm must be >= 0"):
        make_opening(HORIZONTAL, offset_mm=-1.0)


def test_bad_side_is_rejected() -> None:
    with pytest.raises(OpeningGeometryError, match="side must be"):
        make_opening(HORIZONTAL, side="middle")


def test_negative_collinear_tolerance_is_rejected() -> None:
    with pytest.raises(OpeningGeometryError, match="collinear_tolerance"):
        make_opening(HORIZONTAL, collinear_tolerance=-1.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_width_is_rejected(bad: float) -> None:
    with pytest.raises(OpeningGeometryError, match="width_mm must be a finite"):
        make_opening(HORIZONTAL, bad)


def test_non_finite_point_is_rejected() -> None:
    with pytest.raises(OpeningGeometryError, match="reference_line\\[0\\].y"):
        make_opening(((0, float("nan")), (1000, 0)))


def test_short_point_is_rejected() -> None:
    with pytest.raises(OpeningGeometryError, match="reference_line\\[1\\] must be"):
        make_opening(((0, 0), (1.0,)))


# --- (7) ezdxf round trip ---------------------------------------------------------


def test_write_creates_every_entity_once() -> None:
    doc = _new_doc()
    record = write_opening(doc, make_opening(HORIZONTAL))
    assert len(record.entity_handles) == 6
    assert len(set(record.entity_handles)) == 6
    assert len(list(doc.modelspace())) == 6


def test_write_readback_handles_match_the_live_document() -> None:
    doc = _new_doc()
    record = write_opening(doc, make_opening(HORIZONTAL))
    live = {str(entity.dxf.handle) for entity in doc.modelspace()}
    assert set(record.handles()) == live


def test_write_readback_types_are_all_lines() -> None:
    doc = _new_doc()
    record = write_opening(doc, make_opening(HORIZONTAL))
    assert {snap.entity_type for snap in record.snapshots} == {"LINE"}


def test_write_readback_layers_match_the_slot_plan() -> None:
    doc = _new_doc()
    record = write_opening(doc, make_opening(HORIZONTAL))
    assert [snap.layer for snap in record.snapshots] == [
        "TEMP-OPENING-BND",
        "TEMP-OPENING-BND",
        "TEMP-OPENING-SYM",
        "TEMP-OPENING-SYM",
        "TEMP-OPENING-SYM",
        "TEMP-OPENING-SYM",
    ]


def test_write_readback_coordinates_match_the_plan() -> None:
    doc = _new_doc()
    record = write_opening(doc, make_opening(HORIZONTAL, 2000))
    by_role = dict(zip([spec.role for spec in record.geometry.entities], record.snapshots))
    edge = by_role["edge_start"]
    assert edge.geometry["start"] == pytest.approx([0.0, -50.0])
    assert edge.geometry["end"] == pytest.approx([0.0, 50.0])


def test_write_readback_preserves_the_oblique_geometry() -> None:
    doc = _new_doc()
    record = write_opening(doc, make_opening(OBLIQUE))
    face = record.snapshots[2]
    assert face.geometry["end"][0] > face.geometry["start"][0]
    assert face.geometry["end"][1] > face.geometry["start"][1]


def test_write_creates_the_missing_layer_entries() -> None:
    doc = _new_doc()
    write_opening(doc, make_opening(HORIZONTAL))
    for name in DEFAULT_LAYERS:
        assert doc.layers.has_entry(name)


def test_write_does_not_insert_hatch_or_subtract_anything() -> None:
    doc = _new_doc()
    write_opening(doc, make_opening(HORIZONTAL))
    kinds = {entity.dxftype() for entity in doc.modelspace()}
    assert kinds == {"LINE"}
    assert "INSERT" not in kinds
    assert "HATCH" not in kinds


def test_write_records_the_document_version() -> None:
    doc = _new_doc()
    record = write_opening(doc, make_opening(HORIZONTAL))
    assert record.document_id
    assert DXF_VERSION_NAME == "R2018"
    assert str(doc.dxfversion) == DXF_VERSION


def test_custom_layers_are_honoured_on_write() -> None:
    doc = _new_doc()
    record = write_opening(doc, make_opening(HORIZONTAL), layers=("A", "B"))
    assert record.layers == ("A", "B")
    assert {snap.layer for snap in record.snapshots} == {"A", "B"}


def test_wrong_layer_tuple_length_is_rejected() -> None:
    with pytest.raises(OpeningGeometryError, match="exactly"):
        write_opening(_new_doc(), make_opening(HORIZONTAL), layers=("TEMP-A",))


def test_blank_layer_name_is_rejected() -> None:
    with pytest.raises(OpeningGeometryError, match="non-empty"):
        write_opening(_new_doc(), make_opening(HORIZONTAL), layers=(" ", "TEMP-B"))


def test_readback_snapshots_helper_matches_the_record() -> None:
    doc = _new_doc()
    record = write_opening(doc, make_opening(HORIZONTAL))
    again = readback_snapshots(doc, record.handles(), record.document_id)
    assert [snap.digest() for snap in again] == [
        snap.digest() for snap in record.snapshots
    ]


def test_a_second_write_appends_and_does_not_replace() -> None:
    doc = _new_doc()
    first = write_opening(doc, make_opening(HORIZONTAL))
    second = write_opening(doc, make_opening(VERTICAL))
    assert len(list(doc.modelspace())) == 12
    assert not set(first.handles()) & set(second.handles())


def test_snapshot_diff_of_a_rewritten_opening_is_empty() -> None:
    doc = _new_doc()
    record = write_opening(doc, make_opening(HORIZONTAL))
    result = diff_snapshots(
        record.snapshots,
        record.snapshots,
        document_id=record.document_id,
        before_revision=1,
        after_revision=1,
    )
    assert result.changed is False
    assert all(item.kind is DiffKind.UNCHANGED for item in result.entities)


def test_snapshot_diff_reports_a_second_opening_as_added() -> None:
    doc = _new_doc()
    first = write_opening(doc, make_opening(HORIZONTAL))
    before = list(first.snapshots)
    second = write_opening(doc, make_opening(VERTICAL))
    result = diff_snapshots(
        before,
        list(first.snapshots) + list(second.snapshots),
        document_id=first.document_id,
        before_revision=1,
        after_revision=2,
    )
    assert result.changed is True
    assert sum(item.kind is DiffKind.ADDED for item in result.entities) == 6
