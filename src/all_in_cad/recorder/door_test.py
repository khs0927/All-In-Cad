"""Tests for the door recorder.

ENVIRONMENT NOTE (host trap): this host injects PYTHONHOME, which hides the
standard library and breaks ``import ezdxf`` with
``ModuleNotFoundError: No module named 'annotationlib'``. Run with::

    $env:PYTHONHOME=$null; $env:PYTHONPATH=$null
    & C:\\Users\\khs09\\all-in-cad\\.venv\\Scripts\\python.exe -m pytest ...
"""

from __future__ import annotations

import math

import ezdxf
import pytest

from all_in_cad.architecture import infer_opening_hosts
from all_in_cad.readback import DiffKind, EntitySnapshot, diff_snapshots
from all_in_cad.recorder.door import (
    DEFAULT_LAYERS,
    DXF_VERSION,
    WIDTH_PRESET_MM,
    DoorGeometryError,
    make_door,
    make_door_centered,
    normalize_deg,
    readback_snapshots,
    write_door,
)
from all_in_cad.semantic_layers import LayerSemantic, classify_layer

GOAL_CENTER_X = 6000.0
GOAL_WIDTH = 900.0
GOAL_HINGE_X = 5550.0
GOAL_LATCH_X = 6450.0


def _new_doc() -> "ezdxf.document.Drawing":
    return ezdxf.new(DXF_VERSION, setup=True)


def _spec(door, role):
    return next(spec for spec in door.entities if spec.role == role)


# --- (3) the goal case: centre X=6000, width 900 -----------------------------


def test_goal_case_hinge_is_half_width_left_of_centre() -> None:
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, side="left")
    assert door.hinge.x == pytest.approx(GOAL_HINGE_X)
    assert door.hinge.y == pytest.approx(0.0)
    assert door.latch.x == pytest.approx(GOAL_LATCH_X)
    assert door.center.x == pytest.approx(GOAL_CENTER_X)


def test_goal_case_opening_spans_5550_to_6450() -> None:
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, side="left")
    hinge_edge = _spec(door, "opening_edge_hinge")
    latch_edge = _spec(door, "opening_edge_latch")
    assert hinge_edge.start.x == pytest.approx(GOAL_HINGE_X)
    assert latch_edge.start.x == pytest.approx(GOAL_LATCH_X)
    span = latch_edge.start.x - hinge_edge.start.x
    assert span == pytest.approx(GOAL_WIDTH)
    assert (hinge_edge.start.x + latch_edge.start.x) / 2 == pytest.approx(GOAL_CENTER_X)


def test_goal_case_right_side_is_mirrored() -> None:
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, side="right")
    assert door.hinge.x == pytest.approx(GOAL_LATCH_X)
    assert door.latch.x == pytest.approx(GOAL_HINGE_X)
    assert door.center.x == pytest.approx(GOAL_CENTER_X)


def test_goal_case_geometry_is_finite_and_ordered() -> None:
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH)
    arc = _spec(door, "swing_arc")
    assert arc.radius == pytest.approx(GOAL_WIDTH)
    assert arc.start_deg != arc.end_deg
    for value in (arc.start_deg, arc.end_deg):
        assert 0.0 <= value < 360.0
    leaf = _spec(door, "leaf")
    assert math.dist((leaf.start.x, leaf.start.y), (leaf.end.x, leaf.end.y)) == (
        pytest.approx(GOAL_WIDTH)
    )


# --- (4) angle handling -------------------------------------------------------


@pytest.mark.parametrize("swing", [1.0, 30.0, 90.0, 180.0, 270.0, 359.0])
def test_sweeps_produce_valid_arc_angles(swing: float) -> None:
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, swing_deg=swing)
    arc = _spec(door, "swing_arc")
    assert 0.0 <= arc.start_deg < 360.0
    assert 0.0 <= arc.end_deg < 360.0
    assert arc.start_deg != arc.end_deg
    assert arc.center == door.hinge


def test_closed_direction_uses_zero_degrees_by_default() -> None:
    """The '0 degree' case is the arc *start* angle, not a zero sweep."""
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, swing_deg=90.0)
    arc = _spec(door, "swing_arc")
    assert arc.start_deg == pytest.approx(0.0)
    assert arc.end_deg == pytest.approx(90.0)
    assert door.closed_deg == pytest.approx(0.0)


