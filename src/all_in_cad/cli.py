from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .benchmark import run_synthetic_dxf_benchmark
from .capabilities import (
    PYRX_READ_BASELINE,
    build_capability_matrix,
    capability_gaps,
    normalize_probe_payload,
)
from .cross_lane import verify_dwg_across_lanes
from .extraction import ExtractionLane, ToolProbe
from .inventory import build_inventory
from .pipeline import process_manifest
from .project_index import ProjectIndex
from .tool_probes import capability_report, detect_tool_probes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="all-in-cad")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="report headless extraction capabilities")
    _add_probe_arguments(doctor)

    index_parser = subparsers.add_parser(
        "index",
        help="inventory and incrementally index DWG/DXF files without opening CAD",
    )
    index_parser.add_argument("--root", action="append", required=True)
    index_parser.add_argument("--db", required=True)
    index_parser.add_argument("--workdir", required=True)
    index_parser.add_argument("--compute-hash", action="store_true")
    index_parser.add_argument("--fail-fast", action="store_true")
    _add_probe_arguments(index_parser)

    verify_parser = subparsers.add_parser(
        "verify-dwg",
        help="force the same DWG through multiple extraction lanes and compare evidence",
    )
    verify_parser.add_argument("--source", required=True)
    verify_parser.add_argument("--workdir", required=True)
    verify_parser.add_argument(
        "--lane",
        action="append",
        choices=["acadsharp", "oda", "libredwg"],
        help="repeat to select explicit lanes; otherwise all available lanes are used",
    )
    verify_parser.add_argument(
        "--no-digest",
        action="store_true",
        help="compare document/entity/layer census without requiring geometry digest equality",
    )
    _add_probe_arguments(verify_parser)

    matrix_parser = subparsers.add_parser(
        "capability-matrix",
        help="normalize and compare PyRx/ACadSharp probe JSON reports",
    )
    matrix_parser.add_argument("--report", action="append", required=True)

    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="run a reproducible synthetic headless DXF workload",
    )
    benchmark_parser.add_argument("--workdir", required=True)
    benchmark_parser.add_argument("--entities", type=int, default=1000)

    args = parser.parse_args(argv)

    if args.command == "capability-matrix":
        return _run_capability_matrix(args.report)
    if args.command == "benchmark":
        result = run_synthetic_dxf_benchmark(args.workdir, entity_count=args.entities)
        print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0

    probes = detect_tool_probes(
        acadsharp_executable=args.acadsharp_probe,
        libredwg_executable=args.libredwg,
    )
    if args.command == "doctor":
        print(json.dumps(capability_report(probes), indent=2, sort_keys=True))
        return 0
    if args.command == "verify-dwg":
        return _run_verify_dwg(args, probes)

    return _run_index(args, probes)


def _run_capability_matrix(paths: list[str]) -> int:
    reports = []
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"probe report must be a JSON object: {path}")
        reports.append(normalize_probe_payload(payload))

    matrix = build_capability_matrix(reports)
    baseline_gaps = {
        f"{report.adapter}@{report.host}": {
            capability: state.value
            for capability, state in capability_gaps(report, PYRX_READ_BASELINE).items()
        }
        for report in reports
        if report.adapter == "pyrx"
    }
    output = {
        "reports": [report.model_dump(mode="json") for report in reports],
        "matrix": matrix.model_dump(mode="json"),
        "pyrx_read_baseline_gaps": baseline_gaps,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _run_verify_dwg(
    args: argparse.Namespace,
    probes: dict[ExtractionLane, ToolProbe],
) -> int:
    lanes = tuple(ExtractionLane(item) for item in args.lane) if args.lane else None
    manifest = verify_dwg_across_lanes(
        args.source,
        probes,
        args.workdir,
        lanes=lanes,
        require_digest_match=not args.no_digest,
    )
    print(json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0 if manifest.verification.passed else 2


def _run_index(
    args: argparse.Namespace,
    probes: dict[ExtractionLane, ToolProbe],
) -> int:
    manifest = build_inventory(args.root, compute_hash=args.compute_hash)
    db_path = Path(args.db).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(args.workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    with ProjectIndex(db_path) as index:
        result = process_manifest(
            manifest,
            probes=probes,
            workdir=workdir,
            index=index,
            continue_on_error=not args.fail_fast,
        )
        payload = {
            "roots": manifest.roots,
            "inventory_records": len(manifest.records),
            "processed": [
                {
                    "path": item.path,
                    "lane": item.lane.value,
                    "entities": item.index.entity_count,
                    "semantic_nodes": item.semantics.node_count,
                    "semantic_edges": item.semantics.edge_count,
                    "rooms": item.semantics.room_count,
                    "opening_hosts": item.semantics.opening_host_count,
                    "snapshot_digest": item.snapshot_digest,
                    "warnings": list(item.warnings),
                }
                for item in result.processed
            ],
            "skipped": list(result.skipped),
            "ignored": list(result.ignored),
            "failures": [
                {"path": failure.path, "error": failure.error}
                for failure in result.failures
            ],
            "capabilities": capability_report(probes),
        }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if result.failures else 0


def _add_probe_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--acadsharp-probe",
        help="path to AllInCad.ACadSharpProbe executable or DLL; also AIC_ACADSHARP_PROBE",
    )
    parser.add_argument(
        "--libredwg",
        help="path/name of external dwg2dxf executable; also AIC_LIBREDWG_DWG2DXF",
    )


if __name__ == "__main__":
    sys.exit(main())
