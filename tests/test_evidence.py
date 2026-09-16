from all_in_cad.evidence import (
    compare_extraction_runs,
    handle_independent_geometry_digest,
)
from all_in_cad.extraction import ExtractionLane
from all_in_cad.extraction_runtime import ExtractionRun
from all_in_cad.readback import EntitySnapshot


def _line(handle: str, start: list[float], end: list[float]) -> EntitySnapshot:
    return EntitySnapshot(
        document_id="drawing-a",
        handle=handle,
        entity_type="LINE",
        layer="WAL1",
        geometry={"start": start, "end": end},
    )


def test_geometry_digest_ignores_handle_and_entity_order() -> None:
    first = [_line("A", [0, 0, 0], [1, 0, 0]), _line("B", [1, 0, 0], [1, 1, 0])]
    second = [_line("F2", [1, 0, 0], [1, 1, 0]), _line("F1", [0, 0, 0], [1, 0, 0])]
    assert handle_independent_geometry_digest(first) == handle_independent_geometry_digest(second)


def test_cross_lane_manifest_passes_equivalent_geometry() -> None:
    first = ExtractionRun(
        source_path="drawing.dwg",
        lane=ExtractionLane.ACADSHARP,
        snapshots=(
            _line("A", [0, 0, 0], [1, 0, 0]),
            _line("B", [1, 0, 0], [1, 1, 0]),
        ),
    )
    second = ExtractionRun(
        source_path="drawing.dxf",
        lane=ExtractionLane.EZDXF,
        snapshots=(
            _line("20", [1, 0, 0], [1, 1, 0]),
            _line("10", [0, 0, 0], [1, 0, 0]),
        ),
    )
    manifest = compare_extraction_runs([first, second])
    assert manifest.verification.passed is True
    assert len(manifest.sources) == 2
    assert manifest.sources[0].geometry_digest == manifest.sources[1].geometry_digest


def test_cross_lane_manifest_fails_geometry_change() -> None:
    baseline = ExtractionRun(
        source_path="drawing.dwg",
        lane=ExtractionLane.ACADSHARP,
        snapshots=(_line("A", [0, 0, 0], [1, 0, 0]),),
    )
    changed = ExtractionRun(
        source_path="drawing.dxf",
        lane=ExtractionLane.EZDXF,
        snapshots=(_line("10", [0, 0, 0], [2, 0, 0]),),
    )
    manifest = compare_extraction_runs([baseline, changed])
    assert manifest.verification.passed is False
    assert [item.code for item in manifest.verification.findings] == [
        "GEOMETRY_DIGEST_MISMATCH"
    ]
