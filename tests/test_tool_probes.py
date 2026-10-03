from all_in_cad.extraction import ExtractionLane, ToolProbe
from all_in_cad.tool_probes import capability_report, detect_tool_probes


def test_existing_file_detection_does_not_execute_or_verify(tmp_path):
    executable = tmp_path / "probe"
    executable.write_text("not an executable or a valid verifier")
    probes = detect_tool_probes(acadsharp_executable=str(executable))
    report = capability_report(probes)["acadsharp"]
    assert report["available"] is True
    assert report["detection_status"] == "DETECTED"
    assert report["verification_kind"] == "installation_detection"
    assert report["execution_status"] == "NOT_RUN"
    assert report["fixture_status"] == "NOT_RUN"
    assert report["execution_allowed"] is False


def test_version_metadata_does_not_promote_verification():
    probe = ToolProbe(ExtractionLane.ACADSHARP, True, version="1.0")
    report = capability_report({probe.lane: probe})["acadsharp"]
    assert report["version"] == "1.0"
    assert report["execution_status"] == "NOT_RUN"
    assert report["fixture_status"] == "NOT_RUN"
    assert report["execution_allowed"] is False


def test_missing_tool_is_not_verified():
    probe = ToolProbe(ExtractionLane.ACADSHARP, False)
    report = capability_report({probe.lane: probe})["acadsharp"]
    assert report["available"] is False
    assert report["detection_status"] == "NOT_DETECTED"
    assert report["execution_status"] == "NOT_RUN"
    assert report["fixture_status"] == "NOT_RUN"