def test_swing_90_points_leaf_up_and_180_swings_past_the_hinge() -> None:
    up = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, swing_deg=90.0)
    assert (up.leaf_tip.x, up.leaf_tip.y) == pytest.approx((GOAL_HINGE_X, 900.0))

    # 180 deg rotates the closed +X direction onto -X, so the open leaf points
    # back along the wall past the hinge, not towards the latch.
    down = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, swing_deg=180.0)
    assert (down.leaf_tip.x, down.leaf_tip.y) == pytest.approx((4650.0, 0.0))
    assert down.arc_end_deg == pytest.approx(180.0)

    left = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, swing_deg=270.0)
    assert (left.leaf_tip.x, left.leaf_tip.y) == pytest.approx((GOAL_HINGE_X, -900.0))


def test_degenerate_sweeps_are_rejected_not_silently_dropped() -> None:
    for swing in (0.0, 360.0, -90.0, 450.0, float("nan"), float("inf")):
        with pytest.raises(DoorGeometryError):
            make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, swing_deg=swing)


def test_normalize_deg_folds_into_zero_to_360() -> None:
    assert normalize_deg(0.0) == 0.0
    assert normalize_deg(360.0) == 0.0
    assert normalize_deg(-90.0) == pytest.approx(270.0)
    assert normalize_deg(725.0) == pytest.approx(5.0)


# --- (5) validation ------------------------------------------------------------


@pytest.mark.parametrize("width", [0.0, -1.0, -900.0, float("nan")])
def test_non_positive_width_is_rejected(width: float) -> None:
    with pytest.raises(DoorGeometryError):
        make_door_centered(GOAL_CENTER_X, 0.0, width)
    with pytest.raises(DoorGeometryError):
        make_door((GOAL_HINGE_X, 0.0), width)


@pytest.mark.parametrize("thickness", [0.0, -5.0])
def test_non_positive_thickness_is_rejected(thickness: float) -> None:
    with pytest.raises(DoorGeometryError):
        make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, thickness_mm=thickness)


def test_negative_frame_width_is_rejected() -> None:
    with pytest.raises(DoorGeometryError):
        make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, frame_width_mm=-1.0)


def test_unknown_side_is_rejected() -> None:
    with pytest.raises(DoorGeometryError):
        make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, side="middle")


def test_hinge_off_the_wall_centreline_warns_but_still_builds() -> None:
    wall = ((5000.0, 0.0), (7000.0, 0.0))
    on_wall = make_door((GOAL_HINGE_X, 0.0), GOAL_WIDTH, wall_segment=wall)
    assert on_wall.warnings == ()

    off_wall = make_door((GOAL_HINGE_X, 40.0), GOAL_WIDTH, wall_segment=wall)
    assert len(off_wall.warnings) == 1
    assert "wall centreline" in off_wall.warnings[0]
    assert off_wall.hinge.y == pytest.approx(40.0)
    assert len(off_wall.entities) == 6


def test_width_presets_are_accepted() -> None:
    for preset in WIDTH_PRESET_MM:
        door = make_door_centered(GOAL_CENTER_X, 0.0, float(preset))
        assert door.width_mm == pytest.approx(float(preset))


# --- (5) composition and layer assignment --------------------------------------


def test_composition_is_six_line_and_arc_entities_only() -> None:
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH)
    assert [spec.role for spec in door.entities] == [
        "opening_edge_hinge",
        "opening_edge_latch",
        "leaf",
        "swing_arc",
        "frame_face_hinge",
        "frame_face_latch",
    ]
    assert [spec.dxftype for spec in door.entities] == [
        "LINE",
        "LINE",
        "LINE",
        "ARC",
        "LINE",
        "LINE",
    ]


def test_layer_slots_split_door_and_door_ele() -> None:
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH)
    assert [spec.layer_slot for spec in door.entities] == [
        "door",
        "door",
        "door",
        "door",
        "frame",
        "frame",
    ]
    assert DEFAULT_LAYERS == ("DOOR", "DOOR_ELE")
    assert classify_layer("DOOR") is LayerSemantic.DOOR
    assert classify_layer("DOOR_ELE") is LayerSemantic.DOOR


def test_center_on_wall_straddles_the_centreline() -> None:
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH, thickness_mm=100.0)
    edge = _spec(door, "opening_edge_hinge")
    assert edge.start.y == pytest.approx(-50.0)
    assert edge.end.y == pytest.approx(50.0)


def test_flush_placement_is_one_sided() -> None:
    door = make_door_centered(
        GOAL_CENTER_X, 0.0, GOAL_WIDTH, thickness_mm=100.0, center_on_wall=False
    )
    edge = _spec(door, "opening_edge_hinge")
    assert edge.start.y == pytest.approx(0.0)
    assert edge.end.y == pytest.approx(100.0)


