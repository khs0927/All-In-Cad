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


def test_digest_collapses_json_int_and_float_across_lanes() -> None:
    """The .NET probe emits whole-number coordinates as JSON integers (``50``)
    while ezdxf yields Python floats (``50.0``); both describe the same drawing
    and must digest identically.
    """
    dotnet = [_line("A", [50, 0, 0], [100, 50, 0])]
    python = [_line("A", [50.0, 0.0, 0.0], [100.0, 50.0, 0.0])]
    assert handle_independent_geometry_digest(dotnet) == handle_independent_geometry_digest(python)


def test_digest_collapses_rounding_and_roundtrip_noise() -> None:
    """ACadSharp rounds to 6 decimals; the LibreDWG lane keeps full precision,
    so the same point arrives as ``50.0`` versus ``49.99999999999999``. DWG
    round trips add ~1e-14 noise on top. Both must digest identically.
    """
    rounded = [_line("A", [0, 0, 0], [86.60254, 50.0, 0])]
    full_precision = [_line("A", [0.0, 0.0, 0.0], [86.60254037844388, 49.99999999999999, 0.0])]
    assert handle_independent_geometry_digest(rounded) == handle_independent_geometry_digest(
        full_precision
    )


def test_digest_still_detects_a_real_geometry_change_at_lane_precision() -> None:
    """Normalization must not be so coarse that a genuine edit becomes
    invisible: a 1-unit move stays detectable at the 6-decimal lane precision.
    """
    baseline = [_line("A", [0, 0, 0], [86.60254, 50.0, 0])]
    edited = [_line("A", [0, 0, 0], [87.60254, 50.0, 0])]
    baseline_digest = handle_independent_geometry_digest(baseline)
    edited_digest = handle_independent_geometry_digest(edited)
    assert baseline_digest != edited_digest
