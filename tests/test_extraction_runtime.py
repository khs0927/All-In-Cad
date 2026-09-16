from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import ezdxf
import pytest

from all_in_cad.extraction import ExtractionLane, ToolProbe
from all_in_cad.extraction_runtime import (
    convert_with_libredwg,
    normalize_dxf,
    read_with_acadsharp,
)


def test_normalize_dxf_emits_stable_snapshots(tmp_path: Path) -> None:
    path = tmp_path / "fixture.dxf"
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((1, 2, 0), (3, 4, 0), dxfattribs={"layer": "WAL1"})
    msp.add_circle((5, 6, 0), 2.5, dxfattribs={"layer": "COL"})
    msp.add_text("ROOM", dxfattribs={"layer": "TEXT"}).set_placement((7, 8, 0))
    doc.saveas(path)

    run = normalize_dxf(path, document_id="fixture")

    assert run.lane == ExtractionLane.EZDXF
    assert len(run.snapshots) == 3
    line = next(item for item in run.snapshots if item.entity_type == "LINE")
    assert line.layer == "WAL1"
    assert line.geometry == {"start": [1.0, 2.0, 0.0], "end": [3.0, 4.0, 0.0]}
    circle = next(item for item in run.snapshots if item.entity_type == "CIRCLE")
    assert circle.geometry["radius"] == 2.5
    text = next(item for item in run.snapshots if item.entity_type == "TEXT")
    assert text.properties["text"] == "ROOM"


def test_libredwg_remains_external_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "a.dwg"
    destination = tmp_path / "a.dxf"
    source.write_bytes(b"fixture")
    captured: list[str] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured.extend(command)
        destination.write_text("converted", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = convert_with_libredwg("dwg2dxf", source, destination)

    assert result == destination.resolve()
    assert captured == ["dwg2dxf", "-y", "-o", str(destination.resolve()), str(source.resolve())]


def test_acadsharp_json_is_ingested(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "a.dwg"
    source.write_bytes(b"fixture")
    payload = {
        "schema": "all-in-cad-headless-census/v1",
        "entities": [
            {
                "handle": "1a",
                "entity_type": "LINE",
                "layer": "WAL1",
                "geometry": {},
                "properties": {},
            }
        ],
    }

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert command[-1] == str(source.resolve())
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run = read_with_acadsharp(
        ToolProbe(ExtractionLane.ACADSHARP, True, executable="probe.exe"),
        source,
        document_id="doc",
    )

    assert run.lane == ExtractionLane.ACADSHARP
    assert run.snapshots[0].handle == "1A"
    assert run.snapshots[0].document_id == "doc"


def test_libredwg_failure_is_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "a.dwg"
    destination = tmp_path / "a.dxf"
    source.write_bytes(b"fixture")

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=2, stdout="", stderr="bad drawing")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="bad drawing"):
        convert_with_libredwg("dwg2dxf", source, destination)
