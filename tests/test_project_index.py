from all_in_cad.inventory import FileKind, FileRecord, InventoryManifest
from all_in_cad.project_index import ProjectIndex
from all_in_cad.readback import EntitySnapshot


def record(*, modified_ns: int = 10, size_bytes: int = 100) -> FileRecord:
    return FileRecord(
        root="C:/cad",
        path="C:/cad/floor.dwg",
        relative_path="floor.dwg",
        kind=FileKind.DWG,
        size_bytes=size_bytes,
        modified_ns=modified_ns,
        sidecar_group="floor",
    )


def snapshots() -> list[EntitySnapshot]:
    return [
        EntitySnapshot(
            document_id="doc",
            handle="A",
            entity_type="INSERT",
            layer="DOOR",
            properties={"block_name": "DOOR_A"},
        ),
        EntitySnapshot(
            document_id="doc",
            handle="B",
            entity_type="INSERT",
            layer="0",
            properties={"block_name": "SITE", "xref_path": "site.dwg"},
        ),
        EntitySnapshot(
            document_id="doc",
            handle="C",
            entity_type="LINE",
            layer="WAL1",
            geometry={"start": [0, 0], "end": [100, 0]},
        ),
    ]


def test_incremental_index_skips_unchanged_record(tmp_path) -> None:
    path = tmp_path / "index.sqlite"
    with ProjectIndex(path) as index:
        first = index.index_drawing(record(), snapshots())
        second = index.index_drawing(record(), snapshots())
        assert first.changed is True
        assert second.changed is False
        assert second.entity_count == 3
        assert index.drawing_count() == 1


def test_changed_fingerprint_replaces_entities_transactionally(tmp_path) -> None:
    with ProjectIndex(tmp_path / "index.sqlite") as index:
        first = index.index_drawing(record(), snapshots())
        changed = index.index_drawing(record(modified_ns=11), snapshots()[:1])
        assert changed.changed is True
        assert changed.drawing_id == first.drawing_id
        assert changed.entity_count == 1


def test_block_and_xref_queries_are_explicit(tmp_path) -> None:
    with ProjectIndex(tmp_path / "index.sqlite") as index:
        result = index.index_drawing(record(), snapshots())
        assert result.reference_count == 2

        blocks = index.block_usages("door_a")
        assert len(blocks) == 1
        assert blocks[0].source_handle == "A"
        assert blocks[0].drawing_path == "C:/cad/floor.dwg"

        xrefs = index.xrefs("site.dwg")
        assert len(xrefs) == 1
        assert xrefs[0].source_handle == "B"
        assert xrefs[0].name == "SITE"


def test_changed_records_compares_manifest_to_index(tmp_path) -> None:
    with ProjectIndex(tmp_path / "index.sqlite") as index:
        index.index_drawing(record(), snapshots())
        manifest = InventoryManifest(
            roots=["C:/cad"],
            records=[record(), record(modified_ns=99, size_bytes=101)],
        )
        changed = index.changed_records(manifest)
        assert len(changed) == 1
        assert changed[0].modified_ns == 99
