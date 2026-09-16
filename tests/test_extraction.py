from pathlib import Path

from all_in_cad.extraction import ExtractionLane, ToolProbe, plan_extraction, record_for_path


def available(lane: ExtractionLane) -> ToolProbe:
    return ToolProbe(lane=lane, available=True, executable=lane.value)


def test_dwg_prefers_acadsharp_when_available(tmp_path: Path) -> None:
    path = tmp_path / "drawing.dwg"
    path.write_bytes(b"dwg")
    record = record_for_path(path)
    plan = plan_extraction(
        record,
        {
            ExtractionLane.ACADSHARP: available(ExtractionLane.ACADSHARP),
            ExtractionLane.ODA: available(ExtractionLane.ODA),
            ExtractionLane.EZDXF: available(ExtractionLane.EZDXF),
        },
    )
    assert plan.executable
    assert [step.lane for step in plan.steps] == [ExtractionLane.ACADSHARP]


def test_oda_lane_requires_ezdxf_for_normalized_parse(tmp_path: Path) -> None:
    path = tmp_path / "drawing.dwg"
    path.write_bytes(b"dwg")
    record = record_for_path(path)
    plan = plan_extraction(
        record,
        {ExtractionLane.ODA: available(ExtractionLane.ODA)},
    )
    assert not plan.executable
    assert plan.missing_tools == (ExtractionLane.EZDXF,)
