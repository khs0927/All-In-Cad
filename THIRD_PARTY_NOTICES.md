# Third-party notices and provenance

All-In-Cad is an integration runtime, not a blind source-code aggregation repository.

## beiming183-cloud/AutoCAD-MCP

Repository: `https://github.com/beiming183-cloud/AutoCAD-MCP`

Pinned review commit: `11f7c47e5038796a20451b38b23032e625b5aa26`

License: MIT.

Reviewed pattern:

- asynchronous Windows Named Pipe server;
- `PipeOptions.CurrentUserOnly`;
- 4-byte little-endian message length;
- 8 MiB maximum frame;
- exact-read loop and JSON request/response dispatch.

All-In-Cad implements these transport ideas independently under the `aic.native/1` contract.
No upstream source file is vendored here.

See `upstream/upstream.lock.json` for all reviewed upstreams and exact pins.
