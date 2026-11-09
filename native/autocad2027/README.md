# AutoCAD 2027 native adapter

Target: AutoCAD 2027, .NET 10, Managed ObjectARX API.

This directory is intentionally a thin host shell. Protocol, planning, and semantic logic belong in the host-neutral control plane. The plugin should only expose bounded document/context/transaction operations over a local Named Pipe.

Before production use, borrow or adapt reviewed patterns from `bimwright/dwg-mcp` and `beiming183-cloud/AutoCAD-MCP` rather than expanding this placeholder blindly.

Build and load a test session (nothing is installed; the assembly stays loaded until AutoCAD exits):

```powershell
dotnet build native/autocad2027/AllInCad.AutoCAD2027.csproj -c Release -p:ACAD2027_DIR="C:\Program Files\Autodesk\AutoCAD 2027"
```

Copy `bin/Release` elsewhere first, because AutoCAD locks the loaded DLL, then run `NETLOAD` on the copy. The worker listens on `\\.\pipe\all-in-cad-acad-<pid>`.

Required production properties:

- `DocumentLock` plus native `Transaction` for writes;
- current-user Named Pipe ACL;
- session token option;
- document ID + monotonic revision;
- idempotency key;
- fail-closed stale-revision checks;
- handle readback and requested/actual/diff;
- no arbitrary C# execution by default;
- write approval gate (implemented): every writing / command-executing pipe method needs a
  single-use token from `host.plan_write` (`native/shared/HostWriteGuard.cs`, ON by default,
  opt-out `AIC_REQUIRE_APPROVAL=0` in the acad.exe environment), and `host.send_command` /
  `xicad.run` pass an always-on command denylist. See `docs/PROTOCOL.md`.

Repeatable host check: `scripts/windows-live-smoke.ps1` (`-DryRun` detection, default read-only pipe ping/context,
`-Write` one LINE on a new `%TEMP%` drawing with COM readback). Evidence goes to `artifacts/live-smoke/` and is promoted
into `docs/verification-ledger.json` / `docs/ROADMAP.md` with `all-in-cad live-smoke ingest <evidence> --apply`.
