from pathlib import Path

from all_in_cad.benchmark import run_synthetic_dxf_benchmark


def test_synthetic_benchmark_reports_counts_without_perf_gate(tmp_path: Path) -> None:
    result = run_synthetic_dxf_benchmark(tmp_path, entity_count=25)
    assert result.schema == "all-in-cad/benchmark/v1"
    assert result.workload == "synthetic-dxf-lines"
    assert result.requested_entities == 25
    assert result.extracted_entities == 25
    assert result.semantic_nodes >= 25
    assert len(result.snapshot_digest) == 64
    assert [stage.name for stage in result.stages] == [
        "generate_dxf",
        "extract_normalize",
        "semantic_graph",
    ]
    assert all(stage.elapsed_ms >= 0 for stage in result.stages)
