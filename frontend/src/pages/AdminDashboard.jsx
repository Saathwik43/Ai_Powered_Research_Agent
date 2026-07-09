import React, { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { Shield, Users, Activity, Battery } from 'lucide-react';
import { Spinner } from '../components/Loader';

const AdminDashboard = () => {
  const { authFetch, user } = useAuth();
  const [usages, setUsages] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchAdminUsage = async () => {
      try {
        const res = await authFetch('http://localhost:8000/api/admin/usage');
        if (res.ok) {
          const data = await res.json();
          setUsages(data.data || []);
        }
      } catch (error) {
        console.error('Failed to fetch admin usage:', error);
      } finally {
        setLoading(false);
      }
    };
    fetchAdminUsage();
  }, [authFetch]);

  if (loading) {
    return (
      <div className="page-container" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
        <Spinner />
      </div>
    );
  }

  const totalTokens = usages.reduce((acc, curr) => acc + curr.used, 0);

  return (
    <div className="page-container fade-in">
      <header className="page-header">
        <div>
          <h1 className="page-title">
            <Shield size={28} style={{ color: 'var(--primary)' }} />
            Admin Dashboard
          </h1>
          <p className="page-subtitle">Monitor API usage and manage quotas across all users</p>
        </div>
      </header>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '1.5rem', marginBottom: '2rem' }}>
        <div className="card" style={{ display: 'flex', alignItems: 'center', gap: '1rem', padding: '1.5rem' }}>
          <div style={{ width: 48, height: 48, borderRadius: '12px', background: 'rgba(var(--primary-rgb), 0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--primary)' }}>
            <Users size={24} />
          </div>
          <div>
            <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Active Users Today</div>
            <div style={{ fontSize: '1.5rem', fontWeight: 600, color: 'var(--text)' }}>{usages.length}</div>
          </div>
        </div>
        
        <div className="card" style={{ display: 'flex', alignItems: 'center', gap: '1rem', padding: '1.5rem' }}>
          <div style={{ width: 48, height: 48, borderRadius: '12px', background: 'rgba(var(--primary-rgb), 0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--primary)' }}>
            <Activity size={24} />
          </div>
          <div>
            <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Total Tokens Burned</div>
            <div style={{ fontSize: '1.5rem', fontWeight: 600, color: 'var(--text)' }}>{totalTokens.toLocaleString()}</div>
          </div>
        </div>
      </div>

      <div className="card">
        <h3 style={{ margin: '0 0 1rem 0', display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '1.1rem' }}>
          <Battery size={20} className="text-primary" />
          User Quotas
        </h3>
        
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                <th style={{ padding: '1rem', color: 'var(--text-muted)', fontWeight: 500 }}>User Email</th>
                <th style={{ padding: '1rem', color: 'var(--text-muted)', fontWeight: 500 }}>Tokens Burned</th>
                <th style={{ padding: '1rem', color: 'var(--text-muted)', fontWeight: 500 }}>Messages Left</th>
                <th style={{ padding: '1rem', color: 'var(--text-muted)', fontWeight: 500 }}>Usage Bar</th>
              </tr>
            </thead>
            <tbody>
              {usages.map((u) => {
                const pct = Math.min(100, (u.used / u.quota) * 100);
                const isWarning = pct > 80;
                
                return (
                  <tr key={u.user_id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '1rem', fontWeight: 500 }}>{u.email}</td>
                    <td style={{ padding: '1rem' }}>{u.used.toLocaleString()}</td>
                    <td style={{ padding: '1rem' }}>
                      <span style={{ 
                        background: isWarning ? 'rgba(239, 68, 68, 0.1)' : 'rgba(var(--primary-rgb), 0.1)', 
                        color: isWarning ? '#ef4444' : 'var(--primary)',
                        padding: '0.25rem 0.75rem',
                        borderRadius: '2rem',
                        fontSize: '0.85rem',
                        fontWeight: 600
                      }}>
                        {u.messages_left}
                      </span>
                    </td>
                    <td style={{ padding: '1rem', minWidth: '200px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        <span>{pct.toFixed(1)}% used</span>
                        <span>{u.quota.toLocaleString()} max</span>
                      </div>
                      <div style={{ height: '6px', background: 'var(--bg-hover)', borderRadius: '4px', overflow: 'hidden' }}>
                        <div style={{ 
                          height: '100%', 
                          width: `${pct}%`, 
                          background: isWarning ? '#ef4444' : 'var(--primary)', 
                          borderRadius: '4px' 
                        }}></div>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {usages.length === 0 && (
                <tr>
                  <td colSpan={4} style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
                    No usage recorded today.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default AdminDashboard;
