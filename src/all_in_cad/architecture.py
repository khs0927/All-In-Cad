from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .readback import EntitySnapshot
from .semantic_layers import LayerSemantic, classify_layer
from .topology import NodedSegment, Point2D, node_segments, segments_from_entities


class OpeningHostRelation(BaseModel):
    model_config = ConfigDict(frozen=True)

    opening_handle: str
    wall_handle: str
    opening_semantic: LayerSemantic
    distance: float = Field(ge=0)
    anchor: tuple[float, float]


class RoomCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    room_id: str
    polygon: tuple[tuple[float, float], ...]
    area: float = Field(gt=0)
    boundary_handles: tuple[str, ...] = ()


class RoomAdjacency(BaseModel):
    model_config = ConfigDict(frozen=True)

    room_a: str
    room_b: str
    shared_length: float = Field(gt=0)


class AnnotationBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    annotation_handle: str
    target_handles: tuple[str, ...]
    source: str = "explicit-properties"


def infer_opening_hosts(
    entities: Iterable[EntitySnapshot],
    *,
    max_distance: float = 500.0,
) -> list[OpeningHostRelation]:
    if max_distance <= 0:
        raise ValueError("max_distance must be positive")
    items = list(entities)
    wall_segments = segments_from_entities(items, semantics={LayerSemantic.WALL})
    results: list[OpeningHostRelation] = []

    for entity in items:
        semantic = classify_layer(entity.layer)
        if semantic not in {LayerSemantic.DOOR, LayerSemantic.WINDOW}:
            continue
        anchor = _entity_anchor(entity)
        if anchor is None:
            continue
        candidates: list[tuple[float, str]] = []
        for wall in wall_segments:
            distance = _point_segment_distance(anchor, wall.start, wall.end)
            candidates.append((distance, wall.source_handle))
        if not candidates:
            continue
        distance, wall_handle = min(candidates, key=lambda item: (item[0], item[1]))
        if distance <= max_distance:
            results.append(
                OpeningHostRelation(
                    opening_handle=entity.handle.upper(),
                    wall_handle=wall_handle,
                    opening_semantic=semantic,
                    distance=distance,
                    anchor=(anchor.x, anchor.y),
                )
            )
    return sorted(results, key=lambda item: item.opening_handle)


def infer_rooms(
    entities: Iterable[EntitySnapshot],
    *,
    snap_tolerance: float = 1e-6,
    minimum_area: float = 1.0,
) -> list[RoomCandidate]:
    if minimum_area <= 0:
        raise ValueError("minimum_area must be positive")
    walls = segments_from_entities(entities, semantics={LayerSemantic.WALL})
    noded = node_segments(walls, tolerance=snap_tolerance)
    faces = _positive_faces(noded, minimum_area=minimum_area)
    rooms: list[RoomCandidate] = []
    for index, (polygon, area, handles) in enumerate(faces, start=1):
        rooms.append(
            RoomCandidate(
                room_id=f"room:{index:04d}",
                polygon=tuple((point.x, point.y) for point in polygon),
                area=area,
                boundary_handles=tuple(sorted(handles)),
            )
        )
    return rooms


def infer_room_adjacency(rooms: Iterable[RoomCandidate]) -> list[RoomAdjacency]:
    edge_rooms: dict[
        tuple[tuple[float, float], tuple[float, float]], list[str]
    ] = defaultdict(list)
    edge_lengths: dict[
        tuple[tuple[float, float], tuple[float, float]], float
    ] = {}
    for room in rooms:
        points = list(room.polygon)
        for start, end in zip(points, points[1:] + points[:1], strict=False):
            key = tuple(sorted((start, end)))
            edge_rooms[key].append(room.room_id)
            edge_lengths[key] = math.dist(start, end)

    pair_lengths: dict[tuple[str, str], float] = defaultdict(float)
    for key, room_ids in edge_rooms.items():
        unique = sorted(set(room_ids))
        if len(unique) != 2:
            continue
        pair_lengths[(unique[0], unique[1])] += edge_lengths[key]

    return [
        RoomAdjacency(room_a=pair[0], room_b=pair[1], shared_length=length)
        for pair, length in sorted(pair_lengths.items())
        if length > 0
    ]


def infer_annotation_bindings(
    entities: Iterable[EntitySnapshot],
) -> list[AnnotationBinding]:
    bindings: list[AnnotationBinding] = []
    for entity in entities:
        if classify_layer(entity.layer) != LayerSemantic.DIMENSION:
            continue
        raw = entity.properties.get("target_handles")
        if raw is None:
            raw = entity.properties.get("target_handle")
        handles = _normalize_handles(raw)
        if handles:
            bindings.append(
                AnnotationBinding(
                    annotation_handle=entity.handle.upper(),
                    target_handles=handles,
                )
            )
    return sorted(bindings, key=lambda item: item.annotation_handle)


