import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  FileText,
  LayoutDashboard,
  Library,
  PenTool,
  Search,
  ShieldCheck,
  Sparkles,
  Target,
  Zap,
} from 'lucide-react';
import { InteractiveHoverButton } from '@/components/ui/interactive-hover-button';
import './LandingPage.css';

const features = [
  { icon: Search, title: 'Topic Discovery', desc: 'Scan active fields, compare impact, and move from vague interest to a usable research direction.' },
  { icon: Library, title: 'Literature Survey', desc: 'Pull relevant papers into one organized workspace with citation signals and direct paper links.' },
  { icon: PenTool, title: 'Manuscript Builder', desc: 'Draft section by section while keeping control of topic, structure, and saved versions.' },
  { icon: Target, title: 'Venue Matching', desc: 'Compare journal and conference fit with submission guidance before you commit.' },
];

const PREVIEW_SLIDES = [
  { id: 'dashboard', label: 'Topic Discovery' },
  { id: 'gap-analysis', label: 'Gap Analysis' },
  { id: 'manuscript', label: 'Manuscript Builder' },
];

const LandingPage = () => {
  const navigate = useNavigate();
  const [activeSlide, setActiveSlide] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => {
      setActiveSlide((prev) => (prev + 1) % PREVIEW_SLIDES.length);
    }, 4000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="landing-page">
      <nav className="landing-nav">
        <button className="brand-mark" onClick={() => navigate('/')} aria-label="Research Agent home">
          <img src="/9672704.webp" alt="Logo" style={{ width: 32, height: 32, borderRadius: '6px', objectFit: 'cover' }} />
          <span>
            <strong>Research Agent</strong>
            <small>Publishing workspace</small>
          </span>
        </button>
        <div className="landing-nav-actions">
          <button className="nav-signin" onClick={() => navigate('/login')}>Sign in</button>
          <button className="nav-start-btn" onClick={() => navigate('/signup')}>
            <span className="nav-start-dot" />
            Start now
          </button>
        </div>
      </nav>

      <main>
        <section className="landing-hero">
          <div className="hero-copy">
            <div className="hero-kicker">
              <Sparkles size={15} />
              The unified research workspace
            </div>
            <h1>Research Agent</h1>
            <p className="hero-lede">
              A clean professional workspace for discovering topics, surveying literature,
              drafting manuscripts, and choosing publication venues without switching tools.
            </p>
            <div className="hero-actions">
              <button className="hero-btn hero-primary" onClick={() => navigate('/signup')}>
                Create workspace <ArrowRight size={18} />
              </button>
              <button className="hero-btn hero-secondary" onClick={() => navigate('/login')}>
                Open existing account
              </button>
            </div>
            <div className="trust-row" aria-label="Platform highlights">
              <span><ShieldCheck size={16} /> Private account workspace</span>
              <span><BookOpen size={16} /> Academic source integrations</span>
              <span><CheckCircle2 size={16} /> Export-ready outputs</span>
            </div>
          </div>

          <div className="product-preview" aria-label="Research Agent product preview">
            <div className="preview-topbar">
              <div className="preview-dots">
                <span className="preview-dot dot-red" />
                <span className="preview-dot dot-yellow" />
                <span className="preview-dot dot-green" />
              </div>
              <div className="preview-url-bar">
                <span className="preview-url-protocol">https://</span>
                <span className="preview-url-domain">research-agent.app</span>
                <span className="preview-url-path">/workspace/{PREVIEW_SLIDES[activeSlide].id}</span>
              </div>
              <div className="preview-slide-indicators">
                {PREVIEW_SLIDES.map((slide, idx) => (
                  <button
                    key={slide.id}
                    className={`slide-dot-btn ${activeSlide === idx ? 'active' : ''}`}
                    onClick={() => setActiveSlide(idx)}
                    title={slide.label}
                    aria-label={`Go to slide ${idx + 1}: ${slide.label}`}
                  />
                ))}
              </div>
            </div>

            <div className="preview-stage">
              {/* Slide 0: Dashboard Search Bar */}
              <div className={`preview-slide ${activeSlide === 0 ? 'active' : ''}`}>
                <div className="preview-search-bar">
                  <Search size={16} className="text-muted" />
                  <span className="preview-search-text">Quantum Machine Learning in Healthcare</span>
                  <span className="badge badge-primary">Active Query</span>
                </div>
                <div className="preview-grid-3">
                  <div className="preview-card">
                    <small className="text-subtle">Topic Impact</small>
                    <strong className="text-primary" style={{ fontSize: 'var(--fs-lg)' }}>9.4 / 10</strong>
                    <span className="text-xs text-success">High Growth Field</span>
                  </div>
                  <div className="preview-card">
                    <small className="text-subtle">Surveyed Papers</small>
                    <strong className="text-accent" style={{ fontSize: 'var(--fs-lg)' }}>1,284</strong>
                    <span className="text-xs text-muted">OpenAlex & arXiv</span>
                  </div>
                  <div className="preview-card">
                    <small className="text-subtle">Venue Fit</small>
                    <strong className="text-success" style={{ fontSize: 'var(--fs-lg)' }}>18 Match</strong>
                    <span className="text-xs text-muted">IEEE & Nature ML</span>
                  </div>
                </div>
                <div className="preview-paper-item">
                  <div className="preview-paper-header">
                    <span className="serif" style={{ fontWeight: 600, color: 'var(--text)' }}>
                      Privacy-Preserving Federated Learning in Electronic Health Records
                    </span>
                    <span className="badge badge-accent">142 Citations</span>
                  </div>
                  <p className="text-xs text-muted mb-0">arXiv:2403.0192 • CS.AI • Crossref verified</p>
                </div>
              </div>

              {/* Slide 1: Gap Analysis Panel */}
              <div className={`preview-slide ${activeSlide === 1 ? 'active' : ''}`}>
                <div className="preview-tab-header">
                  <span className="tab-pill active">Identified Gaps (3)</span>
                  <span className="tab-pill">Well Covered</span>
                  <span className="tab-pill">Future Directions</span>
                </div>
                <div className="preview-gap-box">
                  <div className="preview-gap-title">
                    <Target size={15} className="text-accent" />
                    <strong>Critical Research Gap #1</strong>
                    <span className="badge badge-warning">High Priority</span>
                  </div>
                  <p className="text-sm text-muted">
                    Lack of real-time clinical validation on non-stationary patient telemetry streams under noisy sensor conditions.
                  </p>
                  <div className="margin-note" style={{ marginTop: '0.25rem' }}>
                    * Key opportunity for IEEE EMBC 2026 submission!
                  </div>
                </div>
                <div className="preview-recommendation-box">
                  <div className="text-xs font-medium text-primary">AI Suggested Direction:</div>
                  <div className="text-xs text-muted">
                    Propose a dual-attention online transformer architecture with adaptive sliding-window loss function.
                  </div>
                </div>
              </div>

              {/* Slide 2: Manuscript Split-Pane Editor */}
              <div className={`preview-slide ${activeSlide === 2 ? 'active' : ''}`}>
                <div className="preview-split-pane">
                  <div className="preview-split-left">
                    <div className="preview-pane-title">
                      <FileText size={14} className="text-primary" /> Outline
                    </div>
                    <ul className="preview-tree-list">
                      <li className="active">1. Introduction</li>
                      <li>2. Methodology</li>
                      <li>3. Results</li>
                      <li>4. References</li>
                    </ul>
                  </div>
                  <div className="preview-split-right">
                    <div className="preview-editor-header">
                      <span className="mono text-xs">manuscript_draft_v2.tex</span>
                      <span className="badge badge-success">Saved</span>
                    </div>
                    <h4 className="serif" style={{ fontSize: 'var(--fs-base)', color: 'var(--text)', textTransform: 'none', letterSpacing: 'normal', margin: '0 0 0.5rem 0' }}>
                      1. Introduction & Background
                    </h4>
                    <p className="serif text-xs text-muted" style={{ lineHeight: 1.5, margin: 0 }}>
                      Recent advancements in Transformer models have demonstrated remarkable success in time-series forecasting. Applying these architectures to ICU telemetry presents distinct challenges <span className="mono citation-id">[1]</span>...
                    </p>
                    <div className="margin-note" style={{ marginTop: '0.5rem' }}>
                      * Note: Insert citation for multi-head self-attention here.
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="feature-section">
          <div className="section-heading">
            <h2>Built for the full paper workflow</h2>
            <p>Every module is designed for repeated research work: quick inputs, clear results, and direct next actions.</p>
          </div>
          <div className="feature-grid">
            {features.map(({ icon: Icon, title, desc }, index) => (
              <article className="feature-card animate-slide-up" style={{ animationDelay: `${index * 0.05}s` }} key={title}>
                <div className="feature-icon"><Icon size={22} /></div>
                <h3>{title}</h3>
                <p>{desc}</p>
              </article>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
};

export default LandingPage;

