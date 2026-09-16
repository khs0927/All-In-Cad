from __future__ import annotations

import re
from enum import StrEnum


class LayerSemantic(StrEnum):
    COLUMN = "column"
    WALL = "wall"
    ELEVATOR = "elevator"
    DOOR = "door"
    WINDOW = "window"
    WINDOW_BAR = "window_bar"
    STAIR = "stair"
    DIMENSION = "dimension"
    CENTERLINE = "centerline"
    UNKNOWN = "unknown"


_EXACT = {
    "COL": LayerSemantic.COLUMN,
    "WAL1": LayerSemantic.WALL,
    "WAL2": LayerSemantic.WALL,
    "WAL3": LayerSemantic.WALL,
    "ELE": LayerSemantic.ELEVATOR,
    "DOOR": LayerSemantic.DOOR,
    "DOOR_ELE": LayerSemantic.DOOR,
    "WIN": LayerSemantic.WINDOW,
    "WINBAR": LayerSemantic.WINDOW_BAR,
    "WINELE": LayerSemantic.WINDOW,
    "STAIR": LayerSemantic.STAIR,
    "DIM": LayerSemantic.DIMENSION,
    "DIMLE": LayerSemantic.DIMENSION,
    "CEN": LayerSemantic.CENTERLINE,
    "CEN1": LayerSemantic.CENTERLINE,
}

_PATTERNS: tuple[tuple[re.Pattern[str], LayerSemantic], ...] = (
    (re.compile(r"(?:^|[-_])WALL?(?:[-_]|$)"), LayerSemantic.WALL),
    (re.compile(r"(?:^|[-_])COL(?:UMN)?(?:[-_]|$)"), LayerSemantic.COLUMN),
    (re.compile(r"(?:^|[-_])DOOR(?:[-_]|$)"), LayerSemantic.DOOR),
    (re.compile(r"(?:^|[-_])WIN(?:DOW)?(?:[-_]|$)"), LayerSemantic.WINDOW),
    (re.compile(r"(?:^|[-_])STAIR(?:[-_]|$)"), LayerSemantic.STAIR),
    (re.compile(r"(?:^|[-_])DIM(?:ENSION)?(?:[-_]|$)"), LayerSemantic.DIMENSION),
)


def normalize_layer_name(name: str) -> str:
    return re.sub(r"\s+", "", name.strip().upper())


def classify_layer(name: str) -> LayerSemantic:
    normalized = normalize_layer_name(name)
    if normalized in _EXACT:
        return _EXACT[normalized]
    for pattern, semantic in _PATTERNS:
        if pattern.search(normalized):
            return semantic
    return LayerSemantic.UNKNOWN
