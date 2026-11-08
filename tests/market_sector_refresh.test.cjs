const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createRefreshController, freshnessView, deadlineRequest} = require('../app/static/market_sector_refresh.js');
const universe = ['XLB', 'XLC', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP', 'XLRE', 'XLU', 'XLV', 'XLY'];
const base = '/api/market-sector-intelligence/refresh';
const operation = {job_id: 91, fetch_run_id: 42, status: 'QUEUED', total_items: 9, completed_items: 2};
function harness(responses, saved, reloadPage) {
  const calls = [], views = [], tasks = [], values = new Map();
  if (saved) values.set('swinglens.msi.refresh.v1', String(saved));
  let reloads = 0, panels = 0;
  const controller = createRefreshController({
    request: async (url, options) => { calls.push({url, options}); const next = responses.shift(); if (next instanceof Error) throw next; return next; },
    schedule: callback => { tasks.push(callback); return callback; }, cancel: callback => { const index = tasks.indexOf(callback); if (index >= 0) tasks.splice(index, 1); },
    storage: {getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)},
    view: value => views.push(value), reloadPage: () => { reloads++; return reloadPage?.(); }, reloadPanels: () => panels++,
  });
  return {controller, calls, views, tasks, values, get reloads() { return reloads; }, get panels() { return panels; },
    tick: async () => { assert.ok(tasks.length); await tasks.shift()(); }};
}

const flushCallbacks = () => new Promise(resolve => setImmediate(resolve));
for (const outcome of ['resolve', 'reject']) test('old completion ' + outcome + ' cannot overwrite a resumed distinct refresh', async () => {
  let finish;
  const oldReload = new Promise((resolve, reject) => { finish = outcome === 'resolve' ? resolve : reject; });
  const next = {...operation, job_id: 92};
  const h = harness([{...operation, status: 'COMPLETED'}, null, next,
    {...next, status: 'RUNNING'}, {...next, status: 'CANCELLED'},
    {...operation, job_id: 93, status: 'COMPLETED'}], null, () => h.reloads === 1 ? oldReload : undefined);
  await h.controller.start();
  assert.equal(h.reloads, 1); assert.equal(h.views.at(-1).state, 'VERIFYING');
  h.controller.stop(); await h.controller.resume();
  assert.equal(h.views.at(-1).state, 'IDLE');
  await h.controller.start();
  assert.equal(h.views.at(-1).state, 'QUEUED'); assert.equal(h.views.at(-1).disabled, true);
  const viewCount = h.views.length;
  finish(outcome === 'reject' ? new Error('old panel failed') : undefined);
  await flushCallbacks();
  assert.equal(h.views.length, viewCount);
  assert.equal(h.views.at(-1).state, 'QUEUED'); assert.equal(h.views.at(-1).disabled, true);
  assert.equal(h.values.get('swinglens.msi.refresh.v1'), '92'); assert.equal(h.tasks.length, 1);
  await h.controller.start(); assert.equal(h.calls.length, 3);
  await h.tick(); assert.equal(h.calls.at(-1).url, base + '/92');
  await h.controller.cancel(); assert.equal(h.calls.at(-1).url, base + '/92/cancel');
  assert.equal(h.tasks.length, 0); assert.equal(h.views.at(-1).state, 'CANCELLED');
  await h.controller.start(); await flushCallbacks();
  assert.equal(h.reloads, 2); assert.equal(h.views.at(-1).state, 'COMPLETED');
  assert.equal(h.views.at(-1).disabled, false);
});

for (const outcome of ['resolve', 'reject']) test('current completion ' + outcome + ' permits the next deliberate refresh exactly once', async () => {
  let finish;
  const reload = new Promise((resolve, reject) => { finish = outcome === 'resolve' ? resolve : reject; });
  const h = harness([{...operation, status: 'COMPLETED'}, {...operation, job_id: 92, status: 'COMPLETED'}], null,
    () => h.reloads === 1 ? reload : undefined);
  await h.controller.start(); await h.controller.start();
  assert.equal(h.calls.length, 1); assert.equal(h.reloads, 1);
  finish(outcome === 'reject' ? new Error('panel unavailable') : undefined); await flushCallbacks();
  assert.equal(h.views.at(-1).state, 'COMPLETED'); assert.equal(h.views.at(-1).disabled, false);
  await h.controller.start(); await flushCallbacks();
  assert.equal(h.calls.length, 2); assert.equal(h.reloads, 2); assert.equal(h.tasks.length, 0);
  assert.equal(h.views.at(-1).state, 'COMPLETED');
});

