import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  Shield, Users, Activity, Search, RefreshCw, Sliders, UserCheck, UserX,
  ShieldCheck, Trash2, RotateCcw, AlertTriangle,
  Server, Zap, X, BookOpen, Cpu, Database,
} from 'lucide-react';
import { Spinner } from '../components/Loader';
import './AdminDashboard.css';

const CATEGORY_META = {
  'LLM Providers': { Icon: Cpu, tone: 'rust' },
  'Literature Sources': { Icon: BookOpen, tone: 'ink' },
  'Document Processing': { Icon: Server, tone: 'forest' },
  Infrastructure: { Icon: Database, tone: 'forest' },
};

function AdminMark({ spinning }) {
  return (
    <div className={`admin-mark${spinning ? ' is-spinning' : ''}`} aria-hidden="true">
      <span className="admin-mark-ring" />
      <span className="admin-mark-ring admin-mark-ring-delay" />
      <Shield className="admin-mark-icon" size={26} strokeWidth={1.75} />
    </div>
  );
}

function StatIcon({ tone, children }) {
  return (
    <div className={`admin-stat-orb admin-stat-orb--${tone}`} aria-hidden="true">
      <span className="admin-stat-orb-glow" />
      {children}
    </div>
  );
}

const AdminDashboard = () => {
  const { authFetch } = useAuth();
  const [users, setUsers] = useState([]);
  const [systemSources, setSystemSources] = useState([]);
  const [systemCategories, setSystemCategories] = useState([]);
  const [statusSummary, setStatusSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshingSources, setRefreshingSources] = useState(false);
  const [activeTab, setActiveTab] = useState('users');
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedUser, setSelectedUser] = useState(null);
  const [customQuotaInput, setCustomQuotaInput] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [deleteConfirmUser, setDeleteConfirmUser] = useState(null);
  const [fetchError, setFetchError] = useState('');
  const [lastCheckedAt, setLastCheckedAt] = useState(null);

  const fetchUsers = useCallback(async () => {
    try {
      const usersRes = await authFetch('/api/admin/users');
      if (usersRes.ok) {
        const uData = await usersRes.json();
        setUsers(uData.users || []);
      } else {
        const err = await usersRes.json().catch(() => ({ detail: 'Failed to fetch admin users' }));
        setFetchError(err.detail || 'Failed to connect to backend server');
      }
    } catch (error) {
      console.error('Failed to fetch admin users:', error);
      setFetchError('Backend server is unreachable (Make sure backend server on port 8000 is running)');
    }
  }, [authFetch]);

  const fetchStatus = useCallback(async (force = false) => {
    try {
      const qs = force ? '?force=true' : '';
      const statusRes = await authFetch(`/api/admin/system-status${qs}`);
      if (statusRes.ok) {
        const sData = await statusRes.json();
        setSystemSources(sData.sources || []);
        setSystemCategories(sData.categories || []);
        setStatusSummary(sData.summary || null);
        setLastCheckedAt(new Date());
      }
    } catch (error) {
      console.error('Failed to fetch system status:', error);
    }
  }, [authFetch]);

  const fetchAdminData = useCallback(async () => {
    try {
      setFetchError('');
      await Promise.all([fetchUsers(), fetchStatus(true)]);
    } finally {
      setLoading(false);
      setRefreshingSources(false);
    }
  }, [fetchUsers, fetchStatus]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (cancelled) return;
      await fetchAdminData();
    })();
    return () => { cancelled = true; };
  }, [fetchAdminData]);

  // Light user refresh — skip when tab hidden
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === 'visible') fetchUsers();
    };
    const intervalId = setInterval(tick, 60000);
    return () => clearInterval(intervalId);
  }, [fetchUsers]);

  // Expensive API probes only while Status tab is open + tab visible
  useEffect(() => {
    if (activeTab !== 'status') return undefined;
    const tick = () => {
      if (document.visibilityState === 'visible') fetchStatus();
    };
    const intervalId = setInterval(tick, 120000);
    const onVis = () => {
      if (document.visibilityState === 'visible') fetchStatus();
    };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      clearInterval(intervalId);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [activeTab, fetchStatus]);

  const handleRoleToggle = async (targetUser) => {
    const newRole = targetUser.role === 'admin' ? 'user' : 'admin';
    try {
      const res = await authFetch(`/api/admin/users/${targetUser.user_id}/role`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: newRole }),
      });
      if (res.ok) fetchAdminData();
    } catch (err) {
      console.error('Failed to update user role', err);
    }
  };

  const handleStatusToggle = async (targetUser) => {
    const newStatus = targetUser.status === 'suspended' ? 'active' : 'suspended';
    try {
      const res = await authFetch(`/api/admin/users/${targetUser.user_id}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      if (res.ok) fetchAdminData();
    } catch (err) {
      console.error('Failed to update user status', err);
    }
  };

  const handleSaveQuota = async (resetToday = false) => {
    if (!selectedUser) return;
    setActionLoading(true);
    try {
      const res = await authFetch(`/api/admin/users/${selectedUser.user_id}/quota`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          custom_quota: customQuotaInput,
          reset_today: resetToday,
        }),
      });
      if (res.ok) {
        setSelectedUser(null);
        fetchAdminData();
      }
    } catch (err) {
      console.error('Failed to update user quota', err);
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteUser = async () => {
    if (!deleteConfirmUser) return;
    setActionLoading(true);
    try {
      const res = await authFetch(`/api/admin/users/${deleteConfirmUser.user_id}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        setDeleteConfirmUser(null);
        fetchAdminData();
      }
    } catch (err) {
      console.error('Failed to delete user', err);
    } finally {
      setActionLoading(false);
    }
  };

  const handleRefresh = () => {
    setRefreshingSources(true);
    fetchAdminData();
  };

  if (loading) {
    return (
      <div className="admin-page admin-page--loading">
        <AdminMark spinning />
        <p>Opening the control desk…</p>
      </div>
    );
  }

  const filteredUsers = users.filter(
    (u) =>
      u.name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      u.email?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const totalTokensBurned = users.reduce((acc, u) => acc + (u.tokens_total || 0), 0);
  const activeTodayCount = users.filter((u) => u.tokens_today > 0).length;
  const operationalSourcesCount =
    statusSummary?.operational ?? systemSources.filter((s) => s.status === 'operational').length;
  const totalSourcesCount = statusSummary?.total ?? systemSources.length;
  const healthPct = totalSourcesCount
    ? Math.round((operationalSourcesCount / totalSourcesCount) * 100)
    : 0;

  const renderSourceCard = (source, idx) => {
    let statusBadgeClass = 'operational';
    let statusLabel = String(source.status || '').replace(/_/g, ' ');

    if (source.status === 'rate_limited' || source.status === 'degraded') {
      statusBadgeClass = 'warning';
    } else if (source.status === 'offline' || source.status === 'no_key') {
      statusBadgeClass = 'offline';
    }

    return (
      <article
        key={`${source.name}-${idx}`}
        className={`source-card-item source-card-item--${statusBadgeClass}`}
        style={{ animationDelay: `${Math.min(idx, 12) * 40}ms` }}
      >
        <div className="source-card-top">
          <div className="source-card-name">{source.name}</div>
          <div className={`source-status-badge ${statusBadgeClass}`}>
            <span className="source-status-pulse" aria-hidden="true" />
            {statusLabel}
          </div>
        </div>

        <div className="source-card-meta">
          <span>{source.details}</span>
          {source.latency_ms != null && (
            <span className="source-card-latency">{source.latency_ms} ms</span>
          )}
          {source.requires_key && (
            <span className="source-card-key-row">
              Env · {source.requires_key}
            </span>
          )}
        </div>
      </article>
    );
  };

  return (
    <div className="admin-page">
      <header className="admin-masthead">
        <div className="admin-masthead-copy">
          <AdminMark spinning={refreshingSources} />
          <div>
            <p className="admin-kicker">Control desk</p>
            <h1 className="admin-title">Admin Center</h1>
            <p className="admin-subtitle">
              Users, quotas, and live status across every integrated research API.
            </p>
            <div className="admin-inline-metrics">
              <span>
                <span className="admin-live-dot" /> Live desk
              </span>
              <span className="admin-metric-dot" />
              <span>{users.length} accounts</span>
              <span className="admin-metric-dot" />
              <span>
                {operationalSourcesCount}/{totalSourcesCount || '—'} APIs healthy
              </span>
              {lastCheckedAt && (
                <>
                  <span className="admin-metric-dot" />
                  <span>
                    Checked {lastCheckedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                </>
              )}
            </div>
          </div>
        </div>

        <button
          type="button"
          className={`admin-refresh-btn${refreshingSources ? ' is-busy' : ''}`}
          onClick={handleRefresh}
          disabled={refreshingSources}
        >
          <RefreshCw size={15} className={refreshingSources ? 'admin-spin' : ''} />
          {refreshingSources ? 'Probing…' : 'Refresh status'}
        </button>
      </header>

      {fetchError && (
        <div className="admin-alert" role="alert">
          <AlertTriangle size={18} />
          <div>
            <strong>Backend data error</strong>
            <span>{fetchError}</span>
          </div>
        </div>
      )}

      <div className="admin-stats-grid">
        <div className="admin-stat-card" style={{ animationDelay: '40ms' }}>
          <StatIcon tone="ink">
            <Users size={20} />
          </StatIcon>
          <div>
            <div className="admin-stat-label">Registered users</div>
            <div className="admin-stat-value">{users.length}</div>
            <div className="admin-stat-sub">{activeTodayCount} active today</div>
          </div>
        </div>

        <div className="admin-stat-card" style={{ animationDelay: '100ms' }}>
          <StatIcon tone="rust">
            <Activity size={20} />
          </StatIcon>
          <div>
            <div className="admin-stat-label">Tokens burned</div>
            <div className="admin-stat-value">{totalTokensBurned.toLocaleString()}</div>
            <div className="admin-stat-sub">Lifetime across all queries</div>
          </div>
        </div>

        <div className="admin-stat-card admin-stat-card--health" style={{ animationDelay: '160ms' }}>
          <div
            className="admin-health-ring"
            style={{ '--health': `${healthPct}%` }}
            aria-hidden="true"
          >
            <Zap size={18} />
          </div>
          <div>
            <div className="admin-stat-label">API health</div>
            <div className="admin-stat-value">
              {operationalSourcesCount}
              <span className="admin-stat-of">/{totalSourcesCount || '—'}</span>
            </div>
            <div className="admin-stat-sub">
              {statusSummary
                ? `${statusSummary.degraded || 0} degraded · ${statusSummary.offline || 0} offline`
                : 'Live probe of integrated services'}
            </div>
          </div>
        </div>
      </div>

      <div className="admin-tabs" role="tablist" aria-label="Admin sections">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'users'}
          className={`admin-tab${activeTab === 'users' ? ' is-active' : ''}`}
          onClick={() => setActiveTab('users')}
        >
          <Users size={15} /> Users
          <span className="admin-tab-count">{users.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'status'}
          className={`admin-tab${activeTab === 'status' ? ' is-active' : ''}`}
          onClick={() => setActiveTab('status')}
        >
          <Server size={15} /> API status
          <span className="admin-tab-count">{totalSourcesCount || 0}</span>
        </button>
        <div
          className={`admin-tab-indicator${activeTab === 'status' ? ' is-status' : ''}`}
          aria-hidden="true"
        />
      </div>

      {activeTab === 'users' && (
        <section className="admin-panel admin-panel--users">
          <div className="admin-table-header">
            <div className="admin-table-title">
              <UserCheck size={18} /> Registered directory
            </div>
            <div className="admin-search-wrapper">
              <Search size={15} className="admin-search-icon" />
              <input
                placeholder="Search by name or email…"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="admin-search-input"
              />
            </div>
          </div>

          <div className="admin-table-container">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Today</th>
                  <th>Lifetime</th>
                  <th>Msgs left</th>
                  <th>Quota</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredUsers.map((u, i) => {
                  const pct = Math.min(100, (u.tokens_today / u.quota) * 100);
                  const isSuspended = u.status === 'suspended';

                  return (
                    <tr
                      key={u.user_id}
                      className={isSuspended ? 'row-suspended' : ''}
                      style={{ animationDelay: `${Math.min(i, 10) * 30}ms` }}
                    >
                      <td>
                        <div className="admin-user-cell">
                          <span className={`admin-user-avatar${u.role === 'admin' ? ' is-admin' : ''}`}>
                            {(u.name || '?').slice(0, 1).toUpperCase()}
                          </span>
                          <div>
                            <div className="admin-user-name">{u.name}</div>
                            <div className="admin-user-email">{u.email}</div>
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className={`badge-role ${u.role}`}>
                          {u.role === 'admin' ? <ShieldCheck size={12} /> : null} {u.role}
                        </span>
                      </td>
                      <td>
                        <span className={`badge-status ${u.status}`}>
                          {isSuspended ? <UserX size={12} /> : <UserCheck size={12} />} {u.status}
                        </span>
                      </td>
                      <td>
                        <span className="admin-mono">{u.tokens_today.toLocaleString()}</span>
                      </td>
                      <td>
                        <span className="admin-mono muted">{u.tokens_total.toLocaleString()}</span>
                      </td>
                      <td>
                        <span className="badge-messages">{u.messages_left}</span>
                      </td>
                      <td>
                        <div className="admin-quota-cell">
                          <span className={u.custom_quota ? 'is-custom' : ''}>
                            {u.quota.toLocaleString()}
                            {u.custom_quota ? ' · custom' : ''}
                          </span>
                          <div className="admin-quota-bar" aria-hidden="true">
                            <div
                              className={`admin-quota-fill${pct > 80 ? ' is-hot' : ''}`}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="admin-row-actions">
                          <button
                            type="button"
                            className="admin-action-btn"
                            title={u.role === 'admin' ? 'Demote to User' : 'Promote to Admin'}
                            onClick={() => handleRoleToggle(u)}
                          >
                            Role
                          </button>
                          <button
                            type="button"
                            className={`admin-action-btn${isSuspended ? ' is-success' : ''}`}
                            title={isSuspended ? 'Reactivate User' : 'Suspend User'}
                            onClick={() => handleStatusToggle(u)}
                          >
                            {isSuspended ? 'Activate' : 'Suspend'}
                          </button>
                          <button
                            type="button"
                            className="admin-action-btn"
                            title="Edit Quota or Reset Usage"
                            onClick={() => {
                              setSelectedUser(u);
                              setCustomQuotaInput(u.custom_quota || '');
                            }}
                          >
                            <Sliders size={13} /> Quota
                          </button>
                          <button
                            type="button"
                            className="admin-action-btn is-danger"
                            title="Delete User"
                            onClick={() => setDeleteConfirmUser(u)}
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
                {filteredUsers.length === 0 && (
                  <tr>
                    <td colSpan={8} className="admin-empty-row">
                      No registered users match your search.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {activeTab === 'status' && (
        <section className="admin-panel admin-status-panel">
          <div className="admin-status-header">
            <div>
              <h3>
                <Server size={18} /> Connected services
              </h3>
              <p className="admin-status-subtitle">
                Live probes across LLM providers, literature sources, document processing, and infrastructure
              </p>
            </div>
            <div className="admin-status-summary-pills">
              <span className="status-pill ok">
                {statusSummary?.operational ?? operationalSourcesCount} operational
              </span>
              <span className="status-pill warn">{statusSummary?.degraded ?? 0} degraded</span>
              <span className="status-pill bad">{statusSummary?.offline ?? 0} offline / no key</span>
            </div>
          </div>

          {(systemCategories.length > 0
            ? systemCategories
            : [{ name: 'All Services', sources: systemSources }]
          ).map((category) => {
            const meta = CATEGORY_META[category.name] || { Icon: Server, tone: 'ink' };
            const CatIcon = meta.Icon;
            return (
              <section key={category.name} className="admin-status-category">
                <div className="admin-status-category-head">
                  <h4>
                    <span className={`admin-cat-mark admin-cat-mark--${meta.tone}`}>
                      <CatIcon size={14} />
                    </span>
                    {category.name}
                  </h4>
                  <span>
                    {category.operational ??
                      category.sources.filter((s) => s.status === 'operational').length}
                    /{category.total ?? category.sources.length} healthy
                  </span>
                </div>
                <div className="sources-grid">
                  {category.sources.map((source, idx) => renderSourceCard(source, idx))}
                </div>
              </section>
            );
          })}

          {systemSources.length === 0 && (
            <div className="admin-status-empty">
              {refreshingSources ? <Spinner size={18} /> : null}
              {refreshingSources ? ' Probing API services…' : 'No status data yet. Click Refresh status.'}
            </div>
          )}
        </section>
      )}

      {selectedUser && (
        <div className="admin-modal-overlay" onClick={() => setSelectedUser(null)}>
          <div className="admin-modal" onClick={(e) => e.stopPropagation()}>
            <div className="admin-modal-header">
              <h3>Manage user quota</h3>
              <button type="button" onClick={() => setSelectedUser(null)} className="admin-modal-close">
                <X size={16} />
              </button>
            </div>
            <div className="admin-modal-body">
              <p className="admin-modal-lead">
                Set a custom daily token limit or reset today&apos;s usage for{' '}
                <strong>{selectedUser.name}</strong> ({selectedUser.email}).
              </p>

              <label className="admin-field-label">
                Custom daily token quota
                <span>Leave empty for default 250,000</span>
              </label>
              <input
                type="number"
                placeholder="e.g. 500000"
                value={customQuotaInput}
                onChange={(e) => setCustomQuotaInput(e.target.value)}
                className="admin-field-input"
              />

              <div className="admin-modal-usage">
                <div>
                  <div className="admin-field-label">Today&apos;s tokens used</div>
                  <div className="admin-modal-usage-value">
                    {selectedUser.tokens_today.toLocaleString()} tokens
                  </div>
                </div>
                <button
                  type="button"
                  className="admin-action-btn"
                  onClick={() => handleSaveQuota(true)}
                  disabled={actionLoading}
                >
                  <RotateCcw size={13} /> Reset usage today
                </button>
              </div>

              <div className="admin-modal-actions">
                <button type="button" className="admin-action-btn" onClick={() => setSelectedUser(null)}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="admin-action-btn is-primary"
                  onClick={() => handleSaveQuota(false)}
                  disabled={actionLoading}
                >
                  {actionLoading ? <Spinner size={14} /> : 'Save quota'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {deleteConfirmUser && (
        <div className="admin-modal-overlay" onClick={() => setDeleteConfirmUser(null)}>
          <div className="admin-modal" onClick={(e) => e.stopPropagation()}>
            <div className="admin-modal-header">
              <h3 className="is-danger">Delete user account?</h3>
              <button
                type="button"
                onClick={() => setDeleteConfirmUser(null)}
                className="admin-modal-close"
              >
                <X size={16} />
              </button>
            </div>
            <div className="admin-modal-body">
              <p className="admin-modal-lead">
                Delete <strong>{deleteConfirmUser.name}</strong> ({deleteConfirmUser.email})? This
                permanently removes their account, manuscripts, and PDF chat history.
              </p>
              <div className="admin-modal-actions">
                <button
                  type="button"
                  className="admin-action-btn"
                  onClick={() => setDeleteConfirmUser(null)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="admin-action-btn is-danger solid"
                  onClick={handleDeleteUser}
                  disabled={actionLoading}
                >
                  {actionLoading ? <Spinner size={14} /> : 'Yes, delete account'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default AdminDashboard;
