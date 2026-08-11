import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  Shield, Users, Activity, RefreshCw, AlertTriangle, Server, Radio, LayoutDashboard,
} from 'lucide-react';
import OverviewPanel from './admin/OverviewPanel';
import ApisPanel from './admin/ApisPanel';
import UsagePanel from './admin/UsagePanel';
import ActivityPanel from './admin/ActivityPanel';
import UsersPanel from './admin/UsersPanel';
import './AdminDashboard.css';

const TABS = [
  { id: 'overview', label: 'Overview', Icon: LayoutDashboard },
  { id: 'apis', label: 'APIs', Icon: Server },
  { id: 'usage', label: 'Usage', Icon: Activity },
  { id: 'activity', label: 'Activity', Icon: Radio },
  { id: 'users', label: 'Users', Icon: Users },
];

function AdminMark({ spinning }) {
  return (
    <div className={`admin-mark${spinning ? ' is-spinning' : ''}`} aria-hidden="true">
      <span className="admin-mark-ring" />
      <span className="admin-mark-ring admin-mark-ring-delay" />
      <Shield className="admin-mark-icon" size={26} strokeWidth={1.75} />
    </div>
  );
}

const AdminDashboard = () => {
  const { authFetch } = useAuth();
  const [users, setUsers] = useState([]);
  const [systemSources, setSystemSources] = useState([]);
  const [systemCategories, setSystemCategories] = useState([]);
  const [statusSummary, setStatusSummary] = useState(null);
  const [events, setEvents] = useState([]);
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshingSources, setRefreshingSources] = useState(false);
  const [activeTab, setActiveTab] = useState('overview');
  const [focusSource, setFocusSource] = useState(null);
  const [probingName, setProbingName] = useState(null);
  const [togglingName, setTogglingName] = useState(null);
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

  const fetchEvents = useCallback(async () => {
    try {
      const res = await authFetch('/api/admin/events?limit=80');
      if (res.ok) {
        const data = await res.json();
        setEvents(data.events || []);
      }
    } catch (error) {
      console.error('Failed to fetch admin events:', error);
    }
  }, [authFetch]);

  const fetchUsage = useCallback(async () => {
    try {
      const res = await authFetch('/api/admin/usage');
      if (res.ok) {
        setUsage(await res.json());
      }
    } catch (error) {
      console.error('Failed to fetch admin usage:', error);
    }
  }, [authFetch]);

  const fetchAdminData = useCallback(async ({ forceStatus = false } = {}) => {
    try {
      setFetchError('');
      await Promise.all([
        fetchUsers(),
        fetchStatus(forceStatus),
        fetchEvents(),
        fetchUsage(),
      ]);
    } finally {
      setLoading(false);
      setRefreshingSources(false);
    }
  }, [fetchUsers, fetchStatus, fetchEvents, fetchUsage]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (cancelled) return;
      await fetchAdminData({ forceStatus: false });
    })();
    return () => { cancelled = true; };
  }, [fetchAdminData]);

  useEffect(() => {
    if (!['overview', 'apis', 'activity'].includes(activeTab)) return undefined;
    const tick = () => {
      if (document.visibilityState === 'visible') {
        fetchStatus(false);
        fetchEvents();
      }
    };
    const intervalId = setInterval(tick, 8000);
    const onVis = () => {
      if (document.visibilityState === 'visible') tick();
    };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      clearInterval(intervalId);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [activeTab, fetchStatus, fetchEvents]);

  useEffect(() => {
    if (!['usage', 'users'].includes(activeTab)) return undefined;
    const tick = () => {
      if (document.visibilityState !== 'visible') return;
      fetchUsage();
      if (activeTab === 'users') fetchUsers();
    };
    const intervalId = setInterval(tick, 60000);
    return () => clearInterval(intervalId);
  }, [activeTab, fetchUsage, fetchUsers]);

  const patchSource = (next) => {
    setSystemSources((prev) => prev.map((s) => (s.name === next.name ? { ...s, ...next } : s)));
    setSystemCategories((prev) => prev.map((cat) => ({
      ...cat,
      sources: (cat.sources || []).map((s) => (s.name === next.name ? { ...s, ...next } : s)),
    })));
  };

  const handleProbe = async (name) => {
    setProbingName(name);
    try {
      const res = await authFetch('/api/admin/system-status/probe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.source) patchSource(data.source);
      }
    } catch (err) {
      console.error('Failed to probe source', err);
    } finally {
      setProbingName(null);
    }
  };

  const handleToggleSkip = async (source) => {
    setTogglingName(source.name);
    try {
      const res = await authFetch('/api/admin/sources/enabled', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: source.name, enabled: source.enabled === false }),
      });
      if (res.ok) {
        patchSource({ ...source, enabled: source.enabled === false });
      }
    } catch (err) {
      console.error('Failed to toggle source', err);
    } finally {
      setTogglingName(null);
    }
  };

  const handleRefresh = () => {
    setRefreshingSources(true);
    fetchAdminData({ forceStatus: true });
  };

  const handleJumpToApi = (name) => {
    setFocusSource(name);
    setActiveTab('apis');
  };

  if (loading) {
    return (
      <div className="admin-page admin-page--loading">
        <AdminMark spinning />
        <p>Opening the control desk…</p>
      </div>
    );
  }

  const operationalSourcesCount =
    statusSummary?.operational ?? systemSources.filter((s) => s.status === 'operational').length;
  const totalSourcesCount = statusSummary?.total ?? systemSources.length;
  const inFlight = statusSummary?.in_flight ?? 0;
  const tokensToday = usage?.today_total
    ?? users.reduce((acc, u) => acc + (u.tokens_today || 0), 0);
  const tabIndex = Math.max(0, TABS.findIndex((t) => t.id === activeTab));

  return (
    <div className="admin-page">
      <header className="admin-masthead">
        <div className="admin-masthead-copy">
          <AdminMark spinning={refreshingSources} />
          <div>
            <p className="admin-kicker">Control desk</p>
            <h1 className="admin-title">Admin Center</h1>
            <p className="admin-subtitle">
              Ops desk for APIs, usage, live traffic, and accounts.
            </p>
            <div className="admin-inline-metrics">
              <span>
                <span className="admin-live-dot" /> Live desk
              </span>
              <span className="admin-metric-dot" />
              <span>
                {operationalSourcesCount}/{totalSourcesCount || '—'} APIs healthy
              </span>
              <span className="admin-metric-dot" />
              <span>{Number(tokensToday || 0).toLocaleString()} tokens today</span>
              {inFlight > 0 && (
                <>
                  <span className="admin-metric-dot" />
                  <span className="admin-inflight-badge">{inFlight} in flight</span>
                </>
              )}
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
          {refreshingSources ? 'Probing…' : 'Refresh'}
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

      <div className="admin-tabs" role="tablist" aria-label="Admin sections">
        {TABS.map((tab) => {
          const Icon = tab.Icon;
          const count = tab.id === 'users'
            ? users.length
            : tab.id === 'apis'
              ? totalSourcesCount || 0
              : tab.id === 'activity'
                ? events.length
                : null;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={`admin-tab${activeTab === tab.id ? ' is-active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <Icon size={15} /> {tab.label}
              {count != null && <span className="admin-tab-count">{count}</span>}
            </button>
          );
        })}
        <div
          className="admin-tab-indicator"
          style={{ '--tab-i': tabIndex }}
          aria-hidden="true"
        />
      </div>

      {activeTab === 'overview' && (
        <OverviewPanel
          users={users}
          sources={systemSources}
          statusSummary={statusSummary}
          events={events}
          usage={usage}
          onJumpToApi={handleJumpToApi}
        />
      )}

      {activeTab === 'apis' && (
        <ApisPanel
          sources={systemSources}
          categories={systemCategories}
          statusSummary={statusSummary}
          focusSource={focusSource}
          probingName={probingName}
          togglingName={togglingName}
          onProbe={handleProbe}
          onToggleSkip={handleToggleSkip}
        />
      )}

      {activeTab === 'usage' && <UsagePanel usage={usage} />}

      {activeTab === 'activity' && (
        <ActivityPanel events={events} sources={systemSources} />
      )}

      {activeTab === 'users' && (
        <UsersPanel users={users} authFetch={authFetch} onChanged={fetchUsers} />
      )}
    </div>
  );
};

export default AdminDashboard;