test('initial load is GET only and leaves no polling timer when idle', async () => {
  const h = harness([null]); await h.controller.resume();
  assert.deepEqual(h.calls, [{url: base + '/active', options: undefined}]);
  assert.equal(h.tasks.length, 0); assert.equal(h.views.at(-1).disabled, false);
});
test('deliberate click posts once, follows exact ID and actual counters, reloads once', async () => {
  const h = harness([operation, {...operation, status: 'RUNNING'}, {...operation, status: 'COMPLETED'}]);
  await h.controller.start(); await h.controller.start();
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].options.method, 'POST');
  assert.match(h.views.at(-1).message, /2 \/ 9/); assert.equal(h.views.at(-1).disabled, true);
  await h.tick(); await h.tick();
  assert.equal(h.reloads, 1); assert.equal(h.tasks.length, 0); assert.equal(h.values.size, 0);
  assert.ok(h.calls.slice(1).every(call => call.url === base + '/91' && !call.options));
});
test('known operation resumes GET observation after navigating back without a new POST', async () => {
  const h = harness([{...operation, status: 'COMPLETED'}], 91); await h.controller.resume();
  assert.equal(h.calls[0].url, base + '/91'); assert.equal(h.reloads, 1);
});
test('active server operation from another tab is recovered with GET', async () => {
  const h = harness([operation]); await h.controller.resume();
  assert.equal(h.values.get('swinglens.msi.refresh.v1'), '91'); assert.equal(h.views.at(-1).disabled, true);
});
test('gateway rejection enables retry without claiming success', async () => {
  const h = harness([Object.assign(new Error('Start and sign in to IB Gateway.'), {code: 'IB_GATEWAY_NOT_READY'})]);
  await h.controller.start(); assert.equal(h.tasks.length, 0); assert.equal(h.reloads, 0);
  assert.equal(h.views.at(-1).disabled, false); assert.match(h.views.at(-1).message, /Gateway/);
});
test('partial refresh reloads cached panels and retains precise warning', async () => {
  const h = harness([{...operation, status: 'PARTIAL', failures: [{ticker: 'XLU', basis: 'ADJUSTED_LAST'}]}]);
  await h.controller.start(); assert.equal(h.panels, 1); assert.equal(h.reloads, 0);
  assert.match(h.views.at(-1).message, /Partially refreshed.*XLU ADJUSTED_LAST/);
  assert.equal(h.views.at(-1).disabled, false);
});
for (const status of ['FAILED', 'CANCELLED']) test(status + ' does not reload cached evidence', async () => {
  const h = harness([{...operation, status}]); await h.controller.start();
  assert.equal(h.panels, 0); assert.equal(h.reloads, 0); assert.equal(h.tasks.length, 0);
  assert.equal(h.views.at(-1).disabled, false);
});
test('transient status failure retries GET without another POST', async () => {
  const h = harness([operation, new Error('network'), {...operation, status: 'COMPLETED'}]);
  await h.controller.start(); await h.tick(); await h.tick();
  assert.equal(h.calls.filter(call => call.options).length, 1); assert.equal(h.reloads, 1);
});
test('bounded failures enable check-status retry for same job', async () => {
  const h = harness([operation, ...Array.from({length: 4}, () => new Error('network')), {...operation, status: 'COMPLETED'}]);
  await h.controller.start(); for (let i = 0; i < 4; i++) await h.tick();
  assert.equal(h.tasks.length, 0); assert.equal(h.views.at(-1).state, 'UNKNOWN');
  await h.controller.start(); assert.equal(h.reloads, 1); assert.equal(h.calls.filter(call => call.options).length, 1);
});
test('unknown operation recovery remains GET-only when checking again', async () => {
  const h = harness([...Array.from({length: 4}, () => new Error('network')), null]);
  await h.controller.resume(); for (let i = 0; i < 3; i++) await h.tick();
  assert.equal(h.views.at(-1).state, 'UNKNOWN'); await h.controller.start();
  assert.ok(h.calls.every(call => !call.options && call.url === base + '/active'));
  assert.equal(h.views.at(-1).state, 'IDLE');
});
test('concurrent admission or uncertain POST recovers via GET, never repeats POST', async () => {
  for (const props of [{code: 'REFRESH_ADMISSION_BUSY'}, {uncertain: true}]) {
    const h = harness([Object.assign(new Error('busy'), props), operation]);
    await h.controller.start(); await h.tick(); assert.ok(h.calls[1].url.startsWith(base + '/active?request_id='));
    assert.equal(h.calls.filter(call => call.options).length, 1);
  }
});
test('wrong status ID cannot trigger a reload', async () => {
  const h = harness([operation, {...operation, job_id: 92, status: 'COMPLETED'}]);
  await h.controller.start(); await h.tick(); assert.equal(h.reloads, 0);
});
test('page unload cancels polling and late completion cannot reload', async () => {
  const h = harness([operation]); await h.controller.start(); h.controller.stop();
  assert.equal(h.tasks.length, 0); await h.controller.start(); assert.equal(h.calls.length, 1);
});
test('browser back-forward cache restoration resumes GET observation', async () => {
  const h = harness([operation, {...operation, status: 'COMPLETED'}]);
  await h.controller.start(); h.controller.stop(); await h.controller.resume();
  assert.equal(h.reloads, 1); assert.equal(h.calls[1].url, base + '/91');
});
test('persistently unknown server status has bounded retries', async () => {
  const h = harness([operation, ...Array.from({length: 4}, () => ({...operation, status: 'UNKNOWN'}))]);
  await h.controller.start(); for (let i = 0; i < 4; i++) await h.tick();
  assert.equal(h.tasks.length, 0); assert.equal(h.views.at(-1).disabled, false); assert.equal(h.reloads, 0);
});
function current() {
  return {status: 'CURRENT', expected_session: '2026-10-23', entries: universe.flatMap(ticker =>
    ['TRADES', 'ADJUSTED_LAST'].map(price_basis => ({ticker, price_basis, status: 'CURRENT', session_lag: 0, last_session: '2026-10-23'})))};
}

