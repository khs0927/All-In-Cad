from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .models import ChangeOperation


class OperationValidationError(ValueError):
    pass


class OperationKind(StrEnum):
    CREATE_LINE = "entity.create_line"
    CREATE_POLYLINE = "entity.create_polyline"
    MOVE = "entity.move"
    COPY = "entity.copy"
    ROTATE = "entity.rotate"
    SCALE = "entity.scale"
    OFFSET = "entity.offset"
    ERASE = "entity.erase"
    LAYER_CREATE = "layer.create"
    LAYER_SET = "layer.set"
    BLOCK_INSERT = "block.insert"
    BLOCK_ATTRIBUTE_SET = "block.attribute.set"
    TEXT_SET = "text.set"
    DIMENSION_LINEAR_CREATE = "dimension.linear.create"


@dataclass(frozen=True, slots=True)
class PrimitiveSpec:
    kind: OperationKind
    minimum_targets: int
    maximum_targets: int | None
    required_parameters: frozenset[str]
    capability: str = "entity.write"


_SPECS: dict[OperationKind, PrimitiveSpec] = {
    OperationKind.CREATE_LINE: PrimitiveSpec(
        OperationKind.CREATE_LINE, 0, 0, frozenset({"start", "end"})
    ),
    OperationKind.CREATE_POLYLINE: PrimitiveSpec(
        OperationKind.CREATE_POLYLINE, 0, 0, frozenset({"points"})
    ),
    OperationKind.MOVE: PrimitiveSpec(
        OperationKind.MOVE, 1, None, frozenset({"delta"})
    ),
    OperationKind.COPY: PrimitiveSpec(
        OperationKind.COPY, 1, None, frozenset({"delta"})
    ),
    OperationKind.ROTATE: PrimitiveSpec(
        OperationKind.ROTATE, 1, None, frozenset({"base_point", "angle_degrees"})
    ),
    OperationKind.SCALE: PrimitiveSpec(
        OperationKind.SCALE, 1, None, frozenset({"base_point", "factor"})
    ),
    OperationKind.OFFSET: PrimitiveSpec(
        OperationKind.OFFSET, 1, None, frozenset({"distance"})
    ),
    OperationKind.ERASE: PrimitiveSpec(
        OperationKind.ERASE, 1, None, frozenset()
    ),
    OperationKind.LAYER_CREATE: PrimitiveSpec(
        OperationKind.LAYER_CREATE, 0, 0, frozenset({"name"}), "layer.write"
    ),
    OperationKind.LAYER_SET: PrimitiveSpec(
        OperationKind.LAYER_SET, 1, None, frozenset({"layer"}), "layer.write"
    ),
    OperationKind.BLOCK_INSERT: PrimitiveSpec(
        OperationKind.BLOCK_INSERT,
        0,
        0,
        frozenset({"name", "insertion_point"}),
        "block.write",
    ),
    OperationKind.BLOCK_ATTRIBUTE_SET: PrimitiveSpec(
        OperationKind.BLOCK_ATTRIBUTE_SET,
        1,
        None,
        frozenset({"tag", "value"}),
        "block.write",
    ),
    OperationKind.TEXT_SET: PrimitiveSpec(
        OperationKind.TEXT_SET, 1, None, frozenset({"text"}), "text.write"
    ),
    OperationKind.DIMENSION_LINEAR_CREATE: PrimitiveSpec(
        OperationKind.DIMENSION_LINEAR_CREATE,
        0,
        0,
        frozenset({"first", "second", "dimension_line"}),
        "dimension.write",
    ),
}


def primitive_spec(kind: str | OperationKind) -> PrimitiveSpec:
    try:
        normalized = OperationKind(kind)
    except ValueError as exc:
        raise OperationValidationError(f"unregistered write operation: {kind}") from exc
    return _SPECS[normalized]


def required_capability(kind: str | OperationKind) -> str:
    return primitive_spec(kind).capability


def registered_write_kinds() -> tuple[str, ...]:
    return tuple(item.value for item in OperationKind)


def validate_change_operation(operation: ChangeOperation) -> PrimitiveSpec:
    spec = primitive_spec(operation.kind)
    target_count = len(operation.targets)
    if target_count < spec.minimum_targets:
        raise OperationValidationError(
            f"{spec.kind} requires at least {spec.minimum_targets} target(s)"
        )
    if spec.maximum_targets is not None and target_count > spec.maximum_targets:
        raise OperationValidationError(
            f"{spec.kind} allows at most {spec.maximum_targets} target(s)"
        )

    missing = sorted(spec.required_parameters - operation.parameters.keys())
    if missing:
        raise OperationValidationError(
            f"{spec.kind} missing required parameter(s): {', '.join(missing)}"
        )

    _validate_common_parameters(spec.kind, operation.parameters)
    return spec


def _validate_common_parameters(kind: OperationKind, parameters: dict[str, Any]) -> None:
    point_keys = (
        "start",
        "end",
        "delta",
        "base_point",
        "insertion_point",
        "first",
        "second",
        "dimension_line",
    )
    for key in point_keys:
        if key in parameters:
            _validate_point(key, parameters[key])

    if kind == OperationKind.CREATE_POLYLINE:
        points = parameters.get("points")
        if not isinstance(points, list) or len(points) < 2:
            raise OperationValidationError("polyline points must contain at least two points")
        for index, point in enumerate(points):
            _validate_point(f"points[{index}]", point)

    if "factor" in parameters:
        factor = parameters["factor"]
        if not isinstance(factor, (int, float)) or isinstance(factor, bool) or factor <= 0:
            raise OperationValidationError("scale factor must be a positive number")

    if "distance" in parameters:
        distance = parameters["distance"]
        if not isinstance(distance, (int, float)) or isinstance(distance, bool):
            raise OperationValidationError("distance must be numeric")

    if "angle_degrees" in parameters:
        angle = parameters["angle_degrees"]
        if not isinstance(angle, (int, float)) or isinstance(angle, bool):
            raise OperationValidationError("angle_degrees must be numeric")


def _validate_point(name: str, value: Any) -> None:
    if not isinstance(value, (list, tuple)) or len(value) not in (2, 3):
        raise OperationValidationError(f"{name} must be a 2D or 3D point")
    if any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value):
        raise OperationValidationError(f"{name} coordinates must be numeric")
