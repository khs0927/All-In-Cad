from all_in_cad.architecture import (
    infer_annotation_bindings,
    infer_opening_hosts,
    infer_room_adjacency,
    infer_rooms,
)
from all_in_cad.readback import EntitySnapshot
from all_in_cad.semantic_layers import LayerSemantic


def line(handle: str, start: tuple[float, float], end: tuple[float, float]) -> EntitySnapshot:
    return EntitySnapshot(
        document_id="doc",
        handle=handle,
        entity_type="LINE",
        layer="WAL1",
        geometry={"start": list(start), "end": list(end)},
    )


def test_two_rectangles_become_two_adjacent_rooms() -> None:
    entities = [
        line("1", (0, 0), (1000, 0)),
        line("2", (1000, 0), (2000, 0)),
        line("3", (2000, 0), (2000, 1000)),
        line("4", (2000, 1000), (1000, 1000)),
        line("5", (1000, 1000), (0, 1000)),
        line("6", (0, 1000), (0, 0)),
        line("7", (1000, 0), (1000, 1000)),
    ]
    rooms = infer_rooms(entities, minimum_area=100)
    assert len(rooms) == 2
    assert sorted(room.area for room in rooms) == [1_000_000, 1_000_000]

    adjacency = infer_room_adjacency(rooms)
    assert len(adjacency) == 1
    assert adjacency[0].shared_length == 1000


def test_opening_is_bound_to_nearest_wall() -> None:
    entities = [
        line("A", (0, 0), (1000, 0)),
        line("B", (0, 500), (1000, 500)),
        EntitySnapshot(
            document_id="doc",
            handle="D1",
            entity_type="INSERT",
            layer="DOOR",
            geometry={"insertion_point": [500, 25]},
        ),
    ]
    relations = infer_opening_hosts(entities, max_distance=100)
    assert len(relations) == 1
    assert relations[0].opening_handle == "D1"
    assert relations[0].wall_handle == "A"
    assert relations[0].opening_semantic == LayerSemantic.DOOR
    assert relations[0].distance == 25


def test_dimension_binding_uses_only_explicit_target_handles() -> None:
    entities = [
        EntitySnapshot(
            document_id="doc",
            handle="D10",
            entity_type="DIMENSION",
            layer="DIM",
            properties={"target_handles": ["a", "B", "a"]},
        ),
        EntitySnapshot(
            document_id="doc",
            handle="D11",
            entity_type="DIMENSION",
            layer="DIM",
            properties={"text": "1000"},
        ),
    ]
    bindings = infer_annotation_bindings(entities)
    assert len(bindings) == 1
    assert bindings[0].annotation_handle == "D10"
    assert bindings[0].target_handles == ("A", "B")
