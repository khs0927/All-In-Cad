import pytest

from all_in_cad.cross_lane import (
    available_verification_lanes,
    verify_dwg_across_lanes,
)
from all_in_cad.extraction import ExtractionLane, ToolProbe


def test_available_verification_lanes_require_executable_where_needed() -> None:
    probes = {
        ExtractionLane.ACADSHARP: ToolProbe(
            lane=ExtractionLane.ACADSHARP,
            available=True,
            executable="probe.dll",
        ),
        ExtractionLane.ODA: ToolProbe(
            lane=ExtractionLane.ODA,
            available=True,
        ),
        ExtractionLane.LIBREDWG: ToolProbe(
            lane=ExtractionLane.LIBREDWG,
            available=True,
            executable=None,
        ),
    }
    assert available_verification_lanes(probes) == (
        ExtractionLane.ACADSHARP,
        ExtractionLane.ODA,
    )


def test_cross_lane_requires_two_available_lanes(tmp_path) -> None:
    probes = {
        ExtractionLane.ODA: ToolProbe(
            lane=ExtractionLane.ODA,
            available=True,
        )
    }
    with pytest.raises(RuntimeError, match="at least two"):
        verify_dwg_across_lanes(
            tmp_path / "drawing.dwg",
            probes,
            tmp_path / "work",
        )
