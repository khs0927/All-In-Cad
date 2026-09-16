# Architecture

## Goal

All-In-Cad is not another thin MCP command wrapper. It is a multi-CAD semantic runtime that can understand legacy DWG state, produce a bounded change plan, execute through the correct native host, and prove the result through independent evidence.

## Runtime topology

```text
LLM / MCP client
      |
      v
All-In-Cad Control Plane
  |-- Capability Router
  |-- Semantic CAD Graph
  |-- ChangePlan validator
  |-- Approval / document lease
  |-- Verification coordinator
      |
      +--> AutoCAD Official MCP ------> analysis / standards / verification
      +--> AutoCAD 2027 native -------> Named Pipe -> .NET 10/ObjectARX
      +--> ZWCAD 2026 native ---------> PyRx/ZRX and/or ZRX.NET
      +--> ZWCAD LISP fallback -------> File IPC / AutoLISP
      +--> ezdxf headless ------------> independent DXF verification
```

## Write authority

The default writer is ZWCAD 2026 native. AutoCAD 2027 native becomes the writer for AutoCAD-specific capabilities or when the operator selects it. Autodesk Official MCP is not selected as the default writer even if a preview exposes mutation tools; keeping it independent makes it more valuable as a second opinion.

Only one host may own the write lease for a physical DWG at a time. A second host may use a read-only snapshot or reload after the authoritative writer saves.

## Transaction contract

Every write request is lowered to a `ChangePlan` with stable plan ID/idempotency key, document ID/expected revision, handle-addressed targets, typed operations, pre/postconditions, and evidence requirements. Native execution fails closed on stale revisions, wrong documents, missing capabilities, or unmet preconditions.

## Verification contract

A successful tool response is not proof. After write commit: read changed entities back from the native database, re-scan the drawing/exported DXF, compare census and geometry digest, optionally capture a viewport image, and store evidence with the execution receipt. Production acceptance should use at least two independent evidence channels when available.

## Semantic graph

Phase 1 maps deterministic layer conventions and native entity relations. Later phases add topology, block ownership, room closure, wall-door-window relationships, dimensions, xrefs, and IFC grounding. Unknown entities remain in the graph as `unknown`; they are never silently discarded.
