(function () {
  'use strict';
  const numeric = value => typeof value === 'number' && Number.isFinite(value);
  const cohortBlocked = data => !data || ['V5_COHORT_NOT_COMPARABLE', 'DISABLED'].includes(data.v5_cohort_status);
  function sortedRows(rows, key, direction) {
    return rows.slice().sort((a, b) => {
      let order;
      if (key === 'sector') order = a.standard_market_sector.localeCompare(b.standard_market_sector);
      else order = (numeric(a.rank) ? a.rank : Infinity) - (numeric(b.rank) ? b.rank : Infinity);
      return (Number.isNaN(order) ? 0 : order) * direction || a.etf_symbol.localeCompare(b.etf_symbol);
    });
  }
  function rotationOverview(rotation, universe) {
    const states = ['Leading', 'Improving', 'Weakening', 'Lagging'];
    const rows = rotation && rotation.cohort_status !== 'DISABLED' ? rotation.sectors || [] : [];
    const eligible = universe.map(definition => rows.find(row => row.etf_symbol === definition.etf_symbol))
      .filter(row => row && row.quality_status === 'ELIGIBLE' && states.includes(row.rotation_state) &&
        numeric(row.rs_63_pct) && numeric(row.relative_momentum_pp));
    const counts = Object.fromEntries(states.map(state => [state, eligible.filter(row => row.rotation_state === state).length]));
    const ranks = new Set(eligible.map(row => row.rank));
    const complete = Boolean(rotation && rotation.cohort_status === 'COMPLETE' && universe.length === 11 &&
      rows.length === 11 && new Set(rows.map(row => row.etf_symbol)).size === 11 && eligible.length === 11 &&
      ranks.size === 11 && eligible.every(row => Number.isInteger(row.rank) && row.rank >= 1 && row.rank <= 11));
    return {counts, eligible: eligible.length, missing: universe.length - eligible.length, complete,
      strongest: complete ? sortedRows(eligible, 'rank', 1).slice(0, 3) : [],
      improving: complete ? eligible.filter(row => row.relative_momentum_pp > 0).slice().sort((a, b) =>
        b.relative_momentum_pp - a.relative_momentum_pp || a.rank - b.rank || a.etf_symbol.localeCompare(b.etf_symbol)).slice(0, 3) : []};
  }
  function technicalComparisons(technical, universe) {
    return universe.map(definition => {
      const row = technical && (technical.rows || []).find(item => item.etf_symbol === definition.etf_symbol) || {};
      const v4 = row.v4 && row.v4.status === 'READY' ? row.v4 : {};
      const v5 = !cohortBlocked(technical) && row.v5 && row.v5.status === 'READY' ? row.v5 : {};
      return {symbol: definition.etf_symbol, v4: numeric(v4.final_v4_score) ? v4.final_v4_score : null,
        v5: numeric(v5.technical_composite_score) ? v5.technical_composite_score : null,
        v4Classification: v4.classification || 'Unavailable', v5Classification: v5.classification || 'Unavailable',
        v4Confidence: v4.confidence || 'Unavailable', v5Confidence: v5.confidence || 'Unavailable',
        delta: numeric(v4.final_v4_score) && numeric(v5.technical_composite_score) && row.comparison && numeric(row.comparison.raw_score_delta) ? row.comparison.raw_score_delta : null};
    });
  }
  function trailSegments(snapshots, symbol, calendar, calendarVersion) {
    const segments = []; let previous = null, gaps = 0, incompatible = 0;
    const indices = new Map(calendar.map((day, index) => [day, index]));
    snapshots.slice().sort((a, b) => a.market_session.localeCompare(b.market_session) || a.snapshot_id - b.snapshot_id).forEach(snapshot => {
      const row = (snapshot.sectors || []).find(item => item.etf_symbol === symbol);
      if (!row || !numeric(row.rs_63_pct) || !numeric(row.relative_momentum_pp)) { previous = null; return; }
      const materialVersion = snapshot.material_identity_version || (snapshot.calendar_version === 'swinglens-us-equities-v1' ? 'market-sector-rotation-material-v1' : 'unknown');
      const identity = [snapshot.algorithm_version, snapshot.configuration_digest, row.price_basis, snapshot.calendar_version, materialVersion].join('|');
      const point = {snapshot, row, identity};
      const consistent = previous && previous.identity === identity && (!calendarVersion || snapshot.calendar_version === calendarVersion);
      const adjacent = previous && indices.has(previous.snapshot.market_session) && indices.has(snapshot.market_session) &&
        indices.get(snapshot.market_session) - indices.get(previous.snapshot.market_session) === 1;
      if (previous && !consistent) incompatible += 1;
      if (previous && consistent && !adjacent) gaps += 1;
      if (consistent && adjacent) segments[segments.length - 1].push(point);
      else segments.push([point]);
      previous = point;
    });
    return {segments, gaps, incompatible};
  }
  function comparisonWarnings(panels) {
    const valid = panels.filter(Boolean);
    const warnings = [];
    if (new Set(valid.map(p => p.market_session).filter(Boolean)).size > 1) {
      warnings.push('Market sessions differ. Saved rotation and current diagnostics are not a same-day signal; cross-panel comparisons are unavailable.');
    }
    if (new Set(valid.map(p => p.calendar_version).filter(Boolean)).size > 1) {
      warnings.push('Exchange calendar versions differ. Cross-panel compatibility is not established; saved evidence remains separate.');
    }
    const bases = new Set(valid.flatMap(p => (p.sectors || p.rows || []).map(r => r.price_basis || r.selected_price_basis)).filter(Boolean));
    if (bases.size > 1) warnings.push('Selected price bases differ. Treat these panels separately; do not compare their numerical results.');
    const shared = valid.filter(p => ['PREVIEW', 'PERSISTED', 'TECHNICAL_PREVIEW', 'CHART_PRICE_HISTORY'].includes(p.source_type));
    const lineage = shared.map(p => p.source_type === 'TECHNICAL_PREVIEW' ? p.d2_configuration_digest : p.configuration_digest)
      .map(value => typeof value === 'string' && value.trim() ? value.trim() : null);
    if (new Set(lineage.filter(Boolean)).size > 1) {
      warnings.push('Shared Market Sector Intelligence configuration differs across panels. Treat these sources separately; cross-panel compatibility is not established.');
    }
    if (shared.length > 1 && lineage.some(value => value === null)) {
      warnings.push('Shared Market Sector Intelligence configuration metadata is unavailable for one or more panels. Cross-panel compatibility cannot be confirmed.');
    }
    if (new Set(valid.filter(p => p.algorithm_version).map(p => p.algorithm_version)).size > 1) {
      warnings.push('Saved rotation uses a different methodology/configuration from the current preview; displayed separately.');
    }
    return warnings;
  }
  function savedRankMovement(snapshots, symbol, calendar, calendarVersion) {
    const saved = snapshots.filter(item => item.source_type === 'PERSISTED');
    const segments = trailSegments(saved, symbol, calendar, calendarVersion).segments;
    const latest = segments[segments.length - 1] || [];
    if (latest.length < 2) return null;
    const [before, after] = latest.slice(-2);
    if (saved.some(item => item.market_session > after.snapshot.market_session)) return null;
    if (!numeric(before.row.rank) || !numeric(after.row.rank)) return null;
    return {from: before.snapshot.market_session, to: after.snapshot.market_session,
      priorRank: before.row.rank, rank: after.row.rank, change: before.row.rank - after.row.rank};
  }
  function chartControlKey(chart, symbol, payload, basis) {
    return JSON.stringify([chart, symbol, payload.source_type, payload.snapshot_id, payload.market_session,
      payload.as_of_utc || payload.cutoff_at_utc || payload.captured_at_utc, payload.algorithm_version,
      payload.configuration_digest, payload.calendar_version, payload.material_identity_version, basis]);
  }
  const helpers = {numeric, cohortBlocked, sortedRows, rotationOverview, technicalComparisons, trailSegments, savedRankMovement, comparisonWarnings, chartControlKey};
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
  if (typeof document === 'undefined') return;
  const root = document.getElementById('msi-dashboard');
  if (!root) return;
  const bootstrap = JSON.parse(document.getElementById('msi-bootstrap').textContent);
  const universe = bootstrap.sectors;
  const defaults = {etf: 'XLK', horizon: 126, snapshot_id: null, mode: 'relative', symbols: ['XLK', 'XLF', 'XLE']};
  const state = Object.assign({}, defaults, bootstrap.initial);
  const data = {}, errors = {}, loading = new Set(), chartCache = new Map(), snapshots = new Map();
  let epoch = 0, chartRequest = 0, archiveRequest = 0, chartAbort = null;
  let sortKey = 'rank', sortDirection = 1, savedIndex = [];
  const $ = id => document.getElementById('msi-' + id);
  const colors = ['#886a20', '#7357a0', '#347f91', '#ad6245', '#306b51', '#505fa8', '#8b563e', '#376c8e', '#885c83', '#548134', '#b04c65'];
  const color = symbol => symbol === 'SPY' ? '#25342d' : colors[Math.max(0, universe.findIndex(row => row.etf_symbol === symbol))];
  const number = (value, unit = '') => numeric(value) ? value.toFixed(2) + unit : 'N/A';
  const reason = values => values ? values.join(' · ') || 'No data warnings' : 'Unavailable';
  const short = value => value ? value.slice(0, 12) : 'N/A';
  function node(tag, text, className) {
    const value = document.createElement(tag);
    if (text !== undefined) value.textContent = text;
    if (className) value.className = className;
    return value;
  }
  function svgNode(tag, attrs, text) {
    const value = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs || {}).forEach(([key, item]) => value.setAttribute(key, item));
    if (text !== undefined) value.textContent = text;
    return value;
  }
  function text(id, value) { $(id).textContent = value; }
  function warning(id, value) { text(id, value); $(id).hidden = !value; }
  function provenance(payload) {
    if (!payload) return 'Unavailable';
    return [payload.source_type, 'session ' + payload.market_session,
      'as of ' + (payload.as_of_utc || payload.cutoff_at_utc || payload.captured_at_utc || 'N/A'),
      payload.captured_at_utc ? 'captured ' + payload.captured_at_utc : '',
      payload.algorithm_version || payload.method_version || 'native V4/V5',
      'config ' + short(payload.configuration_digest || (payload.effective_configuration || {}).adapter_policy_digest)].filter(Boolean).join(' · ');
  }
  function currentRotation() { return state.snapshot_id ? snapshots.get(Number(state.snapshot_id)) : data.rotation; }
  function urlState(replace = false) {
    const url = new URL(window.location.href);
    url.searchParams.set('etf', state.etf); url.searchParams.set('horizon', String(state.horizon));
    url.searchParams.set('mode', state.mode);
    if (state.symbols.length) url.searchParams.set('symbols', state.symbols.join(','));
    else url.searchParams.delete('symbols');
    if (state.snapshot_id) url.searchParams.set('snapshot_id', state.snapshot_id); else url.searchParams.delete('snapshot_id');
    window.history[replace ? 'replaceState' : 'pushState']({}, '', url);
  }
  async function read(path, signal) {
    const response = await fetch('/api/market-sector-intelligence/' + path, {signal, headers: {'Accept': 'application/json'}, cache: 'no-store'});
    if (!response.ok) throw new Error('Unavailable (HTTP ' + response.status + ').');
    return response.json();
  }
  async function panel(key, path) {
    const generation = epoch; loading.add(key); delete errors[key]; renderStatus();
    try { const payload = await read(path); if (generation === epoch) data[key] = payload; }
    catch (error) { if (generation === epoch) { delete data[key]; errors[key] = error.message; } }
    finally { if (generation === epoch) { loading.delete(key); render(); } }
  }
  async function loadChart() {
    const request = ++chartRequest, generation = epoch, horizon = state.horizon;
    if (chartAbort) chartAbort.abort();
    chartAbort = new AbortController();
    loading.add('chart'); delete errors.chart; delete data.chart; render();
    try {
      const payload = chartCache.get(horizon) || await read('charts/price-series?horizon=' + horizon, chartAbort.signal);
      if (request !== chartRequest || generation !== epoch) return;
      chartCache.set(horizon, payload); data.chart = payload;
    } catch (error) {
      if (request === chartRequest && generation === epoch && error.name !== 'AbortError') errors.chart = error.message;
    } finally {
      if (request === chartRequest && generation === epoch) { loading.delete('chart'); render(); }
    }
  }
  async function loadArchive() {
    const request = ++archiveRequest, generation = epoch;
    loading.add('archive'); delete errors.archive; snapshots.clear(); savedIndex = []; render();
    try {
      const index = await read('rotation/history?limit=' + $('history-limit').value);
      if (request !== archiveRequest || generation !== epoch) return;
      savedIndex = index;
      const ids = index.map(item => item.snapshot_id);
      if (state.snapshot_id && !ids.includes(Number(state.snapshot_id))) ids.push(Number(state.snapshot_id));
      let next = 0, failed = 0;
      async function worker() {
        while (next < ids.length) {
          const id = ids[next++];
          try {
            const snapshot = await read('rotation/snapshots/' + id);
            if (request === archiveRequest && generation === epoch) snapshots.set(id, snapshot);
          } catch (_) { failed += 1; }
        }
      }
      await Promise.allSettled([worker(), worker()]);
      if (request !== archiveRequest || generation !== epoch) return;
      if (failed) errors.archive = failed + ' saved detail request(s) unavailable. No missing points were reconstructed.';
    } catch (error) {
      if (request === archiveRequest && generation === epoch) errors.archive = error.message + ' Saved history requires activated D2 tables and complete authorized CLI captures.';
    } finally {
      if (request === archiveRequest && generation === epoch) { loading.delete('archive'); render(); }
    }
  }
  async function reload() {
    epoch += 1; chartCache.clear(); Object.keys(data).forEach(key => delete data[key]);
    $('reload').disabled = true;
    await Promise.allSettled([panel('coverage', 'coverage'), panel('rotation', 'rotation/preview'),
      panel('technical', 'technicals/preview'), loadArchive(), loadChart()]);
    $('reload').disabled = false;
  }
  function renderStatus() {
    ['rotation', 'archive', 'technical', 'chart'].forEach(key => {
      const box = $('status-' + key); box.replaceChildren();
      let summary = 'Unavailable', detail = errors[key] || 'No evidence available.';
      if (loading.has(key)) { summary = 'Loading…'; detail = 'Read-only cached evidence'; }
      else if (key === 'archive' && !errors.archive) {
        summary = snapshots.size ? snapshots.size + ' saved captures' : 'No saved snapshots';
        detail = savedIndex[0] ? 'PERSISTED · latest session ' + savedIndex[0].market_session + ' · captured ' + savedIndex[0].captured_at_utc : 'History appears only after a complete, authorized D2 CLI capture.';
      } else if (data[key]) {
        const value = data[key];
        summary = key === 'technical' ? value.v4_ready_count + '/11 V4 · ' + value.v5_ready_count + '/11 V5' : value.cohort_status;
        detail = provenance(value);
      }
      box.append(node('strong', summary), node('small', detail));
    });
    const coverage = data.coverage;
    text('coverage', loading.has('coverage') ? 'Checking ETF + SPY coverage…' : coverage ?
      'D1 cache coverage: ' + coverage.symbols.filter(row => row.status === 'CURRENT').length + '/12 current · session ' + coverage.latest_completed_session + ' · as of ' + coverage.as_of + '. 11 ETFs + SPY; D3 additionally requires QQQ when market-regime context is active. Cache freshness alone is not technical readiness.' :
      'Coverage unavailable. ' + (errors.coverage || '') + ' D1 checks ETF + SPY; D3 can additionally require QQQ.');
    warning('mismatch', comparisonWarnings([currentRotation(), data.rotation, data.technical, data.chart]).join(' '));
  }
  function fillCell(row, value, extra) {
    const cell = node('td', value);
    if (extra) cell.append(node('small', extra));
    row.append(cell); return cell;
  }
  function sectorButton(symbol, label) {
    const button = node('button', label || symbol); button.type = 'button';
    button.setAttribute('aria-label', 'Inspect ' + symbol); button.addEventListener('click', () => focusSector(symbol));
    return button;
  }
  function focusSector(symbol, control = null) {
    const chart = control && control.closest('#msi-scatter, #msi-technical-chart');
    const key = control && control.dataset.focusKey;
    state.etf = symbol; $('focus').value = symbol; urlState(); renderScatter(); renderTechnicalChart(); renderDetail();
    // Rendering changes trail membership and plot extents. Restore only the initiating observation.
    if (chart && key) {
      const replacement = Array.from(chart.querySelectorAll('[data-focus-key]')).find(item => item.dataset.focusKey === key);
      if (replacement) replacement.focus({preventScroll: true});
    }
  }
  function renderOverview() {
    const rotation = currentRotation(), summary = rotationOverview(rotation, universe);
    text('summary-source', rotation ? provenance(rotation) + ' · benchmark ' + rotation.benchmark_symbol :
      state.snapshot_id ? 'Selected saved snapshot unavailable; no preview substitution.' : errors.rotation || 'Loading current rotation…');
    const counts = $('quadrants'); counts.replaceChildren();
    Object.entries(summary.counts).forEach(([state, count]) => {
      const item = node('div', undefined, 'msi-quadrant-count'); item.append(node('dt', state), node('dd', rotation ? String(count) : 'N/A')); counts.append(item);
    });
    text('summary-coverage', rotation ? rotation.cohort_status + ' · ' + summary.eligible + '/11 eligible · ' + summary.missing + '/11 insufficient' : 'Evidence unavailable');
    function highlights(id, rows, label) {
      const list = $(id); list.replaceChildren();
      rows.forEach(row => {
        const item = node('li'); item.append(sectorButton(row.etf_symbol), node('span', label(row))); list.append(item);
      });
      if (!rows.length) list.append(node('li', summary.complete ? 'No sectors have positive relative momentum.' : 'Unavailable until all eleven sectors form a complete cohort.'));
    }
    highlights('strongest', summary.strongest, row => ' #' + row.rank + ' · ' + row.standard_market_sector + ' · RS63 ' + number(row.rs_63_pct, '%'));
    highlights('improvers', summary.improving, row => ' ' + number(row.relative_momentum_pp, ' PP') + ' · ' + row.standard_market_sector);
    const missing = rotation ? universe.flatMap(definition => {
      const row = (rotation.sectors || []).find(item => item.etf_symbol === definition.etf_symbol);
      return !row || row.quality_status !== 'ELIGIBLE' ? [definition.etf_symbol + ': ' + (row ? reason(row.reasons) : 'Missing row')] : [];
    }) : [];
    warning('summary-note', summary.complete ? '' : 'Partial, missing or disabled evidence: highlights withheld. Quadrant counts cover eligible rows only. ' + missing.join('; '));
  }
  function renderRanks() {
    const rotation = currentRotation();
    text('rotation-source', rotation ? provenance(rotation) : state.snapshot_id ? 'Selected saved snapshot unavailable. Current preview has not been substituted.' : errors.rotation || 'Loading current rotation preview…');
    const rows = rotation ? rotation.sectors : universe.map(row => Object.assign({}, row, {reasons: ['Rotation evidence unavailable']}));
    const body = $('ranks').tBodies[0]; body.replaceChildren();
    sortedRows(rows, sortKey, sortDirection).forEach(item => {
      const row = node('tr'); fillCell(row, numeric(item.rank) ? String(item.rank) : 'N/A');
      const sector = fillCell(row, ''); sector.append(sectorButton(item.etf_symbol, item.standard_market_sector), node('small', item.etf_symbol));
      fillCell(row, item.rotation_state || 'Insufficient data');
      ['rs_5_pct', 'rs_21_pct', 'rs_63_pct', 'relative_momentum_pp', 'etf_return_63_pct'].forEach(key => fillCell(row, number(item[key])));
      fillCell(row, item.last_session || 'N/A', item.quality_status === 'ELIGIBLE' ? item.price_basis : reason(item.reasons)); body.append(row);
    });
  }
  function drawBase(id, title, height) {
    const host = $(id); host.replaceChildren();
    const width = Math.max(300, Math.round(host.clientWidth));
    const svg = svgNode('svg', {viewBox: '0 0 ' + width + ' ' + height, role: 'group', 'aria-label': title});
    svg.append(svgNode('title', {}, title)); host.append(svg); return {svg, width, height};
  }
  function tooltip(marker, message, symbol, target = 'chart-tooltip') {
    marker.setAttribute('aria-label', message); marker.classList.add('msi-dot');
    const trailTarget = symbol && ['chart-tooltip', 'technical-tooltip'].includes(target);
    marker.addEventListener('pointerenter', () => { text(target, message); if (trailTarget) emphasizeTrails(symbol); });
    marker.addEventListener('focus', () => { text(target, message); if (trailTarget) emphasizeTrails(symbol); });
    if (trailTarget) {
      marker.addEventListener('pointerleave', () => emphasizeTrails(state.etf));
      marker.addEventListener('blur', () => emphasizeTrails(state.etf));
    }
    marker.addEventListener('click', () => { text(target, message); if (symbol) focusSector(symbol, document.activeElement === marker ? marker : null); });
    marker.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); if (!event.repeat) marker.dispatchEvent(new Event('click')); } });
    marker.append(svgNode('title', {}, message));
  }
  function emphasizeTrails(symbol) {
    document.querySelectorAll('#msi-scatter polyline[data-etf]').forEach(path => {
      const emphasized = path.dataset.etf === symbol;
      path.setAttribute('data-emphasized', emphasized);
      path.setAttribute('stroke-width', emphasized ? 3.5 : 1.5);
      path.setAttribute('opacity', emphasized ? 1 : .3);
    });
  }
  function renderScatter() {
    const rotation = currentRotation();
    const visible = (rotation && rotation.sectors || []).filter(row => numeric(row.rs_63_pct) && numeric(row.relative_momentum_pp));
    const trailSnapshots = Array.from(snapshots.values()).filter(item => !state.snapshot_id || !rotation || item.market_session <= rotation.market_session);
    const trailSymbols = Array.from(new Set(state.symbols.concat(state.etf)));
    const trails = trailSymbols.map(symbol => ({symbol, trail: trailSegments(trailSnapshots, symbol, data.chart ? data.chart.calendar_sessions : [], data.chart && data.chart.calendar_version)}));
    const stored = $('trails').checked ? trails.flatMap(item => item.trail.segments.flat()) : [];
    const allRows = visible.concat(stored.map(point => point.row));
    const {svg, width, height} = drawBase('scatter', 'D2 rotation map; exact values in sector table below', widthForMobile() ? 330 : 380);
    const padding = width < 500 ? 38 : 55, plotWidth = width - padding * 2, plotHeight = height - 65;
    const xExtent = Math.max(1, ...allRows.map(row => Math.abs(row.rs_63_pct))) * 1.2;
    const yExtent = Math.max(1, ...allRows.map(row => Math.abs(row.relative_momentum_pp))) * 1.2;
    const x = value => padding + plotWidth * (value + xExtent) / (2 * xExtent);
    const y = value => 25 + plotHeight * (yExtent - value) / (2 * yExtent);
    [['#f5f0e3', 'Improving', 0, 0], ['#edf5ed', 'Leading', 1, 0], ['#f8eeeb', 'Lagging', 0, 1], ['#f4f3e9', 'Weakening', 1, 1]].forEach(([fill, label, col, row]) => {
      svg.append(svgNode('rect', {x: padding + col * plotWidth / 2, y: 25 + row * plotHeight / 2, width: plotWidth / 2, height: plotHeight / 2, fill}));
      svg.append(svgNode('text', {x: padding + col * plotWidth / 2 + 10, y: row ? 25 + plotHeight - 12 : 44, fill: '#5e6b61', 'font-size': 12}, label));
    });
    svg.append(svgNode('line', {x1: x(0), y1: 25, x2: x(0), y2: 25 + plotHeight, stroke: '#8a9e8c', 'stroke-dasharray': '4 4'}));
    svg.append(svgNode('line', {x1: padding, y1: y(0), x2: width - padding, y2: y(0), stroke: '#8a9e8c', 'stroke-dasharray': '4 4'}));
    [-1, 0, 1].forEach(sign => {
      svg.append(svgNode('text', {x: x(sign * xExtent), y: height - 20, 'text-anchor': 'middle', fill: '#647067', 'font-size': 10}, number(sign * xExtent) + '%'));
      svg.append(svgNode('text', {x: padding - 5, y: y(sign * yExtent) + 3, 'text-anchor': 'end', fill: '#647067', 'font-size': 10}, number(sign * yExtent)));
    });
    svg.append(svgNode('text', {x: width / 2, y: height - 3, 'text-anchor': 'middle', fill: '#52665a', 'font-size': 11}, 'RS63 (%) → · momentum PP ↑'));
    function dot(row, payload, saved) {
      const focused = row.etf_symbol === state.etf;
      const group = svgNode('g', {tabindex: 0, role: 'button', 'data-etf': row.etf_symbol, 'data-source': payload.source_type,
        'data-focused': focused, 'data-session': payload.market_session, 'aria-pressed': focused,
        'data-focus-key': chartControlKey('scatter', row.etf_symbol, payload, row.price_basis)});
      group.classList.add(focused ? 'msi-focused-mark' : 'msi-context-mark');
      if (focused) group.append(svgNode('circle', {cx: x(row.rs_63_pct), cy: y(row.relative_momentum_pp), r: saved ? 8 : 10, fill: 'none', stroke: '#17372f', 'stroke-width': 1.5, 'stroke-dasharray': '2 2'}));
      group.append(svgNode('circle', {cx: x(row.rs_63_pct), cy: y(row.relative_momentum_pp), r: saved ? 4 : 6, fill: saved ? color(row.etf_symbol) : '#fff', stroke: color(row.etf_symbol), 'stroke-width': focused ? 3 : 2}));
      if (focused || (!saved && state.symbols.includes(row.etf_symbol))) group.append(svgNode('text', {x: x(row.rs_63_pct) + 9, y: y(row.relative_momentum_pp) - 8, fill: color(row.etf_symbol), 'font-size': 12, 'font-weight': 700}, row.etf_symbol));
      tooltip(group, row.etf_symbol + ' · ' + row.rotation_state + ' · rank ' + row.rank + ' · RS63 ' + row.rs_63_pct + '% · momentum ' + row.relative_momentum_pp + ' PP · ' + provenance(payload), row.etf_symbol);
      svg.append(group);
    }
    // Selection changes emphasis, never the saved controls' keyboard order.
    if ($('trails').checked) trails.forEach(item => item.trail.segments.forEach(segment => {
      if (segment.length > 1) svg.append(svgNode('polyline', {points: segment.map(point => x(point.row.rs_63_pct) + ',' + y(point.row.relative_momentum_pp)).join(' '), fill: 'none', stroke: color(item.symbol),
        'data-etf': item.symbol, 'data-focused': item.symbol === state.etf, 'data-emphasized': item.symbol === state.etf, 'data-source': 'PERSISTED',
        'stroke-width': item.symbol === state.etf ? 3.5 : 1.5, opacity: item.symbol === state.etf ? 1 : .3, 'pointer-events': 'none'}));
      segment.forEach(point => { if (Number(state.snapshot_id) !== point.snapshot.snapshot_id) dot(point.row, point.snapshot, true); });
    }));
    visible.forEach(row => dot(row, rotation, rotation.source_type === 'PERSISTED'));
    if (!allRows.length) svg.append(svgNode('text', {x: width / 2, y: height / 2 + 25, 'text-anchor': 'middle', fill: '#52665a', 'font-size': 12}, 'No eligible rotation points'));
    const gaps = trails.reduce((total, item) => total + item.trail.gaps, 0), incompatible = trails.reduce((total, item) => total + item.trail.incompatible, 0);
    text('history-note', errors.archive || (snapshots.size ? snapshots.size + ' actual saved captures. ' + gaps + ' selected trail gap(s); ' + incompatible + ' incompatible transition(s) separated. Solid = saved; hollow = current preview. Only verified consecutive market sessions are connected; no interpolation.' : 'No saved rotation snapshots yet. History appears only after a complete, authorized D2 CLI capture. Cached prices do not create saved signals.'));
    text('trail-focus', state.etf + ' focused: ringed markers and bold saved trail. Other selected trails remain visible; missing sessions stay missing.');
  }
  function widthForMobile() { return window.innerWidth < 600; }
  function renderArchiveSelector() {
    const selector = $('snapshot'); selector.replaceChildren(new Option('Current preview', ''));
    savedIndex.forEach(item => selector.add(new Option(item.market_session + ' · saved #' + item.snapshot_id, item.snapshot_id)));
    if (state.snapshot_id && !savedIndex.some(item => item.snapshot_id === Number(state.snapshot_id))) selector.add(new Option('Saved #' + state.snapshot_id + (snapshots.has(Number(state.snapshot_id)) ? '' : ' · unavailable'), state.snapshot_id));
    selector.value = state.snapshot_id || '';
  }
  function renderPrices() {
    const chart = data.chart, rows = chart ? chart.rows : universe;
    text('price-source', chart ? provenance(chart) + ' · common start ' + chart.start_session + ' · ' + chart.history_semantics : errors.chart || 'Loading cached price history…');
    const body = $('heatmap').tBodies[0]; body.replaceChildren();
    rows.forEach(item => {
      const row = node('tr'); const cell = fillCell(row, ''); cell.append(sectorButton(item.etf_symbol), node('small', item.standard_market_sector));
      [5, 21, 63, 126].forEach(window => {
        const metric = item.returns && item.returns[window];
        const value = metric && metric[state.mode === 'relative' ? 'spy_relative_pct' : 'absolute_pct'];
        const cell = fillCell(row, number(value, '%'), !numeric(value) ? reason(metric && metric.reasons) : '');
        cell.className = !numeric(value) ? 'msi-missing' : value >= 0 ? 'msi-positive' : 'msi-negative';
        cell.title = item.etf_symbol + ' · ' + window + ' sessions · ' + state.mode + ' price return (%) · ' + (chart ? provenance(chart) : 'Unavailable');
      }); body.append(row);
    });
    const selected = chart ? chart.rows.filter(row => state.symbols.includes(row.etf_symbol)) : [];
    if (chart && $('spy').checked) selected.push(chart.benchmark);
    const available = selected.filter(row => row.points && row.points.length);
    const {svg, width, height} = drawBase('lines', 'Normalized price indices, common start 100; accessible data table follows', 300);
    if (!available.length) {
      svg.append(svgNode('text', {x: width / 2, y: 145, 'text-anchor': 'middle', fill: '#637366', 'font-size': 12}, 'No comparable price lines selected'));
    } else {
      const indices = new Map(chart.calendar_sessions.map((day, i) => [day, i]));
      const firstIndex = indices.get(chart.start_session), endIndex = indices.get(chart.market_session);
      const values = available.flatMap(row => row.points.map(point => point.index_100));
      const low = Math.min(100, ...values) - 1, high = Math.max(100, ...values) + 1;
      const x = day => 40 + (width - 80) * (indices.get(day) - firstIndex) / (endIndex - firstIndex);
      const y = value => 20 + 240 * (high - value) / (high - low);
      svg.append(svgNode('line', {x1: 40, x2: width - 40, y1: y(100), y2: y(100), stroke: '#a5b9aa', 'stroke-dasharray': '4 4'}));
      [low, 100, high].forEach(value => svg.append(svgNode('text', {x: 35, y: y(value) + 4, 'text-anchor': 'end', fill: '#5b6e61', 'font-size': 10}, number(value))));
      [chart.start_session, chart.market_session].forEach((day, index) => svg.append(svgNode('text', {x: x(day), y: 289, 'text-anchor': index ? 'end' : 'start', fill: '#5b6e61', 'font-size': 10}, day)));
      available.forEach(row => {
        let segment = [], previous = null;
        function finish() { if (segment.length > 1) svg.append(svgNode('polyline', {points: segment.join(' '), fill: 'none', stroke: color(row.symbol), 'stroke-width': row.symbol === 'SPY' ? 2.5 : 1.8, 'pointer-events': 'none', 'stroke-dasharray': row.symbol === 'SPY' ? '5 3' : ''})); segment = []; }
        row.points.forEach((point, index) => {
          if (!numeric(point.index_100) || !indices.has(point.session)) { finish(); previous = null; return; }
          if (previous !== null && indices.get(point.session) !== previous + 1) finish();
          segment.push(x(point.session) + ',' + y(point.index_100)); previous = indices.get(point.session);
          const last = index === row.points.length - 1;
          const marker = svgNode('circle', {cx: x(point.session), cy: y(point.index_100), r: last ? 4 : 3, fill: color(row.symbol), opacity: last ? 1 : 0, tabindex: last ? 0 : -1, role: 'button'});
          tooltip(marker, row.symbol + ' · ' + point.session + ' · index ' + point.index_100 + ' (start 100) · ' + provenance(chart), row.symbol === 'SPY' ? null : row.symbol, 'line-tooltip'); svg.append(marker);
          if (last) svg.append(svgNode('text', {x: x(point.session) + 4, y: y(point.index_100) - 6, fill: color(row.symbol), 'font-size': 10}, row.symbol));
        }); finish();
      });
    }
    const missing = selected.filter(row => !row.points || !row.points.length).map(row => row.symbol + ': ' + reason(row.reasons));
    text('line-note', chart ? 'Index 100 at exact common start ' + chart.start_session + '. No forward-fill. ' + missing.join('; ') : errors.chart || 'Awaiting cached history.');
    const table = $('line-values'); table.tHead.replaceChildren(); table.tBodies[0].replaceChildren();
    const header = node('tr'); header.append(node('th', 'Session')); selected.forEach(row => header.append(node('th', row.symbol + ' index'))); table.tHead.append(header);
    if (chart) {
      const points = selected.map(row => new Map((row.points || []).map(point => [point.session, point.index_100])));
      chart.calendar_sessions.filter(day => day >= chart.start_session).forEach(day => {
        const row = node('tr'); fillCell(row, day); points.forEach(map => fillCell(row, number(map.get(day)))); table.tBodies[0].append(row);
      });
    }
  }
  function renderTechnicals() {
    const technical = data.technical, blocked = cohortBlocked(technical);
    text('technical-source', technical ? provenance(technical) + ' · V4 ' + (technical.effective_configuration.v4_engine.version || 'native') + ' · V5 ' + (technical.effective_configuration.v5_engine.version || 'native') + ' · SPY benchmark; ETF sector peer N/A; QQQ regime context only.' : errors.technical || 'Loading current technical preview…');
    warning('cohort', technical && blocked ? technical.v5_cohort_status + ' · All final V5 comparisons are withheld. Required cohort evidence: ' + JSON.stringify(technical.v5_missing_members || {}) + '. Individually valid V4 remains available.' : '');
    const body = $('technicals').tBodies[0]; body.replaceChildren();
    (technical ? technical.rows : universe).forEach(item => {
      const row = node('tr'), v4 = item.v4 || {}, v5 = blocked ? {} : item.v5 || {};
      fillCell(row, '').append(sectorButton(item.etf_symbol));
      fillCell(row, number(v4.final_v4_score), v4.confidence || 'Unavailable');
      fillCell(row, v4.classification || 'N/A', v4.action_bias || 'No current diagnostic');
      fillCell(row, number(v5.technical_composite_score) + ' / ' + number(v5.confidence_adjusted_score));
      fillCell(row, v5.classification || 'N/A', [v5.action_bias, v5.confidence].filter(Boolean).join(' · ') || 'Cohort unavailable');
      fillCell(row, number(v5.leadership_score), v5.leadership_scope || 'fixed_11_etf_universe');
      fillCell(row, !blocked && item.comparison ? number(item.comparison.raw_score_delta) : 'N/A');
      fillCell(row, 'V4 ' + (v4.status || 'unavailable') + ' · V5 ' + (blocked ? 'NOT_COMPARABLE' : v5.status || 'unavailable'), reason(item.data_reasons));
      body.append(row);
    });
    renderTechnicalChart();
  }
  function renderTechnicalChart() {
    const rows = technicalComparisons(data.technical, universe), rowHeight = 43;
    const {svg, width} = drawBase('technical-chart', 'Native V4 final and V5 raw scores; classifications and exact values in the adjacent table', rows.length * rowHeight + 45);
    const start = 58, end = width - 68, x = value => start + (end - start) * value / 10;
    [0, 5, 10].forEach(value => {
      svg.append(svgNode('line', {x1: x(value), x2: x(value), y1: 22, y2: rows.length * rowHeight + 15, stroke: '#dce4df', 'stroke-dasharray': '3 3'}));
      svg.append(svgNode('text', {x: x(value), y: 12, 'text-anchor': 'middle', fill: '#58665f', 'font-size': 10}, String(value)));
    });
    rows.forEach((row, index) => {
      const y = 29 + index * rowHeight;
      const label = svgNode('text', {x: 0, y: y + 8, tabindex: 0, role: 'button', 'font-size': 12, 'font-weight': row.symbol === state.etf ? 800 : 500,
        'data-focus-key': chartControlKey('technical', row.symbol, data.technical || {}, null)}, row.symbol);
      tooltip(label, row.symbol + ' · native V4 ' + number(row.v4) + ' (' + row.v4Classification + ', confidence ' + row.v4Confidence + ') · native V5 raw ' + number(row.v5) + ' (' + row.v5Classification + ', confidence ' + row.v5Confidence + ') · descriptive delta ' + number(row.delta), row.symbol, 'technical-tooltip'); svg.append(label);
      [['v4', 'V4', '#126d56', 0], ['v5', 'V5', '#7357a0', 11]].forEach(([key, model, fill, offset]) => {
        const value = row[key];
        if (numeric(value)) {
          const bar = svgNode('rect', {x: start, y: y + offset, width: Math.max(0, x(value) - start), height: 7,
            fill: key === 'v4' ? fill : 'none', stroke: fill, 'stroke-width': 1.5, 'data-model': model, 'data-etf': row.symbol, 'data-value': value});
          svg.append(bar);
        }
        svg.append(svgNode('text', {x: end + 7, y: y + offset + 7, fill: '#17372f', 'font-size': 10}, model + ' ' + number(value)));
      });
      const classifications = 'V4 ' + row.v4Classification + ' · V5 ' + row.v5Classification;
      const limit = Math.max(20, Math.floor((width - start) / 5));
      svg.append(svgNode('text', {x: start, y: y + 32, fill: '#58665f', 'font-size': 9}, classifications.length > limit ? classifications.slice(0, limit - 1) + '…' : classifications));
    });
  }
  function renderDetail() {
    const container = $('detail'); container.replaceChildren();
    const definition = universe.find(row => row.etf_symbol === state.etf);
    if (!definition) { container.append(node('p', 'Sector configuration unavailable.')); return; }
    text('detail-title', state.etf + ' · ' + definition.standard_market_sector);
    const rotation = currentRotation(), technical = data.technical, chart = data.chart;
    const r = rotation && rotation.sectors.find(row => row.etf_symbol === state.etf);
    const t = technical && technical.rows.find(row => row.etf_symbol === state.etf);
    const c = chart && chart.rows.find(row => row.etf_symbol === state.etf);
    const grid = node('div', undefined, 'msi-detail-grid');
    [['Rotation evidence', rotation, r], ['Cached price evidence', chart, c], ['Current research diagnostics', technical, t]].forEach(([label, payload, row]) => {
      const section = node('div'); section.append(node('h3', label), node('p', payload ? provenance(payload) : 'Unavailable'));
      if (label === 'Rotation evidence') section.append(node('p', r ? 'Rank ' + (r.rank || 'N/A') + ' · ' + r.rotation_state + ' · basis ' + r.price_basis : 'No saved or current rotation evidence.'));
      if (label === 'Cached price evidence') section.append(node('p', c ? c.status + ' · ' + c.price_basis + ' · ' + reason(c.reasons) : 'No comparable cached span.'));
      if (label === 'Current research diagnostics' && t) section.append(node('p', 'Native V4/V5 components, warnings and readiness below. Technical values are current only.'));
      const details = node('details'); details.append(node('summary', 'Evidence, components and warnings'), node('pre', JSON.stringify(row || {status: 'UNAVAILABLE'}, null, 2))); section.append(details); grid.append(section);
    });
    container.append(grid);
    const saved = Array.from(snapshots.values()).filter(item => !state.snapshot_id || !rotation || item.market_session <= rotation.market_session);
    const movement = savedRankMovement(saved, state.etf, chart ? chart.calendar_sessions : [], chart && chart.calendar_version);
    container.append(node('p', movement ? 'Stored rank movement · ' + movement.from + ' → ' + movement.to + ': #' + movement.priorRank + ' → #' + movement.rank +
      ' (' + (movement.change > 0 ? 'up ' + movement.change : movement.change < 0 ? 'down ' + Math.abs(movement.change) : 'unchanged') + '). Consecutive comparable saved sessions only; no preview rank included.' :
      'Stored rank movement unavailable: two consecutive comparable saved sessions are required. Missing captures are not reconstructed.', 'msi-rank-movement'));
    const identities = node('details'); identities.append(node('summary', 'Source identities and separate cutoffs'));
    const headers = {};
    [['rotation', rotation], ['technical', technical], ['chart', chart]].forEach(([key, payload]) => {
      if (payload) headers[key] = Object.fromEntries(Object.entries(payload).filter(([name]) => !['sectors', 'rows', 'benchmark', 'calendar_sessions', 'benchmark_quality'].includes(name)));
    });
    identities.append(node('pre', JSON.stringify(headers, null, 2))); container.append(identities);
    const table = node('table'), caption = node('caption', 'Actual saved rotation observations for ' + state.etf + ' · no reconstructed dates'); table.append(caption);
    const head = node('thead'), header = node('tr'); ['Saved session', 'Rank', 'State', 'RS63 %', 'Momentum PP', 'Method / config'].forEach(label => header.append(node('th', label))); head.append(header); table.append(head);
    const body = node('tbody'); Array.from(snapshots.values()).sort((a, b) => a.market_session.localeCompare(b.market_session)).forEach(snapshot => {
      const row = snapshot.sectors.find(item => item.etf_symbol === state.etf); if (!row) return;
      const tr = node('tr'); [snapshot.market_session, row.rank, row.rotation_state, number(row.rs_63_pct), number(row.relative_momentum_pp), snapshot.algorithm_version + ' / ' + short(snapshot.configuration_digest)].forEach(value => fillCell(tr, String(value))); body.append(tr);
    }); table.append(body);
    const scroll = node('div', undefined, 'msi-table-scroll'); scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', 'Saved sector history'); scroll.append(table); container.append(scroll);
    if (!snapshots.size) container.append(node('p', 'No saved rotation snapshots yet. Current technical diagnostics have no historical archive.'));
    const links = node('p');
    [[state.snapshot_id ? 'Saved snapshot JSON' : 'Current rotation JSON', state.snapshot_id ? 'rotation/snapshots/' + state.snapshot_id : 'rotation/preview'], ['Current technical JSON', 'technicals/preview/' + state.etf], ['Price history JSON', 'charts/price-series?horizon=' + state.horizon + '&symbols=' + state.etf]].forEach(([label, path]) => {
      const link = node('a', label + ' ↗'); link.href = '/api/market-sector-intelligence/' + path; links.append(link, document.createTextNode(' · '));
    }); container.append(links);
  }
  function render() {
    renderStatus(); renderOverview(); renderArchiveSelector(); renderRanks(); renderScatter(); renderPrices(); renderTechnicals(); renderDetail();
  }
  $('focus').value = state.etf; $('horizon').value = state.horizon; $('mode').value = state.mode;
  document.querySelectorAll('#msi-selection input').forEach(input => {
    input.checked = state.symbols.includes(input.value); input.parentElement.style.borderLeft = '3px solid ' + color(input.value); input.parentElement.style.paddingLeft = '5px';
    input.addEventListener('change', () => {
      state.symbols = Array.from(document.querySelectorAll('#msi-selection input:checked')).map(item => item.value);
      if (!state.symbols.length) { input.checked = true; state.symbols = [input.value]; warning('notice', 'Keep at least one sector ETF selected.'); }
      else warning('notice', '');
      urlState(); renderScatter(); renderPrices();
    });
  });
  $('focus').addEventListener('change', () => focusSector($('focus').value));
  $('mode').addEventListener('change', () => { state.mode = $('mode').value; urlState(); renderPrices(); });
  $('horizon').addEventListener('change', () => { state.horizon = Number($('horizon').value); urlState(); loadChart(); });
  $('snapshot').addEventListener('change', () => { state.snapshot_id = $('snapshot').value || null; urlState(); render(); });
  $('history-limit').addEventListener('change', loadArchive);
  $('trails').addEventListener('change', renderScatter); $('spy').addEventListener('change', renderPrices);
  $('reload').addEventListener('click', reload);
  document.querySelectorAll('[data-sort]').forEach(button => button.addEventListener('click', () => {
    sortDirection = sortKey === button.dataset.sort ? -sortDirection : 1; sortKey = button.dataset.sort;
    document.querySelectorAll('[data-sort]').forEach(item => item.closest('th').removeAttribute('aria-sort'));
    button.closest('th').setAttribute('aria-sort', sortDirection === 1 ? 'ascending' : 'descending'); renderRanks();
  }));
  window.addEventListener('popstate', () => {
    const params = new URLSearchParams(window.location.search);
    const previousHorizon = state.horizon;
    const etf = (params.get('etf') || defaults.etf).trim().toUpperCase();
    state.etf = universe.some(row => row.etf_symbol === etf) ? etf : defaults.etf;
    state.horizon = [5, 21, 63, 126].includes(Number(params.get('horizon'))) ? Number(params.get('horizon')) : defaults.horizon;
    state.mode = params.get('mode') === 'absolute' ? 'absolute' : defaults.mode;
    state.snapshot_id = /^[1-9][0-9]*$/.test(params.get('snapshot_id') || '') && Number.isSafeInteger(Number(params.get('snapshot_id'))) ? params.get('snapshot_id') : defaults.snapshot_id;
    const selection = (params.get('symbols') || defaults.symbols.join(',')).split(',').map(symbol => symbol.trim().toUpperCase());
    state.symbols = universe.filter(row => selection.includes(row.etf_symbol)).map(row => row.etf_symbol);
    $('focus').value = state.etf; $('horizon').value = state.horizon; $('mode').value = state.mode;
    document.querySelectorAll('#msi-selection input').forEach(input => { input.checked = state.symbols.includes(input.value); });
    if (state.snapshot_id && !snapshots.has(Number(state.snapshot_id))) loadArchive();
    if (previousHorizon !== state.horizon) loadChart();
    render();
  });
  let resizing;
  window.addEventListener('resize', () => { clearTimeout(resizing); resizing = setTimeout(() => { renderScatter(); renderPrices(); renderTechnicalChart(); }, 100); });
  render(); reload();
}());
