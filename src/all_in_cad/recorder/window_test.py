"""Tests for the window recorder (wall.py / door.py conventions followed).

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
from all_in_cad.recorder.layer import layer_config_path
from all_in_cad.recorder.window import (
    DEFAULT_DIVISIONS,
    DEFAULT_LAYERS,
    DEFAULT_THICKNESS_MM,
    DEFAULT_WINDOW_WIDTH_MM,
    DXF_VERSION,
    DXF_VERSION_NAME,
    WindowGeometryError,
    make_window,
    normalize_deg,
    readback_snapshots,
    signed_segment_offset,
    write_window,
)
from all_in_cad.semantic_layers import LayerSemantic, classify_layer
from all_in_cad.topology import Point2D


def record_id(record) -> str:
    return record.document_id


def _layer_config() -> pathlib.Path:
    """The repo's layer table, or an explicit skip.

    This test asserts what ``configs/architectural-layers.json`` contains. The
    path is found by walking upwards rather than by counting parents, so a
    checkout at a different depth -- or an installed package with no repo
    above it at all -- skips with a reason instead of failing somewhere that
    looks like a broken test.
    """
    found = layer_config_path()
    if found is None:
        pytest.skip(
            "configs/architectural-layers.json not found at or above "
            f"{pathlib.Path(__file__).resolve()}"
        )
    return found


def _new_doc() -> ezdxf.document.Drawing:
    return ezdxf.new(DXF_VERSION, setup=True)


def _spec(window, role):
    return next(spec for spec in window.entities if spec.role == role)


def _roles(window):
    return [spec.role for spec in window.entities]


# --- (1) defaults: the observed prompt default of 1500 mm --------------------


def test_default_width_is_the_observed_1500() -> None:
    assert DEFAULT_WINDOW_WIDTH_MM == 1500.0


def test_default_call_uses_the_observed_width() -> None:
    window = make_window((0, 0), (1000, 0))
    assert window.width_mm == 1500.0


def test_default_call_spans_1500_across_the_width_axis() -> None:
    window = make_window((0, 0), (1000, 0))
    assert window.jamb_start.x == pytest.approx(-750.0)
    assert window.jamb_end.x == pytest.approx(750.0)
    assert window.center.x == pytest.approx(0.0)


def test_default_thickness_and_divisions_are_design_constants() -> None:
    window = make_window((0, 0), (1000, 0))
    assert window.thickness_mm == DEFAULT_THICKNESS_MM
    assert window.divisions == DEFAULT_DIVISIONS
    # explicit design, not a measurement
    assert DEFAULT_THICKNESS_MM == 100.0
    assert DEFAULT_DIVISIONS == 1


def test_default_entity_count_is_five_plus_one_per_division() -> None:
    assert len(make_window((0, 0), (1000, 0)).entities) == 6
    assert len(make_window((0, 0), (1000, 0), divisions=3).entities) == 8


def test_default_entity_types_are_line_and_arc_only() -> None:
    window = make_window((0, 0), (1000, 0))
    assert {spec.dxftype for spec in window.entities} == {"LINE", "ARC"}


def test_default_roles_are_the_documented_five() -> None:
    window = make_window((0, 0), (1000, 0))
    assert _roles(window) == [
        "jamb_start",
        "jamb_end",
        "glazing",
        "casement_arc",
        "interior_face",
        "mullion_1",
    ]


def test_no_insert_and_no_hatch_in_the_composition() -> None:
    window = make_window((0, 0), (1000, 0))
    assert all(spec.dxftype in ("LINE", "ARC") for spec in window.entities)
    assert not any("INSERT" in spec.role.upper() for spec in window.entities)
    assert not any("HATCH" in spec.role.upper() for spec in window.entities)


def test_handle_hint_is_stable_text() -> None:
    hint = make_window((0, 0), (1000, 0)).handle_hint
    assert hint == make_window((0, 0), (1000, 0)).handle_hint
    assert hint.startswith("window@0.000,0.000/w1500.000")


# --- (2) layers follow configs/architectural-layers.json ---------------------


def test_default_layers_are_the_observed_window_layers() -> None:
    assert DEFAULT_LAYERS == ("WIN", "WINBAR", "WINELE")


def test_default_layers_all_exist_in_the_layer_config() -> None:
    table = json.loads(_layer_config().read_text(encoding="utf-8"))["exact"]
    for name in DEFAULT_LAYERS:
        assert name in table, f"{name} is not an observed project layer"


def test_default_layers_classify_to_window_semantics() -> None:
    assert classify_layer("WIN") is LayerSemantic.WINDOW
    assert classify_layer("WINBAR") is LayerSemantic.WINDOW_BAR
    assert classify_layer("WINELE") is LayerSemantic.WINDOW


def test_slot_to_layer_mapping_is_win_winbar_winele() -> None:
    window = make_window((0, 0), (1000, 0))
    assert [spec.layer_slot for spec in window.entities] == [
        "window",
        "window",
        "window",
        "window",
        "element",
        "bar",
    ]


def test_specs_for_slot_partitions_the_entities() -> None:
    window = make_window((0, 0), (1000, 0), divisions=2)
    assert len(window.specs_for_slot("bar")) == 2
    assert len(window.specs_for_slot("element")) == 1
    assert len(window.specs_for_slot("window")) == 4
    assert len(window.specs()) == 7


def test_spec_lookup_by_role_returns_the_arc() -> None:
    window = make_window((0, 0), (1000, 0))
    assert window.spec("casement_arc").dxftype == "ARC"
    assert window.specs() == window.entities


# --- (3) angle cases ----------------------------------------------------------


def test_horizontal_axis_keeps_jambs_on_the_x_line() -> None:
    window = make_window((0, 0), (1000, 0))
    assert window.jamb_start.y == pytest.approx(0.0)
    assert window.jamb_end.y == pytest.approx(0.0)
    assert window.width_axis_deg == pytest.approx(0.0)


def test_vertical_axis_swaps_the_roles_of_x_and_y() -> None:
    window = make_window((0, 0), (0, 1000))
    assert window.jamb_start.x == pytest.approx(0.0)
    assert window.jamb_start.y == pytest.approx(-750.0)
    assert window.jamb_end.y == pytest.approx(750.0)
    assert window.width_axis_deg == pytest.approx(90.0)


def test_oblique_axis_keeps_the_1500_width_in_euclidean_terms() -> None:
    window = make_window((0, 0), (1000, 1000))
    assert math.dist(
        (window.jamb_start.x, window.jamb_start.y),
        (window.jamb_end.x, window.jamb_end.y),
    ) == pytest.approx(1500.0)
    assert window.width_axis_deg == pytest.approx(45.0)


def test_reversed_pick_order_flips_the_axis_but_not_the_window() -> None:
    forward = make_window((0, 0), (1000, 0))
    reverse = make_window((0, 0), (-1000, 0))
    assert forward.width_axis_deg == pytest.approx(0.0)
    assert reverse.width_axis_deg == pytest.approx(180.0)
    # both windows are centred on the same pick and 1500 mm long
    assert forward.jamb_start.x == pytest.approx(reverse.jamb_end.x)
    assert forward.jamb_end.x == pytest.approx(reverse.jamb_start.x)


def test_oblique_window_stays_parallel_to_its_width_axis() -> None:
    window = make_window((0, 0), (1000, 1000))
    glazing = window.spec("glazing")
    dx = glazing.end.x - glazing.start.x
    dy = glazing.end.y - glazing.start.y
    assert dy / dx == pytest.approx(1.0)


def test_width_axis_is_normalised_into_zero_360() -> None:
    assert normalize_deg(-45.0) == pytest.approx(315.0)
    assert normalize_deg(720.0) == pytest.approx(0.0)
    assert normalize_deg(-0.0) == 0.0


# --- (4) axis-aligned wall symmetry ------------------------------------------


def test_horizontal_wall_is_symmetric_about_the_pick() -> None:
    window = make_window((3000, 0), (9000, 0))
    assert window.jamb_start.x == pytest.approx(3000 - 750)
    assert window.jamb_end.x == pytest.approx(3000 + 750)
    assert window.jamb_start.x + window.jamb_end.x == pytest.approx(6000)


def test_vertical_wall_is_symmetric_about_the_pick() -> None:
    window = make_window((0, 3000), (0, 9000))
    assert window.jamb_start.y == pytest.approx(2250.0)
    assert window.jamb_end.y == pytest.approx(3750.0)
    assert window.jamb_start.y + window.jamb_end.y == pytest.approx(6000)


def test_oblique_wall_is_symmetric_about_the_pick() -> None:
    window = make_window((0, 0), (1000, 500))
    midpoint = (
        (window.jamb_start.x + window.jamb_end.x) / 2.0,
        (window.jamb_start.y + window.jamb_end.y) / 2.0,
    )
    assert midpoint == pytest.approx((0.0, 0.0))


def test_jamb_edges_span_the_full_thickness() -> None:
    window = make_window((0, 0), (1000, 0))
    jamb = window.spec("jamb_start")
    span = math.dist((jamb.start.x, jamb.start.y), (jamb.end.x, jamb.end.y))
    assert span == pytest.approx(DEFAULT_THICKNESS_MM)


def test_mirroring_the_pick_across_the_wall_mirrors_the_interior_side() -> None:
    left = make_window((0, 50), (1000, 50), wall_segment=((0, 0), (1000, 0)))
    right = make_window((0, -50), (1000, -50), wall_segment=((0, 0), (1000, 0)))
    assert left.interior_side == "left"
    assert right.interior_side == "right"
    assert left.spec("interior_face").start.y == pytest.approx(100.0)
    assert right.spec("interior_face").start.y == pytest.approx(-100.0)


def test_arc_radius_always_equals_the_window_width() -> None:
    arc = make_window((0, 0), (1000, 0)).spec("casement_arc")
    assert arc.radius == pytest.approx(1500.0)


def test_arc_start_and_end_degrees_are_never_equal() -> None:
    for degrees in (10, 45, 90, 179, 359):
        arc = make_window((0, 0), (1000, 0), casement_deg=degrees).spec("casement_arc")
        assert arc.start_deg != arc.end_deg
        assert 0.0 <= arc.start_deg < 360.0
        assert 0.0 <= arc.end_deg < 360.0


def test_casement_sweep_appears_in_the_arc_angles() -> None:
    arc = make_window((0, 0), (1000, 0), casement_deg=45).spec("casement_arc")
    assert normalize_deg(arc.end_deg - arc.start_deg) == pytest.approx(45.0)


# --- (5) the indoor point decides the side ------------------------------------


def test_indoor_point_on_the_positive_side_selects_left() -> None:
    window = make_window((500, 100), (1500, 100), wall_segment=((0, 0), (1000, 0)))
    assert window.interior_side == "left"


def test_indoor_point_on_the_negative_side_selects_right() -> None:
    window = make_window((500, -100), (1500, -100), wall_segment=((0, 0), (1000, 0)))
    assert window.interior_side == "right"


def test_indoor_side_flips_the_casement_arc_direction() -> None:
    left = make_window((0, 100), (1000, 100), wall_segment=((0, 0), (1000, 0)))
    right = make_window((0, -100), (1000, -100), wall_segment=((0, 0), (1000, 0)))
    left_arc = left.spec("casement_arc")
    right_arc = right.spec("casement_arc")
    assert normalize_deg(left_arc.end_deg - left_arc.start_deg) == pytest.approx(90.0)
    assert normalize_deg(right_arc.end_deg - right_arc.start_deg) == pytest.approx(270.0)


def test_explicit_interior_side_overrides_nothing_but_is_recorded() -> None:
    window = make_window((0, 0), (1000, 0), interior_side="right")
    assert window.interior_side == "right"
    assert window.warnings == ()


def test_missing_wall_segment_records_an_interior_side_warning() -> None:
    window = make_window((0, 0), (1000, 0))
    assert any("interior_side" in item for item in window.warnings)
    assert window.interior_side == "left"


def test_indoor_point_far_from_the_wall_warns_but_keeps_the_pick() -> None:
    window = make_window((500, 400), (1500, 400), wall_segment=((0, 0), (1000, 0)))
    assert any("off the wall centreline" in item for item in window.warnings)
    assert window.interior_side == "left"


def test_signed_segment_offset_uses_the_infinite_line() -> None:
    # the pick is far beyond the segment end but still on its line
    offset = signed_segment_offset(
        Point2D(5000, 10), Point2D(0, 0), Point2D(100, 0), Point2D(0, 1)
    )
    assert offset == pytest.approx(10.0)


def test_signed_segment_offset_is_signed() -> None:
    offset = signed_segment_offset(
        Point2D(5000, -10), Point2D(0, 0), Point2D(100, 0), Point2D(0, 1)
    )
    assert offset == pytest.approx(-10.0)


def test_signed_segment_offset_handles_a_zero_length_segment() -> None:
    offset = signed_segment_offset(
        Point2D(3, 4), Point2D(0, 0), Point2D(0, 0), Point2D(0, 1)
    )
    assert offset == pytest.approx(5.0)


# --- (6) mullions -------------------------------------------------------------


def test_one_division_puts_the_bar_at_the_middle() -> None:
    bar = make_window((0, 0), (1000, 0), divisions=1).spec("mullion_1")
    assert bar.start.x == pytest.approx(0.0)
    assert bar.end.x == pytest.approx(0.0)


def test_two_divisions_split_the_width_into_three_equal_bays() -> None:
    window = make_window((0, 0), (1000, 0), divisions=2)
    first = window.spec("mullion_1").start.x
    second = window.spec("mullion_2").start.x
    # 1500 wide, 2 bars -> three 500 mm bays
    assert first == pytest.approx(-250.0)
    assert second == pytest.approx(250.0)


def test_mullion_bars_span_the_thickness() -> None:
    bar = make_window((0, 0), (1000, 0), divisions=1).spec("mullion_1")
    assert math.dist((bar.start.x, bar.start.y), (bar.end.x, bar.end.y)) == pytest.approx(
        DEFAULT_THICKNESS_MM
    )


# --- (7) bad input is rejected ------------------------------------------------


def test_zero_width_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="width_mm must be > 0"):
        make_window((0, 0), (1000, 0), 0)


def test_negative_width_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="width_mm must be > 0"):
        make_window((0, 0), (1000, 0), -10)


def test_zero_thickness_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="thickness_mm must be > 0"):
        make_window((0, 0), (1000, 0), thickness_mm=0)


def test_coincident_picks_are_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="must differ"):
        make_window((0, 0), (0, 0))


def test_zero_divisions_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="divisions must be >= 1"):
        make_window((0, 0), (1000, 0), divisions=0)


def test_non_integer_divisions_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="divisions must be an int"):
        make_window((0, 0), (1000, 0), divisions=1.5)


def test_bad_interior_side_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="interior_side"):
        make_window((0, 0), (1000, 0), interior_side="up")


def test_degenerate_wall_segment_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="wall_segment endpoints must differ"):
        make_window((0, 0), (1000, 0), wall_segment=((0, 0), (0, 0)))


def test_negative_tolerance_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="indoor_tolerance"):
        make_window((0, 0), (1000, 0), indoor_tolerance=-1)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_width_is_rejected(bad: float) -> None:
    with pytest.raises(WindowGeometryError, match="width_mm must be a finite"):
        make_window((0, 0), (1000, 0), bad)


def test_non_finite_point_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="indoor_point.x"):
        make_window((float("nan"), 0), (1000, 0))


def test_short_point_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="indoor_point must be"):
        make_window((1.0,), (1000, 0))


def test_zero_casement_sweep_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="casement_deg"):
        make_window((0, 0), (1000, 0), casement_deg=0)


def test_full_circle_casement_sweep_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="casement_deg"):
        make_window((0, 0), (1000, 0), casement_deg=360)


# --- (8) ezdxf round trip ------------------------------------------------------


def test_write_creates_every_entity_once() -> None:
    doc = _new_doc()
    window = make_window((0, 0), (1000, 0))
    record = write_window(doc, window)
    assert len(record.entity_handles) == len(window.entities) == 6
    assert len(set(record.entity_handles)) == 6
    assert len(list(doc.modelspace())) == 6


def test_write_readback_handles_match_the_live_document() -> None:
    doc = _new_doc()
    record = write_window(doc, make_window((0, 0), (1000, 0)))
    live = {str(entity.dxf.handle) for entity in doc.modelspace()}
    assert set(record.handles()) == live


def test_write_readback_types_are_line_and_arc() -> None:
    doc = _new_doc()
    record = write_window(doc, make_window((0, 0), (1000, 0)))
    assert [snap.entity_type for snap in record.snapshots].count("ARC") == 1
    assert [snap.entity_type for snap in record.snapshots].count("LINE") == 5


def test_write_readback_layers_match_the_slot_plan() -> None:
    doc = _new_doc()
    record = write_window(doc, make_window((0, 0), (1000, 0), divisions=2))
    layers = [snap.layer for snap in record.snapshots]
    assert layers == ["WIN", "WIN", "WIN", "WIN", "WINELE", "WINBAR", "WINBAR"]


def test_write_readback_coordinates_match_the_plan() -> None:
    doc = _new_doc()
    record = write_window(doc, make_window((0, 0), (1000, 0)))
    by_role = dict(
        zip([spec.role for spec in record.geometry.entities], record.snapshots, strict=False)
    )
    jamb = by_role["jamb_start"]
    assert jamb.geometry["start"] == pytest.approx([-750.0, -50.0])
    assert jamb.geometry["end"] == pytest.approx([-750.0, 50.0])


def test_write_readback_arc_geometry_survives() -> None:
    doc = _new_doc()
    record = write_window(doc, make_window((0, 0), (1000, 0)))
    arc = record.snapshots[3]
    assert arc.entity_type == "ARC"
    assert arc.geometry["radius"] == pytest.approx(1500.0)
    assert arc.geometry["start_angle"] == pytest.approx(0.0, abs=1e-6)
    assert arc.geometry["end_angle"] == pytest.approx(90.0, abs=1e-6)


def test_write_creates_the_missing_layer_entries() -> None:
    doc = _new_doc()
    write_window(doc, make_window((0, 0), (1000, 0)))
    for name in DEFAULT_LAYERS:
        assert doc.layers.has_entry(name)


def test_write_does_not_insert_or_hatch() -> None:
    doc = _new_doc()
    write_window(doc, make_window((0, 0), (1000, 0)))
    kinds = {entity.dxftype() for entity in doc.modelspace()}
    assert kinds == {"LINE", "ARC"}
    assert "INSERT" not in kinds
    assert "HATCH" not in kinds


def test_write_records_the_document_version() -> None:
    doc = _new_doc()
    record = write_window(doc, make_window((0, 0), (1000, 0)))
    assert record.document_id
    assert DXF_VERSION_NAME == "R2018"
    assert str(doc.dxfversion) == DXF_VERSION


def test_custom_layers_are_honoured_on_write() -> None:
    doc = _new_doc()
    record = write_window(doc, make_window((0, 0), (1000, 0)), layers=("W1", "W2", "W3"))
    assert record.layers == ("W1", "W2", "W3")
    assert {snap.layer for snap in record.snapshots} == {"W1", "W2", "W3"}


def test_wrong_layer_tuple_length_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="exactly"):
        write_window(_new_doc(), make_window((0, 0), (1000, 0)), layers=("WIN",))


def test_blank_layer_name_is_rejected() -> None:
    with pytest.raises(WindowGeometryError, match="non-empty"):
        write_window(
            _new_doc(), make_window((0, 0), (1000, 0)), layers=("WIN", " ", "WINELE")
        )


def test_readback_snapshots_helper_matches_the_record() -> None:
    doc = _new_doc()
    record = write_window(doc, make_window((0, 0), (1000, 0)))
    again = readback_snapshots(doc, record.handles(), record.document_id)
    assert [snap.digest() for snap in again] == [
        snap.digest() for snap in record.snapshots
    ]


def test_a_second_write_appends_and_does_not_replace() -> None:
    doc = _new_doc()
    first = write_window(doc, make_window((0, 0), (1000, 0)))
    second = write_window(doc, make_window((4000, 0), (5000, 0)))
    assert len(list(doc.modelspace())) == 12
    assert not set(first.handles()) & set(second.handles())


def test_snapshot_diff_of_a_rewritten_window_is_empty() -> None:
    doc = _new_doc()
    record = write_window(doc, make_window((0, 0), (1000, 0)))
    result = diff_snapshots(
        record.snapshots,
        record.snapshots,
        document_id=record.document_id,
        before_revision=1,
        after_revision=1,
    )
    assert result.changed is False
    assert all(item.kind is DiffKind.UNCHANGED for item in result.entities)


def test_snapshot_diff_reports_a_second_window_as_added() -> None:
    doc = _new_doc()
    first = write_window(doc, make_window((0, 0), (1000, 0)))
    before = list(first.snapshots)
    second = write_window(doc, make_window((4000, 0), (5000, 0)))
    result = diff_snapshots(
        before,
        list(first.snapshots) + list(second.snapshots),
        document_id=record_id(first),
        before_revision=1,
        after_revision=2,
    )
    assert result.changed is True
    assert sum(item.kind is DiffKind.ADDED for item in result.entities) == 6
