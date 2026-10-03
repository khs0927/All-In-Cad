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

## AutoCAD 2027 read-only pipe bring-up

Build the host adapter against the installed AutoCAD 2027 managed API files:

```powershell
$env:DOTNET_ROOT = Join-Path (Get-Location) '.dotnet'
$env:DOTNET_CLI_HOME = Join-Path (Get-Location) '.dotnet-home'
& "$env:DOTNET_ROOT\dotnet.exe" build native\autocad2027\AllInCad.AutoCAD2027.csproj `
  --configuration Release `
  -p:ACAD2027_DIR='E:\Program files\AutoCAD 2027'
```

Load `native\autocad2027\bin\Release\net10.0-windows\AllInCad.AutoCAD2027.dll` with AutoCAD's `NETLOAD` command. Keep AutoCAD's secure loading enabled and add the build output directory to its trusted locations if needed. The adapter exposes read-only `system.ping`, `host.context`, and `host.capabilities`; its capability response advertises no writes. Drawing writes are disabled and revision tracking is not implemented.

With the plugin loaded, run the protocol smoke client using the AutoCAD process ID:

```powershell
$acad = Get-Process acad | Where-Object { $_.Path -like '*\AutoCAD 2027\acad.exe' } | Select-Object -First 1
if (-not $acad) { throw 'AutoCAD 2027 is not running.' }
$acadPid = $acad.Id
& "$env:DOTNET_ROOT\dotnet.exe" run --project native\smoke\AllInCad.SmokeClient.csproj -- $acadPid
```

The pipe name is `all-in-cad-acad-<process id>`. If `AIC_AUTOCAD_SESSION_TOKEN` is set before AutoCAD starts, the smoke client reads the same environment variable for authentication.
## ZWCAD 2026 read-only adapter

The ZWCAD plugin targets .NET Framework 4.8 and binds the managed assemblies from the local
ZWCAD 2026 installation. Build and load instructions are in [native/zwcad2026/README.md](native/zwcad2026/README.md).
The current adapter advertises only ping, context, and capabilities; live loading and native
transactions still require host verification.
