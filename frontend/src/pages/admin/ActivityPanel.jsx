import React, { useMemo, useState } from 'react';
import { Radio } from 'lucide-react';
import { formatAgo } from './adminUtils';

export default function ActivityPanel({ events, sources }) {
  const [provider, setProvider] = useState('all');
  const [outcome, setOutcome] = useState('all');

  const names = useMemo(() => {
    const set = new Set((sources || []).map((s) => s.name));
    (events || []).forEach((e) => { if (e.name) set.add(e.name); });
    return [...set].sort();
  }, [events, sources]);

  const rows = (events || []).filter((e) => {
    if (provider !== 'all' && e.name !== provider) return false;
    if (outcome === 'ok' && !e.ok) return false;
    if (outcome === 'fail' && e.ok) return false;
    return true;
  });

  return (
    <section className="admin-panel admin-activity-panel">
      <div className="admin-panel-toolbar">
        <div>
          <div className="admin-table-title">
            <Radio size={18} /> Live event log
          </div>
          <p className="admin-panel-hint">Last ~200 in-process calls — search, generate, stream, embed, parse.</p>
        </div>
        <label className="admin-filter-select">
          Provider
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="all">All</option>
            {names.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="admin-chip-row">
        {['all', 'ok', 'fail'].map((id) => (
          <button
            key={id}
            type="button"
            className={`admin-chip${outcome === id ? ' is-on' : ''}`}
            onClick={() => setOutcome(id)}
          >
            {id === 'all' ? 'All outcomes' : id}
          </button>
        ))}
        <span className="admin-chip-count">{rows.length} events</span>
      </div>

      <ol className="admin-event-log">
        {rows.map((ev, i) => (
          <li key={`${ev.ts}-${ev.name}-${ev.operation}-${i}`} className={`admin-event-row${ev.ok ? ' is-ok' : ' is-fail'}`}>
            <span className={`admin-event-dot${ev.ok ? ' is-ok' : ' is-fail'}`} aria-hidden="true" />
            <span className="admin-mono admin-event-time">{formatAgo(ev.ts) || '—'}</span>
            <span className="admin-event-name">{ev.name}</span>
            <span className="admin-event-op">{ev.operation}</span>
            <span className="admin-mono muted">
              {ev.latency_ms != null ? `${ev.latency_ms} ms` : '—'}
              {ev.http_status != null ? ` · ${ev.http_status}` : ''}
              {ev.items != null ? ` · ${ev.items} items` : ''}
            </span>
            {!ev.ok && ev.error && <span className="admin-event-error">{ev.error}</span>}
          </li>
        ))}
        {rows.length === 0 && (
          <li className="admin-empty-copy">No events yet. Run a literature search or generate a section.</li>
        )}
      </ol>
    </section>
  );
}
