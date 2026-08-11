import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, RefreshCw, Server } from 'lucide-react';
import { Spinner } from '../../components/Loader';
import { afterText, CATEGORY_META, sourceStatusMeta } from './adminUtils';

export default function ApisPanel({
  sources,
  categories,
  statusSummary,
  focusSource,
  probingName,
  togglingName,
  onProbe,
  onToggleSkip,
}) {
  const [catFilter, setCatFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [inUseOnly, setInUseOnly] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const rowRefs = useRef({});

  useEffect(() => {
    if (!focusSource) return;
    setExpanded(focusSource);
    const el = rowRefs.current[focusSource];
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [focusSource]);

  const rows = useMemo(() => {
    return sources.filter((src) => {
      if (catFilter !== 'all' && src.category !== catFilter) return false;
      const meta = sourceStatusMeta(src);
      if (inUseOnly && !meta.inUse) return false;
      if (statusFilter === 'operational' && (src.status !== 'operational' || meta.skipped)) return false;
      if (statusFilter === 'degraded' && !['degraded', 'rate_limited'].includes(src.status)) return false;
      if (statusFilter === 'offline' && !['offline', 'no_key'].includes(src.status)) return false;
      if (statusFilter === 'skipped' && !meta.skipped) return false;
      if (statusFilter === 'in-use' && !meta.inUse) return false;
      return true;
    });
  }, [sources, catFilter, statusFilter, inUseOnly]);

  const catNames = categories.length
    ? categories.map((c) => c.name)
    : Object.keys(CATEGORY_META);

  return (
    <section className="admin-panel admin-apis-panel">
      <div className="admin-panel-toolbar">
        <div>
          <div className="admin-table-title">
            <Server size={18} /> API command center
          </div>
          <p className="admin-panel-hint">
            Before: synthetic probe. During: in-flight. After: last live call. Probe one card without burning every LLM.
          </p>
        </div>
        <div className="admin-status-summary-pills">
          <span className="status-pill ok">{statusSummary?.operational ?? 0} operational</span>
          <span className="status-pill warn">{statusSummary?.degraded ?? 0} degraded</span>
          <span className="status-pill bad">{statusSummary?.offline ?? 0} offline / no key</span>
          {(statusSummary?.in_flight ?? 0) > 0 && (
            <span className="status-pill warn">{statusSummary.in_flight} in use now</span>
          )}
        </div>
      </div>

      <div className="admin-chip-row" role="toolbar" aria-label="Filter APIs">
        <button type="button" className={`admin-chip${catFilter === 'all' ? ' is-on' : ''}`} onClick={() => setCatFilter('all')}>
          All categories
        </button>
        {catNames.map((name) => (
          <button
            key={name}
            type="button"
            className={`admin-chip${catFilter === name ? ' is-on' : ''}`}
            onClick={() => setCatFilter(name)}
          >
            {name}
          </button>
        ))}
        <span className="admin-chip-sep" />
        {['all', 'operational', 'degraded', 'offline', 'skipped', 'in-use'].map((id) => (
          <button
            key={id}
            type="button"
            className={`admin-chip${statusFilter === id ? ' is-on' : ''}`}
            onClick={() => setStatusFilter(id)}
          >
            {id === 'all' ? 'Any status' : id.replace('-', ' ')}
          </button>
        ))}
        <button
          type="button"
          className={`admin-chip${inUseOnly ? ' is-on' : ''}`}
          onClick={() => setInUseOnly((v) => !v)}
        >
          In use
        </button>
      </div>

      <div className="admin-table-container">
        <table className="admin-table admin-ops-table">
          <thead>
            <tr>
              <th />
              <th>Name</th>
              <th>Category</th>
              <th>Status</th>
              <th>In-flight</th>
              <th>Last latency</th>
              <th>Last error</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((src) => {
              const meta = sourceStatusMeta(src);
              const live = meta.live;
              const open = expanded === src.name;
              const latency = live.last_latency_ms ?? src.latency_ms;
              const lastError = (!live.last_ok && live.last_error)
                || (['offline', 'no_key', 'degraded', 'rate_limited'].includes(src.status) ? src.details : '');
              const probing = probingName === src.name;
              const toggling = togglingName === src.name;

              return (
                <React.Fragment key={src.name}>
                  <tr
                    ref={(el) => { rowRefs.current[src.name] = el; }}
                    className={`${meta.skipped ? 'is-skipped' : ''}${focusSource === src.name ? ' is-focus' : ''}${meta.inUse ? ' is-in-use-row' : ''}`}
                  >
                    <td>
                      <button
                        type="button"
                        className={`admin-expand-btn${open ? ' is-open' : ''}`}
                        aria-expanded={open}
                        onClick={() => setExpanded(open ? null : src.name)}
                      >
                        <ChevronDown size={14} />
                      </button>
                    </td>
                    <td>
                      <div className="admin-api-name">
                        {src.name}
                        {meta.skipped && <span className="admin-skipped-badge">skipped</span>}
                      </div>
                    </td>
                    <td className="muted">{src.category}</td>
                    <td>
                      <span className={`source-status-badge ${meta.badge}`}>
                        <span className="source-status-pulse" aria-hidden="true" />
                        {meta.label}
                      </span>
                    </td>
                    <td className="admin-mono">{meta.inFlight || 0}</td>
                    <td className="admin-mono">{latency != null ? `${latency} ms` : '—'}</td>
                    <td className="admin-error-cell">{lastError || '—'}</td>
                    <td>
                      <div className="admin-row-actions">
                        <button
                          type="button"
                          className="admin-action-btn"
                          disabled={probing}
                          onClick={() => onProbe(src.name)}
                        >
                          {probing ? <Spinner size={12} /> : <RefreshCw size={12} />}
                          Probe
                        </button>
                        {src.skippable && (
                          <button
                            type="button"
                            className={`admin-action-btn${meta.skipped ? ' is-success' : ''}`}
                            disabled={toggling}
                            onClick={() => onToggleSkip(src)}
                          >
                            {meta.skipped ? 'Include' : 'Skip in search'}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {open && (
                    <tr className="admin-expand-row">
                      <td colSpan={8}>
                        <div className="source-card-phases">
                          <div className="source-phase">
                            <span className="source-phase-tag">Before</span>
                            <span>
                              {src.details}
                              {src.latency_ms != null ? ` · ${src.latency_ms} ms probe` : ''}
                            </span>
                          </div>
                          <div className="source-phase">
                            <span className="source-phase-tag">During</span>
                            <span>
                              {meta.inUse
                                ? `${meta.inFlight || 1} request${meta.inFlight === 1 ? '' : 's'} in flight`
                                : 'Idle'}
                            </span>
                          </div>
                          <div className="source-phase">
                            <span className="source-phase-tag">After</span>
                            <span>{afterText(src)}</span>
                          </div>
                        </div>
                        <div className="source-card-meta admin-expand-meta">
                          {src.probe?.model && <span>Model · {src.probe.model}</span>}
                          {src.requires_key && <span>Env · {src.requires_key}</span>}
                          {(Number(live.calls_ok || 0) + Number(live.calls_fail || 0)) > 0 && (
                            <span className="source-card-latency">
                              {live.calls_ok || 0} ok / {live.calls_fail || 0} fail
                              {live.success_pct != null ? ` · ${live.success_pct}%` : ''}
                            </span>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="admin-empty-row">No APIs match these filters.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
