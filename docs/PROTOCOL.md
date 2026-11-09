# Native protocol

All-In-Cad uses one host-neutral local protocol for AutoCAD 2027 and ZWCAD 2026.

## Transport

- Windows Named Pipe.
- Pipe mode: byte stream.
- Framing: 4-byte little-endian unsigned body length followed by UTF-8 JSON.
- Maximum body: 8 MiB.
- Server pipe security: `PipeOptions.CurrentUserOnly`.
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

## AutoCAD 2027 plugin methods and the write approval gate

The AutoCAD 2027 worker (`native/autocad2027/Plugin.cs`) also answers host-specific methods.
Every method that writes to the drawing or executes command-line / LISP text is **gated**:

| Method | Gate |
|---|---|
| `system.ping`, `host.context`, `host.capabilities`, `xicad.state`, `xicad.run_status` | read, never gated |
| `xicad.probe` | gated only when `force_load` is anything but absent / `null` / `false` (it loads a LISP file) |
| `host.draw_line`, `host.send_command`, `xicad.ensure_loaded`, `xicad.draw_wall`, `xicad.draw_door`, `xicad.draw_window`, `xicad.smoke_wall_door_window`, `xicad.run`, `xicad.rollback` | gated |
| `host.plan_write` | issues approval tokens; writes nothing |

Flow (same concept as power-cad-mcp `cad_plan_create` -> `cad_plan_execute`, and as the signed
`ChangePlan` token above):

1. **plan**: `host.plan_write` with `{"method": "host.draw_line", "params": {...}}`. The worker
   validates the operation (including the command policy below), keeps a pending plan and
   returns `plan_id`, `approval_token`, `operation_digest`, `operation`, `expires_at` (unix
   seconds), `ttl_seconds` and `approval_required`.
2. **execute**: call the gated method with the **same** params plus `params.approval_token`.

The token is `base64url(body).base64url(HMAC-SHA256(body))` with a per-process random secret, so a
token only works on the worker process that issued it. It is bound to the method and to
`operation_digest = sha256(canonical_json({"method", "params" minus approval_token}))`, expires
after `AIC_APPROVAL_TTL_SECONDS` (default 120, max 600), and is consumed on first use. Canonical
JSON sorts keys and writes `1000` and `1000.0` the same way, so a client may re-serialize
numbers; any changed value is a mismatch. At most 64 plans can be pending at once.

Refusal codes: `E_APPROVAL_REQUIRED` (no token), `E_APPROVAL_INVALID` (malformed, bad signature,
another process, not pending), `E_APPROVAL_MISMATCH` (other method or params; the token stays
valid for the planned operation), `E_APPROVAL_EXPIRED`, `E_APPROVAL_REUSED`, `E_APPROVAL_BUSY`,
`E_COMMAND_DENIED`, `E_BAD_PARAMS`. `host.capabilities.approval` and `system.ping.approval_required`
report the live setting.

Composite handlers (`xicad.smoke_wall_door_window`) call their parts directly, so one approved
request runs exactly the planned composite and nothing else.

### Command policy (`host.send_command`, `xicad.run`)

Always enforced, also when the token gate is switched off:

- every whitespace-separated token (`._-'+` prefixes stripped, case-insensitive) is checked
  against a denylist, because space/newline is Enter on the command line and a later token can
  start a new command. Default denylist: process/code execution (`SHELL`, `SCRIPT`, `NETLOAD`,
  `APPLOAD`, `ARX`, `VBA*`, ...), customisation (`CUI*`, `MENU*`), file writes and document
  lifecycle (`SAVE*`, `QSAVE`, `WBLOCK`, `EXPORT`, `PLOT`, `PUBLISH`, `OPEN`, `NEW`, `CLOSE*`,
  `QUIT`, `EXIT`, `PURGE`, ...), security settings (`SECURELOAD`, `TRUSTEDPATHS`, ...) and undo
  (`U`, `UNDO`; see the C-2 hazard in `Plugin.cs`). The full list is `SendCommandPolicy.DefaultDenylist`
  in `native/shared/HostWriteGuard.cs`. This is conservative: a `U` option inside `LINE` is refused too;
- any `(` (AutoLISP) is refused unless `AIC_SEND_COMMAND_ALLOW_LISP=1`;
- `AIC_SEND_COMMAND_DENY=NAME,NAME` adds names; `AIC_SEND_COMMAND_ALLOW=LINE,ZOOM` restricts the
  first command to an allowlist (the denylist still wins);
- for `xicad.run` the command and every `args` entry are checked.

### Opt-out (trusted local automation only)

Set `AIC_REQUIRE_APPROVAL=0` (also `false`/`off`/`no`) in the environment of `acad.exe` before
AutoCAD starts. Any other value, or none, keeps the gate on. The worker then logs
`approval SKIPPED (opt-out)` per write and prints a warning on the command line;
`host.plan_write` still issues tokens so the same client code works in both modes. The command
policy is not affected.

The gate stops blind single-call writes and binds what runs to what was planned. It is not by
itself a human in the loop: an MCP client should show the `host.plan_write` result to the user
before sending the execute call. The Python reference implementation is
`src/all_in_cad/host_approval.py`; both sides replay `tests/fixtures/host_approval_vectors.json`.

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
