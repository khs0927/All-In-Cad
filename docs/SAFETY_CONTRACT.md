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
