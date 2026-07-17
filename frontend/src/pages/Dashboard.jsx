import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Search, TrendingUp, ArrowUpRight, ExternalLink, FileText, X, Sparkles, Trash2, ArrowRight } from 'lucide-react';
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
  { title: 'Artificial Intelligence', subtitle: 'LLMs, agents & reasoning',    arxiv: 'cs.AI',    query: 'artificial intelligence', image: 'https://images.unsplash.com/photo-1677442135703-1787eea5ce01?w=600&q=80', color: '#0057ff' },
  { title: 'Cybersecurity',           subtitle: 'Threat detection & privacy',   arxiv: 'cs.CR',    query: 'cybersecurity',           image: 'https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=600&q=80', color: '#ef4444' },
  { title: 'Machine Learning',        subtitle: 'Models, training & evaluation', arxiv: 'cs.LG',   query: 'machine learning',        image: 'https://images.unsplash.com/photo-1517694712202-14dd9538aa97?w=600&q=80', color: '#ff4d00' },
  { title: 'Data Science',            subtitle: 'Analytics & big data',          arxiv: 'cs.DS',   query: 'data science',            image: 'https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=600&q=80', color: '#10b981' },
  { title: 'Quantum Computing',       subtitle: 'Qubits & algorithms',           arxiv: 'quant-ph', query: 'quantum computing',       image: 'https://images.unsplash.com/photo-1635070041078-e363dbe005cb?w=600&q=80', color: '#f59e0b' },
  { title: 'Computer Vision',         subtitle: 'Images, video & perception',    arxiv: 'cs.CV',   query: 'computer vision',         image: 'https://images.unsplash.com/photo-1576086213369-97a306d36557?w=600&q=80', color: '#f5333f' },
];

const impactScore = (i) => ({ 'Very High': 4, 'High': 3, 'Medium': 2, 'Low': 1 }[i] || 1);
const impactColor = (i) => ({ 'Very High': '#0057ff', 'High': '#ff4d00', 'Medium': '#00a36c', 'Low': '#ffb000' }[i] || '#0057ff');

const ChartTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', padding: 'var(--space-3) var(--space-3)', fontSize: 'var(--fs-sm)' }}>
      <div style={{ fontWeight: 600, color: 'var(--text)', marginBottom: 'var(--space-1)' }}>{payload[0].payload.title}</div>
      <div style={{ color: 'var(--text-muted)' }}>{payload[0].payload.impact} impact</div>
    </div>
  );
};

