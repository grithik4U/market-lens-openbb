import React, { useEffect, useRef, useState } from 'react';

// Pass the app's authenticated base44 SDK client as a prop.
// This component is a reference, not installed in the existing app.
export default function MarketDataPanel({ base44, symbol = 'BTCUSD' }) {
  const [state, setState] = useState({ loading: true, data: null });
  const [refresh, setRefresh] = useState(0);
  const [auto, setAuto] = useState(false);
  const [clock, setClock] = useState(Date.now());
  const requestId = useRef(0);
  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    let cancelled = false, timer, failures = 0, inFlight = false;
    const id = ++requestId.current;
    const operation = symbol === 'XAUUSD' ? 'gold_quote' : 'btc_quote';
    setState({ loading: true, data: null });
    async function load() {
      clearTimeout(timer);
      if (document.hidden || cancelled || inFlight) return;
      inFlight = true;
      setState(previous => ({ ...previous, loading: true }));
      try {
        const response = await base44.functions.invoke('market-data', { operation, symbol });
        if (cancelled || id !== requestId.current) return;
        const data = response.data;
        failures = ['fresh', 'stale', 'market_closed'].includes(data.status) ? 0 : failures + 1;
        setState({ loading: false, data });
      } catch {
        if (cancelled || id !== requestId.current) return;
        failures += 1;
        // Clear the last quote on error; never leave it labelled live.
        setState({ loading: false, data: { status: 'unavailable', price: null } });
      } finally {
        inFlight = false;
      }
      if (auto && !cancelled && !document.hidden) {
        timer = setTimeout(load, Math.min(300000, 30000 * 2 ** Math.min(failures, 4)));
      }
    }
    function visibility() {
      clearTimeout(timer);
      if (!document.hidden) load();
    }
    load();
    document.addEventListener('visibilitychange', visibility);
    return () => { cancelled = true; clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [base44, symbol, refresh, auto]);
  const data = state.data;
  const observed = data?.source_observed_at ? Date.parse(data.source_observed_at) : NaN;
  const age = (clock - observed) / 1000;
  const status = !data ? 'loading' : data.status === 'fresh'
    ? (!Number.isFinite(age) ? 'unknown' : age < -5 ? 'clock_skew' : age > 60 ? 'stale' : 'fresh')
    : data.status;
  const labels = { fresh: 'Fresh quote', stale: 'Stale quote', not_configured: 'Connection needed',
    unavailable: 'Data unavailable', unknown: 'Time unknown', clock_skew: 'Check source time',
    market_closed: 'Market closed', loading: 'Checking data' };
  return <section style={{ background: '#fff', border: '1px solid #dbe1e8', borderRadius: 20, padding: 24, color: '#132337' }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
      <strong>{symbol === 'BTCUSD' ? 'Bitcoin / USD' : 'Gold / USD'}</strong>
      <span role="status">{labels[status] || 'Connection error'}</span>
    </div>
    <p style={{ fontSize: 36, fontWeight: 700, margin: '16px 0' }}>
      {Number.isFinite(data?.price) ? data.price.toLocaleString('en-US', { style: 'currency', currency: 'USD' }) : '—'}
    </p>
    <p>{data?.provider || 'Provider pending'}{data?.price_type === 'last_trade' ? ' · last trade' : ''}</p>
    <p>Source: {Number.isFinite(observed) ? new Date(observed).toISOString() : 'No verified timestamp'}</p>
    <p>Retrieved: {data?.retrieved_at || '—'}</p>
    <button disabled={state.loading} onClick={() => setRefresh(value => value + 1)}>
      {state.loading ? 'Checking…' : 'Refresh'}
    </button>{' '}
    <label><input type="checkbox" checked={auto} onChange={event => setAuto(event.target.checked)} /> Refresh while open</label>
    <p style={{ fontSize: 13 }}>Price feed only. Your dated analyst view is separate.</p>
  </section>;
}
