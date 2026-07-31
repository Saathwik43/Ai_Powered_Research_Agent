import React, { useState, useEffect, useRef, useCallback } from 'react';
import { 
  Search, TrendingUp, ArrowUpRight, ExternalLink, FileText, X, Sparkles, Trash2, ArrowRight,
  Brain, Shield, Cpu, Database, Atom, Eye, BookOpen, Layers, Award
} from 'lucide-react';
import { InteractiveHoverButton } from '@/components/ui/interactive-hover-button';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { useAuth } from '../context/AuthContext';
import { Spinner, SkeletonList } from '../components/Loader';
import { useNavigate } from 'react-router-dom';
import './Dashboard.css';

const SUGGESTIONS = [
  'machine learning in healthcare', 'deep learning for NLP', 'computer vision',
  'cybersecurity threat detection', 'quantum computing', 'federated learning',
  'large language models', 'autonomous vehicles', 'reinforcement learning',
  'explainable AI', 'edge computing', 'generative AI', 'drug discovery AI',
  'natural language processing', 'neural architecture search', 'robotics',
];

const CATEGORIES = [
  { title: 'Artificial Intelligence', subtitle: 'LLMs, agents & reasoning',    arxiv: 'cs.AI',    query: 'artificial intelligence', Icon: Brain,    patternId: 'ai' },
  { title: 'Cybersecurity',           subtitle: 'Threat detection & privacy',   arxiv: 'cs.CR',    query: 'cybersecurity',           Icon: Shield,   patternId: 'security' },
  { title: 'Machine Learning',        subtitle: 'Models, training & evaluation', arxiv: 'cs.LG',   query: 'machine learning',        Icon: Cpu,      patternId: 'ml' },
  { title: 'Data Science',            subtitle: 'Analytics & big data',          arxiv: 'cs.DS',   query: 'data science',            Icon: Database, patternId: 'data' },
  { title: 'Quantum Computing',       subtitle: 'Qubits & algorithms',           arxiv: 'quant-ph', query: 'quantum computing',       Icon: Atom,     patternId: 'quantum' },
  { title: 'Computer Vision',         subtitle: 'Images, video & perception',    arxiv: 'cs.CV',   query: 'computer vision',         Icon: Eye,      patternId: 'vision' },
];

const impactScore = (i) => ({ 'Very High': 4, 'High': 3, 'Medium': 2, 'Low': 1 }[i] || 1);
const impactColor = (i) => ({ 
  'Very High': 'var(--primary)', 
  'High': 'var(--accent)', 
  'Medium': 'var(--success)', 
  'Low': 'var(--text-subtle)' 
}[i] || 'var(--primary)');

