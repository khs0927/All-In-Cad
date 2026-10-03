# Native protocol

All-In-Cad uses one host-neutral local protocol for AutoCAD 2027 and ZWCAD 2026.

## Transport

- Windows Named Pipe.
- Pipe mode: byte stream.
- Framing: 4-byte little-endian unsigned body length followed by UTF-8 JSON.
- Maximum body: 8 MiB.
- Server pipe security is restricted to the current user: `PipeOptions.CurrentUserOnly` for
  .NET 10 and an explicit current-user SID ACL for the .NET Framework 4.8 ZWCAD adapter.
- One host process owns one pipe endpoint. A document write lease still prevents the same DWG
  from being modified by both hosts concurrently.
- Optional per-session token adds defense in depth. It is not a replacement for the signed write
  approval token.

This framing follows the reviewed, pinned MIT implementation in
`beiming183-cloud/AutoCAD-MCP@11f7c47e5038796a20451b38b23032e625b5aa26`.
All-In-Cad implements its own protocol namespace and request contracts.

## Version

`aic.native/1`

A peer must reject unknown major protocol versions rather than guessing compatibility.

## Methods

| Method | Read/write | Purpose |
|---|---|---|
| `system.ping` | read | liveness |
| `host.context` | read | host, version, active document and revision |
| `host.capabilities` | read | capability discovery |
| `document.snapshot` | read | normalized document/entity snapshot |
| `entity.read` | read | read one native entity by stable handle |
| `plan.execute` | write | execute an approved `ChangePlan` atomically |
| `verification.capture` | read | collect post-write native verification evidence |

The current AutoCAD 2027 and ZWCAD 2026 bring-up adapters report only `system.ping`,
`host.context`, and `host.capabilities` under `read`, an empty `write` list,
`write_enabled: false`, and `revision_tracking: false`. Omitted methods are not advertised and
continue to fail closed. The ZWCAD adapter reads document context on the CAD UI thread.

## Write invariants

`plan.execute` must fail closed unless all are true:

1. protocol/session validation succeeds;
2. the signed approval token validates the exact `ChangePlan`;
3. `document_id` matches the active document;
4. `expected_revision` matches the host revision;
5. the document write lease is acquired;
6. the idempotency key is new or maps to the already-committed identical request;
7. the native host transaction commits;
8. post-write readback is captured.

## Readback

Host workers return normalized entity snapshots. A snapshot digest deliberately ignores host
runtime wrapper identity and uses stable handle + normalized geometry/properties. The independent
DXF lane can therefore compare semantic results even when runtime object wrappers differ.
