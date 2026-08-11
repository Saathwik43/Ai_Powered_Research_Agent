import React, { useState, useEffect, Suspense, lazy } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { Menu } from 'lucide-react';
import { AuthProvider, useAuth } from './context/AuthContext';
import { AppProvider } from './context/AppContext';
import { ThemeProvider } from './context/ThemeContext';
import { MotionConfig } from 'motion/react';
import Sidebar from './components/Sidebar';
import './App.css';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const LiteratureSurvey = lazy(() => import('./pages/LiteratureSurvey'));
const ManuscriptBuilder = lazy(() => import('./pages/ManuscriptBuilder'));
const VenueRecommendations = lazy(() => import('./pages/VenueRecommendations'));
const PdfAnalysis = lazy(() => import('./pages/PdfAnalysis'));
const LandingPage = lazy(() => import('./pages/LandingPage'));
const AdminDashboard = lazy(() => import('./pages/AdminDashboard'));
const Login = lazy(() => import('./pages/AuthPages').then((m) => ({ default: m.Login })));
const Signup = lazy(() => import('./pages/AuthPages').then((m) => ({ default: m.Signup })));
const ForgotPassword = lazy(() =>
  import('./pages/AuthPages').then((m) => ({ default: m.ForgotPassword }))
);
const ResetPassword = lazy(() =>
  import('./pages/AuthPages').then((m) => ({ default: m.ResetPassword }))
);

const AppLoader = () => (
  <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
    <div style={{ width: 40, height: 40, border: '3px solid var(--border)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
    <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
  </div>
);

const PageFallback = () => (
  <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '40vh' }}>
    <div style={{ width: 32, height: 32, border: '3px solid var(--border)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
  </div>
);

const ProtectedLayout = () => {
  const { user, loading } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    return localStorage.getItem('sidebarCollapsed') === 'true';
  });
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' ? window.matchMedia('(max-width: 768px)').matches : false
  );

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)');
    const onChange = (e) => {
      setIsMobile(e.matches);
      if (e.matches) setSidebarOpen(false);
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const toggleCollapse = () => {
    const val = !sidebarCollapsed;
    setSidebarCollapsed(val);
    localStorage.setItem('sidebarCollapsed', val);
  };

  if (loading) return <AppLoader />;
  if (!user) return <Navigate to="/" replace />;

  const collapsed = isMobile ? false : sidebarCollapsed;

  return (
    <>
      <div className="mobile-header">
        <button
          onClick={() => setSidebarOpen(true)}
          className="btn btn-icon"
          aria-label="Open navigation"
        >
          <Menu size={22} />
        </button>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
          <img src="/9672704.webp" alt="Logo" style={{ width: 32, height: 32, borderRadius: '6px', objectFit: 'cover' }} />
          <span style={{ fontWeight: 700, fontSize: 'var(--fs-base)', color: 'var(--text)' }}>Research Agent</span>
        </div>
        <div style={{ width: 40 }} />
      </div>

      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        collapsed={collapsed}
        onToggleCollapse={toggleCollapse}
      />
      <main className={`main-content ${collapsed ? 'collapsed' : ''}`}>
        <Suspense fallback={<PageFallback />}>
          <Routes>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/literature-survey" element={<LiteratureSurvey />} />
            <Route path="/pdf-analysis" element={<PdfAnalysis />} />
            <Route path="/manuscript-builder" element={<ManuscriptBuilder />} />
            <Route path="/venue-recommendations" element={<VenueRecommendations />} />
            <Route path="/admin" element={<AdminDashboard />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </Suspense>
      </main>
    </>
  );
};

const PublicRoute = ({ children }) => {
  const { user, loading } = useAuth();
  if (loading) return <AppLoader />;
  if (user) return <Navigate to="/dashboard" replace />;
  return children;
};

function AppRoutes() {
  return (
    <div className="app-container">
      <Suspense fallback={<AppLoader />}>
        <Routes>
          <Route path="/" element={<PublicRoute><LandingPage /></PublicRoute>} />
          <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
          <Route path="/signup" element={<PublicRoute><Signup /></PublicRoute>} />
          <Route path="/forgot-password" element={<PublicRoute><ForgotPassword /></PublicRoute>} />
          <Route path="/reset-password" element={<PublicRoute><ResetPassword /></PublicRoute>} />
          <Route path="/*" element={<ProtectedLayout />} />
        </Routes>
      </Suspense>
    </div>
  );
}

function App() {
  return (
    <MotionConfig reducedMotion="user">
      <ThemeProvider>
        <AuthProvider>
          <AppProvider>
            <AppRoutes />
          </AppProvider>
        </AuthProvider>
      </ThemeProvider>
    </MotionConfig>
  );
}

export default App;
