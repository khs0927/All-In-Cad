import pytest

from all_in_cad.models import ChangeOperation, EntityRef
from all_in_cad.operations import (
    OperationKind,
    OperationValidationError,
    validate_change_operation,
)


def target() -> EntityRef:
    return EntityRef(document_id="doc", handle="AA")


def test_move_requires_target_and_delta() -> None:
    operation = ChangeOperation(
        op_id="move-1",
        kind=OperationKind.MOVE,
        targets=[target()],
        parameters={"delta": [10, 20]},
    )
    spec = validate_change_operation(operation)
    assert spec.kind == OperationKind.MOVE


def test_unregistered_write_fails_closed() -> None:
    operation = ChangeOperation(op_id="raw", kind="command.raw", parameters={})
    with pytest.raises(OperationValidationError, match="unregistered"):
        validate_change_operation(operation)


def test_scale_must_be_positive() -> None:
    operation = ChangeOperation(
        op_id="scale-1",
        kind=OperationKind.SCALE,
        targets=[target()],
        parameters={"base_point": [0, 0], "factor": 0},
    )
    with pytest.raises(OperationValidationError, match="positive"):
        validate_change_operation(operation)
