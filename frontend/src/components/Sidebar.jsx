import React from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { LayoutDashboard, BookOpen, PenTool, LayoutList, LogOut, X, ChevronLeft, ChevronRight, FileText, Shield, Moon, Sun } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useTheme } from '../context/ThemeContext';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import DailyUsageCard from './DailyUsageCard';
import './Sidebar.css';

// Grouped by where you are in the research workflow rather than as five peers.
// Routes are unchanged — only the labelling and order of the same paths.
const NAV_GROUPS = [
  {
    label: 'Discover',
    items: [
      { name: 'Topic Discovery', path: '/dashboard', icon: LayoutDashboard },
      { name: 'Literature Survey', path: '/literature-survey', icon: BookOpen },
    ],
  },
  {
    label: 'Analyze',
    items: [
      { name: 'PDF & Evidence', path: '/pdf-analysis', icon: FileText },
    ],
  },
  {
    label: 'Write',
    items: [
      { name: 'Manuscript', path: '/manuscript-builder', icon: PenTool },
    ],
  },
  {
    label: 'Publish',
    items: [
      { name: 'Venues', path: '/venue-recommendations', icon: LayoutList },
    ],
  },
];

const ADMIN_GROUP = {
  label: 'Admin',
  items: [{ name: 'Administration', path: '/admin', icon: Shield }],
};

const Sidebar = ({ open, onClose, collapsed, onToggleCollapse }) => {
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();

  const groups = user?.role === 'admin' ? [...NAV_GROUPS, ADMIN_GROUP] : NAV_GROUPS;

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  const initials = user?.name
    ? user.name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2)
    : 'U';

  // Collapsed, the group labels are hidden and every rail is icon-only, so the
  // name has to come back on hover — otherwise the regrouping costs legibility.
  const renderLink = (item) => {
    const link = (
      // A *string* className, not the usual `({ isActive }) => …` function.
      // Collapsed, each link is wrapped in `TooltipTrigger asChild`, and Radix's
      // Slot merges className by string-joining it — so the function was handed
      // to the DOM stringified, and every link carried the literal text
      // "({ isActive }) => `nav-link ${…}`" as its class. Measured in the page:
      // `.sidebar .nav-link` matched 0 elements while collapsed and 6 while
      // expanded, which is why the collapsed rail had no padding, no centring,
      // no hover and no active marker. react-router already sets
      // aria-current="page" on the active link, so the selected state is read
      // from that instead and nothing has to be computed here.
      <NavLink
        key={item.path}
        to={item.path}
        className="nav-link"
        onClick={onClose}
      >
        <item.icon size={16} className="nav-link-icon" />
        <span className="nav-link-text">{item.name}</span>
      </NavLink>
    );

    if (!collapsed) return link;

    return (
      <Tooltip key={item.path}>
        <TooltipTrigger asChild>{link}</TooltipTrigger>
        <TooltipContent side="right">{item.name}</TooltipContent>
      </Tooltip>
    );
  };

  return (
    <>
      {/* Mobile overlay */}
      {open && <div className="mobile-overlay" onClick={onClose} />}

      <aside className={`sidebar ${open ? 'open' : ''} ${collapsed ? 'collapsed' : ''}`}>
        <div className="sidebar-header">
          <img src="/9672704.webp" alt="Logo" className="sidebar-mark" />
          <div className="sidebar-brand-text">
            <h2>Research Agent</h2>
            <span>Research workspace</span>
          </div>
          <button
            type="button"
            className="sidebar-close-btn hide-desktop"
            onClick={onClose}
            aria-label="Close navigation"
          >
            <X size={18} />
          </button>
          <button
            type="button"
            className="sidebar-toggle-btn hide-mobile"
            onClick={onToggleCollapse}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {collapsed ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
          </button>
        </div>

        {/* Scroll lives here, not on the aside: the seam toggle sits at
            right: -13px and was half-clipped by overflow:auto on .sidebar. */}
        <div className="sidebar-body">
          <nav className="sidebar-nav">
            {groups.map((group) => (
              <div className="nav-group" key={group.label}>
                <div className="nav-section-label">{group.label}</div>
                {group.items.map(renderLink)}
              </div>
            ))}
          </nav>

          {!collapsed && <DailyUsageCard />}

          <div className="sidebar-footer">
            {user && (
              <div className="user-info">
                <div className="user-avatar">
                  {user.picture ? (
                    <img src={user.picture} alt={user.name} referrerPolicy="no-referrer" />
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
        </div>
      </aside>
    </>
  );
};

export default Sidebar;
