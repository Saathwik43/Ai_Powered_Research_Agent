import React from 'react';
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

const workflow = [
  'Find an active topic',
  'Collect related papers',
  'Draft the manuscript',
  'Match the best venue',
];

const LandingPage = () => {
  const navigate = useNavigate();

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
          <button className="btn btn-ghost" onClick={() => navigate('/login')}>Sign in</button>
          <InteractiveHoverButton text="Start now" onClick={() => navigate('/signup')} />
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
              <InteractiveHoverButton text="Create workspace" onClick={() => navigate('/signup')} />
              <button className="btn hero-secondary" onClick={() => navigate('/login')}>
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
              <div>
                <span className="preview-dot dot-red" />
                <span className="preview-dot dot-yellow" />
                <span className="preview-dot dot-green" />
              </div>
              <span>Topic discovery</span>
            </div>
            <div className="preview-body">
              <aside className="preview-sidebar">
                <div className="preview-logo"><Sparkles size={18} /></div>
                <span className="active"><LayoutDashboard size={15} /></span>
                <span><Library size={15} /></span>
                <span><FileText size={15} /></span>
              </aside>
              <div className="preview-main">
                <div className="preview-search">
                  <Search size={17} />
                  <span>machine learning in healthcare</span>
                  <InteractiveHoverButton text="Discover" />
                </div>
                <div className="preview-grid">
                  <div className="metric-card blue">
                    <small>Impact</small>
                    <strong>Very High</strong>
                  </div>
                  <div className="metric-card orange">
                    <small>Papers</small>
                    <strong>128</strong>
                  </div>
                  <div className="metric-card green">
                    <small>Venues</small>
                    <strong>24</strong>
                  </div>
                </div>
                <div className="preview-list">
                  <span />
                  <span />
                  <span />
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
