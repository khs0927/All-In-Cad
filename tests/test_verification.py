from all_in_cad.verification import DrawingEvidence, cross_check_evidence


def test_matching_independent_evidence_passes() -> None:
    evidences = [
        DrawingEvidence("native", "doc", 10, 3, {"WAL1": 2, "DOOR": 1}, "abc"),
        DrawingEvidence("ezdxf", "doc", 10, 3, {"WAL1": 2, "DOOR": 1}, "abc"),
    ]
    report = cross_check_evidence(evidences)
    assert report.passed
    assert not report.findings


def test_geometry_mismatch_fails() -> None:
    evidences = [
        DrawingEvidence("native", "doc", 10, 3, {"WAL1": 2, "DOOR": 1}, "abc"),
        DrawingEvidence("ezdxf", "doc", 10, 3, {"WAL1": 2, "DOOR": 1}, "xyz"),
    ]
    report = cross_check_evidence(evidences)
    assert not report.passed
    assert {finding.code for finding in report.findings} == {"GEOMETRY_DIGEST_MISMATCH"}
