# Safety and transaction contract

All-In-Cad treats every DWG mutation as a guarded transaction rather than an LLM tool call.

## Required fences

A write may execute only when: the adapter advertises the capability; active document/revision match the plan; the short-lived operator approval token matches the exact plan digest; the idempotency key is not reused for another request; the physical drawing has one exclusive write lease; and operation preconditions pass.

## Approval token

The bootstrap uses a short-lived HMAC-SHA256 token containing plan ID, document ID, expected revision, SHA-256 plan digest, expiry, and nonce. The signing secret stays in the control plane.

## Idempotency

Accepted, committed, and failed requests are journaled. Replaying a committed request with the same key and exact digest returns the original receipt without executing the host twice. Reusing a key with a different digest fails closed.

## Rollback semantics

Native AutoCAD/ZWCAD adapters are expected to use host database transactions. The in-memory adapter in tests proves the contract with snapshot rollback. LISP/File-IPC compatibility paths must explicitly report weaker rollback guarantees.

## Cross-CAD rule

AutoCAD and ZWCAD may both be open, but they must not concurrently write the same physical DWG. Default mode is ZWCAD authoritative writer and AutoCAD analysis/verification mirror. Writer switching happens only after lease release and host reload.


## Source identity fence

When a plan originates from AEC Ontology context, `DocumentRef.source_binding` carries an
`aec-executor-handoff/1` reference. It is accepted only for the same live `document_id`
and remains explicitly non-authorizing. Native executors still require their normal
approval, revision, lease, transaction and read-back fences.

Execution receipts may carry the Ontology handoff digest plus source/revision identifiers.
This creates an auditable loop `Ontology SOURCE_BOUND -> CAD execution -> receipt ->
Ontology evidence` without making GraphRAG or source lookup an execution authority.


## Drawing grammar hint boundary

`ChangePlan.drawing_grammar` may carry an HS-CAD
`cad-drawing-grammar/1` contract. The grammar is a frozen read-only hint for
layer/text/dimension/style generation. Its sample digest and nearby entity count
are validated, while `execution_authorized=false` and
`may_execute_mutation=false` are invariant.

A drawing grammar never replaces document binding, revision checks, approval,
native transaction fences, or read-back verification.