export default function Dashboard() {
  const { authFetch } = useAuth();
  const [topic, setTopic]           = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [showSug, setShowSug]       = useState(false);
  const [results, setResults]       = useState([]);
  const [relatedPapers, setRelatedPapers] = useState([]);
  const [loading, setLoading]       = useState(false);
  const [papersLoading, setPapersLoading] = useState(false);
  const [activeCategory, setActiveCategory] = useState(null);
  const [categoryPapers, setCategoryPapers] = useState([]);
  const [catLoading, setCatLoading] = useState(false);
  const [error, setError] = useState('');
  const [hasSearched, setHasSearched] = useState(false);
  const [recentSurveys, setRecentSurveys] = useState([]);
  const [loadingRecent, setLoadingRecent] = useState(false);
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
      <div style={{ marginBottom: 'var(--space-6)' }}>
        <h1>Research Discovery</h1>
        <p className="text-muted">Explore trending research areas and discover high-impact topics.</p>
      </div>

      {/* Search */}
      <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-5)', marginBottom: 'var(--space-6)' }}>
        <div style={{ display: 'flex', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
          <div ref={inputWrap} style={{ position: 'relative', flex: 1, minWidth: '220px' }}>
            <Search size={15} style={{ position: 'absolute', left: '1rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-subtle)', pointerEvents: 'none' }} />
            <input
              placeholder="e.g. machine learning in healthcare..."
              value={topic}
              onChange={e => handleInputChange(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') discover(); if (e.key === 'Escape') setShowSug(false); }}
              onFocus={() => suggestions.length && setShowSug(true)}
              style={{ paddingLeft: 'var(--space-7)' }}
            />
            {showSug && (
              <div style={{ position: 'absolute', top: 'calc(100% + 5px)', left: 0, right: 0, background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', zIndex: 50, overflow: 'hidden', boxShadow: 'var(--shadow-lg)' }}>
                {suggestions.map((s, i) => (
                  <div key={i} onMouseDown={() => discover(s)}
                    style={{ padding: 'var(--space-2) var(--space-4)', cursor: 'pointer', fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', borderBottom: i < suggestions.length - 1 ? '1px solid var(--border)' : 'none', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}
                    onMouseEnter={e => { e.currentTarget.style.background = 'var(--primary-light)'; e.currentTarget.style.color = 'var(--primary)'; }}
                    onMouseLeave={e => { e.currentTarget.style.background = ''; e.currentTarget.style.color = 'var(--text-muted)'; }}
                  >
                    <Search size={12} />{s}
                  </div>
                ))}
              </div>
            )}
          </div>
          <InteractiveHoverButton 
            text={loading ? "Discovering..." : "Discover"} 
            onClick={() => discover()} 
            disabled={loading} 
          />
        </div>
        {error && (
          <div style={{ marginTop: 'var(--space-4)', padding: 'var(--space-3) var(--space-4)', background: 'rgba(229,28,35,0.08)', border: '1px solid rgba(229,28,35,0.2)', borderRadius: 'var(--radius-md)', color: 'var(--danger)', fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <X size={15} /> {error}
          </div>
        )}
      </div>

      {loading && (
        <div style={{ marginTop: 'var(--space-6)' }}>
          <h2 style={{ fontSize: 'var(--fs-md)', fontWeight: 600, margin: '0 0 var(--space-4)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <Sparkles size={18} style={{ color: 'var(--primary)' }} /> Finding relevant literature...
          </h2>
          <SkeletonList count={4} />
        </div>
      )}

      {/* Preloaded Neat Data (Empty State) */}
      {!loading && results.length === 0 && !error && !activeCategory && (
        <div className="dashboard-welcome animate-fade-in" style={{ marginTop: 'var(--space-4)', marginBottom: 'var(--space-7)' }}>
          <div className="grid-cols-2" style={{ display: 'grid', gap: 'var(--space-6)' }}>
            
            {/* Trending Research Domains */}
            <div>
              <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, marginBottom: 'var(--space-4)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)', color: 'var(--text)' }}>
                <TrendingUp size={16} style={{ color: 'var(--primary)' }} /> Trending Research Domains
              </h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
                {[
                  { title: "Machine Learning in Healthcare", tag: "AI/Medical", trend: "+12%" },
                  { title: "Quantum Computing Algorithms", tag: "Physics/CS", trend: "+8%" },
                  { title: "LLM Alignment and Safety", tag: "AI/Ethics", trend: "+24%" },
                  { title: "CRISPR Gene Editing", tag: "Bio/Genetics", trend: "+18%" }
                ].map((item, i) => (
                  <div key={item.title} onClick={() => discover(item.title)} className="solid-card animate-card-in" style={{ padding: 'var(--space-4)', cursor: 'pointer', borderRadius: '12px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', animationDelay: `${i * 0.05}s` }}>
                    <div>
                      <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>{item.title}</div>
                      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-subtle)', marginTop: 'var(--space-1)' }}>{item.tag}</div>
                    </div>
                    <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, color: 'var(--success)', background: 'rgba(39, 201, 63, 0.12)', padding: 'var(--space-1) var(--space-3)', borderRadius: '999px' }}>{item.trend}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Recent Activity */}
            <div>
              <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, marginBottom: 'var(--space-4)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)', color: 'var(--text)' }}>
                <FileText size={16} style={{ color: 'var(--accent)' }} /> Recent Surveys
              </h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
                {loadingRecent ? (
                  <SkeletonList count={3} />
                ) : recentSurveys.length === 0 ? (
                  <div className="solid-card" style={{ padding: 'var(--space-4)', borderRadius: '12px', textAlign: 'center', color: 'var(--text-subtle)' }}>
                    No recent surveys found.
                  </div>
                ) : (
                  recentSurveys.map((survey, i) => (
                    <div key={i} className="solid-card animate-card-in" style={{ padding: 'var(--space-4)', borderRadius: '12px', display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', animationDelay: `${i * 0.1}s` }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: 'var(--text-subtle)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Literature Survey</span>
                        <button 
                          className="btn btn-icon"
                          onClick={(e) => deleteRecentSurvey(survey.query, e)}
                          style={{ color: 'var(--danger)', padding: 'var(--space-1)', margin: '-var(--space-1)' }}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                      <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>{survey.query}</div>
                      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)' }}>{survey.papers?.length || 0} papers saved</div>
                      <button 
                        className="btn btn-ghost" 
                        onClick={() => navigate('/literature-survey')}
                        style={{ alignSelf: 'flex-start', padding: 0, marginTop: 'var(--space-1)', color: 'var(--primary)', fontSize: 'var(--fs-sm)' }}
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

      {/* AI Results */}
      {!loading && results.length > 0 && (
        <div style={{ marginBottom: 'var(--space-6)' }}>
          <div style={{ background: 'var(--accent-light)', border: '1px solid rgba(255,77,0,0.2)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-4) var(--space-4)', marginBottom: 'var(--space-4)', color: 'var(--text)', fontSize: 'var(--fs-sm)', fontWeight: 700 }}>
            Topic suggestions are ready. Related research papers are shown below so you can continue from discovery into reading.
          </div>
          <div className="grid-cols-2" style={{ display: 'grid', gap: 'var(--space-4)', marginBottom: 'var(--space-4)' }}>
            {results.map((t, i) => (
              <div key={i} className="stat-card animate-slide-up" style={{ animationDelay: `${i * 0.07}s`, cursor: 'pointer' }} onClick={() => navigate('/literature-survey')}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 'var(--space-2)' }}>
                  <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-subtle)' }}>#{i + 1}</span>
                  <span style={{ background: 'var(--primary-light)', color: 'var(--primary)', padding: 'var(--space-1) var(--space-2)', borderRadius: '999px', fontSize: 'var(--fs-xs)', fontWeight: 600 }}>{t.impact}</span>
                </div>
                <p style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--text)', margin: '0 0 var(--space-3)', lineHeight: 1.4 }}>{t.title}</p>
                <a href={`https://scholar.google.com/scholar?q=${encodeURIComponent(t.title)}`} target="_blank" rel="noreferrer"
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-1)', fontSize: 'var(--fs-xs)', color: 'var(--primary)', textDecoration: 'none', fontWeight: 500 }}>
                  Explore <ExternalLink size={11} />
                </a>
              </div>
            ))}
          </div>

          <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-5)', marginBottom: 'var(--space-4)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--space-4)', marginBottom: 'var(--space-4)', flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                <FileText size={17} color="var(--accent)" />
                <span style={{ fontWeight: 750 }}>Papers related to "{topic}"</span>
              </div>
              <button className="btn btn-ghost" onClick={() => window.location.href = '/literature-survey'}>
                Open Literature Survey <ArrowUpRight size={14} />
              </button>
            </div>
            {papersLoading && <p style={{ margin: 0, color: 'var(--text-muted)' }}><Spinner size={16} /> Loading related papers...</p>}
            {!papersLoading && relatedPapers.length === 0 && (
              <p style={{ margin: 0, color: 'var(--text-muted)' }}>No related papers were found for this search.</p>
            )}
            {!papersLoading && relatedPapers.length > 0 && (
              <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
                {relatedPapers.map((paper, i) => (
                  <a
                    key={paper.id || `${paper.title}-${i}`}
                    href={paper.url || `https://scholar.google.com/scholar?q=${encodeURIComponent(paper.title)}`}
                    target="_blank"
                    rel="noreferrer"
                    className="animate-slide-up"
                    style={{ animationDelay: `${i * 0.04}s`, display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 'var(--space-4)', padding: 'var(--space-3) var(--space-4)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', textDecoration: 'none', color: 'var(--text)', background: '#fff' }}
                  >
                    <span style={{ minWidth: 0 }}>
                      <strong style={{ display: 'block', fontSize: 'var(--fs-sm)', lineHeight: 1.4 }}>{paper.title}</strong>
                      <span style={{ display: 'block', color: 'var(--text-muted)', fontSize: 'var(--fs-xs)', marginTop: 'var(--space-1)' }}>{paper.authors || paper.year || 'Research paper'}</span>
                    </span>
                    <ExternalLink size={14} color="var(--text-subtle)" style={{ flexShrink: 0, marginTop: 'var(--space-1)' }} />
                  </a>
                ))}
              </div>
            )}
          </div>

          <div className="grid-cols-2" style={{ display: 'grid', gap: 'var(--space-4)' }}>
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-5)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}>
                <TrendingUp size={16} color="var(--primary)" />
                <span style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}>Impact Overview</span>
              </div>
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={chartData} barCategoryGap="35%">
                  <XAxis dataKey="title" tick={{ fontSize: 9.5, fill: 'var(--text-subtle)' }} tickLine={false} axisLine={false} interval={0} angle={-12} textAnchor="end" height={55} />
                  <YAxis tick={{ fontSize: 9.5, fill: 'var(--text-subtle)' }} tickLine={false} axisLine={false} domain={[0, 4]} ticks={[1,2,3,4]} width={20} />
                  <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(0,87,255,0.05)' }} />
                  <Bar dataKey="score" radius={[5,5,0,0]}>
                    {chartData.map((e, i) => <Cell key={i} fill={impactColor(e.impact)} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-5)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}>
                <TrendingUp size={16} color="var(--accent)" />
                <span style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}>Suggested Fields</span>
              </div>
              {results.map((t, i) => (
                <a key={i} href={`https://scholar.google.com/scholar?q=${encodeURIComponent(t.title)}`} target="_blank" rel="noreferrer"
                  style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: 'var(--space-3) var(--space-3)', marginBottom: 'var(--space-2)', background: 'var(--bg-elevated)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', transition: 'var(--transition)' }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(0,87,255,0.28)'; e.currentTarget.style.background = 'var(--bg-input)'; }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.background = 'var(--bg-elevated)'; }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                    <div style={{ width: 7, height: 7, borderRadius: '50%', background: impactColor(t.impact), flexShrink: 0 }} />
                    <span style={{ fontWeight: 500, fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>{t.title}</span>
                  </div>
                  <ArrowUpRight size={13} color="var(--text-subtle)" />
                </a>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Category cards */}
      <div>
        <h2 style={{ fontSize: 'var(--fs-md)', fontWeight: 600, marginBottom: 'var(--space-4)', color: 'var(--text)' }}>Browse by Field</h2>
        <div className="grid-cols-3" style={{ display: 'grid', gap: 'var(--space-3)' }}>
          {CATEGORIES.map((cat, i) => (
            <div key={i} className="animate-slide-up" onClick={() => openCategory(cat)}
              style={{ animationDelay: `${i * 0.05}s`, position: 'relative', borderRadius: 'var(--radius-lg)', overflow: 'hidden', cursor: 'pointer', border: '1px solid var(--border)', aspectRatio: '16/9', transition: 'transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease' }}
              onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-3px)'; e.currentTarget.style.boxShadow = `0 10px 32px ${cat.color}28`; e.currentTarget.style.borderColor = cat.color + '55'; }}
              onMouseLeave={e => { e.currentTarget.style.transform = ''; e.currentTarget.style.boxShadow = ''; e.currentTarget.style.borderColor = 'var(--border)'; }}
            >
              <img src={cat.image} alt={cat.title} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} onError={e => { e.target.style.display = 'none'; }} />
              <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to top, rgba(0,0,0,0.82) 0%, rgba(0,0,0,0.25) 55%, transparent 100%)' }} />
              <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: '3px', background: cat.color }} />
              <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, padding: 'var(--space-3)' }}>
                <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)', color: '#fff', marginBottom: 'var(--space-1)' }}>{cat.title}</div>
                <div style={{ fontSize: 'var(--fs-xs)', color: 'rgba(255,255,255,0.6)' }}>{cat.subtitle}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Category paper drawer */}
      {activeCategory && (
        <div className="animate-fade-in" style={{ marginTop: 'var(--space-6)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--space-4)', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
              <div style={{ width: 10, height: 10, borderRadius: '50%', background: activeCategory.color }} />
              <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600 }}>Latest in {activeCategory.title}</h2>
            </div>
            <button className="btn btn-ghost" style={{ fontSize: 'var(--fs-sm)' }} onClick={() => { setActiveCategory(null); setCategoryPapers([]); }}>
              <X size={14} /> Close
            </button>
          </div>

          {catLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', padding: 'var(--space-5) 0' }}>
              <Spinner size={20} /> Loading papers...
            </div>
          )}

          {!catLoading && categoryPapers.length === 0 && (
            <div className="empty-state">
              No recent papers found for this category at the moment.
            </div>
          )}

          <div className="grid-cols-3" style={{ display: 'grid', gap: 'var(--space-3)' }}>
            {categoryPapers.map((p, i) => (
              <div key={i} className="animate-slide-up" style={{ animationDelay: `${i * 0.04}s`, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-4)', transition: 'transform 0.2s ease, border-color 0.2s ease' }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = activeCategory.color + '45'; e.currentTarget.style.transform = 'translateY(-2px)'; }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.transform = ''; }}
              >
                <p style={{ margin: '0 0 var(--space-2)', fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--text)', lineHeight: 1.45 }}>{p.title}</p>
                <p style={{ margin: '0 0 var(--space-2)', fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>{p.authors}</p>
                <p style={{ margin: '0 0 var(--space-3)', fontSize: 'var(--fs-sm)', color: 'var(--text-subtle)', lineHeight: 1.55 }}>
                  {p.abstract ? p.abstract.substring(0, 150) + '...' : ''}
                </p>
                <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                  {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="btn btn-ghost" style={{ fontSize: 'var(--fs-xs)', padding: 'var(--space-1) var(--space-2)', textDecoration: 'none' }}><ExternalLink size={12} /> Abstract</a>}
                  {p.pdf_url && <a href={p.pdf_url} target="_blank" rel="noreferrer" className="btn btn-ghost" style={{ fontSize: 'var(--fs-xs)', padding: 'var(--space-1) var(--space-2)', textDecoration: 'none' }}><FileText size={12} /> PDF</a>}
                  <a href={`https://scholar.google.com/scholar?q=${encodeURIComponent(p.title)}`} target="_blank" rel="noreferrer" className="btn btn-ghost" style={{ fontSize: 'var(--fs-xs)', padding: 'var(--space-1) var(--space-2)', textDecoration: 'none' }}><Search size={12} /> Scholar</a>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {!results.length && !loading && !activeCategory && hasSearched && !error && (
        <div style={{ marginTop: 'var(--space-6)', textAlign: 'center', padding: 'var(--space-7) var(--space-5)', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)' }}>
          <Search size={32} style={{ color: 'var(--text-subtle)', marginBottom: 'var(--space-4)', opacity: 0.5 }} />
          <h3 style={{ margin: '0 0 var(--space-2)', fontSize: 'var(--fs-md)', color: 'var(--text)' }}>No results found for "{topic}"</h3>
          <p style={{ margin: 0, color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>Try a different search term or select a category below.</p>
        </div>
      )}
    </div>
  );
}
