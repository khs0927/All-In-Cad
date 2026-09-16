from all_in_cad.readback import DiffKind, EntitySnapshot, diff_snapshots


def entity(handle: str, *, layer: str = "WAL1", x: float = 0.0) -> EntitySnapshot:
    return EntitySnapshot(
        document_id="doc",
        handle=handle,
        entity_type="LINE",
        layer=layer,
        geometry={"start": [x, 0.0, 0.0], "end": [10.0, 0.0, 0.0]},
        properties={"color": 256},
    )


def test_snapshot_diff_detects_modified_added_removed_and_unchanged() -> None:
    before = [entity("A"), entity("B"), entity("C")]
    after = [entity("A"), entity("B", x=1.0), entity("D")]
    report = diff_snapshots(
        before,
        after,
        document_id="doc",
        before_revision=4,
        after_revision=5,
    )
    kinds = {item.handle: item.kind for item in report.entities}
    assert kinds == {
        "A": DiffKind.UNCHANGED,
        "B": DiffKind.MODIFIED,
        "C": DiffKind.REMOVED,
        "D": DiffKind.ADDED,
    }
    modified = next(item for item in report.entities if item.handle == "B")
    assert modified.changed_fields == ["geometry"]


def test_float_noise_is_canonicalized() -> None:
    left = entity("A", x=1.12345678901)
    right = entity("A", x=1.12345678902)
    assert left.digest() == right.digest()
