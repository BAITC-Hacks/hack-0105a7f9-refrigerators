import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {ApiClientError, buildRecommendationRequest, createApiClient, createLatestRecommender} from '../api-client.mjs';

const fixture = async name => JSON.parse(await readFile(new URL(`../examples/${name}.json`, import.meta.url), 'utf8'));
const sample = await fixture('matched');
const jsonResponse = (value, status = 200) => new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}});

test('sends exactly one JSON request with no browser credentials', async () => {
  const calls = [];
  const client = createApiClient({baseUrl: 'http://localhost:8000/', fetchImpl: async (...args) => {
    calls.push(args); return jsonResponse(sample.response);
  }});
  const response = await client.recommend(sample.request);
  assert.deepEqual(response, sample.response);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'http://localhost:8000/recommendations');
  assert.deepEqual(JSON.parse(calls[0][1].body), sample.request);
  assert.equal(calls[0][1].headers['Content-Type'], 'application/json');
  assert.equal(calls[0][1].credentials, 'omit');
  assert.equal(calls[0][1].headers.Authorization, undefined);
});

test('all business outcomes are success, including empty results', async () => {
  for (const name of ['matched', 'short_with_alternative', 'category_absent', 'no_eligible']) {
    const data = await fixture(name);
    const client = createApiClient({fetchImpl: async () => jsonResponse(data.response)});
    assert.deepEqual(await client.recommend(data.request), data.response);
  }
});

test('catalogue options are read from API with proxy prefix preserved', async () => {
  const options = await fixture('catalogue_options');
  const client = createApiClient({baseUrl: '/api', fetchImpl: async (url, init) => {
    assert.equal(url, '/api/catalogue/options');
    assert.equal(init.method, 'GET');
    assert.equal(init.headers['Content-Type'], undefined);
    return jsonResponse(options);
  }});
  assert.deepEqual(await client.catalogueOptions(), options);
});

test('422 retains field errors; non-JSON proxy error stays a safe HTTP error', async () => {
  const invalid = await fixture('invalid_request');
  const client = createApiClient({fetchImpl: async () => jsonResponse(invalid.response, 422)});
  await assert.rejects(client.recommend(invalid.request), error => {
    assert(error instanceof ApiClientError);
    assert.equal(error.kind, 'http');
    assert.equal(error.status, 422);
    assert.equal(error.details[0].field, 'budget_kzt');
    return true;
  });
  const broken = createApiClient({fetchImpl: async () => new Response('<html>private proxy details</html>', {status: 502})});
  await assert.rejects(broken.recommend(sample.request), error => error.kind === 'http' && error.status === 502 && !error.message.includes('private'));
});

test('HTTP and malformed response errors retain the server request ID', async () => {
  const requestId = '0123456789abcdef0123456789abcdef';
  for (const status of [422, 500, 200]) {
    const client = createApiClient({fetchImpl: async () => new Response('{}', {
      status, headers: {'Content-Type': 'application/json', 'X-Request-ID': requestId},
    })});
    await assert.rejects(client.recommend(sample.request), error =>
      error.requestId === requestId && error.kind === (status === 200 ? 'invalid_response' : 'http'));
  }
  const invalidId = createApiClient({fetchImpl: async () => new Response('{}', {
    status: 500, headers: {'X-Request-ID': 'arbitrary-private-value'},
  })});
  await assert.rejects(invalidId.recommend(sample.request), error => error.requestId === undefined);
});

test('body timeout keeps a received request ID; network failure has no invented ID', async () => {
  const requestId = 'abcdef0123456789abcdef0123456789';
  const timeout = createApiClient({timeoutMs: 15, fetchImpl: async () => ({
    ok: true, headers: new Headers({'X-Request-ID': requestId}), json: () => new Promise(() => {}),
  })});
  await assert.rejects(timeout.recommend(sample.request), error => error.kind === 'timeout' && error.requestId === requestId);
  const offline = createApiClient({fetchImpl: async () => { throw Error('offline'); }});
  await assert.rejects(offline.recommend(sample.request), error => error.kind === 'network' && error.requestId === undefined);
});

test('invalid successful response is not treated as an empty recommendation', async () => {
  for (const payload of [{}, {status: 'matched', cards: []}, '<html>not API</html>']) {
    const client = createApiClient({fetchImpl: async () => jsonResponse(payload)});
    await assert.rejects(client.recommend(sample.request), error => error.kind === 'invalid_response');
  }
});

test('network failure is distinguishable and not retried automatically', async () => {
  let calls = 0;
  const client = createApiClient({fetchImpl: async () => { calls++; throw Error('private transport detail'); }});
  await assert.rejects(client.recommend(sample.request), error => error.kind === 'network' && !error.message.includes('private'));
  assert.equal(calls, 1);
});

test('timeout covers fetch and body decoding and aborts the request', async () => {
  let signal;
  const client = createApiClient({timeoutMs: 15, fetchImpl: async (_url, init) => {
    signal = init.signal;
    return {ok: true, json: () => new Promise(() => {})};
  }});
  await assert.rejects(client.recommend(sample.request), error => error.kind === 'timeout');
  assert.equal(signal.aborted, true);
});

test('cancellation before and during a request', async () => {
  let calls = 0;
  const client = createApiClient({fetchImpl: async () => { calls++; return new Promise(() => {}); }});
  const early = new AbortController(); early.abort();
  await assert.rejects(client.recommend(sample.request, {signal: early.signal}), error => error.kind === 'cancelled');
  assert.equal(calls, 0);
  const active = new AbortController();
  const pending = client.recommend(sample.request, {signal: active.signal});
  active.abort();
  await assert.rejects(pending, error => error.kind === 'cancelled');
  assert.equal(calls, 1);
});

test('older response cannot replace the newer selection, even if transport ignores abort', async () => {
  let releaseOld;
  let count = 0;
  const latest = createLatestRecommender(createApiClient({fetchImpl: async () => {
    count++;
    if (count === 1) return new Promise(resolve => { releaseOld = resolve; });
    return jsonResponse(sample.response);
  }}));
  const old = latest.recommend(sample.request);
  const current = latest.recommend({...sample.request, date: '2026-12-19'});
  assert.equal(await old, null);
  assert.deepEqual(await current, sample.response);
  releaseOld(jsonResponse(sample.response));
  latest.cancel();
});

test('form builder converts numeric controls, optional blanks and keeps calendar date', () => {
  const form = {...sample.request, budget_kzt: '1200000', duration_hours: '', language: '', brief: '  ', uiTab: 'details'};
  const result = buildRecommendationRequest(form);
  assert.equal(result.budget_kzt, 1200000);
  assert.equal(result.duration_hours, null);
  assert.equal(result.language, null);
  assert.equal(result.brief, null);
  assert.equal(result.date, sample.request.date);
  assert.equal(result.uiTab, undefined);
  assert.equal(form.budget_kzt, '1200000');
});

test('bad numbers do not silently turn into zero or null', () => {
  for (const value of [true, '', Infinity, NaN, '1e6', '1 200 000', '1200000 ₸', Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => buildRecommendationRequest({...sample.request, budget_kzt: value}), error => error.kind === 'input');
  }
});

test('client leaves applying alternatives to the caller and preserves the full request', async () => {
  const short = await fixture('short_with_alternative');
  const request = short.response.alternatives[0].request;
  let sent;
  const client = createApiClient({fetchImpl: async (_url, init) => {
    sent = JSON.parse(init.body); return jsonResponse(sample.response);
  }});
  assert.equal(sent, undefined);
  await client.recommend(request);
  assert.deepEqual(sent, request);
  assert.equal(short.request.date, '2026-12-19');
});
