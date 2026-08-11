import React, { useMemo, useState } from 'react';
import {
  Search, Sliders, UserCheck, UserX, ShieldCheck, Trash2, RotateCcw, X,
} from 'lucide-react';
import { Spinner } from '../../components/Loader';

export default function UsersPanel({ users, authFetch, onChanged }) {
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedUser, setSelectedUser] = useState(null);
  const [customQuotaInput, setCustomQuotaInput] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [deleteConfirmUser, setDeleteConfirmUser] = useState(null);

  const filteredUsers = useMemo(() => {
    const q = searchTerm.toLowerCase();
    return users.filter((u) => {
      const matchesSearch =
        u.name?.toLowerCase().includes(q) || u.email?.toLowerCase().includes(q);
      if (!matchesSearch) return false;
      if (statusFilter === 'admin') return u.role === 'admin';
      if (statusFilter === 'suspended') return u.status === 'suspended';
      if (statusFilter === 'over-quota') return (u.tokens_today || 0) >= (u.quota || 0);
      return true;
    });
  }, [users, searchTerm, statusFilter]);

  const handleRoleToggle = async (targetUser) => {
    const newRole = targetUser.role === 'admin' ? 'user' : 'admin';
    try {
      const res = await authFetch(`/api/admin/users/${targetUser.user_id}/role`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: newRole }),
      });
      if (res.ok) onChanged();
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
      if (res.ok) onChanged();
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
        onChanged();
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
        onChanged();
      }
    } catch (err) {
      console.error('Failed to delete user', err);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <>
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

        <div className="admin-chip-row admin-chip-row--inset">
          {[
            { id: 'all', label: 'All' },
            { id: 'admin', label: 'Admin' },
            { id: 'suspended', label: 'Suspended' },
            { id: 'over-quota', label: 'Over quota' },
          ].map((chip) => (
            <button
              key={chip.id}
              type="button"
              className={`admin-chip${statusFilter === chip.id ? ' is-on' : ''}`}
              onClick={() => setStatusFilter(chip.id)}
            >
              {chip.label}
            </button>
          ))}
          <span className="admin-chip-count">{filteredUsers.length} shown</span>
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
                    No registered users match your filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

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
                Delete <strong>{deleteConfirmUser.name}</strong> ({deleteConfirmUser.email})?
                This permanently removes their account, manuscripts, PDF chats, and usage logs.
                It cannot be undone.
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
    </>
  );
}
