from __future__ import annotations

from pathlib import Path

import ezdxf

from all_in_cad.extraction import record_for_path
from all_in_cad.inventory import InventoryManifest
from all_in_cad.pipeline import process_manifest, snapshot_digest
from all_in_cad.project_index import ProjectIndex
from all_in_cad.readback import EntitySnapshot


def _write_room_fixture(path: Path) -> None:
    doc = ezdxf.new("R2018")
    doc.layers.add("WAL1")
    doc.layers.add("DOOR")
    modelspace = doc.modelspace()
    walls = [
        ((0, 0), (1000, 0)),
        ((1000, 0), (1000, 1000)),
        ((1000, 1000), (0, 1000)),
        ((0, 1000), (0, 0)),
    ]
    for start, end in walls:
        modelspace.add_line(start, end, dxfattribs={"layer": "WAL1"})
    modelspace.add_line((450, 0), (550, 0), dxfattribs={"layer": "DOOR"})
    doc.saveas(path)


def test_manifest_pipeline_persists_semantics_and_skips_unchanged(tmp_path: Path) -> None:
    drawing = tmp_path / "room.dxf"
    _write_room_fixture(drawing)
    record = record_for_path(drawing)
    manifest = InventoryManifest(roots=[str(tmp_path)], records=[record])

    with ProjectIndex(tmp_path / "index.sqlite3") as index:
        first = process_manifest(
            manifest,
            probes={},
            workdir=tmp_path / "work",
            index=index,
        )
        assert not first.failures
        assert len(first.processed) == 1
        assert first.processed[0].index.entity_count == 5
        assert first.processed[0].semantics.room_count == 1
        assert first.processed[0].semantics.opening_host_count == 1

        summary = index.semantic_summary(str(drawing.resolve()))
        assert summary is not None
        assert summary.room_count == 1
        assert summary.opening_host_count == 1
        assert summary.extraction_lane == "ezdxf"
        assert summary.snapshot_digest == first.processed[0].snapshot_digest

        second = process_manifest(
            manifest,
            probes={},
            workdir=tmp_path / "work",
            index=index,
        )
        assert not second.processed
        assert second.skipped == (str(drawing.resolve()),)
        assert not second.failures


def test_snapshot_digest_is_order_independent() -> None:
    left = EntitySnapshot(
        document_id="drawing",
        handle="A",
        entity_type="LINE",
        layer="WAL1",
        geometry={"start": [0, 0, 0], "end": [1, 0, 0]},
    )
    right = EntitySnapshot(
        document_id="drawing",
        handle="B",
        entity_type="LINE",
        layer="WAL1",
        geometry={"start": [1, 0, 0], "end": [1, 1, 0]},
    )
    assert snapshot_digest([left, right]) == snapshot_digest([right, left])
