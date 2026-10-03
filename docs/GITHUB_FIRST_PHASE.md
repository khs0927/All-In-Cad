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
- ZWCAD 2026 .NET Framework 4.8 adapter bound to the installed `ZwManaged.dll` and
  `ZwDatabaseMgd.dll`, with current-user ACL, read-only RPC, and UI-thread context dispatch;
- `AIC_STATUS` visibility into named-pipe listener readiness and its latest startup error;
- a .NET smoke client for both host pipe names and capability contracts;
- Windows doctor detection of an installed .NET SDK, rather than only the `dotnet` launcher.

The ZWCAD adapter built locally against the installed 2026 assemblies with zero warnings and zero
errors. Both adapters are read-only bring-up workers; a successful build is not live-host evidence.

## Still requires a live CAD session

- Autodesk official MCP endpoint activation and inspection;
- loading either native adapter and recording ping/context/capability smoke output;
- loading PyRx in each host and capturing capability JSON;
- actual DBObject/ZRX transactions, readback, and cross-lane checks;
- performance and semantic validation on representative real DWGs.

A local AutoCAD 2027 blank-template attempt returned from the `NETLOAD` call, but the expected PID
pipe did not become reachable and the smoke client timed out. The automation session could not
access a main CAD window or command history, so extension initialization could not be confirmed;
the live load remains unverified. `AIC_STATUS` reports listener readiness and the latest listener
error when run in the host. A blank ZWCAD 2026 session was also attempted: NETLOAD did not expose the expected PID pipe, the smoke client timed out, and the test process exited. No drawing was opened or changed; plugin initialization remains unconfirmed and live loading is unverified.

Both bring-up workers expose read-only ping, context, and capability discovery. `plan.execute` and
all drawing mutation methods return `E_METHOD_DISABLED`; neither reports revision tracking. Extend
the workers from observed capability JSON and live evidence, not from assumed API parity.

The next live milestone is the same capability matrix across Autodesk Official MCP,
`bimwright/dwg-mcp` 2027, PyRx in AutoCAD 2027, and PyRx in ZWCAD 2026.
