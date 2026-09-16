# Upstream integration policy

The machine-readable source of truth is `upstream/upstream.lock.json`.

All-In-Cad is not a source-code aggregation repository. Each upstream has one of four roles:

1. **dependency** — consume the released package or executable through a narrow adapter;
2. **reference-and-adapt** — independently implement a reviewed design pattern;
3. **test oracle / compatibility matrix** — compare feature coverage and behavior;
4. **reference-only** — study concepts only; never copy source until licensing is resolved.

## Core execution and host bridges

| Upstream | License | Role | All-In-Cad use |
| --- | --- | --- | --- |
| `bimwright/dwg-mcp` | Apache-2.0 | reference-and-adapt | AutoCAD 2027 plugin shell, discovery, local IPC, handle-based tool patterns |
| `CEXT-Dan/PyRx` | LGPL-3.0 | external dependency | ObjectARX/ZRX common experiments for AutoCAD/ZWCAD; never vendor into this repo |
| `FsDiG/Fs.Fox.CAD` | MIT | package/adapt | ZWCAD managed helpers, DB transactions and shared AutoCAD/ZWCAD patterns |
| `beiming183-cloud/AutoCAD-MCP` | MIT | reference-and-adapt | revision fencing, idempotency, native transaction worker, requested/actual/diff |
| `dalingo81/ZWCAD-MCP` | MIT | fallback reference | ZWCAD 2026 LISP/File-IPC compatibility and operational quirks |

## Headless drawing ingestion and independent evidence

| Upstream | License | Role | All-In-Cad use |
| --- | --- | --- | --- |
| `DomCR/ACadSharp` | MIT | .NET dependency/adapt | **primary unopened-DWG direct parser** before conversion fallbacks |
| `mozman/ezdxf` | MIT | Python dependency | DXF parse, geometry census, recovery/audit and independent verification |
| `LibreDWG/libredwg` | GPL-3.0 | **external executable only** | emergency `dwg2dxf` fallback and independent converter cross-check |
| ODA File Converter | proprietary/external | external tool only | DWG→DXF fallback; not part of the open-source upstream set and never vendored |

The intended unopened-file order is:

```text
DWG
 ├─ ACadSharp direct read → normalized semantic IR
 └─ if unsupported/corrupt
      ├─ ODA File Converter → DXF
      └─ LibreDWG dwg2dxf → DXF
                              ↓
                         ezdxf evidence
```

For DXF evidence, the current ezdxf documentation recommends `ezdxf.recover.readfile()` when structural repair/auditing is desired. It returns `(Drawing, Auditor)`; callers must check the auditor for unrecoverable errors before trusting recovered output. Modelspace entities are then enumerated and their DXF namespace attributes such as `layer` are read for census/verification.

## Planning, semantic and safety references

| Upstream | License | Role | All-In-Cad use |
| --- | --- | --- | --- |
| `LokmenoWer/best-cad-mcp` | MIT | reference-and-adapt | CADPlan, CAD-IR, dry-run, semantic graph and visual evidence concepts |
| `jeremylongshore/cad-ai-agent` | Apache-2.0 | reference-and-adapt | typed ChangeSet, protected-layer and AEC-safe-edit patterns |
| `U-C4N/Autocad-MCP` | MIT | test oracle/reference | broad AutoCAD tool coverage, COM/headless parity, GD&T/dimension correctness gates |
| `AnCode666/multiCAD-mcp` | Apache-2.0 | compatibility matrix | AutoCAD/ZWCAD command coverage and cross-CAD routing behavior |
| `puran-water/autocad-mcp` | MIT | fallback reference | File IPC, focus-free dispatch, AutoLISP and undo/redo behavior |
| `Psalmustrack/lambdacad-mcp` | Apache-2.0 | coverage oracle | AutoLISP-capable CAD portability, broad tool inventory, drawing-graph ideas |

## Reference-only until licensing is resolved

- `A-SHOJAEI/ConstructDrawingAI` — useful CIR/connectivity concepts; GitHub license metadata is `NOASSERTION`.
- `Jovinull/cad-preproc` — useful topology/layer-semantic pipeline; do not copy source before a license review.

## License boundary

- MIT/Apache code may be adapted only after file-level provenance review and attribution handling.
- LGPL dependencies stay dynamically/external-dependency separated.
- GPL tools such as LibreDWG remain separate executables; no LibreDWG source is copied or linked into All-In-Cad.
- Proprietary tools such as ODA File Converter are optional user-installed external tools.

The weekly upstream audit checks whether every pinned commit still exists and whether each pin is current relative to its repository default branch. A pin being behind is a review signal, not an automatic upgrade instruction.
