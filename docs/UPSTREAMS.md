# Upstream integration policy

The machine-readable source of truth is `upstream/upstream.lock.json`.

## Adopt first

- **bimwright/dwg-mcp** — AutoCAD 2027 shell/Named Pipe/discovery pattern. Apache-2.0.
- **PyRx** — external LGPL dependency for ObjectARX/ZRX common experiments. Do not vendor.
- **Fs.Fox.CAD** — MIT helper layer for ZWCAD 2026 managed API work and DB transaction patterns.
- **beiming183-cloud/AutoCAD-MCP** — MIT safety patterns: native transaction worker, revision fencing, idempotency, readback.
- **best-cad-mcp** — MIT CADPlan/CAD-IR/dry-run/semantic-graph patterns.
- **cad-ai-agent** — Apache-2.0 ChangeSet and AEC-safe-edit patterns.
- **ZWCAD-MCP** — MIT fallback path and ZWCAD 2026 operational quirks.
- **ezdxf** — MIT independent file-based verification.

## Do not copy yet

Projects with unclear or unreviewed licensing stay reference-only. Architecture ideas may be reimplemented from documented concepts, but source code is not copied until its license is explicitly reviewed.
