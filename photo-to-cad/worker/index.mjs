/**
 * Cloudflare Worker control-plane only. Never processes photos or runs GPU work.
 * Configure JOB_BACKEND_URL, JOB_BACKEND_TOKEN, API_BEARER_TOKEN as secrets/config.
 * No deployment is done by this repository.
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const json = (body, status = 200) => new Response(JSON.stringify(body), {
      status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", "x-content-type-options": "nosniff" }
    });
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ status: "ok", service: "photo-to-cad-controller", backend_configured: Boolean(env.JOB_BACKEND_URL) });
    }
    if (request.method !== "POST" || url.pathname !== "/jobs") return json({ error: "not_found" }, 404);
    if (!env.API_BEARER_TOKEN || request.headers.get("authorization") !== `Bearer ${env.API_BEARER_TOKEN}`) {
      return json({ error: "unauthorized" }, 401);
    }
    if (!env.JOB_BACKEND_URL || !env.JOB_BACKEND_TOKEN) return json({ error: "backend_not_configured" }, 503);
    if (Number(request.headers.get("content-length") || "0") > 16384) return json({ error: "payload_too_large" }, 413);
    const bodyText = await request.text();
    if (new TextEncoder().encode(bodyText).length > 16384) return json({ error: "payload_too_large" }, 413);
    let body;
    try { body = JSON.parse(bodyText); } catch { return json({ error: "invalid_json" }, 400); }
    if (!body || typeof body !== "object" || Array.isArray(body) || typeof body.job_id !== "string" ||
        !/^[A-Za-z0-9_-]{8,64}$/.test(body.job_id) || typeof body.input_manifest_ref !== "string" ||
        !/^[A-Za-z0-9_./-]{1,512}$/.test(body.input_manifest_ref) || body.input_manifest_ref.includes("..")) {
      return json({ error: "invalid_job_manifest" }, 422);
    }
    // Only small references, never untrusted raw URLs, binary uploads or shell commands.
    const backend = new URL(env.JOB_BACKEND_URL);
    if (backend.protocol !== "https:") return json({ error: "backend_requires_https" }, 503);
    const downstream = await fetch(backend.toString(), {
      method: "POST", redirect: "manual",
      headers: { "authorization": `Bearer ${env.JOB_BACKEND_TOKEN}`, "content-type": "application/json" },
      body: JSON.stringify({ job_id: body.job_id, input_manifest_ref: body.input_manifest_ref }),
      signal: AbortSignal.timeout(15000),
    });
    if (downstream.status >= 300 && downstream.status < 400) return json({ error: "unexpected_redirect" }, 502);
    if (downstream.status >= 500) return json({ error: "backend_unavailable" }, 502);
    // Do not echo arbitrary untrusted backend responses back to the requester.
    return json({ forwarded: downstream.ok, status: downstream.status }, downstream.ok ? 202 : 400);
  }
};
