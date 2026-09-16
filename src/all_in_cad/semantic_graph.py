from __future__ import annotations

import math
from collections import defaultdict
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .readback import EntitySnapshot
from .semantic_layers import LayerSemantic, classify_layer


class NodeKind(StrEnum):
    ENTITY = "entity"
    VERTEX = "vertex"


class EdgeKind(StrEnum):
    HAS_VERTEX = "has_vertex"
    TOUCHES = "touches"
    WALL_PAIR = "wall_pair"


class GraphNode(BaseModel):
    node_id: str
    kind: NodeKind
    attributes: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: EdgeKind
    attributes: dict[str, Any] = Field(default_factory=dict)


class SemanticGraph(BaseModel):
    document_id: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    def edges_of_kind(self, kind: EdgeKind) -> list[GraphEdge]:
        return [edge for edge in self.edges if edge.kind == kind]


def build_semantic_graph(
    entities: list[EntitySnapshot],
    *,
    snap_tolerance: float = 1.0,
    wall_parallel_tolerance_degrees: float = 3.0,
    wall_max_thickness: float = 500.0,
    wall_min_overlap: float = 100.0,
) -> SemanticGraph:
    if snap_tolerance <= 0:
        raise ValueError("snap_tolerance must be positive")
    if not entities:
        return SemanticGraph(document_id="unknown")

    document_ids = {item.document_id for item in entities}
    if len(document_ids) != 1:
        raise ValueError("semantic graph requires entities from exactly one document")
    document_id = next(iter(document_ids))

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    vertex_members: dict[tuple[int, int, int], list[str]] = defaultdict(list)

    for entity in entities:
        entity_id = f"entity:{entity.handle.upper()}"
        semantic = classify_layer(entity.layer)
        nodes.append(
            GraphNode(
                node_id=entity_id,
                kind=NodeKind.ENTITY,
                attributes={
                    "handle": entity.handle.upper(),
                    "entity_type": entity.entity_type,
                    "layer": entity.layer,
                    "semantic": semantic.value,
                },
            )
        )
        for point in _entity_points(entity):
            key = _snap_key(point, snap_tolerance)
            vertex_members[key].append(entity_id)

    for key, members in sorted(vertex_members.items()):
        vertex_id = f"vertex:{key[0]}:{key[1]}:{key[2]}"
        nodes.append(
            GraphNode(
                node_id=vertex_id,
                kind=NodeKind.VERTEX,
                attributes={
                    "position": [component * snap_tolerance for component in key],
                    "member_count": len(set(members)),
                },
            )
        )
        unique = sorted(set(members))
        for member in unique:
            edges.append(GraphEdge(source=member, target=vertex_id, kind=EdgeKind.HAS_VERTEX))
        for index, left in enumerate(unique):
            for right in unique[index + 1 :]:
                edges.append(GraphEdge(source=left, target=right, kind=EdgeKind.TOUCHES))

    walls = [
        entity
        for entity in entities
        if classify_layer(entity.layer) == LayerSemantic.WALL and _line_segment(entity) is not None
    ]
    for index, left in enumerate(walls):
        for right in walls[index + 1 :]:
            metrics = _wall_pair_metrics(
                left,
                right,
                wall_parallel_tolerance_degrees=wall_parallel_tolerance_degrees,
            )
            if metrics is None:
                continue
            distance, overlap, parallel_error = metrics
            if distance <= wall_max_thickness and overlap >= wall_min_overlap:
                edges.append(
                    GraphEdge(
                        source=f"entity:{left.handle.upper()}",
                        target=f"entity:{right.handle.upper()}",
                        kind=EdgeKind.WALL_PAIR,
                        attributes={
                            "distance": distance,
                            "overlap": overlap,
                            "parallel_error_degrees": parallel_error,
                        },
                    )
                )

    return SemanticGraph(document_id=document_id, nodes=nodes, edges=edges)


def _entity_points(entity: EntitySnapshot) -> list[tuple[float, float, float]]:
    segment = _line_segment(entity)
    if segment is not None:
        return [segment[0], segment[1]]
    raw = entity.geometry.get("points")
    if isinstance(raw, list):
        points: list[tuple[float, float, float]] = []
        for item in raw:
            point = _point3(item)
            if point is not None:
                points.append(point)
        return points
    return []


def _line_segment(
    entity: EntitySnapshot,
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    start = _point3(entity.geometry.get("start"))
    end = _point3(entity.geometry.get("end"))
    if start is None or end is None or start == end:
        return None
    return start, end


def _point3(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) not in (2, 3):
        return None
    if any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value):
        return None
    x = float(value[0])
    y = float(value[1])
    z = float(value[2]) if len(value) == 3 else 0.0
    return x, y, z


def _snap_key(point: tuple[float, float, float], tolerance: float) -> tuple[int, int, int]:
    return tuple(round(component / tolerance) for component in point)  # type: ignore[return-value]


def _wall_pair_metrics(
    left: EntitySnapshot,
    right: EntitySnapshot,
    *,
    wall_parallel_tolerance_degrees: float,
) -> tuple[float, float, float] | None:
    left_segment = _line_segment(left)
    right_segment = _line_segment(right)
    if left_segment is None or right_segment is None:
        return None

    l0, l1 = left_segment
    r0, r1 = right_segment
    lv = (l1[0] - l0[0], l1[1] - l0[1])
    rv = (r1[0] - r0[0], r1[1] - r0[1])
    llen = math.hypot(*lv)
    rlen = math.hypot(*rv)
    if llen == 0 or rlen == 0:
        return None

    dot = abs((lv[0] * rv[0] + lv[1] * rv[1]) / (llen * rlen))
    dot = max(-1.0, min(1.0, dot))
    parallel_error = math.degrees(math.acos(dot))
    if parallel_error > wall_parallel_tolerance_degrees:
        return None

    ux = lv[0] / llen
    uy = lv[1] / llen
    normal = (-uy, ux)
    distance = abs((r0[0] - l0[0]) * normal[0] + (r0[1] - l0[1]) * normal[1])

    left_interval = sorted((0.0, llen))
    right_projection = sorted(
        (
            (r0[0] - l0[0]) * ux + (r0[1] - l0[1]) * uy,
            (r1[0] - l0[0]) * ux + (r1[1] - l0[1]) * uy,
        )
    )
    overlap = max(
        0.0,
        min(left_interval[1], right_projection[1])
        - max(left_interval[0], right_projection[0]),
    )
    return distance, overlap, parallel_error
