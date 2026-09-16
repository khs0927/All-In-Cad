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

- [ ] AutoCAD 2027: enable Autodesk Assistant Tech Preview and verify official MCP endpoint.
- [ ] AutoCAD 2027: install/test `bimwright/dwg-mcp` 2027 shell.
- [ ] AutoCAD 2027: load All-In-Cad .NET 10 plugin and pass Named Pipe ping/context tests.
- [ ] AutoCAD 2027: load PyRx 26.0 and run the common capability matrix.
- [ ] ZWCAD 2026: load PyRx ZRX loader and run the same capability matrix.
- [ ] ZWCAD 2026: compile/load ZRX.NET adapter scaffold.
- [ ] ZWCAD 2026: install LISP/File-IPC fallback and mark capability gaps.

## Phase 2 — guarded writes

- [ ] Named Pipe protocol with current-user ACL and optional session token.
- [ ] document_id + revision events + idempotency journal.
- [ ] create/move/copy/rotate/scale/offset/erase primitives.
- [ ] layer/block/text/dimension primitives.
- [ ] requested/actual/diff readback.
- [ ] approval token and one-document write lease.

## Phase 3 — architectural semantic graph

- [ ] DWG/DXF census and normalized entity IR.
- [ ] snapping/intersection topology graph.
- [ ] wall pairing/thickness inference.
- [ ] door/window openings and host-wall relations.
- [ ] room/zone closure and adjacency.
- [ ] dimensions and annotation bindings.
- [ ] visual/DXF/native cross-check.

## Phase 4 — project-scale indexing

- [ ] offline `Z:\\` DWG inventory.
- [ ] ACadSharp/ODA/ezdxf extraction lanes with provenance.
- [ ] drawing-level graph database and incremental re-index.
- [ ] cross-file blocks/xrefs/symbol relationships.
