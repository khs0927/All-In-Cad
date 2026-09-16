from pathlib import Path

from all_in_cad.inventory import FileKind, build_inventory, group_sidecars


def test_inventory_is_deterministic_and_groups_sidecars(tmp_path: Path) -> None:
    for suffix, content in (("dwg", b"dwg"), ("dxf", b"dxf"), ("pdf", b"pdf")):
        (tmp_path / f"A.{suffix}").write_bytes(content)
    (tmp_path / "ignored.txt").write_text("no")

    manifest = build_inventory([tmp_path], compute_hash=True)
    assert [record.kind for record in manifest.records] == [
        FileKind.DWG,
        FileKind.DXF,
        FileKind.PDF,
    ]
    assert manifest.dwg_count == 1
    assert all(record.sha256 for record in manifest.records)
    groups = group_sidecars(manifest)
    assert len(groups["a"]) == 3
