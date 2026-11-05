from __future__ import annotations

import json
import warnings

from all_in_cad.benchmark import BenchmarkResult
from all_in_cad.capabilities import CapabilityReport
from all_in_cad.evidence import EvidenceManifest


def test_schema_field_keeps_wire_name_and_roundtrips() -> None:
    for model in (BenchmarkResult, CapabilityReport, EvidenceManifest):
        fields = model.model_fields
        assert fields["schema_id"].alias == "schema"
        assert "schema" in model.model_json_schema()["properties"]


def test_benchmark_json_uses_schema_key() -> None:
    result = BenchmarkResult(
        workload="w",
        requested_entities=1,
        extracted_entities=1,
        semantic_nodes=0,
        semantic_edges=0,
        snapshot_digest="d",
        stages=(),
    )
    payload = json.loads(result.model_dump_json())
    assert payload["schema"] == "all-in-cad/benchmark/v1"
    assert "schema_id" not in payload
    assert BenchmarkResult.model_validate(payload) == result
    assert BenchmarkResult.model_validate({**payload, "schema": "x"}).schema_id == "x"


def test_models_import_without_shadow_warning() -> None:
    import importlib

    import all_in_cad.models as models

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        importlib.reload(models)