def test_frame_lines_stand_off_by_the_frame_width() -> None:
    door = make_door_centered(
        GOAL_CENTER_X, 0.0, GOAL_WIDTH, frame_width_mm=60.0
    )
    hinge_frame = _spec(door, "frame_face_hinge")
    latch_frame = _spec(door, "frame_face_latch")
    assert hinge_frame.start.x == pytest.approx(GOAL_HINGE_X - 60.0)
    assert latch_frame.start.x == pytest.approx(GOAL_LATCH_X + 60.0)


def test_rotated_wall_axis_is_respected() -> None:
    wall = ((6000.0, 0.0), (6000.0, 2000.0))
    door = make_door_centered(6000.0, 0.0, GOAL_WIDTH, wall_segment=wall, side="left")
    assert door.hinge.x == pytest.approx(6000.0)
    assert door.hinge.y == pytest.approx(-450.0)
    assert door.latch.y == pytest.approx(450.0)


# --- (5) ezdxf roundtrip -------------------------------------------------------


def test_write_door_emits_layers_handles_and_readback_snapshots() -> None:
    doc = _new_doc()
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH)
    record = write_door(doc, door)

    assert record.layers == ("DOOR", "DOOR_ELE")
    assert len(record.entity_handles) == 6
    assert all(handle and handle != "0" for handle in record.entity_handles)
    assert [snap.entity_type for snap in record.snapshots] == [
        "LINE",
        "LINE",
        "LINE",
        "ARC",
        "LINE",
        "LINE",
    ]
    assert [snap.layer for snap in record.snapshots] == [
        "DOOR",
        "DOOR",
        "DOOR",
        "DOOR",
        "DOOR_ELE",
        "DOOR_ELE",
    ]
    assert all(len(snap.digest()) == 64 for snap in record.snapshots)


def test_written_document_contains_no_insert_and_no_hatch() -> None:
    doc = _new_doc()
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH)
    write_door(doc, door)
    types = {entity.dxftype() for entity in doc.modelspace()}
    assert "INSERT" not in types
    assert "HATCH" not in types
    assert types == {"LINE", "ARC"}


def test_save_and_reload_keeps_geometry_identical(tmp_path) -> None:
    doc = _new_doc()
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH)
    record = write_door(doc, door)
    path = tmp_path / "goal_door.dxf"
    doc.saveas(path)

    reloaded = ezdxf.readfile(path)
    assert reloaded.dxfversion == DXF_VERSION == "AC1032"
    after = readback_snapshots(reloaded, record.entity_handles, record.document_id)

    result = diff_snapshots(
        list(record.snapshots),
        after,
        document_id=record.document_id,
        before_revision=0,
        after_revision=1,
    )
    assert result.changed is False
    assert all(item.kind is DiffKind.UNCHANGED for item in result.entities)


def test_reloaded_arc_survives_with_the_goal_radius() -> None:
    doc = _new_doc()
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH)
    record = write_door(doc, door)
    arc = record.snapshots[3]
    assert arc.entity_type == "ARC"
    assert arc.geometry["radius"] == pytest.approx(GOAL_WIDTH)
    assert arc.geometry["center"] == pytest.approx([GOAL_HINGE_X, 0.0], abs=1e-9)
    assert arc.geometry["start_angle"] == pytest.approx(0.0)
    assert arc.geometry["end_angle"] == pytest.approx(90.0)


# --- (5) topology fit ----------------------------------------------------------


def _wall_snapshots() -> list[EntitySnapshot]:
    return [
        EntitySnapshot(
            document_id="doc",
            handle="W1",
            entity_type="LINE",
            layer="WAL1",
            geometry={"start": [5000.0, 0.0], "end": [GOAL_HINGE_X, 0.0]},
        ),
        EntitySnapshot(
            document_id="doc",
            handle="W2",
            entity_type="LINE",
            layer="WAL1",
            geometry={"start": [GOAL_LATCH_X, 0.0], "end": [7000.0, 0.0]},
        ),
    ]


def test_door_entities_are_reachable_by_infer_opening_hosts() -> None:
    doc = _new_doc()
    door = make_door_centered(GOAL_CENTER_X, 0.0, GOAL_WIDTH)
    record = write_door(doc, door)

    relations = infer_opening_hosts(
        [*_wall_snapshots(), *record.snapshots], max_distance=500.0
    )
    assert len(relations) == 6
    assert {relation.opening_handle for relation in relations} == {
        handle.upper() for handle in record.entity_handles
    }
    assert all(
        relation.opening_semantic is LayerSemantic.DOOR for relation in relations
    )
    # The two opening edges, the arc centre and the frame faces sit on the wall.
    on_wall = [relation for relation in relations if relation.distance == pytest.approx(0.0)]
    assert len(on_wall) == 5