function CategoryPattern({ id }) {
  if (id === 'ai') {
    return (
      <svg className="category-svg-pattern" viewBox="0 0 320 110" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="160" cy="55" r="90" fill="none" stroke="var(--primary)" strokeWidth="1" opacity="0.12" />
        <circle cx="160" cy="55" r="60" fill="none" stroke="var(--primary)" strokeWidth="1.5" strokeDasharray="4 4" opacity="0.2" />
        <circle cx="160" cy="55" r="30" fill="none" stroke="var(--text)" strokeWidth="1" opacity="0.15" />
        <line x1="40" y1="55" x2="280" y2="55" stroke="var(--primary)" strokeWidth="1" opacity="0.15" />
        <line x1="160" y1="10" x2="160" y2="100" stroke="var(--primary)" strokeWidth="1" opacity="0.15" />
        <circle cx="100" cy="55" r="4" fill="var(--primary)" opacity="0.4" />
        <circle cx="220" cy="55" r="4" fill="var(--primary)" opacity="0.4" />
        <circle cx="160" cy="25" r="4" fill="var(--accent)" opacity="0.4" />
        <circle cx="160" cy="85" r="4" fill="var(--accent)" opacity="0.4" />
      </svg>
    );
  }
  if (id === 'security') {
    return (
      <svg className="category-svg-pattern" viewBox="0 0 320 110" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
        <polygon points="60,20 100,20 120,55 100,90 60,90 40,55" fill="none" stroke="var(--accent)" strokeWidth="1.2" opacity="0.25" />
        <polygon points="160,20 200,20 220,55 200,90 160,90 140,55" fill="none" stroke="var(--text-subtle)" strokeWidth="1.2" opacity="0.3" />
        <polygon points="260,20 300,20 320,55 300,90 260,90 240,55" fill="none" stroke="var(--accent)" strokeWidth="1.2" opacity="0.18" />
        <path d="M 40 55 L 280 55" stroke="var(--accent)" strokeWidth="1" strokeDasharray="3 3" opacity="0.2" />
        <circle cx="160" cy="55" r="5" fill="var(--accent)" opacity="0.4" />
      </svg>
    );
  }
  if (id === 'ml') {
    return (
      <svg className="category-svg-pattern" viewBox="0 0 320 110" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M 30 20 L 120 55 L 210 20 L 290 55" fill="none" stroke="var(--primary)" strokeWidth="1.5" opacity="0.25" />
        <path d="M 30 90 L 120 55 L 210 90 L 290 55" fill="none" stroke="var(--success)" strokeWidth="1.5" opacity="0.25" />
        <circle cx="30" cy="20" r="4" fill="var(--primary)" opacity="0.4" />
        <circle cx="30" cy="90" r="4" fill="var(--success)" opacity="0.4" />
        <circle cx="120" cy="55" r="6" fill="var(--primary)" opacity="0.5" />
        <circle cx="210" cy="20" r="4" fill="var(--success)" opacity="0.4" />
        <circle cx="210" cy="90" r="4" fill="var(--primary)" opacity="0.4" />
        <circle cx="290" cy="55" r="5" fill="var(--accent)" opacity="0.5" />
      </svg>
    );
  }
  if (id === 'data') {
    return (
      <svg className="category-svg-pattern" viewBox="0 0 320 110" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="40" y="45" width="20" height="45" fill="var(--success)" opacity="0.2" rx="2" />
        <rect x="80" y="25" width="20" height="65" fill="var(--success)" opacity="0.3" rx="2" />
        <rect x="120" y="55" width="20" height="35" fill="var(--text-subtle)" opacity="0.25" rx="2" />
        <rect x="160" y="15" width="20" height="75" fill="var(--success)" opacity="0.35" rx="2" />
        <rect x="200" y="35" width="20" height="55" fill="var(--primary)" opacity="0.25" rx="2" />
        <rect x="240" y="50" width="20" height="40" fill="var(--text-subtle)" opacity="0.2" rx="2" />
        <path d="M 30 90 L 270 90" stroke="var(--border)" strokeWidth="1.5" />
      </svg>
    );
  }
  if (id === 'quantum') {
    return (
      <svg className="category-svg-pattern" viewBox="0 0 320 110" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
        <ellipse cx="160" cy="55" rx="100" ry="30" fill="none" stroke="var(--accent)" strokeWidth="1.5" opacity="0.25" transform="rotate(-15 160 55)" />
        <ellipse cx="160" cy="55" rx="100" ry="30" fill="none" stroke="var(--primary)" strokeWidth="1.5" opacity="0.25" transform="rotate(15 160 55)" />
        <circle cx="160" cy="55" r="8" fill="var(--accent)" opacity="0.5" />
        <circle cx="110" cy="35" r="4" fill="var(--primary)" opacity="0.6" />
        <circle cx="210" cy="75" r="4" fill="var(--primary)" opacity="0.6" />
      </svg>
    );
  }
  return (
    <svg className="category-svg-pattern" viewBox="0 0 320 110" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="160" cy="55" r="45" fill="none" stroke="var(--text)" strokeWidth="1.5" opacity="0.2" />
      <circle cx="160" cy="55" r="25" fill="none" stroke="var(--success)" strokeWidth="1.2" strokeDasharray="3 3" opacity="0.35" />
      <circle cx="160" cy="55" r="6" fill="var(--success)" opacity="0.5" />
      <path d="M 120 20 L 135 20 M 120 20 L 120 35" stroke="var(--text)" strokeWidth="1.5" opacity="0.4" />
      <path d="M 200 20 L 185 20 M 200 20 L 200 35" stroke="var(--text)" strokeWidth="1.5" opacity="0.4" />
      <path d="M 120 90 L 135 90 M 120 90 L 120 75" stroke="var(--text)" strokeWidth="1.5" opacity="0.4" />
      <path d="M 200 90 L 185 90 M 200 90 L 200 75" stroke="var(--text)" strokeWidth="1.5" opacity="0.4" />
    </svg>
  );
}

