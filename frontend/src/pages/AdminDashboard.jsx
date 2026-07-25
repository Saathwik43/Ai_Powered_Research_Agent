import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { Shield, Users, Activity, Battery, Search, RefreshCw, Sliders, UserCheck, UserX, ShieldCheck, Trash2, RotateCcw, CheckCircle2, AlertTriangle, XCircle, Server, Zap, X } from 'lucide-react';
import { Spinner } from '../components/Loader';
import './AdminDashboard.css';

const AdminDashboard = () => {
  const { authFetch } = useAuth();
  const [users, setUsers] = useState([]);
  const [systemSources, setSystemSources] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshingSources, setRefreshingSources] = useState(false);
  const [activeTab, setActiveTab] = useState('users');
  const [searchTerm, setSearchTerm] = useState('');
  
  // Quota Modal state
  const [selectedUser, setSelectedUser] = useState(null);
  const [customQuotaInput, setCustomQuotaInput] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [deleteConfirmUser, setDeleteConfirmUser] = useState(null);

  const [fetchError, setFetchError] = useState('');

  const fetchAdminData = useCallback(async () => {
    try {
      setFetchError('');
      const [usersRes, statusRes] = await Promise.all([
        authFetch('/api/admin/users'),
        authFetch('/api/admin/system-status')
      ]);

      if (usersRes.ok) {
        const uData = await usersRes.json();
        setUsers(uData.users || []);
      } else {
        const err = await usersRes.json().catch(() => ({ detail: 'Failed to fetch admin users' }));
        setFetchError(err.detail || 'Failed to connect to backend server');
      }

      if (statusRes.ok) {
        const sData = await statusRes.json();
        setSystemSources(sData.sources || []);
      }
    } catch (error) {
      console.error('Failed to fetch admin data:', error);
      setFetchError('Backend server is unreachable (Make sure backend server on port 8000 is running)');
    } finally {
      setLoading(false);
      setRefreshingSources(false);
    }
  }, [authFetch]);

  useEffect(() => {
    fetchAdminData();
    const intervalId = setInterval(fetchAdminData, 10000);
    return () => clearInterval(intervalId);
  }, [fetchAdminData]);

  const handleRoleToggle = async (targetUser) => {
    const newRole = targetUser.role === 'admin' ? 'user' : 'admin';
    try {
      const res = await authFetch(`/api/admin/users/${targetUser.user_id}/role`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: newRole })
      });
      if (res.ok) {
        fetchAdminData();
      }
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
        body: JSON.stringify({ status: newStatus })
      });
      if (res.ok) {
        fetchAdminData();
      }
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
          reset_today: resetToday
        })
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
        method: 'DELETE'
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

  if (loading) {
    return (
      <div className="page-container" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
        <Spinner />
      </div>
    );
  }

  const filteredUsers = users.filter(u => 
    u.name?.toLowerCase().includes(searchTerm.toLowerCase()) || 
    u.email?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const totalTokensBurned = users.reduce((acc, u) => acc + (u.tokens_total || 0), 0);
  const activeTodayCount = users.filter(u => u.tokens_today > 0).length;
  const operationalSourcesCount = systemSources.filter(s => s.status === 'operational').length;

  return (
    <div className="page-container fade-in" style={{ paddingBottom: '3rem' }}>
      <header className="page-header">
        <div>
          <h1 className="page-title">
            <Shield size={28} style={{ color: 'var(--primary)' }} />
            Admin Control Center
          </h1>
          <p className="page-subtitle">Full control over registered users, custom token quotas, and API source status</p>
        </div>
        <button 
          className="btn btn-secondary"
          onClick={() => { setRefreshingSources(true); fetchAdminData(); }}
          disabled={refreshingSources}
        >
          <RefreshCw size={14} className={refreshingSources ? 'animate-spin' : ''} /> Refresh Status
        </button>
      </header>

      {fetchError && (
        <div style={{ padding: '0.85rem 1.25rem', background: 'rgba(229, 28, 35, 0.08)', border: '1px solid rgba(229, 28, 35, 0.25)', borderRadius: 'var(--radius-md)', color: 'var(--danger)', fontSize: 'var(--fs-sm)', marginBottom: '1.5rem', display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <AlertTriangle size={18} />
          <div>
            <strong>Backend Data Error:</strong> {fetchError}
          </div>
        </div>
      )}

      {/* Overview Metric Cards */}
      <div className="admin-stats-grid">
        <div className="card admin-stat-card">
          <div className="admin-stat-icon-wrapper blue">
            <Users size={22} />
          </div>
          <div>
            <div className="admin-stat-label">Total Registered Users</div>
            <div className="admin-stat-value">{users.length}</div>
            <div className="admin-stat-sub">{activeTodayCount} active today</div>
          </div>
        </div>

        <div className="card admin-stat-card">
          <div className="admin-stat-icon-wrapper orange">
            <Activity size={22} />
          </div>
          <div>
            <div className="admin-stat-label">Total Tokens Burned</div>
            <div className="admin-stat-value">{totalTokensBurned.toLocaleString()}</div>
            <div className="admin-stat-sub">Across all user queries</div>
          </div>
        </div>

        <div className="card admin-stat-card">
          <div className="admin-stat-icon-wrapper green">
            <Zap size={22} />
          </div>
          <div>
            <div className="admin-stat-label">API Sources Health</div>
            <div className="admin-stat-value">{operationalSourcesCount} / {systemSources.length || 6}</div>
            <div className="admin-stat-sub">Services Operational</div>
          </div>
        </div>
      </div>

      {/* Admin Navigation Tabs */}
      <div className="admin-tabs">
        <button 
          className={`admin-tab ${activeTab === 'users' ? 'active' : ''}`}
          onClick={() => setActiveTab('users')}
        >
          <Users size={16} /> User Management ({users.length})
        </button>
        <button 
          className={`admin-tab ${activeTab === 'status' ? 'active' : ''}`}
          onClick={() => setActiveTab('status')}
        >
          <Server size={16} /> API Sources & Health Status
        </button>
      </div>

      {/* TAB 1: Registered Users Control */}
      {activeTab === 'users' && (
        <div className="card" style={{ padding: '0', overflow: 'hidden' }}>
          <div className="admin-table-header">
            <div className="admin-table-title">
              <UserCheck size={18} style={{ color: 'var(--primary)' }} /> Registered Users Directory
            </div>
            <div className="admin-search-wrapper">
              <Search size={15} className="admin-search-icon" />
              <input 
                placeholder="Search user by name or email..."
                value={searchTerm}
                onChange={e => setSearchTerm(e.target.value)}
                className="admin-search-input"
              />
            </div>
          </div>

          <div className="admin-table-container">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>User Details</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Today's Tokens</th>
                  <th>Lifetime Tokens</th>
                  <th>Messages Left</th>
                  <th>Daily Quota</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredUsers.map((u) => {
                  const pct = Math.min(100, (u.tokens_today / u.quota) * 100);
                  const isSuspended = u.status === 'suspended';

                  return (
                    <tr key={u.user_id} className={isSuspended ? 'row-suspended' : ''}>
                      <td>
                        <div style={{ fontWeight: 700, color: 'var(--text)' }}>{u.name}</div>
                        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-subtle)' }}>{u.email}</div>
                      </td>
                      <td>
                        <span className={`badge-role ${u.role}`}>
                          {u.role === 'admin' ? <ShieldCheck size={12} /> : null} {u.role.toUpperCase()}
                        </span>
                      </td>
                      <td>
                        <span className={`badge-status ${u.status}`}>
                          {isSuspended ? <UserX size={12} /> : <UserCheck size={12} />} {u.status.toUpperCase()}
                        </span>
                      </td>
                      <td>
                        <span style={{ fontWeight: 650 }}>{u.tokens_today.toLocaleString()}</span>
                      </td>
                      <td>
                        <span style={{ color: 'var(--text-muted)' }}>{u.tokens_total.toLocaleString()}</span>
                      </td>
                      <td>
                        <span className="badge-messages">
                          {u.messages_left}
                        </span>
                      </td>
                      <td>
                        <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 600 }}>
                          {u.custom_quota ? (
                            <span style={{ color: 'var(--primary)' }}>{u.quota.toLocaleString()} (Custom)</span>
                          ) : (
                            <span style={{ color: 'var(--text-muted)' }}>{u.quota.toLocaleString()} (Default)</span>
                          )}
                        </div>
                        <div style={{ height: '4px', background: 'var(--border)', borderRadius: '99px', overflow: 'hidden', marginTop: '4px', width: '100px' }}>
                          <div style={{ height: '100%', width: `${pct}%`, background: pct > 80 ? 'var(--danger)' : 'var(--primary)' }} />
                        </div>
                      </td>
                      <td>
                        <div style={{ display: 'flex', gap: '0.4rem' }}>
                          <button 
                            className="btn btn-secondary btn-sm"
                            title={u.role === 'admin' ? "Demote to User" : "Promote to Admin"}
                            onClick={() => handleRoleToggle(u)}
                          >
                            Role
                          </button>
                          <button 
                            className={`btn btn-sm ${isSuspended ? 'btn-success' : 'btn-secondary'}`}
                            title={isSuspended ? "Reactivate User" : "Suspend User"}
                            onClick={() => handleStatusToggle(u)}
                          >
                            {isSuspended ? 'Activate' : 'Suspend'}
                          </button>
                          <button 
                            className="btn btn-secondary btn-sm"
                            title="Edit Quota or Reset Usage"
                            onClick={() => { setSelectedUser(u); setCustomQuotaInput(u.custom_quota || ''); }}
                          >
                            <Sliders size={13} /> Quota
                          </button>
                          <button 
                            className="btn btn-danger btn-sm btn-icon"
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
                    <td colSpan={8} style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
                      No registered users match your search filter.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* TAB 2: API Sources & System Status */}
      {activeTab === 'status' && (
        <div className="card" style={{ padding: '1.5rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem' }}>
            <h3 style={{ margin: 0, fontSize: 'var(--fs-md)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Server size={18} color="var(--primary)" /> Connected API Services & Engines Status
            </h3>
            <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-subtle)' }}>
              Real-time service health check
            </span>
          </div>

          <div className="sources-grid">
            {systemSources.map((source, idx) => {
              let statusBadgeClass = 'operational';
              let statusIcon = <CheckCircle2 size={16} />;
              
              if (source.status === 'rate_limited' || source.status === 'degraded') {
                statusBadgeClass = 'warning';
                statusIcon = <AlertTriangle size={16} />;
              } else if (source.status === 'offline' || source.status === 'no_key') {
                statusBadgeClass = 'offline';
                statusIcon = <XCircle size={16} />;
              }

              return (
                <div key={idx} className="source-card-item">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.75rem' }}>
                    <div>
                      <div style={{ fontWeight: 750, fontSize: 'var(--fs-base)', color: 'var(--text)' }}>{source.name}</div>
                      <span className="source-type-pill">{source.type}</span>
                    </div>
                    <div className={`source-status-badge ${statusBadgeClass}`}>
                      {statusIcon} {source.status.replace('_', ' ').toUpperCase()}
                    </div>
                  </div>

                  <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', gap: '0.3rem', borderTop: '1px solid var(--border)', paddingTop: '0.6rem' }}>
                    <div><strong>Details:</strong> {source.details}</div>
                    {source.latency_ms && (
                      <div><strong>Ping Latency:</strong> <span style={{ color: 'var(--primary)', fontWeight: 650 }}>{source.latency_ms} ms</span></div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Quota & Reset Modal */}
      {selectedUser && (
        <div className="admin-modal-overlay" onClick={() => setSelectedUser(null)}>
          <div className="admin-modal" onClick={e => e.stopPropagation()}>
            <div className="admin-modal-header">
              <h3>Manage User Quota</h3>
              <button onClick={() => setSelectedUser(null)} className="admin-modal-close"><X size={16} /></button>
            </div>
            <div className="admin-modal-body">
              <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', margin: '0 0 1rem 0' }}>
                Set a custom daily token limit or reset today's usage for <strong>{selectedUser.name}</strong> ({selectedUser.email}).
              </p>

              <div style={{ marginBottom: '1.25rem' }}>
                <label style={{ display: 'block', fontSize: 'var(--fs-xs)', fontWeight: 700, marginBottom: '0.4rem', color: 'var(--text-subtle)' }}>
                  CUSTOM DAILY TOKEN QUOTA (Leave empty for Default 250,000)
                </label>
                <input 
                  type="number"
                  placeholder="e.g. 500000"
                  value={customQuotaInput}
                  onChange={e => setCustomQuotaInput(e.target.value)}
                  style={{ width: '100%', padding: '0.6rem 0.8rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', background: 'var(--bg-card)' }}
                />
              </div>

              <div style={{ padding: '0.85rem', background: 'var(--bg-elevated)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', marginBottom: '1.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontWeight: 650, fontSize: 'var(--fs-xs)' }}>Today's Tokens Used</div>
                  <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--primary)', fontWeight: 750 }}>{selectedUser.tokens_today.toLocaleString()} tokens</div>
                </div>
                <button 
                  className="btn btn-secondary btn-sm"
                  onClick={() => handleSaveQuota(true)}
                  disabled={actionLoading}
                >
                  <RotateCcw size={13} /> Reset Usage Today
                </button>
              </div>

              <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
                <button className="btn btn-ghost" onClick={() => setSelectedUser(null)}>Cancel</button>
                <button className="btn btn-primary" onClick={() => handleSaveQuota(false)} disabled={actionLoading}>
                  {actionLoading ? <Spinner size={14} /> : 'Save Quota Changes'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Delete User Modal */}
      {deleteConfirmUser && (
        <div className="admin-modal-overlay" onClick={() => setDeleteConfirmUser(null)}>
          <div className="admin-modal" onClick={e => e.stopPropagation()}>
            <div className="admin-modal-header">
              <h3 style={{ color: 'var(--danger)' }}>Delete User Account?</h3>
              <button onClick={() => setDeleteConfirmUser(null)} className="admin-modal-close"><X size={16} /></button>
            </div>
            <div className="admin-modal-body">
              <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text)', margin: '0 0 1.25rem 0' }}>
                Are you sure you want to delete <strong>{deleteConfirmUser.name}</strong> ({deleteConfirmUser.email})? This action will permanently purge their user account, manuscripts, and PDF chat history.
              </p>
              <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
                <button className="btn btn-ghost" onClick={() => setDeleteConfirmUser(null)}>Cancel</button>
                <button className="btn btn-danger" onClick={handleDeleteUser} disabled={actionLoading}>
                  {actionLoading ? <Spinner size={14} /> : 'Yes, Delete Account'}
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
