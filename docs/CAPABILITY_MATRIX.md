# Capability matrix and benchmark workflow

All-In-Cad treats a feature as supported only when a probe or a real transaction provides evidence. Missing data stays `unknown`; it is not silently promoted to supported or unsupported.

## Canonical states

- `supported` — the probe observed or an adapter explicitly proved the capability.
- `unsupported` — the probe positively observed that the surface is absent.
- `unknown` — the probe did not test the capability or did not provide enough evidence.
- `error` — the probe attempted the capability and failed.

Evidence is classified as `observed`, `declared`, or `inferred`.

## AutoCAD 2027 and ZWCAD 2026 PyRx reports

Load the same `scripts/pyrx/aic_probe.py` in each host and set a distinct label before running it:

```powershell
$env:AIC_HOST_LABEL = "autocad-2027"
```

or:

```powershell
$env:AIC_HOST_LABEL = "zwcad-2026"
```

Run `AIC_PYRX_PROBE` inside the CAD host. The probe writes JSON under the local All-In-Cad artifacts directory.

Compare the two reports:

```powershell
all-in-cad capability-matrix `
  --report ".aic\pyrx-probe-autocad-2027.json" `
  --report ".aic\pyrx-probe-zwcad-2026.json"
```

The output contains:

- normalized reports;
- one matrix row per canonical capability;
- `common_supported` for capabilities proven in every supplied report;
- PyRx read-baseline gaps for each host.

The PyRx read baseline currently requires current database access, model-space access, LINE, POLYLINE, block-reference, DBText, MText, DIMENSION and layer-table surfaces.

## ACadSharp capability report

The headless probe can report its compile/runtime reader surface:

```powershell
dotnet .\native\headless\bin\Release\net10.0\AllInCad.ACadSharpProbe.dll --capabilities > .aic\acadsharp-capabilities.json
```

This report proves that the DWG reader/census surface is present. It deliberately does **not** claim normalized geometry fidelity; that remains `unknown` until representative DWGs are checked.

## Synthetic benchmark

Run a deterministic LINE workload:

```powershell
all-in-cad benchmark --workdir ".aic\bench" --entities 10000
```

The benchmark reports separate timings for:

1. DXF generation;
2. recover/audit + normalized extraction;
3. semantic graph construction.

The report schema is `all-in-cad/benchmark/v1` and includes entity counts and a snapshot digest. CI exercises this path only for correctness. Wall-clock numbers are **not** pass/fail gates because shared CI runners are not stable performance environments.

## Production benchmark rule

Real performance decisions such as adding an R-tree/spatial index must be made only after running the same structured benchmark against representative project drawings on the target Windows workstation. Synthetic results are a regression instrument, not proof of production throughput.
