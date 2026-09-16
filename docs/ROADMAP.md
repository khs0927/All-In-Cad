# Roadmap

## Phase 0 — repository bootstrap

- [x] Upstream projects pinned by reviewed commit.
- [x] Vendor-neutral document/entity/change-plan contracts.
- [x] Deterministic capability router.
- [x] Architectural layer semantic baseline.
- [x] Independent evidence cross-check primitive.
- [x] AutoCAD 2027 and ZWCAD 2026 native adapter scaffolds.
- [x] CI for the host-independent core.
- [x] Windows doctor/benchmark scripts.

## Phase 1 — live host bring-up

These items require the Windows CAD host and are intentionally not simulated in CI.

- [ ] AutoCAD 2027: enable Autodesk Assistant Tech Preview and verify official MCP endpoint.
- [ ] AutoCAD 2027: install/test `bimwright/dwg-mcp` 2027 shell.
- [ ] AutoCAD 2027: load All-In-Cad .NET 10 plugin and pass Named Pipe ping/context tests.
- [ ] AutoCAD 2027: load PyRx 26.0 and run the common capability matrix.
- [ ] ZWCAD 2026: load PyRx ZRX loader and run the same capability matrix.
- [ ] ZWCAD 2026: resolve installed ZRX.NET assemblies and compile/load the native adapter.
- [ ] ZWCAD 2026: install LISP/File-IPC fallback and mark capability gaps.

## Phase 2 — guarded writes

Host-independent work is completed first; CAD-specific execution remains live-host work.

- [x] Host-neutral RPC envelope and 4-byte little-endian framing.
- [x] 8 MiB fail-closed frame limit.
- [x] Shared .NET 10 current-user-only Named Pipe server.
- [x] Session-token gate and host-neutral worker dispatcher.
- [x] document revision events and idempotency journal.
- [x] approval token and one-document write lease.
- [x] create/move/copy/rotate/scale/offset/erase operation contracts.
- [x] layer/block/text/dimension operation contracts.
- [x] normalized native snapshot and requested/actual diff model.
- [ ] Bind shared Named Pipe worker to AutoCAD 2027 document/database APIs.
- [ ] Bind shared Named Pipe worker to ZWCAD 2026 ZRX.NET APIs.
- [ ] Execute v1 primitives in real native transactions on both hosts.
- [ ] Capture native post-commit readback from both hosts.

## Phase 3 — architectural semantic graph

- [x] Host-neutral semantic graph data model and relation primitives.
- [x] Initial layer-aware graph construction contracts.
- [x] Deterministic 2D snapping/intersection noding baseline.
- [x] Wall pairing/thickness candidate inference baseline.
- [x] Door/window nearest-host-wall relation baseline.
- [x] Room bounded-face extraction and shared-wall adjacency baseline.
- [x] Explicit dimension/annotation target binding contract.
- [x] Executable DWG/DXF extraction into normalized `EntitySnapshot` IR.
- [x] Persist semantic graph, room, opening-host and annotation relations in project index.
- [ ] Validate normalized census against representative real DWG/DXF drawings.
- [ ] Tune topology gap healing, tolerances, arcs, and bulges against real drawings.
- [ ] Measure wall/opening/room precision and recall on representative fixtures.
- [ ] visual/DXF/native cross-check on live fixtures.

## Phase 4 — project-scale indexing

- [x] Offline DWG inventory model and extraction-lane planner.
- [x] ACadSharp/ODA/ezdxf/LibreDWG provenance contracts.
- [x] SQLite drawing/entity index with incremental file fingerprints.
- [x] Transactional re-index of changed drawing entities/references.
- [x] Conservative cross-file block/xref relationship queries.
- [x] Executable ACadSharp/ODA/LibreDWG/ezdxf extraction adapters.
- [x] Changed-only extraction → semantic analysis → SQLite indexing pipeline.
- [x] Persist extraction lane, warnings and snapshot digest as evidence provenance.
- [ ] Run inventory against the real `Z:\\` drawing tree.
- [ ] Add spatial/indexing acceleration after real project benchmarks.
- [ ] Add sidecar PDF/vector evidence ingestion after real drawing normalization is validated.

## Continuous maintenance

- [x] Upstream pins are machine-validated in core CI.
- [x] Weekly/manual GitHub Action checks pinned commits against current upstream heads.
- [ ] Review upstream audit artifacts before intentionally advancing a pin.
