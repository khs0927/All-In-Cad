import assert from 'node:assert/strict';
import test from 'node:test';
import gateway from '../worker/index.mjs';

const origin = 'https://test.example';
const make = (payload, bearer) => new Request(origin + '/jobs', {
  method: 'POST', headers: { 'content-type': 'application/json', ...(bearer ? { authorization: 'Bearer ' + bearer } : {}) },
  body: JSON.stringify(payload),
});

test('health is public and explicitly reports unset backend', async () => {
  const resp = await gateway.fetch(new Request(origin + '/health'), {});
  assert.equal(resp.status, 200);
  assert.equal((await resp.json()).backend_configured, false);
});

test('jobs reject missing bearer', async () => {
  const resp = await gateway.fetch(make({ job_id: 'job_12345', input_manifest_ref: 'project/manifest.json' }), { API_BEARER_TOKEN: 'abc' });
  assert.equal(resp.status, 401);
});

test('jobs reject unknown data', async () => {
  const env = { API_BEARER_TOKEN: 'abc', JOB_BACKEND_URL: 'https://oracle.example/jobs', JOB_BACKEND_TOKEN: 'def' };
  const resp = await gateway.fetch(make({ job_id: 'job_12345', input_manifest_ref: '../secret' }, 'abc'), env);
  assert.equal(resp.status, 422);
});

test('jobs return backend-not-configured before any outgoing request', async () => {
  const resp = await gateway.fetch(make({ job_id: 'job_12345', input_manifest_ref: 'project/manifest.json' }, 'abc'), { API_BEARER_TOKEN: 'abc' });
  assert.equal(resp.status, 503);
});
