# Third-party notices and provenance

All-In-Cad is an integration runtime, not a blind source-code aggregation repository.

The authoritative reviewed revisions live in `upstream/upstream.lock.json`. No upstream repository is copied wholesale into this repository.

## Runtime/package dependencies

- **PyRx** — LGPL-3.0. Kept as an external dependency for ObjectARX/ZRX experiments; not vendored.
- **ezdxf** — MIT. Optional Python `headless` dependency for DXF census, recovery/audit and independent evidence.
- **ACadSharp** — MIT. Used through a .NET package/probe boundary for unopened DWG parsing; source is not vendored.
- **Fs.Fox.CAD** — MIT. Intended as a package or narrowly adapted helper layer for ZWCAD managed API work.

## External executable boundaries

- **LibreDWG** — GPL-3.0. May be invoked only as a separately installed executable such as `dwg2dxf`. All-In-Cad does not copy or link LibreDWG source.
- **ODA File Converter** — proprietary external utility. Optional user-installed DWG→DXF fallback; not an open-source dependency and never vendored.

## Reviewed design/reference projects

The following projects are pinned for architecture review, compatibility comparison, or independently reimplemented patterns. Pinning them does **not** mean their source files are included in All-In-Cad:

- `bimwright/dwg-mcp` — Apache-2.0
- `beiming183-cloud/AutoCAD-MCP` — MIT
- `LokmenoWer/best-cad-mcp` — MIT
- `jeremylongshore/cad-ai-agent` — Apache-2.0
- `dalingo81/ZWCAD-MCP` — MIT
- `U-C4N/Autocad-MCP` — MIT
- `AnCode666/multiCAD-mcp` — Apache-2.0
- `puran-water/autocad-mcp` — MIT
- `Psalmustrack/lambdacad-mcp` — Apache-2.0

## Pattern provenance: beiming183-cloud/AutoCAD-MCP

Reviewed pattern:

- asynchronous Windows Named Pipe server;
- `PipeOptions.CurrentUserOnly`;
- 4-byte little-endian message length;
- 8 MiB maximum frame;
- exact-read loop and JSON request/response dispatch.

All-In-Cad implements these transport ideas independently under the `aic.native/1` contract. No upstream source file is vendored here.

## Reference-only projects

`A-SHOJAEI/ConstructDrawingAI` and `Jovinull/cad-preproc` remain concept references only until their licensing/provenance is explicitly cleared for any source-level reuse.

Before copying any individual permissively licensed source file in the future, preserve its required notices and record file-level provenance in this document or a dedicated provenance manifest.
