# All-In-Cad

**Multi-CAD Semantic Runtime for AutoCAD 2027 + ZWCAD 2026.**

All-In-Cad is designed around one rule: an AI agent does not get to call a CAD command and assume the drawing is correct. The runtime separates **inspect → understand → plan → approve → transact → read back → independently verify**.

## Target operating model

- **ZWCAD 2026** is the preferred day-to-day write host.
- **AutoCAD 2027** provides Autodesk Official MCP analysis/standards checks and a separate native ObjectARX/.NET 10 writer when AutoCAD-specific execution is needed.
- **PyRx** is evaluated first as the common ObjectARX/ZRX native bridge and stays an external LGPL dependency.
- **ZRX.NET + Fs.Fox.CAD** provide the managed ZWCAD path where native Python binding coverage is insufficient.
- **ZWCAD-MCP LISP/File-IPC** remains a fallback, not the primary execution architecture.
- **ACadSharp → ODA → LibreDWG → ezdxf** form the unopened-drawing extraction/evidence lanes.

## Runtime flow

```text
LLM / MCP client
      |
      v
All-In-Cad control plane
      |-- capability router
      |-- semantic CAD graph
      |-- ChangePlan + revision/idempotency fences
      |-- approval + document write lease
      |-- verification coordinator
      |
      +--> Autodesk Official MCP ---- analysis / verify
      +--> AutoCAD 2027 native ------ .NET 10 / ObjectARX / Named Pipe
      +--> ZWCAD 2026 native -------- PyRx/ZRX and ZRX.NET
      +--> ZWCAD LISP fallback ------ compatibility only
      +--> headless extraction ------- ACadSharp / ODA / LibreDWG / ezdxf
```

## Current bootstrap

The repository now contains:

- typed document/entity/change-plan/execution/verification contracts;
- deterministic host capability routing;
- the architectural layer semantic baseline (`COL`, `WAL1/2/3`, `ELE`, `DOOR*`, `WIN*`, `STAIR`, `DIM*`, `CEN*`);
- pinned upstream research with explicit license/integration policy;
- AutoCAD 2027 and ZWCAD 2026 native project scaffolds;
- executable headless DWG/DXF extraction and normalized `EntitySnapshot` IR;
- changed-only extraction → semantic graph/room/opening inference → SQLite indexing;
- extraction provenance, snapshot digests and semantic relations persisted in the project index;
- Windows doctor/benchmark scripts;
- host-independent CI and tests.

See `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/HEADLESS_PIPELINE.md`, and `upstream/upstream.lock.json`.

## Local core test

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,headless]"
pytest
```

## Headless indexing

Check available extraction lanes:

```powershell
all-in-cad doctor
```

Incrementally index a drawing tree without opening AutoCAD/ZWCAD:

```powershell
all-in-cad index `
  --root "Z:\" `
  --db ".aic\project.sqlite3" `
  --workdir ".aic\work"
```

For ACadSharp direct-DWG parsing, point the runtime at the built probe:

```powershell
$env:AIC_ACADSHARP_PROBE = "C:\path\to\AllInCad.ACadSharpProbe.dll"
```

LibreDWG remains an external GPL executable boundary:

```powershell
$env:AIC_LIBREDWG_DWG2DXF = "C:\path\to\dwg2dxf.exe"
```

ODA File Converter is detected through `ezdxf.addons.odafc.is_installed()` and can be configured through ezdxf's `odafc-addon` executable-path settings. Original DWG/DXF files are never modified by the headless indexing pipeline.

## Windows CAD bring-up

After AutoCAD 2027 installation is complete:

```powershell
.\scripts\windows\doctor.ps1
.\scripts\windows\benchmark_hosts.ps1
```

The next live milestone is the same capability matrix across Autodesk Official MCP, `bimwright/dwg-mcp` 2027, PyRx in AutoCAD 2027, and PyRx in ZWCAD 2026.

### Extraction probe evidence

Capability reports retain `available` for extraction routing, but it means installation detection only. Reports identify `verification_kind=installation_detection`, execution and fixture status as `NOT_RUN`, and `execution_allowed=false`. Even a supplied version or a detected executable is not a validated DWG capability. Actual extraction results and independent fixture verification must be recorded separately; these probes never authorize CAD mutations.
