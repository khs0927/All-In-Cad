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
- no arbitrary C# execution by default.
