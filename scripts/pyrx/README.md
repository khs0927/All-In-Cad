# PyRx common-host probe

Use the same script in AutoCAD 2027 and ZWCAD 2026 so the first comparison is about actual API availability, not assumptions.

1. Install/load the matching PyRx ARX/ZRX loader according to the pinned PyRx release.
2. Set `AIC_HOST_LABEL=autocad-2027` before starting AutoCAD, or `AIC_HOST_LABEL=zwcad-2026` before starting ZWCAD.
3. Run `PYLOAD` and load `scripts/pyrx/aic_probe.py`.
4. Run `AIC_PYRX_PROBE`.
5. Compare the two JSON reports under `%LOCALAPPDATA%\All-In-Cad\artifacts`.

The initial probe is intentionally read-only. Mutation benchmarks are added only after host identity, transaction semantics, and rollback behavior are proven on both applications.
