const test = require('node:test');
const assert = require('node:assert/strict');
const {trailSegments, cohortBlocked, sortedRows, comparisonWarnings, numeric, rotationOverview, technicalComparisons, savedRankMovement, chartControlKey} = require('../app/static/market_sector_intelligence.js');
const calendar = ['2026-10-16', '2026-10-19', '2026-10-20', '2026-10-21'];
const saved = (id, day, config = 'a') => ({snapshot_id: id, source_type: 'PERSISTED', market_session: day,
  algorithm_version: 'market-rotation-v1', configuration_digest: config,
  sectors: [{etf_symbol: 'XLK', price_basis: 'TRADES', rank: 1, rotation_state: 'Leading', rs_63_pct: 0, relative_momentum_pp: 0}]});
test('zero, one, adjacent and sparse captures never synthesize historical points', () => {
  assert.equal(trailSegments([], 'XLK', calendar).segments.length, 0);
  assert.deepEqual(trailSegments([saved(1, calendar[0])], 'XLK', calendar).segments.map(x => x.length), [1]);
  assert.deepEqual(trailSegments([saved(3, calendar[2]), saved(1, calendar[0]), saved(2, calendar[1])], 'XLK', calendar).segments.map(x => x.length), [3]);
  const sparse = trailSegments([saved(1, calendar[0]), saved(2, calendar[3])], 'XLK', calendar);
  assert.deepEqual(sparse.segments.map(x => x.length), [1, 1]); assert.equal(sparse.gaps, 1);
  assert.equal(sparse.segments.flat().length, 2);
});
test('configuration, version and basis boundaries are never connected', () => {
  const first = saved(1, calendar[0]);
  for (const second of [saved(2, calendar[1], 'b'), {...saved(2, calendar[1]), algorithm_version: 'other'}, {...saved(2, calendar[1]), sectors: [{...first.sectors[0], price_basis: 'ADJUSTED_LAST'}]}]) {
    const result = trailSegments([first, second], 'XLK', calendar);
    assert.equal(result.incompatible, 1); assert.deepEqual(result.segments.map(x => x.length), [1, 1]);
  }
  assert.deepEqual(trailSegments([first, saved(2, calendar[1])], 'XLK', []).segments.map(x => x.length), [1, 1]);
});
test('incomplete cohort stays blocked even if a stale row contains a V5 number', () => {
  assert.equal(cohortBlocked({v5_cohort_status: 'V5_COHORT_NOT_COMPARABLE', rows: [{v5: {technical_composite_score: 9}}]}), true);
  assert.equal(cohortBlocked({v5_cohort_status: 'COMPLETE'}), false);
});
test('sort preserves authoritative rank, missing values and unrounded zero coordinates', () => {
  const rows = [{etf_symbol: 'XLK', standard_market_sector: 'Technology', rank: 2}, {etf_symbol: 'XLB', standard_market_sector: 'Materials', rank: 1}];
  assert.deepEqual(sortedRows(rows, 'rank', 1).map(x => x.rank), [1, 2]);
  sortedRows(rows, 'sector', -1); assert.deepEqual(rows.map(x => x.rank), [2, 1]);
  assert.equal(numeric(null), false); assert.equal(numeric(0), true); assert.equal(numeric(Infinity), false);
  const point = trailSegments([saved(1, calendar[0])], 'XLK', calendar).segments[0][0];
  assert.equal(point.row.rotation_state, 'Leading'); assert.equal(point.row.rs_63_pct, 0);
});
test('different sessions and bases prohibit cross-panel interpretation', () => {
  assert.equal(comparisonWarnings([{market_session: calendar[0]}, {market_session: calendar[1]}]).length, 1);
  assert.equal(comparisonWarnings([{market_session: calendar[0], sectors: [{price_basis: 'TRADES'}]}, {market_session: calendar[0], rows: [{selected_price_basis: 'ADJUSTED_LAST'}]}]).length, 1);
  assert.deepEqual(comparisonWarnings([{market_session: calendar[0]}, {market_session: calendar[0]}]), []);
  assert.equal(comparisonWarnings([saved(1, calendar[0]), saved(2, calendar[0], 'different')]).length, 1);
});
test('shared configuration mismatch covers D2 versus D3 and chart schemas', () => {
  const rotation = saved(1, calendar[0], 'a');
  for (const other of [
    {source_type: 'TECHNICAL_PREVIEW', market_session: calendar[0], d2_configuration_digest: 'b', effective_configuration: {adapter_policy_digest: 'separate-technical-policy'}},
    {source_type: 'CHART_PRICE_HISTORY', market_session: calendar[0], configuration_digest: 'b', method_version: 'cached-price-charts-v1'}
  ]) {
    assert.ok(comparisonWarnings([rotation, other]).some(value => /configuration/i.test(value)));
  }
});
test('equal shared lineage permits legitimately different native model methods', () => {
  assert.deepEqual(comparisonWarnings([
    saved(1, calendar[0], 'a'),
    {source_type: 'TECHNICAL_PREVIEW', market_session: calendar[0], d2_configuration_digest: 'a', effective_configuration: {adapter_policy_digest: 'different-native-policy'}},
    {source_type: 'CHART_PRICE_HISTORY', market_session: calendar[0], configuration_digest: 'a', method_version: 'different-chart-method'}
  ]), []);
});
test('missing shared configuration metadata does not imply verified compatibility', () => {
  for (const source_type of ['PREVIEW', 'PERSISTED', 'TECHNICAL_PREVIEW', 'CHART_PRICE_HISTORY']) {
    const warnings = comparisonWarnings([saved(1, calendar[0], 'a'), {source_type, market_session: calendar[0]}]);
    assert.ok(warnings.some(value => /configuration/i.test(value) && /unavailable|unknown|cannot/i.test(value)));
  }
});
test('calendar material identities and calendar adjacency never cross versions', () => {
  const first = {...saved(1, calendar[0]), calendar_version: 'swinglens-us-equities-v1'};
  const legacy = {...saved(2, calendar[1]), calendar_version: first.calendar_version, material_identity_version: 'market-sector-rotation-material-v1'};
  assert.deepEqual(trailSegments([first, legacy], 'XLK', calendar, first.calendar_version).segments.map(s => s.length), [2]);
  const current = {...legacy, calendar_version: 'swinglens-us-equities-v2', material_identity_version: 'market-sector-rotation-material-v2'};
  assert.equal(trailSegments([first, current], 'XLK', calendar, current.calendar_version).incompatible, 1);
  assert.deepEqual(trailSegments([first, legacy], 'XLK', calendar, current.calendar_version).segments.map(s => s.length), [1, 1]);
  assert.ok(comparisonWarnings([first, current]).some(s => /calendar versions differ/i.test(s)));
});

