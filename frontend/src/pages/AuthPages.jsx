import React, { useState } from 'react';
import { useNavigate, Link, useSearchParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { GoogleLogin } from '@react-oauth/google';
import { AlertCircle, ArrowRight, CheckCircle2, Eye, EyeOff, Lock, Mail, Sparkles, User } from 'lucide-react';
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
      {/* Premium Left Panel */}
      <section className="auth-info-panel">
        <div>
          <Link to="/" className="auth-brand">
            <span><Sparkles size={24} /></span>
            Research Agent
          </Link>
        </div>
        <div>
          <span className="auth-eyebrow">{eyebrow || "Accelerate Discovery"}</span>
          <h1>A unified workspace for researchers.</h1>
          <p className="auth-subtitle">Go from topic discovery to literature review, drafting, and venue matching in one seamless flow.</p>
          
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

      {/* Form Right Panel */}
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
