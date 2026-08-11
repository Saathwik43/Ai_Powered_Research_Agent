import React from 'react';
import { Activity, Cpu, Users } from 'lucide-react';

export default function UsagePanel({ usage }) {
  const todayTotal = Number(usage?.today_total || 0);
  const defaultQuota = Number(usage?.default_quota || 250000);
  const pct = Math.min(100, (todayTotal / Math.max(1, defaultQuota)) * 100);
  const providers = usage?.by_provider || [];
  const maxProvider = Math.max(1, ...providers.map((p) => p.tokens || 0));
  const topUsers = usage?.by_user || usage?.data || [];

  return (
    <section className="admin-panel admin-usage-panel">
      <div className="admin-panel-toolbar">
        <div className="admin-table-title">
          <Activity size={18} /> Usage today
        </div>
        <p className="admin-panel-hint">UTC day from usage_logs. Provider split is the logged model name.</p>
      </div>

      <div className="admin-usage-hero">
        <div>
          <div className="admin-stat-label">Tokens today</div>
          <div className="admin-stat-value">
            {todayTotal.toLocaleString()}
            <span className="admin-stat-of">/{defaultQuota.toLocaleString()}</span>
          </div>
          <div className="admin-stat-sub">
            vs default daily quota · {usage?.today_calls || 0} logged calls
          </div>
        </div>
        <div className="admin-quota-bar admin-usage-bar" aria-hidden="true">
          <div className={`admin-quota-fill${pct > 80 ? ' is-hot' : ''}`} style={{ width: `${pct}%` }} />
        </div>
      </div>

      <div className="admin-usage-split">
        <div>
          <h3 className="admin-overview-h">
            <Cpu size={15} /> By provider
          </h3>
          {providers.length === 0 ? (
            <p className="admin-empty-copy">No token logs today.</p>
          ) : (
            <ul className="admin-provider-list">
              {providers.map((p) => {
                const width = Math.max(4, Math.round(((p.tokens || 0) / maxProvider) * 100));
                return (
                  <li key={p.provider}>
                    <div className="admin-provider-head">
                      <span>{p.provider}</span>
                      <span className="admin-mono">{Number(p.tokens || 0).toLocaleString()} · {p.calls || 0} calls</span>
                    </div>
                    <div className="admin-quota-bar" aria-hidden="true">
                      <div className="admin-quota-fill" style={{ width: `${width}%` }} />
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div>
          <h3 className="admin-overview-h">
            <Users size={15} /> Top users
          </h3>
          <div className="admin-table-container">
            <table className="admin-table admin-ops-table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Used</th>
                  <th>Quota</th>
                  <th>Left</th>
                </tr>
              </thead>
              <tbody>
                {topUsers.map((u) => (
                  <tr key={u.user_id}>
                    <td>
                      <div className="admin-user-name">{u.name || '—'}</div>
                      <div className="admin-user-email">{u.email}</div>
                    </td>
                    <td className="admin-mono">{Number(u.used || 0).toLocaleString()}</td>
                    <td className="admin-mono muted">{Number(u.quota || 0).toLocaleString()}</td>
                    <td><span className="badge-messages">{u.messages_left}</span></td>
                  </tr>
                ))}
                {topUsers.length === 0 && (
                  <tr>
                    <td colSpan={4} className="admin-empty-row">No usage today.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}
