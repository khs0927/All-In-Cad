# ChatGPT Work execution handoff — Photo-to-CAD (2026-11-13)

Project: `khs0927/All-In-Cad`, branch `feature/photo-to-cad-mvp-20261113`, folder `photo-to-cad/`.

## Scope & constraints

- iPhone-only user, no PC required for final usage, free-tier first, no paid GPU/API services without explicit approval.
- Do not modify running Oracle n8n or existing Cloudflare Workers. Do not touch production domains, authentication rules or secrets without authorization. Do not merge to main without approval.
- Do not use Google Street View imagery for reconstruction unless explicit rights exist; use user's original authorized images.
- MapAnything *Apache* weights; never silently substitute a noncommercial checkpoint into a commercial workflow.

## Agent tasks in Work mode

1. Read branch and PR, inspect code and run `pytest` and Node tests, review path traversal, idempotency, concurrency and authentication.
2. Verify exact VWorld WFS layer name, endpoint, response CRS and licensing with an authorized API key through provider docs. Never expose the key.
3. Perform one independent project test using licensed photos of a known building and its reference GIS footprint; collect distinct-camera images, withheld measurements and logs.
4. Run MapAnything Apache on explicitly free GPU compute **only if available**; otherwise mark inference `NOT_RUN`, do not infer a result.
5. Run COLMAP if enough actual parallax and the runtime exists. Export camera poses/point map and record SHA256; quantify differences.
6. Implement model-to-footprint similarity alignment (rotation, translation, scale), rejected outlier report, independent check distances and measured RMSE. Never claim cm accuracy without measured ground truth.
7. Inspect Oracle resources READ-ONLY before changes. Keep existing n8n VM unaffected. Assess a dedicated CPU-only job receiver and TLS/reverse proxy; no deployment without a distinct approval.
8. Inspect Cloudflare Workers free quotas and identify safe dedicated worker name; no deployment before approval.
9. Use IFC mass output only after installing IfcOpenShell and verifying in an independent viewer/parser. Do not represent unvalidated mass as permit-ready BIM.
10. Post a GitHub PR test/evidence report, do not merge.

## Acceptance criteria

- End-to-end with at least one permissioned building photo set, metric geo-controls and independent measured check distances.
- Evidence bundle contains original source hashes, model checkpoint commit/license, GPU/runtime, repro command, coordinates/CRS, transformation matrix, withheld errors and output file hashes.
- Import DXF into ezdxf and IFC into IfcOpenShell independently; preserve source files.
- No accidental spending, no authentication/production changes.

## iPhone shortcuts API (only after an authorized backend is deployed)

Send `POST https://YOUR-CONTROLLER.workers.dev/jobs` with
`Authorization: Bearer <USER_PRIVATE_TOKEN>` and JSON
`{"job_id":"phonejob_001","input_manifest_ref":"project/manifest.json"}`.

This is only a gateway. The manifest must be uploaded separately to the backend's private inbox by an authenticated channel; the backend must never accept arbitrary remote shell commands or user-specified URLs. Check job outputs in an authenticated storage service; no public file downloads are currently implemented.
