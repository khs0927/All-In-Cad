from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .extraction_runtime import normalize_dxf
from .pipeline import snapshot_digest
from .semantic_graph import build_semantic_graph


class BenchmarkStage(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    elapsed_ms: float = Field(ge=0)


class BenchmarkResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema: str = "all-in-cad/benchmark/v1"
    workload: str
    requested_entities: int = Field(gt=0)
    extracted_entities: int = Field(ge=0)
    semantic_nodes: int = Field(ge=0)
    semantic_edges: int = Field(ge=0)
    snapshot_digest: str
    stages: tuple[BenchmarkStage, ...]


def run_synthetic_dxf_benchmark(
    workspace: str | Path,
    *,
    entity_count: int = 1000,
) -> BenchmarkResult:
    """Generate and process a deterministic DXF workload without performance pass/fail gates."""
    if entity_count <= 0:
        raise ValueError("entity_count must be positive")

    try:
        import ezdxf
    except ImportError as exc:  # pragma: no cover - packaging/configuration error
        raise RuntimeError("install All-In-Cad with the 'headless' extra") from exc

    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"synthetic-{entity_count}.dxf"

    started = time.perf_counter()
    doc = ezdxf.new("R2018")
    doc.layers.add("CEN")
    modelspace = doc.modelspace()
    for index in range(entity_count):
        x = float(index % 1000)
        y = float(index // 1000)
        modelspace.add_line((x, y), (x + 0.5, y), dxfattribs={"layer": "CEN"})
    doc.saveas(path)
    generate_ms = _elapsed_ms(started)

    started = time.perf_counter()
    extraction = normalize_dxf(path, document_id=path.name)
    extraction_ms = _elapsed_ms(started)

    started = time.perf_counter()
    snapshots = list(extraction.snapshots)
    graph = build_semantic_graph(snapshots, snap_tolerance=0.01)
    semantic_ms = _elapsed_ms(started)

    return BenchmarkResult(
        workload="synthetic-dxf-lines",
        requested_entities=entity_count,
        extracted_entities=len(snapshots),
        semantic_nodes=len(graph.nodes),
        semantic_edges=len(graph.edges),
        snapshot_digest=snapshot_digest(snapshots),
        stages=(
            BenchmarkStage(name="generate_dxf", elapsed_ms=generate_ms),
            BenchmarkStage(name="extract_normalize", elapsed_ms=extraction_ms),
            BenchmarkStage(name="semantic_graph", elapsed_ms=semantic_ms),
        ),
    )


def _elapsed_ms(started: float) -> float:
    return max(0.0, (time.perf_counter() - started) * 1000.0)
