import React from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { LayoutDashboard, BookOpen, PenTool, LayoutList, LogOut, X, ChevronLeft, ChevronRight, FileText } from 'lucide-react';
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

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  const initials = user?.name
    ? user.name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2)
    : 'U';

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

        <div className="sidebar-footer">
          {user && (
            <div className="user-info">
              <div className="user-avatar">{initials}</div>
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
