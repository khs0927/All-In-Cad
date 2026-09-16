# GitHub-first completion phase

The goal of this phase is to finish everything that can be proven without launching AutoCAD or
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
- CI build of the vendor-neutral .NET protocol assembly.

## Still requires the Windows CAD machine

- Autodesk official MCP endpoint activation and inspection;
- actual ObjectARX 2027 managed reference binding;
- exact ZRX.NET 2026 managed reference binding;
- loading the two plugins;
- live Named Pipe ping/context round trips inside each CAD host;
- actual DBObject/ZRX object transactions;
- PyRx capability probe results;
- performance measurements on real DWGs.

The repository should be extended from observed capability JSON, not from assumed API parity.
