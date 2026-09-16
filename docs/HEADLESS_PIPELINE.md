# Headless DWG/DXF pipeline

The headless lane exists so All-In-Cad can inventory and understand drawings without opening AutoCAD or ZWCAD. It is read-only with respect to source drawings.

## Extraction order

For DWG inputs the runtime uses a deterministic fallback order:

1. **ACadSharp** direct DWG parsing through the separate `AllInCad.ACadSharpProbe` process.
2. **ODA File Converter** through `ezdxf.addons.odafc.convert()` when the ODA converter is installed.
3. **LibreDWG** through the external `dwg2dxf` executable only.
4. The resulting DXF is recovered/audited and normalized with **ezdxf**.

DXF inputs go directly through `ezdxf.recover.readfile()` and auditor checks.

No GPL/LGPL implementation is imported into the core process. LibreDWG is an executable boundary and PyRx remains an external native dependency.

## Normalized output

Supported entities are converted into stable `EntitySnapshot` records containing:

- document ID;
- handle;
- entity type;
- layer;
- normalized geometry;
- normalized properties;
- deterministic digest.

The current geometry baseline covers LINE, LWPOLYLINE, CIRCLE, ARC, TEXT, MTEXT, INSERT and DIMENSION. Unsupported entities remain valid census records with empty or partial geometry rather than being silently deleted.

## Semantic pipeline

Each changed DWG/DXF follows this path:

```text
FileRecord
  -> extraction lane
  -> EntitySnapshot[]
  -> semantic graph
  -> wall/opening/room/annotation inference
  -> SQLite drawing/entity/reference index
  -> persisted semantic nodes/edges/relations
  -> extraction provenance + snapshot digest
```

The project index stores drawing fingerprints. Unchanged drawings are skipped on subsequent runs.

## Tool detection

```powershell
all-in-cad doctor
```

The report includes `acadsharp`, `oda`, `ezdxf`, and `libredwg` availability.

ACadSharp probe path:

```powershell
$env:AIC_ACADSHARP_PROBE = "C:\path\to\AllInCad.ACadSharpProbe.dll"
```

LibreDWG path:

```powershell
$env:AIC_LIBREDWG_DWG2DXF = "C:\path\to\dwg2dxf.exe"
```

ODA File Converter is detected using the current ezdxf `odafc.is_installed()` API. If the default Windows installation path is not sufficient, configure ezdxf's `odafc-addon.win_exec_path` setting with the absolute executable path.

## Index a project tree

```powershell
all-in-cad index `
  --root "Z:\" `
  --db ".aic\project.sqlite3" `
  --workdir ".aic\work"
```

Useful options:

- `--root` can be supplied multiple times.
- `--compute-hash` uses file SHA-256 rather than stat metadata for change detection.
- `--fail-fast` stops on the first drawing failure. Without it, each failing drawing is recorded and the batch continues.
- `--acadsharp-probe` overrides `AIC_ACADSHARP_PROBE`.
- `--libredwg` overrides `AIC_LIBREDWG_DWG2DXF`.

The JSON result reports processed, skipped, ignored and failed paths plus the extraction lane, entity count, semantic counts, warnings and snapshot digest for each processed drawing.

## Evidence policy

A successful extraction is not treated as proof that the drawing was understood correctly. Production acceptance still requires representative real-drawing validation and, after live CAD bring-up, native readback plus independent DXF/visual cross-checking.
