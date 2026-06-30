import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { AlertCircle, ArrowRight, Eye, EyeOff, Lock, Mail, Sparkles, User } from 'lucide-react';
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
      </form>
    </AuthLayout>
  );
};

const AuthLayout = ({ eyebrow, title, subtitle, footer, children }) => (
  <div className="auth-page">
    <div className="auth-shell animate-scale-in">
      <section className="auth-panel auth-info-panel">
        <Link to="/" className="auth-brand">
          <span><Sparkles size={20} /></span>
          <strong>Research Agent</strong>
        </Link>
        <div>
          <p className="auth-eyebrow">{eyebrow}</p>
          <h1>{title}</h1>
          <p className="auth-subtitle">{subtitle}</p>
        </div>
        <div className="auth-proof">
          <div><strong>4</strong><span>Research modules</span></div>
          <div><strong>1</strong><span>Focused workspace</span></div>
        </div>
      </section>

      <section className="auth-panel auth-form-panel">
        {children}
        <p className="auth-footer">{footer}</p>
      </section>
    </div>
  </div>
);

const InputField = ({ icon, label, suffix, ...props }) => (
  <div className="field-group">
    <label>{label}</label>
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

const Spin = () => <span className="auth-spin" />;

export { Login, Signup };
