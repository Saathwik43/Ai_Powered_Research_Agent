import React, { useEffect, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { LayoutDashboard, BookOpen, PenTool, LayoutList, LogOut, X, ChevronLeft, ChevronRight, FileText, Shield } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import './Sidebar.css';

const Sidebar = ({ open, onClose, collapsed, onToggleCollapse }) => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const navItems = [
    { name: 'Dashboard', path: '/dashboard', icon: <LayoutDashboard size={18} /> },
    { name: 'Literature Survey', path: '/literature-survey', icon: <BookOpen size={18} /> },
    { name: 'PDF Analysis', path: '/pdf-analysis', icon: <FileText size={18} /> },
    { name: 'Manuscript Builder', path: '/manuscript-builder', icon: <PenTool size={18} /> },
    { name: 'Venue Recommendations', path: '/venue-recommendations', icon: <LayoutList size={18} /> },
  ];

  if (user?.role === 'admin') {
    navItems.push({ name: 'Admin Dashboard', path: '/admin', icon: <Shield size={18} /> });
  }

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  const initials = user?.name
    ? user.name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2)
    : 'U';

  const [usage, setUsage] = useState(null);
  const { authFetch } = useAuth();
  
  useEffect(() => {
    if (!user) return;
    const fetchUsage = async () => {
      try {
        const res = await authFetch('http://localhost:8000/api/user/usage');
        if (res.ok) {
          const data = await res.json();
          setUsage(data);
        }
      } catch (err) {
        console.error("Failed to fetch usage:", err);
      }
    };
    fetchUsage();
  }, [user, authFetch]);

  return (
    <>
      {/* Mobile overlay */}
      {open && <div className="mobile-overlay" onClick={onClose} />}

      <aside className={`sidebar ${open ? 'open' : ''} ${collapsed ? 'collapsed' : ''}`}>
        <div className="sidebar-header">
          <img src="/9672704.webp" alt="Logo" style={{ width: 34, height: 34, borderRadius: '6px', objectFit: 'cover' }} />
          <div className="sidebar-brand-text">
            <h2>Research Agent</h2>
            <span>AI Publishing Platform</span>
          </div>
          <button className="sidebar-toggle-btn hide-mobile" onClick={onToggleCollapse}>
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        </div>

        <nav className="sidebar-nav">
          <div className="nav-section-label">Navigation</div>
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
              onClick={onClose}
            >
              {item.icon}
              <span className="nav-link-text">{item.name}</span>
            </NavLink>
          ))}
        </nav>

        {usage && !collapsed && (
          <div style={{ padding: '1rem 1.25rem', borderTop: '1px solid var(--border)', fontSize: '0.8rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.4rem', color: 'var(--text)' }}>
              <span>Session: {Math.min(100, (usage.used / usage.quota) * 100).toFixed(0)}%</span>
              <span>Reset in: {usage.reset_in}</span>
            </div>
            <div style={{ height: '6px', background: 'var(--bg-hover)', borderRadius: '4px', overflow: 'hidden', marginBottom: '0.4rem' }}>
              <div style={{ height: '100%', width: `${Math.min(100, (usage.used / usage.quota) * 100)}%`, background: 'var(--primary)', borderRadius: '4px', transition: 'width 0.3s ease' }}></div>
            </div>
            <div style={{ color: 'var(--text-muted)' }}>Messages left: {usage.messages_left}</div>
          </div>
        )}

        <div className="sidebar-footer">
          {user && (
            <div className="user-info">
              <div className="user-avatar" style={{ overflow: 'hidden' }}>
                {user.picture ? (
                  <img src={user.picture} alt={user.name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} referrerPolicy="no-referrer" />
                ) : (
                  initials
                )}
              </div>
              <div className="user-details">
                <div className="user-name">{user.name}</div>
                <div className="user-email">{user.email}</div>
              </div>
            </div>
          )}
          <button className="logout-btn" onClick={handleLogout}>
            <LogOut size={15} />
            <span className="logout-text">Sign Out</span>
          </button>
        </div>
      </aside>
    </>
  );
};

export default Sidebar;
