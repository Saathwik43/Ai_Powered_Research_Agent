import React, { useState } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { Menu, Sparkles } from 'lucide-react';
import { AuthProvider, useAuth } from './context/AuthContext';
import Sidebar from './components/Sidebar';
import Dashboard from './pages/Dashboard';
import LiteratureSurvey from './pages/LiteratureSurvey';
import ManuscriptBuilder from './pages/ManuscriptBuilder';
import VenueRecommendations from './pages/VenueRecommendations';
import PdfAnalysis from './pages/PdfAnalysis';
import LandingPage from './pages/LandingPage';
import { Login, Signup } from './pages/AuthPages';
import './App.css';

const ProtectedLayout = () => {
  const { user, loading } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  if (loading) return <AppLoader />;
  if (!user) return <Navigate to="/" replace />;

  return (
    <>
      {/* Mobile top bar */}
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
          <span style={{ fontWeight: 700, fontSize: '0.95rem', color: 'var(--text)' }}>Research Agent</span>
        </div>
        <div style={{ width: 30 }} />
      </div>

      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <main className="main-content">
        <Routes>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/literature-survey" element={<LiteratureSurvey />} />
          <Route path="/pdf-analysis" element={<PdfAnalysis />} />
          <Route path="/manuscript-builder" element={<ManuscriptBuilder />} />
          <Route path="/venue-recommendations" element={<VenueRecommendations />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
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

const AppLoader = () => (
  <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
    <div style={{ width: 40, height: 40, border: '3px solid var(--border)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
    <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
  </div>
);

function AppRoutes() {
  return (
    <div className="app-container">
      <Routes>
        <Route path="/" element={<PublicRoute><LandingPage /></PublicRoute>} />
        <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
        <Route path="/signup" element={<PublicRoute><Signup /></PublicRoute>} />
        <Route path="/*" element={<ProtectedLayout />} />
      </Routes>
    </div>
  );
}

function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}

export default App;
