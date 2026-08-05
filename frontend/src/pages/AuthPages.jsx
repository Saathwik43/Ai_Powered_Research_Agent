import React, { useState , useEffect } from 'react';
import { useNavigate, Link, useSearchParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { GoogleLogin } from '@react-oauth/google';
import { AlertCircle, ArrowRight, CheckCircle2, Eye, EyeOff, Lock, Mail, User } from 'lucide-react';
import './AuthPages.css';

const Login = () => {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ email: '', password: '' });
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleChange = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Login failed.'); return; }
      login(data.token, data.user);
      navigate('/dashboard');
    } catch {
      setError('Could not connect to server.');
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleSuccess = async (credentialResponse) => {
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/auth/google`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: credentialResponse.credential }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Google sign-in failed.'); return; }
      login(data.token, data.user);
      navigate('/dashboard');
    } catch {
      setError('Could not connect to server.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout
      eyebrow="Welcome back"
      title="Sign in to your workspace"
      subtitle="Continue researching, drafting, and matching venues from your saved account."
      footer={<>New to Research Agent? <Link to="/signup">Create an account</Link></>}
    >
      <form onSubmit={handleSubmit} className="auth-form">
        {error && <ErrorBanner message={error} />}
        <InputField icon={<Mail size={17} />} label="Email" name="email" type="email" value={form.email} onChange={handleChange} placeholder="you@example.com" />
        <InputField
          icon={<Lock size={17} />}
          label="Password"
          labelExtra={<Link to="/forgot-password" className="forgot-password-link">Forgot password?</Link>}
          name="password"
          type={showPassword ? 'text' : 'password'}
          value={form.password}
          onChange={handleChange}
          placeholder="Your password"
          suffix={
            <button className="input-icon-btn" type="button" onClick={() => setShowPassword(p => !p)} aria-label={showPassword ? 'Hide password' : 'Show password'}>
              {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          }
        />
        <button type="submit" className="btn btn-primary w-full auth-submit" disabled={loading}>
          {loading ? <><Spin /> Signing in</> : <>Sign in <ArrowRight size={16} /></>}
        </button>

        <div className="auth-divider">
          <span>or</span>
        </div>
        
        <div className="google-login-container">
          <GoogleLogin
            onSuccess={handleGoogleSuccess}
            onError={() => setError('Google sign-in failed.')}
            useOneTap
          />
        </div>
      </form>
    </AuthLayout>
  );
};

const Signup = () => {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: '', email: '', password: '' });
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleChange = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (form.password.length < 6) { setError('Password must be at least 6 characters.'); return; }
    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/auth/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Signup failed.'); return; }
      login(data.token, data.user);
      navigate('/dashboard');
    } catch {
      setError('Could not connect to server.');
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleSuccess = async (credentialResponse) => {
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/auth/google`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: credentialResponse.credential }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Google sign-up failed.'); return; }
      login(data.token, data.user);
      navigate('/dashboard');
    } catch {
      setError('Could not connect to server.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout
      eyebrow="Start free"
      title="Create your research workspace"
      subtitle="Set up a private account for topic discovery, literature review, drafts, and venue recommendations."
      footer={<>Already have an account? <Link to="/login">Sign in</Link></>}
    >
      <form onSubmit={handleSubmit} className="auth-form">
        {error && <ErrorBanner message={error} />}
        <InputField icon={<User size={17} />} label="Full name" name="name" type="text" value={form.name} onChange={handleChange} placeholder="Your name" />
        <InputField icon={<Mail size={17} />} label="Email" name="email" type="email" value={form.email} onChange={handleChange} placeholder="you@example.com" />
        <InputField
          icon={<Lock size={17} />}
          label="Password"
          name="password"
          type={showPassword ? 'text' : 'password'}
          value={form.password}
          onChange={handleChange}
          placeholder="Min. 6 characters"
          suffix={
            <button className="input-icon-btn" type="button" onClick={() => setShowPassword(p => !p)} aria-label={showPassword ? 'Hide password' : 'Show password'}>
              {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          }
        />
        <button type="submit" className="btn btn-primary w-full auth-submit" disabled={loading}>
          {loading ? <><Spin /> Creating account</> : <>Create account <ArrowRight size={16} /></>}
        </button>

        <div className="auth-divider">
          <span>or</span>
        </div>
        
        <div className="google-login-container">
          <GoogleLogin
            onSuccess={handleGoogleSuccess}
            onError={() => setError('Google sign-up failed.')}
            useOneTap
          />
        </div>
      </form>
    </AuthLayout>
  );
};

const ForgotPassword = () => {
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setMessage('');
    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/auth/forgot-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Failed to send reset link.'); return; }
      setMessage(data.message || 'If that email exists, a reset link has been sent.');
    } catch {
      setError('Could not connect to server.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout
      eyebrow="Account recovery"
      title="Reset your password"
      subtitle="Enter the email associated with your account and we will send you a link to reset your password."
      footer={<>Remember your password? <Link to="/login">Sign in</Link></>}
    >
      <form onSubmit={handleSubmit} className="auth-form">
        {error && <ErrorBanner message={error} />}
        {message && <SuccessBanner message={message} />}
        <InputField
          icon={<Mail size={17} />}
          label="Email"
          name="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          required
        />
        <button type="submit" className="btn btn-primary w-full auth-submit" disabled={loading}>
          {loading ? <><Spin /> Sending reset link</> : <>Send reset link <ArrowRight size={16} /></>}
        </button>
      </form>
    </AuthLayout>
  );
};

const ResetPassword = () => {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') || '';
  const navigate = useNavigate();

  const [newPassword, setNewPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  
  const [tokenStatus, setTokenStatus] = useState('checking'); // 'checking' | 'valid' | 'invalid'

  useEffect(() => {
    if (!token) { setTokenStatus('invalid'); return; }
    fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/auth/validate-reset-token?token=${token}`)
      .then(res => res.json())
      .then(data => setTokenStatus(data.valid ? 'valid' : 'invalid'))
      .catch(() => setTokenStatus('invalid'));
  }, [token]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setMessage('');
    if (!token) {
      setError('Invalid or missing reset token.');
      return;
    }
    if (newPassword.length < 6) {
      setError('Password must be at least 6 characters.');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/auth/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, new_password: newPassword }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Failed to reset password.'); return; }
      setMessage(data.message || 'Password updated. Please log in.');
      setTimeout(() => {
        navigate('/login');
      }, 2500);
    } catch {
      setError('Could not connect to server.');
    } finally {
      setLoading(false);
    }
  };

  if (tokenStatus === 'checking') {
    return (
      <AuthLayout eyebrow="Security" title="Checking link..." subtitle="One moment.">
        <div />
      </AuthLayout>
    );
  }

  if (tokenStatus === 'invalid') {
    return (
      <AuthLayout
        eyebrow="Security"
        title="Link expired"
        subtitle="This reset link has already been used or has expired."
        footer={<>Back to <Link to="/login">Sign in</Link></>}
      >
        <Link to="/forgot-password" className="btn btn-primary w-full auth-submit">
          Request a new link
        </Link>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      eyebrow="Security"
      title="Create new password"
      subtitle="Enter a new password for your account."
      footer={<>Back to <Link to="/login">Sign in</Link></>}
    >
      <form onSubmit={handleSubmit} className="auth-form">
        {error && <ErrorBanner message={error} />}
        {message && <SuccessBanner message={message} />}
        {!token && <ErrorBanner message="No reset token found in URL. Please use the reset link sent to your email." />}
        <InputField
          icon={<Lock size={17} />}
          label="New Password"
          name="newPassword"
          type={showPassword ? 'text' : 'password'}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          placeholder="Min. 6 characters"
          suffix={
            <button className="input-icon-btn" type="button" onClick={() => setShowPassword(p => !p)} aria-label={showPassword ? 'Hide password' : 'Show password'}>
              {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          }
          required
        />
        <button type="submit" className="btn btn-primary w-full auth-submit" disabled={loading || !token}>
          {loading ? <><Spin /> Updating password</> : <>Update password <ArrowRight size={16} /></>}
        </button>
      </form>
    </AuthLayout>
  );
};

const AuthLayout = ({ eyebrow, title, subtitle, footer, children }) => (
  <div className="auth-page">
    <div className="auth-shell">
      <section className="auth-info-panel" aria-label="Research Agent overview">
        <div className="auth-info-atmosphere" aria-hidden="true">
          <span className="auth-orb auth-orb-a" />
          <span className="auth-orb auth-orb-b" />
          <span className="auth-grid" />
        </div>

        <div className="auth-info-top">
          <Link to="/" className="auth-brand">
            <img src="/9672704.webp" alt="" width={40} height={40} />
            Research Agent
          </Link>
        </div>

        <div className="auth-info-visual" aria-hidden="true">
          <AuthResearchScene />
        </div>

        <div className="auth-info-copy">
          <span className="auth-eyebrow">{eyebrow || 'Accelerate Discovery'}</span>
          <h1>A unified workspace for researchers.</h1>
          <p className="auth-subtitle">
            Go from topic discovery to literature review, drafting, and venue matching in one seamless flow.
          </p>
          <div className="auth-proof">
            <div>
              <strong>4</strong>
              <span>Core Modules</span>
            </div>
            <div>
              <strong>1</strong>
              <span>Seamless Workflow</span>
            </div>
          </div>
        </div>
      </section>

      <section className="auth-form-panel">
        <div className="auth-form-wrapper">
          <h2>{title}</h2>
          <p className="auth-form-subtitle">{subtitle}</p>
          {children}
          <div className="auth-footer">{footer}</div>
        </div>
      </section>
    </div>
  </div>
);

/** Research-desk illustration: discovery → papers → manuscript */
const AuthResearchScene = () => (
  <svg
    className="auth-scene"
    viewBox="0 0 420 280"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    role="img"
    aria-label="Illustration of topic discovery, literature, and manuscript drafting"
  >
    <defs>
      <linearGradient id="authDesk" x1="40" y1="240" x2="380" y2="260" gradientUnits="userSpaceOnUse">
        <stop stopColor="#2A241C" />
        <stop offset="1" stopColor="#1A1713" />
      </linearGradient>
      <linearGradient id="authPaper" x1="0" y1="0" x2="1" y2="1">
        <stop stopColor="#FFFEFA" />
        <stop offset="1" stopColor="#F2EFE6" />
      </linearGradient>
      <linearGradient id="authInk" x1="0" y1="0" x2="0" y2="1">
        <stop stopColor="#2B5EA8" />
        <stop offset="1" stopColor="#1F4A87" />
      </linearGradient>
      <filter id="authSoft" x="-20%" y="-20%" width="140%" height="140%">
        <feDropShadow dx="0" dy="10" stdDeviation="12" floodColor="#0A0B0E" floodOpacity="0.45" />
      </filter>
    </defs>

    {/* Desk */}
    <ellipse className="auth-scene-desk" cx="210" cy="248" rx="168" ry="14" fill="url(#authDesk)" opacity="0.9" />

    {/* Paper stack (literature) */}
    <g className="auth-scene-stack" filter="url(#authSoft)">
      <rect x="48" y="118" width="118" height="108" rx="6" fill="#E8E4D8" transform="rotate(-8 107 172)" />
      <rect x="56" y="112" width="118" height="108" rx="6" fill="#F4F1E8" transform="rotate(-3 115 166)" />
      <g transform="translate(64 98)">
        <rect width="118" height="108" rx="6" fill="url(#authPaper)" />
        <rect x="14" y="18" width="72" height="6" rx="2" fill="#C9C5B8" />
        <rect className="auth-scene-line" x="14" y="34" width="90" height="4" rx="2" fill="#D8D4C8" />
        <rect className="auth-scene-line auth-scene-line-b" x="14" y="46" width="84" height="4" rx="2" fill="#D8D4C8" />
        <rect className="auth-scene-line auth-scene-line-c" x="14" y="58" width="78" height="4" rx="2" fill="#D8D4C8" />
        <rect x="14" y="78" width="40" height="14" rx="3" fill="#2B5EA8" opacity="0.18" />
        <text x="22" y="88" fill="#2B5EA8" fontSize="9" fontFamily="IBM Plex Mono, monospace" fontWeight="700">[1]</text>
        <text x="48" y="88" fill="#C9622A" fontSize="9" fontFamily="IBM Plex Mono, monospace" fontWeight="700">[2]</text>
      </g>
    </g>

    {/* Open manuscript */}
    <g className="auth-scene-manuscript" filter="url(#authSoft)">
      <rect x="198" y="72" width="168" height="148" rx="8" fill="url(#authPaper)" />
      <rect x="198" y="72" width="168" height="22" rx="8" fill="#2B5EA8" />
      <rect x="198" y="86" width="168" height="8" fill="#2B5EA8" />
      <text x="214" y="87" fill="#FFFEFA" fontSize="10" fontFamily="Source Serif 4, Georgia, serif" fontWeight="600">Manuscript</text>
      <path className="auth-scene-stroke" d="M218 118 H346" stroke="#C9C5B8" strokeWidth="3.5" strokeLinecap="round" />
      <path className="auth-scene-stroke auth-scene-stroke-b" d="M218 134 H334" stroke="#C9C5B8" strokeWidth="3.5" strokeLinecap="round" />
      <path className="auth-scene-stroke auth-scene-stroke-c" d="M218 150 H340" stroke="#C9C5B8" strokeWidth="3.5" strokeLinecap="round" />
      <path className="auth-scene-stroke auth-scene-stroke-d" d="M218 166 H300" stroke="#C9C5B8" strokeWidth="3.5" strokeLinecap="round" />
      <rect x="218" y="186" width="56" height="18" rx="4" fill="#C9622A" opacity="0.2" />
      <text x="228" y="199" fill="#A44E20" fontSize="9" fontFamily="IBM Plex Mono, monospace" fontWeight="700">DRAFT</text>
    </g>

    {/* Magnifier — topic discovery */}
    <g className="auth-scene-lens">
      <circle cx="152" cy="96" r="28" fill="rgba(255,254,250,0.12)" stroke="#FFE8A3" strokeWidth="3" />
      <circle cx="152" cy="96" r="18" fill="none" stroke="#2B5EA8" strokeWidth="2.5" opacity="0.85" />
      <path d="M172 116 L188 132" stroke="#C9622A" strokeWidth="5" strokeLinecap="round" />
      <circle className="auth-scene-pulse" cx="152" cy="96" r="34" stroke="#2B5EA8" strokeWidth="1.5" fill="none" />
    </g>

    {/* Floating venue chip */}
    <g className="auth-scene-chip">
      <rect x="286" y="42" width="92" height="28" rx="14" fill="#1F4A87" />
      <circle cx="302" cy="56" r="5" fill="#4F8F6B" />
      <text x="314" y="60" fill="#FFFEFA" fontSize="10" fontFamily="IBM Plex Mono, monospace" fontWeight="650">IEEE · ACM</text>
    </g>

    {/* Workflow sparks */}
    <g className="auth-scene-sparks" stroke="#C9622A" strokeWidth="2" strokeLinecap="round">
      <path d="M120 64 l4 -10 M124 64 l10 -4 M124 64 l4 8" />
      <path className="auth-scene-sparks-b" d="M360 168 l6 -8 M366 168 l8 2 M366 168 l2 8" />
    </g>
  </svg>
);

const InputField = ({ icon, label, labelExtra, suffix, ...props }) => (
  <div className="field-group">
    <div className="field-header">
      <label>{label}</label>
      {labelExtra}
    </div>
    <div className="field-control">
      <span className="field-icon">{icon}</span>
      <input {...props} />
      {suffix && <span className="field-suffix">{suffix}</span>}
    </div>
  </div>
);

const ErrorBanner = ({ message }) => (
  <div className="error-banner">
    <AlertCircle size={17} />
    {message}
  </div>
);

const SuccessBanner = ({ message }) => (
  <div className="success-banner">
    <CheckCircle2 size={17} />
    {message}
  </div>
);

const Spin = () => <span className="auth-spin" />;

export { Login, Signup, ForgotPassword, ResetPassword };
