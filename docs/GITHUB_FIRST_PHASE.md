# GitHub-first completion phase

This phase separates repository-verifiable behavior from tests that require running AutoCAD or
ZWCAD. Live host behavior is never guessed.

## Completed in repository

- protocol envelope and 4-byte little-endian frame codec;
- incremental frame decoder and 8 MiB fail-closed limit;
- optional session-token gate;
- host-neutral RPC dispatcher;
- stable native method registry;
- normalized entity snapshots and deterministic requested/actual diff primitives;
- shared .NET 10 protocol library with `PipeOptions.CurrentUserOnly`;
- protocol conformance tests;
- CI build of the vendor-neutral .NET protocol assembly;
- AutoCAD 2027 adapter wiring for read-only ping and document context;
- `AIC_STATUS` visibility into named-pipe listener readiness and its latest startup error;
- UI-thread dispatch for AutoCAD context reads;
- a .NET smoke client for the shared framed protocol;
- Windows doctor detection of an installed .NET SDK, rather than only the `dotnet` launcher.

## Still requires a live CAD session

- Autodesk official MCP endpoint activation and inspection;
- loading the AutoCAD adapter and recording ping/context smoke output;
- exact ZRX.NET 2026 managed reference binding and plugin load;
- loading PyRx in each host and capturing capability JSON;
- actual DBObject/ZRX transactions, readback, and cross-lane checks;
- performance and semantic validation on representative real DWGs.

A local AutoCAD 2027 blank-template attempt returned from the `NETLOAD` call, but the expected PID pipe did not become reachable and the smoke client timed out. The automation session could not access a main CAD window or command history, so extension initialization could not be confirmed; the live load remains unverified. `AIC_STATUS` now reports listener readiness and the latest listener error when run in the host.

The AutoCAD bring-up worker is deliberately read-only: `plan.execute` and all drawing mutation methods return `E_METHOD_DISABLED`. Its context response states that revision tracking is not enabled. Extend the worker from observed capability JSON and live evidence, not from assumed API parity.
