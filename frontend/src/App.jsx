import React, { useState, useEffect, Suspense, lazy } from 'react';
import { Routes, Route, Navigate, Outlet } from 'react-router-dom';
import { Menu, X } from 'lucide-react';
import { AuthProvider, useAuth } from './context/AuthContext';
import { AppProvider } from './context/AppContext';
import { ThemeProvider } from './context/ThemeContext';
import { MotionConfig } from 'motion/react';
import { TooltipProvider } from './components/ui/tooltip';
import Sidebar from './components/Sidebar';
import PageMeta from './components/PageMeta';
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
const VerifyEmail = lazy(() =>
  import('./pages/AuthPages').then((m) => ({ default: m.VerifyEmail }))
);
const SignupComplete = lazy(() =>
  import('./pages/AuthPages').then((m) => ({ default: m.SignupComplete }))
);
const NotFound = lazy(() => import('./pages/NotFound'));

const AppLoader = () => (
  <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--canvas)' }}>
    <div style={{ width: 28, height: 28, border: '2px solid var(--line)', borderTopColor: 'var(--focus)', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
    <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
  </div>
);

const PageFallback = () => (
  <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '40vh' }}>
    <div style={{ width: 24, height: 24, border: '2px solid var(--line)', borderTopColor: 'var(--focus)', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
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
          type="button"
          onClick={() => setSidebarOpen((open) => !open)}
          className="btn btn-icon"
          aria-label={sidebarOpen ? 'Close navigation' : 'Open navigation'}
          aria-expanded={sidebarOpen}
        >
          {sidebarOpen ? <X size={18} /> : <Menu size={18} />}
        </button>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
          <img src="/9672704.webp" alt="Logo" style={{ width: 26, height: 26, borderRadius: 'var(--radius-sm)', objectFit: 'cover' }} />
          <span style={{ fontFamily: 'var(--font-serif)', fontWeight: 600, fontSize: 'var(--fs-md)', color: 'var(--ink)' }}>Research Agent</span>
        </div>
        <div style={{ width: 32 }} />
      </div>

      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        collapsed={collapsed}
        onToggleCollapse={toggleCollapse}
      />
      <main className={`main-content ${collapsed ? 'collapsed' : ''}`}>
        <Suspense fallback={<PageFallback />}>
          <Outlet />
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
      <PageMeta />
      <Suspense fallback={<AppLoader />}>
        <Routes>
          <Route path="/" element={<PublicRoute><LandingPage /></PublicRoute>} />
          <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
          <Route path="/signup" element={<PublicRoute><Signup /></PublicRoute>} />
          <Route path="/forgot-password" element={<PublicRoute><ForgotPassword /></PublicRoute>} />
          <Route path="/reset-password" element={<PublicRoute><ResetPassword /></PublicRoute>} />
          {/* Not wrapped in PublicRoute: the page signs the user in itself, and
              a redirect-if-authenticated guard would bounce them off it. */}
          <Route path="/verify-email" element={<VerifyEmail />} />
          <Route path="/signup/complete" element={<SignupComplete />} />
          <Route element={<ProtectedLayout />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/literature-survey" element={<LiteratureSurvey />} />
            <Route path="/pdf-analysis" element={<PdfAnalysis />} />
            <Route path="/manuscript-builder" element={<ManuscriptBuilder />} />
            <Route path="/venue-recommendations" element={<VenueRecommendations />} />
            <Route path="/admin" element={<AdminDashboard />} />
          </Route>
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </div>
  );
}

function App() {
  return (
    <MotionConfig reducedMotion="user">
      <TooltipProvider delayDuration={200}>
        <ThemeProvider>
          <AuthProvider>
            <AppProvider>
              <AppRoutes />
            </AppProvider>
          </AuthProvider>
        </ThemeProvider>
      </TooltipProvider>
    </MotionConfig>
  );
}

export default App;
