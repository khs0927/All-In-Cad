from __future__ import annotations

import ezdxf

from all_in_cad.dxf_evidence import inspect_dxf


def test_inspect_dxf_recovers_audits_and_censuses_modelspace(tmp_path) -> None:
    path = tmp_path / "evidence.dxf"
    document = ezdxf.new("R2018")
    document.layers.add("WAL1")
    modelspace = document.modelspace()
    line = modelspace.add_line((0, 0), (10, 0), dxfattribs={"layer": "WAL1"})
    modelspace.add_circle((5, 5), radius=2, dxfattribs={"layer": "0"})
    expected_handle = line.dxf.handle
    document.saveas(path)

    evidence = inspect_dxf(path)

    assert evidence.auditor_has_errors is False
    assert evidence.entity_count == 2
    assert evidence.layer_counts == {"0": 1, "WAL1": 1}
    assert any(
        entity.handle == expected_handle
        and entity.dxftype == "LINE"
        and entity.layer == "WAL1"
        for entity in evidence.entities
    )