function AnimatedNumber({ value, duration = 1200, prefix = '', suffix = '' }) {
  const [displayValue, setDisplayValue] = useState(0);

  useEffect(() => {
    let target = typeof value === 'number' ? value : parseFloat(value) || 0;
    let startTime = null;
    let frameId;

    const step = (timestamp) => {
      if (!startTime) startTime = timestamp;
      const progress = Math.min((timestamp - startTime) / duration, 1);
      const ease = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
      const current = Math.floor(ease * target);
      setDisplayValue(current);
      if (progress < 1) {
        frameId = requestAnimationFrame(step);
      } else {
        setDisplayValue(target);
      }
    };

    frameId = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frameId);
  }, [value, duration]);

  return <span>{prefix}{displayValue.toLocaleString()}{suffix}</span>;
}

const ChartTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">{payload[0].payload.title}</div>
      <div className="chart-tooltip-impact">{payload[0].payload.impact} impact</div>
    </div>
  );
};

export default function Dashboard() {
  const { authFetch } = useAuth();
  const [topic, setTopic]           = useState(() => sessionStorage.getItem('dash_topic') || '');
  const [suggestions, setSuggestions] = useState([]);
  const discoverControllerRef = useRef(null);
  const [showSug, setShowSug]       = useState(false);
  const [results, setResults]       = useState(() => {try { return JSON.parse(sessionStorage.getItem('dash_results') || '[]'); } catch { return []; }});
  const [relatedPapers, setRelatedPapers] = useState(() => {try { return JSON.parse(sessionStorage.getItem('dash_relatedPapers') || '[]'); } catch { return []; }});
  const [loading, setLoading]       = useState(false);
  const [papersLoading, setPapersLoading] = useState(false);
  const [activeCategory, setActiveCategory] = useState(null);
  const [categoryPapers, setCategoryPapers] = useState([]);
  const [catLoading, setCatLoading] = useState(false);
  const [error, setError] = useState('');
  const [hasSearched, setHasSearched] = useState(() => sessionStorage.getItem('dash_hasSearched') === 'true');
  const [recentSurveys, setRecentSurveys] = useState([]);
  const [loadingRecent, setLoadingRecent] = useState(false);
  useEffect(() => {
    sessionStorage.setItem('dash_topic', topic);
    sessionStorage.setItem('dash_results', JSON.stringify(results));
    sessionStorage.setItem('dash_relatedPapers', JSON.stringify(relatedPapers));
    sessionStorage.setItem('dash_hasSearched', String(hasSearched));
  }, [topic, results, relatedPapers, hasSearched]);
  
  const debounce = useRef(null);
  const inputWrap = useRef(null);
  const navigate = useNavigate();

  const handleInputChange = useCallback((val) => {
    setTopic(val);
    clearTimeout(debounce.current);
    if (!val.trim()) { setSuggestions([]); setShowSug(false); return; }
    debounce.current = setTimeout(() => {
      const f = SUGGESTIONS.filter(s => s.includes(val.toLowerCase())).slice(0, 6);
      setSuggestions(f);
      setShowSug(f.length > 0);
    }, 180);
  }, []);

  const discover = async (q = topic) => {
    if (!q.trim()) return;
    setTopic(q); setShowSug(false); setLoading(true); setPapersLoading(true); setRelatedPapers([]); setError(''); setHasSearched(true);
    try {
      const [topicRes, paperRes] = await Promise.all([
        authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/topics?intent=${encodeURIComponent(q)}`),
        authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature?query=${encodeURIComponent(q)}&limit=6`),
      ]);
      
      if (topicRes.status === 429 || paperRes.status === 429 || topicRes.status === 503 || paperRes.status === 503) {
        if (topicRes.status === 503) {
          try {
            const data = await topicRes.json();
            if (data?.detail?.verification_unavailable) {
              setError('Verification temporarily unavailable, please try again shortly.');
              setResults([]);
              setRelatedPapers([]);
              return;
            }
          } catch(e) {}
        }
        if (paperRes.status === 503) {
          try {
            const data = await paperRes.json();
            if (data?.detail?.verification_unavailable) {
              setError('Verification temporarily unavailable, please try again shortly.');
              setResults([]);
              setRelatedPapers([]);
              return;
            }
          } catch(e) {}
        }
        
        setError('Rate limit exceeded. Please wait a minute before trying again.');
        return;
      }
      if (!topicRes.ok) {
        const topicData = await topicRes.json().catch(() => ({}));
        setError(topicData.detail || 'Failed to discover topics. Please try again.');
        return;
      }
      if (!paperRes.ok) {
        setError('Failed to fetch literature data. Please try again.');
        return;
      }

      const topicData = await topicRes.json();
      
      if (topicData.coherence_check === 'failed') {
        setError(`"${q}" doesn't look like a research topic. Try a specific field or subject area.`);
        setResults([]);
        setRelatedPapers([]);
        return;
      }

      const paperData = await paperRes.json();
      setResults(topicData.data || []);
      setRelatedPapers(paperData.data || []);
    } catch (e) {
      console.error(e);
      setError('Network error. Please try again.');
    }
    finally { setLoading(false); setPapersLoading(false); }
  };

  const openCategory = async (cat) => {
    setActiveCategory(cat); setCategoryPapers([]); setCatLoading(true);
    discover(cat.query);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/arxiv/feed?category=${cat.arxiv}&limit=9`);
      const data = await res.json();
      setCategoryPapers(data.data || []);
    } catch (e) { console.error(e); }
    finally { setCatLoading(false); }
  };

  useEffect(() => {
    const h = (e) => { if (!inputWrap.current?.contains(e.target)) setShowSug(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  useEffect(() => {
    const fetchRecentSurveys = async () => {
      setLoadingRecent(true);
      try {
        const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature/list`);
        if (res.ok) {
          const data = await res.json();
          setRecentSurveys((data.data || []).slice(0, 3));
        }
      } catch(e) {}
      setLoadingRecent(false);
    };
    fetchRecentSurveys();
  }, [authFetch]);

  const deleteRecentSurvey = async (query, e) => {
    e.stopPropagation();
    if (!window.confirm(`Are you sure you want to delete the survey "${query}"?`)) return;
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature/delete/${encodeURIComponent(query)}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        setRecentSurveys(prev => prev.filter(s => s.query !== query));
      }
    } catch (err) {}
  };

  const chartData = results.map(t => ({ ...t, score: impactScore(t.impact) }));

  return (
    <div className="animate-fade-in">
      {/* Header */}
      <div className="dashboard-header">
        <h1 className="dashboard-title">Research Discovery</h1>
        <p className="dashboard-subtitle">Explore trending research areas and discover high-impact topics.</p>
      </div>

      {/* Stat Metrics Summary Bar */}
      <div className="dashboard-stats-bar animate-fade-in">
        <div className="dashboard-stat-card">
          <span className="dashboard-stat-label"><BookOpen size={13} /> Indexed Topics</span>
          <span className="dashboard-stat-value"><AnimatedNumber value={14280} suffix="+" /></span>
          <span className="dashboard-stat-desc">Curated across arXiv domains</span>
        </div>
        <div className="dashboard-stat-card">
          <span className="dashboard-stat-label"><Layers size={13} /> Active Fields</span>
          <span className="dashboard-stat-value"><AnimatedNumber value={6} /></span>
          <span className="dashboard-stat-desc">Live research categories</span>
        </div>
        <div className="dashboard-stat-card">
          <span className="dashboard-stat-label"><FileText size={13} /> Surveys Saved</span>
          <span className="dashboard-stat-value"><AnimatedNumber value={recentSurveys.length || 3} /></span>
          <span className="dashboard-stat-desc">Personal literature surveys</span>
        </div>
        <div className="dashboard-stat-card">
          <span className="dashboard-stat-label"><Award size={13} /> Max Impact</span>
          <span className="dashboard-stat-value"><AnimatedNumber value={4} suffix=".0" /></span>
          <span className="dashboard-stat-desc">High-priority impact score</span>
        </div>
      </div>

      {/* Search Bar */}
      <div className="dashboard-search-card">
        <div className="dashboard-search-row">
          <div ref={inputWrap} className="dashboard-search-input-wrap">
            <Search size={15} className="dashboard-search-icon" />
            <input
              className="dashboard-search-input"
              placeholder="e.g. machine learning in healthcare..."
              value={topic}
              onChange={e => handleInputChange(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') discover(); if (e.key === 'Escape') setShowSug(false); }}
              onFocus={() => suggestions.length && setShowSug(true)}
            />
            {showSug && (
              <div className="dashboard-suggestions-menu">
                {suggestions.map((s, i) => (
                  <div key={i} onMouseDown={() => discover(s)} className="dashboard-suggestion-item">
                    <Search size={12} />
                    {s}
                  </div>
                ))}
              </div>
            )}
          </div>
          <InteractiveHoverButton 
            text={loading ? "Discovering" : "Discover"}
            loading={loading}
            onClick={() => discover()} 
            disabled={loading} 
          />
        </div>
        {error && (
          <div className="dashboard-error-banner">
            <X size={15} /> {error}
          </div>
        )}
      </div>

      {loading && (
        <div className="dashboard-loading-container">
          <h2 className="dashboard-loading-title">
            <Sparkles size={18} className="dashboard-loading-icon" /> Finding relevant literature...
          </h2>
          <SkeletonList count={4} />
        </div>
      )}

      {/* Welcome / Empty State */}
      {!loading && results.length === 0 && !error && !activeCategory && (
        <div className="dashboard-welcome-container animate-fade-in">
          <div className="dashboard-welcome-grid">
            
            {/* Trending Research Domains */}
            <div>
              <h3 className="dashboard-section-title">
                <TrendingUp size={16} className="dashboard-title-icon primary" /> Trending Research Domains
              </h3>
              <div className="dashboard-item-list">
                {[
                  { title: "Machine Learning in Healthcare", tag: "AI/Medical", trend: "+12%" },
                  { title: "Quantum Computing Algorithms", tag: "Physics/CS", trend: "+8%" },
                  { title: "LLM Alignment and Safety", tag: "AI/Ethics", trend: "+24%" },
                  { title: "CRISPR Gene Editing", tag: "Bio/Genetics", trend: "+18%" }
                ].map((item) => (
                  <div key={item.title} onClick={() => discover(item.title)} className="dashboard-trending-card animate-card-in">
                    <div>
                      <div className="dashboard-trending-title">{item.title}</div>
                      <div className="dashboard-trending-tag">{item.tag}</div>
                    </div>
                    <span className="dashboard-trending-badge">{item.trend}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Recent Surveys */}
            <div>
              <h3 className="dashboard-section-title">
                <FileText size={16} className="dashboard-title-icon accent" /> Recent Surveys
              </h3>
              <div className="dashboard-item-list">
                {loadingRecent ? (
                  <SkeletonList count={3} />
                ) : recentSurveys.length === 0 ? (
                  <div className="dashboard-empty-surveys">
                    No recent surveys found.
                  </div>
                ) : (
                  recentSurveys.map((survey, i) => (
                    <div key={i} className="dashboard-survey-card animate-card-in">
                      <div className="dashboard-survey-header">
                        <span className="dashboard-survey-label">Literature Survey</span>
                        <button 
                          className="btn btn-icon dashboard-delete-btn"
                          onClick={(e) => deleteRecentSurvey(survey.query, e)}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                      <div className="dashboard-survey-query">{survey.query}</div>
                      <div className="dashboard-survey-meta">{survey.papers?.length || 0} papers saved</div>
                      <button 
                        className="btn btn-ghost dashboard-survey-link" 
                        onClick={() => navigate('/literature-survey')}
                      >
                        View in Surveys <ArrowRight size={12} />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>

          </div>
        </div>
      )}

      {/* AI Discovery Results */}
      {!loading && results.length > 0 && (
        <div className="dashboard-results-container">
          <div className="dashboard-results-banner">
            Topic suggestions are ready. Related research papers are shown below so you can continue from discovery into reading.
          </div>
          <div className="dashboard-results-grid">
            {results.map((t, i) => (
              <div key={i} className="dashboard-topic-card animate-slide-up" onClick={() => navigate('/literature-survey')}>
                <div className="dashboard-topic-header">
                  <span className="dashboard-topic-num">#{i + 1}</span>
                  <span className="dashboard-topic-badge">{t.impact}</span>
                </div>
                <p className="dashboard-topic-title">{t.title}</p>
                <a href={`https://scholar.google.com/scholar?q=${encodeURIComponent(t.title)}`} target="_blank" rel="noreferrer" className="dashboard-topic-link">
                  Explore <ExternalLink size={11} />
                </a>
              </div>
            ))}
          </div>

          <div className="dashboard-related-box">
            <div className="dashboard-related-header">
              <div className="dashboard-related-title-wrap">
                <FileText size={17} className="dashboard-title-icon accent" />
                <span>Papers related to "{topic}"</span>
              </div>
              <button className="btn btn-ghost" onClick={() => window.location.href = '/literature-survey'}>
                Open Literature Survey <ArrowUpRight size={14} />
              </button>
            </div>
            {papersLoading && <p className="text-muted"><Spinner size={16} /> Loading related papers...</p>}
            {!papersLoading && relatedPapers.length === 0 && (
              <p className="text-muted">No related papers were found for this search.</p>
            )}
            {!papersLoading && relatedPapers.length > 0 && (
              <div className="dashboard-papers-list">
                {relatedPapers.map((paper, i) => (
                  <a
                    key={paper.id || `${paper.title}-${i}`}
                    href={paper.url || `https://scholar.google.com/scholar?q=${encodeURIComponent(paper.title)}`}
                    target="_blank"
                    rel="noreferrer"
                    className="dashboard-paper-item animate-slide-up"
                  >
                    <span>
                      <strong className="dashboard-paper-title">{paper.title}</strong>
                      <span className="dashboard-paper-authors">{paper.authors || paper.year || 'Research paper'}</span>
                    </span>
                    <ExternalLink size={14} className="text-subtle" />
                  </a>
                ))}
              </div>
            )}
          </div>

          <div className="dashboard-analytics-grid">
            <div className="dashboard-analytics-card">
              <div className="dashboard-section-title">
                <TrendingUp size={16} className="dashboard-title-icon primary" /> Impact Overview
              </div>
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={chartData} barCategoryGap="35%">
                  <XAxis dataKey="title" tick={{ fontSize: 9.5, fill: 'var(--text-subtle)' }} tickLine={false} axisLine={false} interval={0} angle={-12} textAnchor="end" height={55} />
                  <YAxis tick={{ fontSize: 9.5, fill: 'var(--text-subtle)' }} tickLine={false} axisLine={false} domain={[0, 4]} ticks={[1,2,3,4]} width={20} />
                  <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(43,94,168,0.06)' }} />
                  <Bar dataKey="score" radius={[5,5,0,0]}>
                    {chartData.map((e, i) => <Cell key={i} fill={impactColor(e.impact)} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div className="dashboard-analytics-card">
              <div className="dashboard-section-title">
                <TrendingUp size={16} className="dashboard-title-icon accent" /> Suggested Fields
              </div>
              {results.map((t, i) => (
                <a key={i} href={`https://scholar.google.com/scholar?q=${encodeURIComponent(t.title)}`} target="_blank" rel="noreferrer" className="dashboard-suggested-item">
                  <div className="dashboard-suggested-left">
                    <div className="dashboard-impact-dot" style={{ background: impactColor(t.impact) }} />
                    <span className="dashboard-suggested-title">{t.title}</span>
                  </div>
                  <ArrowUpRight size={13} className="text-subtle" />
                </a>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Category Grid Section */}
      <div className="dashboard-categories-section">
        <h2 className="dashboard-section-heading">Browse by Field</h2>
        <div className="dashboard-category-grid">
          {CATEGORIES.map((cat) => (
            <div key={cat.arxiv} className="dashboard-category-card" onClick={() => openCategory(cat)}>
              <div className="dashboard-category-pattern-wrap">
                <CategoryPattern id={cat.patternId} />
                <div className="dashboard-category-icon-badge">
                  <cat.Icon size={18} />
                </div>
              </div>
              <div className="dashboard-category-body">
                <div className="dashboard-category-header-row">
                  <h3 className="dashboard-category-title">{cat.title}</h3>
                  <span className="dashboard-category-code">{cat.arxiv}</span>
                </div>
                <p className="dashboard-category-subtitle">{cat.subtitle}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Category Paper Drawer */}
      {activeCategory && (
        <div className="dashboard-drawer animate-fade-in">
          <div className="dashboard-drawer-header">
            <div className="dashboard-drawer-title-wrap">
              <div className="dashboard-drawer-dot" />
              <h2 className="dashboard-drawer-title">Latest in {activeCategory.title}</h2>
            </div>
            <button className="btn btn-ghost" onClick={() => { setActiveCategory(null); setCategoryPapers([]); }}>
              <X size={14} /> Close
            </button>
          </div>

          {catLoading && (
            <div className="dashboard-drawer-loading">
              <Spinner size={20} /> Loading papers...
            </div>
          )}

          {!catLoading && categoryPapers.length === 0 && (
            <div className="empty-state">
              No recent papers found for this category at the moment.
            </div>
          )}

          <div className="dashboard-drawer-grid">
            {categoryPapers.map((p, i) => (
              <div key={i} className="dashboard-drawer-card animate-slide-up">
                <p className="dashboard-drawer-paper-title">{p.title}</p>
                <p className="dashboard-drawer-paper-authors">{p.authors}</p>
                <p className="dashboard-drawer-paper-abstract">
                  {p.abstract ? p.abstract.substring(0, 150) + '...' : ''}
                </p>
                <div className="dashboard-drawer-actions">
                  {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="btn btn-ghost text-xs"><ExternalLink size={12} /> Abstract</a>}
                  {p.pdf_url && <a href={p.pdf_url} target="_blank" rel="noreferrer" className="btn btn-ghost text-xs"><FileText size={12} /> PDF</a>}
                  <a href={`https://scholar.google.com/scholar?q=${encodeURIComponent(p.title)}`} target="_blank" rel="noreferrer" className="btn btn-ghost text-xs"><Search size={12} /> Scholar</a>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {!results.length && !loading && !activeCategory && hasSearched && !error && (
        <div className="dashboard-no-results">
          <Search size={32} className="dashboard-no-results-icon" />
          <h3 className="dashboard-no-results-title">No results found for "{topic}"</h3>
          <p className="dashboard-no-results-desc">Try a different search term or select a category below.</p>
        </div>
      )}
    </div>
  );
}
