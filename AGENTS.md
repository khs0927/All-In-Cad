# Agent instructions

All-In-Cad is a safety-oriented multi-CAD runtime. Preserve the separation between analysis, planning, execution, and verification.

## Non-negotiable rules

1. Never let an LLM directly mutate a DWG through arbitrary code when a typed operation exists.
2. Every write plan carries `document_id`, `expected_revision`, and `idempotency_key`.
3. A write plan requires explicit approval before native execution.
4. Prefer ZWCAD 2026 native execution for normal drafting; AutoCAD 2027 native execution is the alternate writer.
5. Treat Autodesk Official MCP primarily as an independent analysis/verification channel.
6. Every successful native write must be read back and independently cross-checked when possible.
7. Same-file simultaneous writes from AutoCAD and ZWCAD are forbidden. The runtime must acquire one document write lease.
8. LGPL components such as PyRx remain external dependencies/adapters. Do not copy their source into this repository.
9. Unknown layers/entities are preserved and flagged; never delete them by inference.
10. Raw AutoLISP / arbitrary host code remains an opt-in fallback and must never be the default execution path.

## Target hosts

- AutoCAD 2027 / .NET 10 / ObjectARX 26
- ZWCAD 2026 / ZRX or ZRX.NET
- Headless DXF verification with ezdxf
