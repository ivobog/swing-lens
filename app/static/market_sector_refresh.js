(function () {
  'use strict';
  const base = '/api/market-sector-intelligence/refresh';
  const storageKey = 'swinglens.msi.refresh.v1';
  function freshnessView(payload, universe) {
    const value = payload || {}, entries = value.entries || [];
    const stale = entries.filter(row => row.status === 'STALE' && Number.isInteger(row.session_lag) && row.session_lag > 0);
    const unavailable = entries.filter(row => ['MISSING', 'UNKNOWN'].includes(row.status));
    const complete = universe.length === 11 && entries.length === 22 && universe.every(symbol =>
      ['TRADES', 'ADJUSTED_LAST'].every(basis => entries.filter(row => row.ticker === symbol && row.price_basis === basis &&
        row.status === 'CURRENT' && row.last_session === value.expected_session && row.session_lag === 0).length === 1));
    const status = stale.length ? 'STALE' : complete && value.status === 'CURRENT' ? 'CURRENT' :
      value.status === 'MISSING' ? 'MISSING' : 'UNKNOWN';
    const lag = stale.length ? Math.max(...stale.map(row => row.session_lag)) : null;
    return {status, label: status + (lag ? ' · ' + lag + ' trading session' + (lag === 1 ? '' : 's') + ' behind' : ''),
      detail: ['Expected completed session: ' + (value.expected_session || 'unknown'),
        ...stale.map(row => row.ticker + ' ' + row.price_basis + ': ' + row.session_lag + ' session(s) behind'),
        ...unavailable.map(row => row.ticker + ' ' + row.price_basis + ': ' + row.status)].join(' · ')};
  }

  function createRefreshController({request, schedule = setTimeout, cancel = clearTimeout, storage,
    view, reloadPage, reloadPanels}) {
    let active = null, busy = false, checkOnly = false, timer = null, stopped = false, reloaded = false, failures = 0, generation = 0;
    function remember(id) {
      try { if (id) storage.setItem(storageKey, String(id)); else storage.removeItem(storageKey); } catch (_) { /* Storage is optional. */ }
    }
    function saved() {
      try { const id = Number(storage.getItem(storageKey)); return Number.isSafeInteger(id) && id > 0 ? id : null; }
      catch (_) { return null; }
    }
    function show(message, disabled, state) { if (!stopped) view({message, disabled, state}); }
    function later(callback) { timer = schedule(callback, 2500); }
    function terminal(payload) {
      active = null; busy = false; checkOnly = false; remember(null);
      const failed = (payload.failures || []).map(item => item.ticker + ' ' + item.basis).join(', ');
      if (payload.status === 'COMPLETED') {
        show('Sector data refreshed. Updating page…', true, 'COMPLETED');
        if (!reloaded) { reloaded = true; reloadPage(); }
      } else if (payload.status === 'PARTIAL') {
        show('Partially refreshed.' + (failed ? ' Failed: ' + failed + '.' : '') + ' Checking stored coverage.', false, 'PARTIAL');
        reloadPanels();
      } else {
        show('Sector refresh ' + payload.status.toLowerCase() + '.' + (failed ? ' Failed: ' + failed + '.' : '') + ' Cached evidence remains visible; try again.', false, payload.status);
      }
    }
    function observe(payload) {
      if (stopped) return;
      if (!payload || !Number.isSafeInteger(payload.job_id) || payload.job_id < 1 ||
          (active && payload.job_id !== active)) throw new Error('Refresh status could not be verified.');
      if (['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED'].includes(payload.status)) { terminal(payload); return; }
      if (!['QUEUED', 'RUNNING'].includes(payload.status)) throw new Error('Refresh status could not be verified.');
      active = payload.job_id; remember(active); failures = 0;
      const counts = Number.isInteger(payload.completed_items) && Number.isInteger(payload.total_items) ?
        ' ' + payload.completed_items + ' / ' + payload.total_items + ' acquisition items completed.' : '';
      show('Refreshing sector data…' + counts + ' Showing cached evidence until completion.', true, payload.status);
      later(poll);
    }
    function observationError() {
      if (stopped) return;
      if (++failures <= 3) {
        show('Refresh may still be running; retrying status check…', true, 'RETRYING'); later(poll);
      } else {
        busy = false; checkOnly = true;
        show('Refresh may still be running; status cannot be checked. Use the button to check again.', false, 'UNKNOWN');
      }
    }
    async function poll() {
      if (stopped) return;
      const current = generation;
      try {
        const payload = await request(active ? base + '/' + active : base + '/active');
        if (stopped || current !== generation) return;
        if (!payload && !active) { busy = false; checkOnly = false; show('No sector refresh is active.', false, 'IDLE'); return; }
        observe(payload);
      } catch (_) { if (current === generation) observationError(); }
    }
    async function start() {
      if (busy || stopped || reloaded) return;
      busy = true; failures = 0;
      const current = generation;
      if (active || checkOnly) { show('Checking refresh status…', true, 'CHECKING'); await poll(); return; }
      show('Checking IB Gateway…', true, 'CHECKING');
      try { const payload = await request(base, {method: 'POST'}); if (current === generation) observe(payload); }
      catch (error) {
        if (stopped || current !== generation) return;
        if (error.uncertain || !error.code || error.code === 'REFRESH_ADMISSION_BUSY') {
          show('Checking whether sector refresh was admitted…', true, 'CHECKING'); later(poll);
        } else { busy = false; show(error.message || 'Sector refresh could not start. Try again.', false, 'FAILED'); }
      }
    }
    return {start, resume() { cancel(timer); generation++; stopped = false; reloaded = false;
      active = saved(); busy = true; failures = 0; return poll(); },
      stop() { generation++; stopped = true; cancel(timer); }};
  }
  const api = {createRefreshController, freshnessView};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.SectorRefresh = api;
}());
