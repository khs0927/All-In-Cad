from all_in_cad.capabilities import (
    CapabilityState,
    build_capability_matrix,
    capability_gaps,
    normalize_acadsharp_capabilities,
    normalize_pyrx_report,
)


def test_normalize_pyrx_report_maps_observed_host_surface() -> None:
    report = normalize_pyrx_report(
        {
            "schema": "all-in-cad/pyrx-probe/v1",
            "host_label": "autocad-2027",
            "python": "3.14.0",
            "platform": "Windows",
            "db_types": {
                "Line": True,
                "Circle": True,
                "Arc": True,
                "Polyline": True,
                "BlockReference": True,
                "DBText": True,
                "MText": True,
                "Dimension": True,
                "Hatch": False,
                "LayerTable": True,
            },
            "current_database": {"ok": True},
            "model_space": {"ok": True},
            "block_reference_scan": {"ok": True, "count": 2},
        }
    )
    assert report.host == "autocad-2027"
    assert report.state("document.database.read") == CapabilityState.SUPPORTED
    assert report.state("entity.hatch.type") == CapabilityState.UNSUPPORTED
    assert report.state("entity.block_reference.scan") == CapabilityState.SUPPORTED


def test_normalize_acadsharp_report_does_not_overclaim_geometry() -> None:
    report = normalize_acadsharp_capabilities(
        {
            "adapter": "acadsharp",
            "assembly": "ACadSharp, Version=3.7.1.0",
            "reader": "ACadSharp.IO.DwgReader",
            "output": "all-in-cad-headless-census/v1",
        }
    )
    assert report.state("file.dwg.read") == CapabilityState.SUPPORTED
    assert report.state("entity.census.read") == CapabilityState.SUPPORTED
    assert report.state("entity.geometry.normalized") == CapabilityState.UNKNOWN


def test_matrix_marks_only_shared_supported_capabilities() -> None:
    autocad = normalize_pyrx_report(
        {
            "host_label": "autocad-2027",
            "db_types": {"Line": True, "Hatch": True},
            "current_database": {"ok": True},
            "model_space": {"ok": True},
        }
    )
    zwcad = normalize_pyrx_report(
        {
            "host_label": "zwcad-2026",
            "db_types": {"Line": True, "Hatch": False},
            "current_database": {"ok": True},
            "model_space": {"ok": True},
        }
    )
    matrix = build_capability_matrix([autocad, zwcad])
    rows = {row.capability: row for row in matrix.rows}
    assert rows["entity.line.type"].common_supported is True
    assert rows["entity.hatch.type"].common_supported is False


def test_capability_gaps_keep_unknown_distinct_from_unsupported() -> None:
    report = normalize_pyrx_report(
        {
            "host_label": "zwcad-2026",
            "db_types": {"Line": True, "Hatch": False},
        }
    )
    gaps = capability_gaps(
        report,
        {"entity.line.type", "entity.hatch.type", "document.database.read"},
    )
    assert gaps == {
        "document.database.read": CapabilityState.UNKNOWN,
        "entity.hatch.type": CapabilityState.UNSUPPORTED,
    }