test('successful unchanged polls become actionable without a second admission', async () => {
  const views = [], tasks = [], calls = [], values = new Map(); let clock = 0;
  const controller = createRefreshController({now: () => clock, actionAfterMs: 5000,
    schedule: callback => { tasks.push(callback); }, cancel: () => {}, storage: {
      getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)},
    request: async (url, options) => { calls.push({url, options}); return operation; },
    view: value => views.push(value), reloadPage: () => assert.fail('queued job cannot complete'), reloadPanels() {},
  });
  await controller.start();
  for (let i = 0; i < 5; i++) { clock += 1000; await tasks.shift()(); }
  assert.equal(views.at(-1).state, 'WAITING_ACTION'); assert.equal(views.at(-1).disabled, false);
  assert.equal(tasks.length, 0); assert.equal(values.get('swinglens.msi.refresh.v1'), '91');
  await controller.start(); assert.equal(calls.filter(call => call.options).length, 1);
  await controller.cancel(); assert.equal(calls.at(-1).url, base + '/91/cancel');
});

test('a never resolving panel reaches deadline and ignores its eventual response', async () => {
  let timeout, finish; const controller = new AbortController();
  const result = deadlineRequest(() => new Promise(resolve => { finish = resolve; }), {
    controller, schedule: callback => { timeout = callback; return 1; }, cancel: () => {},
  });
  await Promise.resolve(); timeout();
  await assert.rejects(result, /timed out/); assert.ok(controller.signal.aborted);
  finish({status: 'CURRENT'}); await assert.rejects(result, /timed out/);
});

test('ambiguous admission survives reload using the same durable request identity', async () => {
  const h = harness([Object.assign(new Error('response lost'), {uncertain: true}), {...operation, status: 'COMPLETED'}]);
  await h.controller.start();
  const requestId = h.calls[0].options.body.request_id;
  assert.equal(h.values.get('swinglens.msi.refresh.v1.request'), requestId);
  await h.controller.resume();
  assert.equal(h.calls.at(-1).url, base + '/active?request_id=' + requestId);
  assert.equal(h.reloads, 1); assert.equal(h.calls.filter(call => call.options).length, 1);
});
test('CURRENT requires complete evidence for all eleven ETFs in both bases', () => {
  const payload = current(); assert.equal(freshnessView(payload, universe).status, 'CURRENT');
  payload.entries.pop(); assert.equal(freshnessView(payload, universe).status, 'UNKNOWN');
});
test('STALE uses trading-session lag and discloses affected bases and missingness', () => {
  const payload = current(); payload.status = 'STALE';
  Object.assign(payload.entries[0], {status: 'STALE', session_lag: 2});
  Object.assign(payload.entries[1], {status: 'MISSING', session_lag: null});
  const view = freshnessView(payload, universe); assert.equal(view.status, 'STALE');
  assert.match(view.label, /2 trading sessions behind/); assert.match(view.detail, /XLB TRADES/);
  assert.match(view.detail, /ADJUSTED_LAST: MISSING/);
  // Completion has no bearing on this stored-evidence projection.
  assert.deepEqual(freshnessView(payload, universe), view);
});
for (const status of ['MISSING', 'UNKNOWN']) test(status + ' does not invent a session lag', () => {
  const result = freshnessView({status, entries: []}, universe);
  assert.equal(result.label, status); assert.doesNotMatch(result.label, /behind/);
});
