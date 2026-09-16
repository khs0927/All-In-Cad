from all_in_cad.readback import EntitySnapshot
from all_in_cad.semantic_graph import EdgeKind, build_semantic_graph


def line(
    handle: str,
    start: list[float],
    end: list[float],
    *,
    layer: str = "WAL1",
) -> EntitySnapshot:
    return EntitySnapshot(
        document_id="doc",
        handle=handle,
        entity_type="LINE",
        layer=layer,
        geometry={"start": start, "end": end},
    )


def test_snap_connects_near_touching_lines() -> None:
    graph = build_semantic_graph(
        [line("A", [0, 0], [100, 0], layer="CEN"), line("B", [100.4, 0], [200, 0], layer="CEN")],
        snap_tolerance=1.0,
    )
    touches = graph.edges_of_kind(EdgeKind.TOUCHES)
    assert len(touches) == 1


def test_parallel_wall_lines_form_pair() -> None:
    graph = build_semantic_graph(
        [line("A", [0, 0], [3000, 0]), line("B", [0, 200], [3000, 200])]
    )
    pairs = graph.edges_of_kind(EdgeKind.WALL_PAIR)
    assert len(pairs) == 1
    assert pairs[0].attributes["distance"] == 200.0
    assert pairs[0].attributes["overlap"] == 3000.0


def test_orthogonal_wall_is_not_paired() -> None:
    graph = build_semantic_graph(
        [line("A", [0, 0], [3000, 0]), line("B", [1500, -1000], [1500, 1000])]
    )
    assert graph.edges_of_kind(EdgeKind.WALL_PAIR) == []
