from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

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

    args = parser.parse_args(argv)
    probes = detect_tool_probes(
        acadsharp_executable=args.acadsharp_probe,
        libredwg_executable=args.libredwg,
    )

    if args.command == "doctor":
        print(json.dumps(capability_report(probes), indent=2, sort_keys=True))
        return 0

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
