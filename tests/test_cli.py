from __future__ import annotations

import json
from pathlib import Path

import ezdxf

from all_in_cad.cli import main


def _write_fixture(path: Path) -> None:
    doc = ezdxf.new("R2018")
    doc.layers.add("WAL1")
    modelspace = doc.modelspace()
    modelspace.add_line((0, 0), (1000, 0), dxfattribs={"layer": "WAL1"})
    modelspace.add_line((1000, 0), (1000, 1000), dxfattribs={"layer": "WAL1"})
    modelspace.add_line((1000, 1000), (0, 1000), dxfattribs={"layer": "WAL1"})
    modelspace.add_line((0, 1000), (0, 0), dxfattribs={"layer": "WAL1"})
    doc.saveas(path)


def test_doctor_emits_capability_json(capsys) -> None:
    assert main(["doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ezdxf"]["available"] is True
    assert "oda" in payload
    assert "acadsharp" in payload
    assert "libredwg" in payload


def test_index_command_processes_dxf_and_then_skips_unchanged(tmp_path: Path, capsys) -> None:
    drawing = tmp_path / "fixture.dxf"
    database = tmp_path / "index.sqlite3"
    workdir = tmp_path / "work"
    _write_fixture(drawing)

    args = [
        "index",
        "--root",
        str(tmp_path),
        "--db",
        str(database),
        "--workdir",
        str(workdir),
    ]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["inventory_records"] == 1
    assert len(first["processed"]) == 1
    assert first["processed"][0]["rooms"] == 1

    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["processed"] == []
    assert second["skipped"] == [str(drawing.resolve())]
