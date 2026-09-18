# AutoCAD 2027 native adapter

Target: AutoCAD 2027, .NET 10, Managed ObjectARX API. Build references are taken from the installed `AcCoreMgd.dll`, `AcDbMgd.dll`, and `AcMgd.dll` files; none are copied into this repository.

The current adapter starts a current-user-only Named Pipe at `all-in-cad-acad-<process id>`. It supports read-only `system.ping`, `host.context`, and `host.capabilities` requests. The capability response lists only those read methods and an empty write list. AutoCAD document metadata is read on AutoCAD's UI thread. The response reports `revision_tracking: false` and `write_enabled: false`; no drawing transaction method is enabled. `AIC_AUTOCAD_SESSION_TOKEN` can add a session-token check when set before AutoCAD starts.

Build the adapter with `ACAD2027_DIR` set to the AutoCAD 2027 install folder. Load the output assembly through `NETLOAD`; leave `SECURELOAD` enabled and trust the build output folder through AutoCAD's trusted-location settings when required. The sibling `native/smoke` client checks framed ping and context responses by process ID.

Still required before production use:

- live load and pipe ping/context evidence from AutoCAD 2027;
- revision events and monotonic revision persistence;
- typed write plans with approval, idempotency, and one-document lease checks;
- native `DocumentLock` and `Transaction` execution;
- post-commit handle readback and independent verification;
- reviewed upstream compatibility patterns from `bimwright/dwg-mcp` and `beiming183-cloud/AutoCAD-MCP`.
