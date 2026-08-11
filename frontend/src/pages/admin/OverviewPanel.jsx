import React from 'react';
import { Activity, AlertTriangle, ArrowRight, Radio, Users, Zap } from 'lucide-react';
import { formatAgo, isUnhealthy, sourceStatusMeta } from './adminUtils';

export default function OverviewPanel({
  users,
  sources,
  statusSummary,
  events,
  usage,
  onJumpToApi,
}) {
  const inFlight = statusSummary?.in_flight ?? 0;
  const tokensToday = usage?.today_total
    ?? users.reduce((acc, u) => acc + (u.tokens_today || 0), 0);
  const activeUsers = users.filter((u) => (u.tokens_today || 0) > 0).length;
  const unhealthy = sources.filter(isUnhealthy);
  const failures = (events || []).filter((e) => !e.ok).slice(0, 8);

  return (
    <section className="admin-panel admin-overview-panel">
      <div className="admin-panel-toolbar">
        <div className="admin-table-title">
          <Radio size={18} /> Incident strip
        </div>
        <p className="admin-panel-hint">Live snapshot — jump a failing API to probe or skip it.</p>
      </div>

      <div className="admin-incident-grid">
        <button type="button" className="admin-incident-card" onClick={() => onJumpToApi(null)}>
          <span className="admin-incident-label">In flight</span>
          <span className="admin-incident-value">{inFlight}</span>
          <span className="admin-incident-sub">requests right now</span>
        </button>
        <button type="button" className="admin-incident-card is-warn" onClick={() => onJumpToApi(unhealthy[0]?.name || null)}>
          <span className="admin-incident-label">Unhealthy APIs</span>
          <span className="admin-incident-value">{unhealthy.length}</span>
          <span className="admin-incident-sub">offline / degraded / no key</span>
        </button>
        <div className="admin-incident-card">
          <span className="admin-incident-label">Tokens today</span>
          <span className="admin-incident-value">{Number(tokensToday || 0).toLocaleString()}</span>
          <span className="admin-incident-sub">across all users</span>
        </div>
        <div className="admin-incident-card">
          <span className="admin-incident-label">Active users</span>
          <span className="admin-incident-value">{activeUsers}</span>
          <span className="admin-incident-sub">used quota today</span>
        </div>
      </div>

      <div className="admin-overview-split">
        <div>
          <h3 className="admin-overview-h">Unhealthy sources</h3>
          {unhealthy.length === 0 ? (
            <p className="admin-empty-copy">All probed APIs look operational.</p>
          ) : (
            <ul className="admin-fail-list">
              {unhealthy.map((src) => {
                const meta = sourceStatusMeta(src);
                return (
                  <li key={src.name}>
                    <button type="button" className="admin-fail-row" onClick={() => onJumpToApi(src.name)}>
                      <span className={`source-status-badge ${meta.badge}`}>
                        <span className="source-status-pulse" aria-hidden="true" />
                        {meta.label}
                      </span>
                      <span className="admin-fail-name">{src.name}</span>
                      <span className="admin-fail-detail">{src.details}</span>
                      <ArrowRight size={14} />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div>
          <h3 className="admin-overview-h">
            <AlertTriangle size={15} /> Last failures
          </h3>
          {failures.length === 0 ? (
            <p className="admin-empty-copy">No failed live calls in the event ring yet.</p>
          ) : (
            <ul className="admin-fail-list">
              {failures.map((ev, i) => (
                <li key={`${ev.ts}-${ev.name}-${i}`}>
                  <button type="button" className="admin-fail-row" onClick={() => onJumpToApi(ev.name)}>
                    <span className="admin-mono muted">{formatAgo(ev.ts) || '—'}</span>
                    <span className="admin-fail-name">{ev.name}</span>
                    <span className="admin-fail-detail">
                      {ev.operation}
                      {ev.latency_ms != null ? ` · ${ev.latency_ms} ms` : ''}
                      {ev.error ? ` · ${ev.error}` : ''}
                    </span>
                    <ArrowRight size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="admin-overview-foot">
        <Activity size={14} />
        <span>{users.length} accounts</span>
        <span className="admin-metric-dot" />
        <Users size={14} />
        <span>{activeUsers} active today</span>
        <span className="admin-metric-dot" />
        <Zap size={14} />
        <span>{statusSummary?.operational ?? 0}/{statusSummary?.total ?? sources.length} healthy</span>
      </div>
    </section>
  );
}