const universe = ['XLB', 'XLC', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP', 'XLRE', 'XLU', 'XLV', 'XLY'].map(etf_symbol => ({etf_symbol}));
const complete = () => ({cohort_status: 'COMPLETE', sectors: universe.map((item, i) => ({...item,
  standard_market_sector: item.etf_symbol, rank: 11 - i, quality_status: 'ELIGIBLE',
  rotation_state: ['Leading', 'Improving', 'Weakening', 'Lagging'][i % 4], rs_63_pct: i - 5,
  relative_momentum_pp: i - 6, reasons: []}))});

test('overview counts use authoritative states and strongest sectors use unchanged D2 ranks', () => {
  const payload = complete(), before = JSON.stringify(payload);
  const result = rotationOverview(payload, universe);
  assert.equal(result.complete, true);
  assert.deepEqual(result.counts, {Leading: 3, Improving: 3, Weakening: 3, Lagging: 2});
  assert.deepEqual(result.strongest.map(row => row.etf_symbol), ['XLY', 'XLV', 'XLU']);
  assert.equal(Object.values(result.counts).reduce((a, b) => a + b), 11);
  assert.equal(JSON.stringify(payload), before);
});
test('fastest improvers use positive unrounded momentum and deterministic D2 rank ties', () => {
  const payload = complete();
  payload.sectors.forEach(row => { row.relative_momentum_pp = -1; });
  payload.sectors[0].relative_momentum_pp = 2.001;
  payload.sectors[1].relative_momentum_pp = 2.004;
  payload.sectors[2].relative_momentum_pp = 2.004;
  payload.sectors[3].relative_momentum_pp = 0;
  assert.deepEqual(rotationOverview(payload, universe).improving.map(row => row.etf_symbol), ['XLE', 'XLC', 'XLB']);
  payload.sectors.forEach(row => { row.relative_momentum_pp = 0; });
  assert.deepEqual(rotationOverview(payload, universe).improving, []);
});
test('partial stale missing or disabled evidence never supplies cohort-wide highlights', () => {
  const partial = complete(); partial.cohort_status = 'PARTIAL';
  partial.sectors[0] = {...partial.sectors[0], quality_status: 'INSUFFICIENT_DATA', reasons: ['STALE'], rs_63_pct: null};
  const result = rotationOverview(partial, universe);
  assert.equal(result.eligible, 10); assert.equal(result.missing, 1);
  assert.equal(Object.values(result.counts).reduce((a, b) => a + b), 10);
  for (const payload of [partial, null, {cohort_status: 'DISABLED', sectors: complete().sectors}, {cohort_status: 'COMPLETE', sectors: []}]) {
    const overview = rotationOverview(payload, universe);
    assert.equal(overview.complete, false); assert.deepEqual(overview.strongest, []); assert.deepEqual(overview.improving, []);
  }
});
test('duplicate symbols and missing ranks cannot masquerade as a complete summary', () => {
  for (const alter of [p => { p.sectors[0].rank = null; }, p => { p.sectors[0].etf_symbol = p.sectors[1].etf_symbol; }, p => { p.sectors[0].rank = 2; }]) {
    const payload = complete(); alter(payload); assert.equal(rotationOverview(payload, universe).complete, false);
  }
});
test('paired technical series uses exact native raw values and supplied diagnostic delta', () => {
  const payload = {v5_cohort_status: 'COMPLETE', rows: [{etf_symbol: 'XLK',
    v4: {status: 'READY', final_v4_score: 7.12345, classification: 'V4 classification', confidence: 'high'},
    v5: {status: 'READY', technical_composite_score: 8.23456, confidence_adjusted_score: 5.1, classification: 'V5 classification', confidence: 'medium'},
    comparison: {raw_score_delta: 1.1111}}]};
  const before = JSON.stringify(payload), result = technicalComparisons(payload, universe);
  assert.equal(result.length, 11);
  assert.deepEqual(result.find(row => row.symbol === 'XLK'), {symbol: 'XLK', v4: 7.12345, v5: 8.23456,
    v4Classification: 'V4 classification', v5Classification: 'V5 classification', v4Confidence: 'high', v5Confidence: 'medium', delta: 1.1111});
  assert.equal(result.find(row => row.symbol === 'XLF').v4, null);
  assert.equal(JSON.stringify(payload), before);
});
test('paired chart suppresses blocked V5 and nonready or nonfinite native scores without zero defaults', () => {
  const payload = {v5_cohort_status: 'V5_COHORT_NOT_COMPARABLE', rows: [{etf_symbol: 'XLK',
    v4: {status: 'READY', final_v4_score: 0}, v5: {status: 'READY', technical_composite_score: 9}, comparison: {raw_score_delta: 9}}]};
  let row = technicalComparisons(payload, universe).find(r => r.symbol === 'XLK');
  assert.equal(row.v4, 0); assert.equal(row.v5, null); assert.equal(row.delta, null);
  payload.v5_cohort_status = 'COMPLETE'; payload.rows[0].v4.final_v4_score = NaN;
  payload.rows[0].v5.status = 'INSUFFICIENT_DATA';
  row = technicalComparisons(payload, universe).find(r => r.symbol === 'XLK');
  assert.equal(row.v4, null); assert.equal(row.v5, null);
});
test('stored rank movement compares only consecutive compatible saved captures', () => {
  const first = saved(1, calendar[0]), second = saved(2, calendar[1]);
  first.sectors[0].rank = 4; second.sectors[0].rank = 2;
  assert.deepEqual(savedRankMovement([second, first], 'XLK', calendar), {from: calendar[0], to: calendar[1], priorRank: 4, rank: 2, change: 2});
  assert.equal(savedRankMovement([first, saved(3, calendar[3])], 'XLK', calendar), null);
  assert.equal(savedRankMovement([first, saved(2, calendar[1], 'different')], 'XLK', calendar), null);
  assert.equal(savedRankMovement([first, {...second, source_type: 'PREVIEW'}], 'XLK', calendar), null);
  assert.equal(savedRankMovement([first, second, {...saved(3, calendar[3]), sectors: []}], 'XLK', calendar), null);
  assert.equal(savedRankMovement([], 'XLK', calendar), null);
  assert.equal(savedRankMovement([first], 'XLK', calendar), null);
});

test('chart focus identity distinguishes ETF observations and chart sources without changing evidence', () => {
  const payload = {...saved(1, calendar[0]), calendar_version: 'v2', material_identity_version: 'material-v2'};
  const before = JSON.stringify(payload), key = chartControlKey('scatter', 'XLF', payload, 'TRADES');
  assert.equal(chartControlKey('scatter', 'XLF', {...payload}, 'TRADES'), key);
  const alternatives = [
    chartControlKey('technical', 'XLF', payload, 'TRADES'),
    chartControlKey('scatter', 'XLE', payload, 'TRADES'),
    chartControlKey('scatter', 'XLF', {...payload, snapshot_id: 2}, 'TRADES'),
    chartControlKey('scatter', 'XLF', {...payload, source_type: 'PREVIEW'}, 'TRADES'),
    chartControlKey('scatter', 'XLF', {...payload, market_session: calendar[1]}, 'TRADES'),
    chartControlKey('scatter', 'XLF', {...payload, configuration_digest: 'other'}, 'TRADES'),
    chartControlKey('scatter', 'XLF', {...payload, algorithm_version: 'other'}, 'TRADES'),
    chartControlKey('scatter', 'XLF', {...payload, calendar_version: 'v1'}, 'TRADES'),
    chartControlKey('scatter', 'XLF', {...payload, material_identity_version: 'material-v1'}, 'TRADES'),
    chartControlKey('scatter', 'XLF', {...payload, as_of_utc: 'different cutoff'}, 'TRADES'),
    chartControlKey('scatter', 'XLF', payload, 'ADJUSTED_LAST')
  ];
  assert.equal(new Set([key, ...alternatives]).size, alternatives.length + 1);
  assert.equal(JSON.stringify(payload), before);
});
