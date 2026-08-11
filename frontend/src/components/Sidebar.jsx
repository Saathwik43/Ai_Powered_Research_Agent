import React, { useEffect, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { LayoutDashboard, BookOpen, PenTool, LayoutList, LogOut, X, ChevronLeft, ChevronRight, FileText, Shield, Clock, MessageSquare, Moon, Sun } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useTheme } from '../context/ThemeContext';
import './Sidebar.css';

const Sidebar = ({ open, onClose, collapsed, onToggleCollapse }) => {
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
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
        const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/user/usage`);
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
          <button
            type="button"
            className="sidebar-close-btn hide-desktop"
            onClick={onClose}
            aria-label="Close navigation"
          >
            <X size={18} />
          </button>
          <button className="sidebar-toggle-btn hide-mobile" onClick={onToggleCollapse} aria-label="Collapse sidebar">
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
          <div className="sidebar-usage-card">
            <div className="sidebar-usage-row">
              <span className="sidebar-usage-stat">
                <Clock size={13} className="sidebar-usage-icon" /> Session: {Math.min(100, (usage.used / usage.quota) * 100).toFixed(0)}%
              </span>
              <span className="sidebar-usage-reset">Reset in {usage.reset_in}</span>
            </div>
            <div className="sidebar-usage-track">
              <div className="sidebar-usage-fill" style={{ width: `${Math.min(100, (usage.used / usage.quota) * 100)}%` }} />
            </div>
            <div className="sidebar-usage-messages">
              <MessageSquare size={13} className="sidebar-usage-icon" /> Messages left: <strong>{usage.messages_left}</strong>
            </div>
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
          <button
            type="button"
            className="theme-toggle-btn"
            onClick={toggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            title={theme === 'dark' ? 'Light theme' : 'Dark theme'}
          >
            {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
            <span className="theme-toggle-text">{theme === 'dark' ? 'Light theme' : 'Dark theme'}</span>
          </button>
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
