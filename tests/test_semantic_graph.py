from all_in_cad.readback import EntitySnapshot
from all_in_cad.semantic_graph import EdgeKind, NodeKind, build_semantic_graph


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


def test_lwpolyline_width_and_bulge_payload_still_produces_vertices() -> None:
    polyline = EntitySnapshot(
        document_id="doc",
        handle="P1",
        entity_type="LWPOLYLINE",
        layer="WAL1",
        geometry={
            "points": [
                [0.0, 0.0, 0.0, 0.0, 0.0],
                [1000.0, 0.0, 0.0, 0.0, 0.25],
                [1000.0, 1000.0, 0.0, 0.0, 0.0],
            ],
            "closed": False,
        },
    )
    graph = build_semantic_graph([polyline], snap_tolerance=1.0)
    vertices = [node for node in graph.nodes if node.kind == NodeKind.VERTEX]
    assert len(vertices) == 3
