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


def test_capability_matrix_command_compares_two_pyrx_reports(tmp_path: Path, capsys) -> None:
    reports = []
    for host, hatch in (("autocad-2027", True), ("zwcad-2026", False)):
        path = tmp_path / f"{host}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": "all-in-cad/pyrx-probe/v1",
                    "host_label": host,
                    "db_types": {
                        "Line": True,
                        "Polyline": True,
                        "BlockReference": True,
                        "DBText": True,
                        "MText": True,
                        "Dimension": True,
                        "Hatch": hatch,
                        "LayerTable": True,
                    },
                    "current_database": {"ok": True},
                    "model_space": {"ok": True},
                }
            ),
            encoding="utf-8",
        )
        reports.extend(["--report", str(path)])

    assert main(["capability-matrix", *reports]) == 0
    payload = json.loads(capsys.readouterr().out)
    rows = {row["capability"]: row for row in payload["matrix"]["rows"]}
    assert rows["entity.line.type"]["common_supported"] is True
    assert rows["entity.hatch.type"]["common_supported"] is False
    assert payload["pyrx_read_baseline_gaps"] == {
        "pyrx@autocad-2027": {},
        "pyrx@zwcad-2026": {},
    }


def test_benchmark_command_emits_structured_report(tmp_path: Path, capsys) -> None:
    assert main(["benchmark", "--workdir", str(tmp_path), "--entities", "12"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "all-in-cad/benchmark/v1"
    assert payload["requested_entities"] == 12
    assert payload["extracted_entities"] == 12
    assert len(payload["stages"]) == 3


def test_verify_dwg_reports_missing_lanes_instead_of_crashing(tmp_path: Path, capsys) -> None:
    drawing = tmp_path / "sample.dxf"
    _write_fixture(drawing)

    # With no acadsharp/oda/libredwg probes installed the host has fewer than
    # two DWG lanes, so the CLI must report that cleanly and exit non-zero.
    assert main(["verify-dwg", "--source", str(drawing), "--workdir", str(tmp_path / "work")]) == 2

    captured = capsys.readouterr()
    assert "requires at least two available DWG lanes" in captured.err
    assert "Traceback" not in captured.err
