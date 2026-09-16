from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .readback import EntitySnapshot
from .semantic_layers import LayerSemantic, classify_layer


@dataclass(frozen=True, order=True, slots=True)
class Point2D:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Segment2D:
    source_handle: str
    start: Point2D
    end: Point2D


@dataclass(frozen=True, slots=True)
class NodedSegment:
    start: Point2D
    end: Point2D
    source_handles: tuple[str, ...]

    @property
    def length(self) -> float:
        return math.hypot(self.end.x - self.start.x, self.end.y - self.start.y)

    def undirected_key(self) -> tuple[Point2D, Point2D]:
        return tuple(sorted((self.start, self.end)))  # type: ignore[return-value]


def segments_from_entities(
    entities: Iterable[EntitySnapshot],
    *,
    semantics: set[LayerSemantic] | None = None,
) -> list[Segment2D]:
    segments: list[Segment2D] = []
    for entity in entities:
        if semantics is not None and classify_layer(entity.layer) not in semantics:
            continue
        handle = entity.handle.upper()
        start = _point2(entity.geometry.get("start"))
        end = _point2(entity.geometry.get("end"))
        if start is not None and end is not None and start != end:
            segments.append(Segment2D(handle, start, end))
            continue

        raw_points = entity.geometry.get("points")
        if not isinstance(raw_points, list):
            continue
        points = [point for item in raw_points if (point := _point2(item)) is not None]
        for left, right in zip(points, points[1:]):
            if left != right:
                segments.append(Segment2D(handle, left, right))
        if entity.geometry.get("closed") is True and len(points) > 2 and points[-1] != points[0]:
            segments.append(Segment2D(handle, points[-1], points[0]))
    return segments


def node_segments(
    segments: Iterable[Segment2D],
    *,
    tolerance: float = 1e-6,
) -> list[NodedSegment]:
    if tolerance <= 0:
        raise ValueError("tolerance must be positive")

    original = [
        Segment2D(
            segment.source_handle.upper(),
            _snap_point(segment.start, tolerance),
            _snap_point(segment.end, tolerance),
        )
        for segment in segments
        if segment.start != segment.end
    ]
    split_points: list[set[Point2D]] = [
        {segment.start, segment.end} for segment in original
    ]

    for left_index, left in enumerate(original):
        for right_index in range(left_index + 1, len(original)):
            right = original[right_index]
            for point in _segment_intersections(left, right, tolerance=tolerance):
                split_points[left_index].add(point)
                split_points[right_index].add(point)

    merged: dict[tuple[Point2D, Point2D], set[str]] = {}
    for segment, points in zip(original, split_points):
        ordered = sorted(points, key=lambda point: _parameter(segment, point))
        for start, end in zip(ordered, ordered[1:]):
            if _distance(start, end) <= tolerance * 0.5:
                continue
            key = tuple(sorted((start, end)))
            merged.setdefault(key, set()).add(segment.source_handle)

    return [
        NodedSegment(start=key[0], end=key[1], source_handles=tuple(sorted(handles)))
        for key, handles in sorted(
            merged.items(),
            key=lambda item: (item[0][0].x, item[0][0].y, item[0][1].x, item[0][1].y),
        )
    ]


def _segment_intersections(
    left: Segment2D,
    right: Segment2D,
    *,
    tolerance: float,
) -> tuple[Point2D, ...]:
    p = left.start
    q = right.start
    r = Point2D(left.end.x - p.x, left.end.y - p.y)
    s = Point2D(right.end.x - q.x, right.end.y - q.y)
    cross_rs = _cross(r, s)
    q_minus_p = Point2D(q.x - p.x, q.y - p.y)
    cross_qp_r = _cross(q_minus_p, r)
    epsilon = tolerance * max(1.0, _distance(left.start, left.end), _distance(right.start, right.end))

    if abs(cross_rs) <= epsilon:
        if abs(cross_qp_r) > epsilon:
            return ()
        shared: set[Point2D] = set()
        for candidate in (left.start, left.end, right.start, right.end):
            if _point_on_segment(candidate, left, tolerance) and _point_on_segment(
                candidate, right, tolerance
            ):
                shared.add(_snap_point(candidate, tolerance))
        return tuple(sorted(shared))

    t = _cross(q_minus_p, s) / cross_rs
    u = _cross(q_minus_p, r) / cross_rs
    if -tolerance <= t <= 1 + tolerance and -tolerance <= u <= 1 + tolerance:
        point = Point2D(p.x + t * r.x, p.y + t * r.y)
        return (_snap_point(point, tolerance),)
    return ()


def _point_on_segment(point: Point2D, segment: Segment2D, tolerance: float) -> bool:
    ab = Point2D(segment.end.x - segment.start.x, segment.end.y - segment.start.y)
    ap = Point2D(point.x - segment.start.x, point.y - segment.start.y)
    if abs(_cross(ab, ap)) > tolerance * max(1.0, _distance(segment.start, segment.end)):
        return False
    dot = ap.x * ab.x + ap.y * ab.y
    length_sq = ab.x * ab.x + ab.y * ab.y
    return -tolerance <= dot <= length_sq + tolerance


def _parameter(segment: Segment2D, point: Point2D) -> float:
    dx = segment.end.x - segment.start.x
    dy = segment.end.y - segment.start.y
    denominator = dx * dx + dy * dy
    if denominator == 0:
        return 0.0
    return ((point.x - segment.start.x) * dx + (point.y - segment.start.y) * dy) / denominator


def _snap_point(point: Point2D, tolerance: float) -> Point2D:
    return Point2D(
        round(point.x / tolerance) * tolerance,
        round(point.y / tolerance) * tolerance,
    )


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


def _cross(left: Point2D, right: Point2D) -> float:
    return left.x * right.y - left.y * right.x


def _distance(left: Point2D, right: Point2D) -> float:
    return math.hypot(right.x - left.x, right.y - left.y)