def _positive_faces(
    segments: list[NodedSegment],
    *,
    minimum_area: float,
) -> list[tuple[list[Point2D], float, set[str]]]:
    adjacency: dict[Point2D, set[Point2D]] = defaultdict(set)
    edge_handles: dict[tuple[Point2D, Point2D], set[str]] = defaultdict(set)
    for segment in segments:
        adjacency[segment.start].add(segment.end)
        adjacency[segment.end].add(segment.start)
        edge_handles[segment.undirected_key()].update(segment.source_handles)

    ordered_neighbors = {
        vertex: sorted(
            neighbors,
            key=lambda item: math.atan2(item.y - vertex.y, item.x - vertex.x),
        )
        for vertex, neighbors in adjacency.items()
    }
    directed = sorted(
        (left, right)
        for left, neighbors in adjacency.items()
        for right in neighbors
    )
    visited: set[tuple[Point2D, Point2D]] = set()
    faces: dict[
        tuple[Point2D, ...], tuple[list[Point2D], float, set[str]]
    ] = {}

    for start in directed:
        if start in visited:
            continue
        cycle = _walk_face(start, ordered_neighbors, visited)
        if len(cycle) < 3:
            continue
        area = _signed_area(cycle)
        if area < minimum_area:
            continue
        key = _canonical_polygon(cycle)
        handles: set[str] = set()
        for left, right in zip(cycle, cycle[1:] + cycle[:1], strict=False):
            handles.update(edge_handles[tuple(sorted((left, right)))])
        faces[key] = (cycle, area, handles)

    return [faces[key] for key in sorted(faces)]


def _walk_face(
    start: tuple[Point2D, Point2D],
    ordered_neighbors: dict[Point2D, list[Point2D]],
    visited: set[tuple[Point2D, Point2D]],
) -> list[Point2D]:
    cycle: list[Point2D] = []
    current = start
    maximum_steps = max(4, sum(len(items) for items in ordered_neighbors.values()) + 1)
    for _ in range(maximum_steps):
        if current in visited and current != start:
            return []
        visited.add(current)
        left, right = current
        cycle.append(left)
        neighbors = ordered_neighbors.get(right, [])
        if not neighbors or left not in neighbors:
            return []
        index = neighbors.index(left)
        next_vertex = neighbors[(index - 1) % len(neighbors)]
        current = (right, next_vertex)
        if current == start:
            return cycle
    return []


def _canonical_polygon(points: list[Point2D]) -> tuple[Point2D, ...]:
    rotations = [tuple(points[index:] + points[:index]) for index in range(len(points))]
    return min(rotations)


def _signed_area(points: list[Point2D]) -> float:
    return 0.5 * sum(
        left.x * right.y - right.x * left.y
        for left, right in zip(points, points[1:] + points[:1], strict=False)
    )


def _entity_anchor(entity: EntitySnapshot) -> Point2D | None:
    for key in ("insertion_point", "position", "center"):
        point = _point2(entity.geometry.get(key))
        if point is not None:
            return point
    raw = entity.geometry.get("points")
    if isinstance(raw, list):
        points = [point for item in raw if (point := _point2(item)) is not None]
        if points:
            return Point2D(
                sum(point.x for point in points) / len(points),
                sum(point.y for point in points) / len(points),
            )
    start = _point2(entity.geometry.get("start"))
    end = _point2(entity.geometry.get("end"))
    if start is not None and end is not None:
        return Point2D((start.x + end.x) / 2, (start.y + end.y) / 2)
    return None


def _point_segment_distance(point: Point2D, start: Point2D, end: Point2D) -> float:
    dx = end.x - start.x
    dy = end.y - start.y
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.dist((point.x, point.y), (start.x, start.y))
    parameter = ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_sq
    parameter = max(0.0, min(1.0, parameter))
    projection = Point2D(start.x + parameter * dx, start.y + parameter * dy)
    return math.dist((point.x, point.y), (projection.x, projection.y))


def _normalize_handles(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (list, tuple)):
        candidates = [item for item in value if isinstance(item, str)]
    else:
        return ()
    return tuple(sorted({item.strip().upper() for item in candidates if item.strip()}))


def _point2(value: object) -> Point2D | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    x, y = value[0], value[1]
    if (
        not isinstance(x, (int, float))
        or isinstance(x, bool)
        or not isinstance(y, (int, float))
        or isinstance(y, bool)
    ):
        return None
    return Point2D(float(x), float(y))
