import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Search, TrendingUp, ArrowUpRight, ExternalLink, FileText, X, Trash2, ArrowRight,
  Brain, Shield, Cpu, Database, Atom, Eye, BookOpen, Layers, Square
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Spinner, SkeletonList } from '../components/Loader';
import { useNavigate } from 'react-router-dom';
import {
  validateSearchQuery,
  normalizeSearchQuery,
  isAbortError,
} from '../utils/searchHeuristics';
import './Dashboard.css';

const SUGGESTIONS = [
  'machine learning in healthcare', 'deep learning for NLP', 'computer vision',
  'cybersecurity threat detection', 'quantum computing', 'federated learning',
  'large language models', 'autonomous vehicles', 'reinforcement learning',
  'explainable AI', 'edge computing', 'generative AI', 'drug discovery AI',
  'natural language processing', 'neural architecture search', 'robotics',
];

const CATEGORIES = [
  { title: 'Artificial Intelligence', subtitle: 'LLMs, agents & reasoning', arxiv: 'cs.AI', query: 'artificial intelligence', Icon: Brain },
  { title: 'Cybersecurity', subtitle: 'Threat detection & privacy', arxiv: 'cs.CR', query: 'cybersecurity', Icon: Shield },
  { title: 'Machine Learning', subtitle: 'Models, training & evaluation', arxiv: 'cs.LG', query: 'machine learning', Icon: Cpu },
  { title: 'Data Science', subtitle: 'Analytics & big data', arxiv: 'cs.DS', query: 'data science', Icon: Database },
  { title: 'Quantum Computing', subtitle: 'Qubits & algorithms', arxiv: 'quant-ph', query: 'quantum computing', Icon: Atom },
  { title: 'Computer Vision', subtitle: 'Images, video & perception', arxiv: 'cs.CV', query: 'computer vision', Icon: Eye },
];

const TRENDING = [
  { title: 'Machine Learning in Healthcare', field: 'cs.LG / q-bio', delta: '+12%' },
  { title: 'Quantum Computing Algorithms', field: 'quant-ph / cs.CC', delta: '+8%' },
  { title: 'LLM Alignment and Safety', field: 'cs.AI / cs.CL', delta: '+24%' },
  { title: 'CRISPR Gene Editing', field: 'q-bio.GN', delta: '+18%' },
];

const impactScore = (i) => ({ 'Very High': 4, 'High': 3, 'Medium': 2, 'Low': 1 }[i] || 1);
const impactHint = (i) => ({
  'Very High': 'Top-priority signal — strong survey candidate.',
  'High': 'Strong signal across recent papers.',
  'Medium': 'Emerging theme — validate with related work.',
  'Low': 'Niche angle for a narrower question.',
}[i] || 'Open a survey to dig deeper.');
const RELATED_PAGE_SIZE = 5;

function AnimatedNumber({ value, duration = 900, prefix = '', suffix = '' }) {
  const [displayValue, setDisplayValue] = useState(0);

  useEffect(() => {
    let target = typeof value === 'number' ? value : parseFloat(value) || 0;
    let startTime = null;
    let frameId;

    const step = (timestamp) => {
      if (!startTime) startTime = timestamp;
      const progress = Math.min((timestamp - startTime) / duration, 1);
      const ease = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
      setDisplayValue(Math.floor(ease * target));
      if (progress < 1) frameId = requestAnimationFrame(step);
      else setDisplayValue(target);
    };

    frameId = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frameId);
  }, [value, duration]);

  return <span>{prefix}{displayValue.toLocaleString()}{suffix}</span>;
}

export default function Dashboard() {
  const { authFetch } = useAuth();
  const [topic, setTopic] = useState(() => sessionStorage.getItem('dash_topic') || '');
  const [suggestions, setSuggestions] = useState([]);
  const [showSug, setShowSug] = useState(false);
  const [results, setResults] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem('dash_results') || '[]'); } catch { return []; }
  });
  const [relatedPapers, setRelatedPapers] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem('dash_relatedPapers') || '[]'); } catch { return []; }
  });
  const [visibleRelatedCount, setVisibleRelatedCount] = useState(RELATED_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [papersLoading, setPapersLoading] = useState(false);
  const [activeCategory, setActiveCategory] = useState(null);
  const [categoryPapers, setCategoryPapers] = useState([]);
  const [catLoading, setCatLoading] = useState(false);
  const [error, setError] = useState('');
  const [hasSearched, setHasSearched] = useState(() => sessionStorage.getItem('dash_hasSearched') === 'true');
  const [recentSurveys, setRecentSurveys] = useState([]);
  const [loadingRecent, setLoadingRecent] = useState(false);

  useEffect(() => {
    const id = setTimeout(() => {
      sessionStorage.setItem('dash_topic', topic);
    }, 300);
    return () => clearTimeout(id);
  }, [topic]);

  useEffect(() => {
    sessionStorage.setItem('dash_results', JSON.stringify(results));
  }, [results]);

  useEffect(() => {
    sessionStorage.setItem('dash_relatedPapers', JSON.stringify(relatedPapers));
  }, [relatedPapers]);

  useEffect(() => {
    sessionStorage.setItem('dash_hasSearched', String(hasSearched));
  }, [hasSearched]);

  useEffect(() => {
    setVisibleRelatedCount(RELATED_PAGE_SIZE);
  }, [relatedPapers]);

  const debounce = useRef(null);
  const inputWrap = useRef(null);
  const abortRef = useRef(null);
  const lastQueryRef = useRef('');
  const navigate = useNavigate();

  useEffect(() => () => {
    abortRef.current?.abort();
  }, []);

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

  const stopDiscover = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setLoading(false);
    setPapersLoading(false);
  }, []);

  const clearSearch = useCallback(() => {
    stopDiscover();
    setTopic('');
    setSuggestions([]);
    setShowSug(false);
    setResults([]);
    setRelatedPapers([]);
    setError('');
    setHasSearched(false);
    setActiveCategory(null);
    setCategoryPapers([]);
    lastQueryRef.current = '';
    sessionStorage.removeItem('dash_topic');
    sessionStorage.removeItem('dash_results');
    sessionStorage.removeItem('dash_relatedPapers');
    sessionStorage.removeItem('dash_hasSearched');
  }, [stopDiscover]);

  const discover = async (q = topic) => {
    const check = validateSearchQuery(q);
    if (!check.ok) {
      setError(check.message);
      if (check.code === 'empty') setHasSearched(false);
      return;
    }
    const normalized = normalizeSearchQuery(check.query);
    // Ignore duplicate submits while a matching search is already in flight
    if (loading && lastQueryRef.current === normalized) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    lastQueryRef.current = normalized;
    setTopic(check.query);
    setShowSug(false);
    setLoading(true);
    setPapersLoading(true);
    setRelatedPapers([]);
    setError('');
    setHasSearched(true);
    try {
      const [topicRes, paperRes] = await Promise.all([
        authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/topics?intent=${encodeURIComponent(check.query)}`, {
          signal: controller.signal,
        }),
        authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature?query=${encodeURIComponent(check.query)}&limit=6`, {
          signal: controller.signal,
        }),
      ]);

      if (controller.signal.aborted) return;

      if (topicRes.status === 429 || paperRes.status === 429 || topicRes.status === 503 || paperRes.status === 503) {
        if (topicRes.status === 503 || paperRes.status === 503) {
          try {
            const data = await (topicRes.status === 503 ? topicRes : paperRes).json();
            if (data?.detail?.verification_unavailable) {
              setError('Verification temporarily unavailable, please try again shortly.');
              setResults([]);
              setRelatedPapers([]);
              return;
            }
          } catch (e) {}
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
        setError(`"${check.query}" doesn't look like a research topic. Try a specific field or subject area.`);
        setResults([]);
        setRelatedPapers([]);
        return;
      }

      const paperData = await paperRes.json();
      setResults(topicData.data || []);
      setRelatedPapers(paperData.data || []);
    } catch (e) {
      if (isAbortError(e)) {
        setError('Search stopped.');
        return;
      }
      console.error(e);
      setError('Network error. Please try again.');
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setLoading(false);
      setPapersLoading(false);
    }
  };

  const openCategory = async (cat) => {
    setActiveCategory(cat);
    setCategoryPapers([]);
    setCatLoading(true);
    discover(cat.query);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/arxiv/feed?category=${cat.arxiv}&limit=9`);
      const data = await res.json();
      setCategoryPapers(data.data || []);
    } catch (e) {
      console.error(e);
    } finally {
      setCatLoading(false);
    }
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
      } catch (e) {}
      setLoadingRecent(false);
    };
    fetchRecentSurveys();
  }, [authFetch]);

  const deleteRecentSurvey = async (query, e) => {
    e.stopPropagation();
    if (!window.confirm(`Are you sure you want to delete the survey "${query}"?`)) return;
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature/delete/${encodeURIComponent(query)}`, {
        method: 'DELETE',
      });
      if (res.ok) setRecentSurveys(prev => prev.filter(s => s.query !== query));
    } catch (err) {}
  };

  const showWelcome = !loading && results.length === 0 && !error && !activeCategory;
  const showResults = !loading && results.length > 0;

  return (
    <div className="dashboard-page">
      <header className="dashboard-masthead">
        <p className="dashboard-kicker">Literature desk</p>
        <h1 className="dashboard-title">Research Discovery</h1>
        <p className="dashboard-subtitle">
          Browse fields on the left. Search and read on the right.
        </p>
        <div className="dashboard-inline-metrics">
          <span><BookOpen size={12} /> <AnimatedNumber value={14280} suffix="+" /> topics</span>
          <span className="dashboard-metric-dot" aria-hidden="true" />
          <span><Layers size={12} /> {CATEGORIES.length} fields</span>
          <span className="dashboard-metric-dot" aria-hidden="true" />
          <span><FileText size={12} /> {recentSurveys.length} surveys</span>
        </div>
      </header>

      <div className="dashboard-split">
        {/* ── Left rail: browse ── */}
        <aside className="dashboard-rail">
          <section className="dashboard-rail-section">
            <h2 className="dashboard-rail-heading">Fields</h2>
            <nav className="dashboard-rail-nav" aria-label="Research fields">
              {CATEGORIES.map((cat) => {
                const active = activeCategory?.arxiv === cat.arxiv;
                return (
                  <button
                    key={cat.arxiv}
                    type="button"
                    className={`dashboard-rail-item${active ? ' is-active' : ''}`}
                    onClick={() => openCategory(cat)}
                  >
                    <cat.Icon size={15} className="dashboard-rail-icon" />
                    <span className="dashboard-rail-item-body">
                      <span className="dashboard-rail-item-title">{cat.title}</span>
                      <span className="dashboard-rail-item-meta">{cat.arxiv}</span>
                    </span>
                  </button>
                );
              })}
            </nav>
          </section>

          <section className="dashboard-rail-section">
            <div className="dashboard-rail-heading-row">
              <h2 className="dashboard-rail-heading">Surveys</h2>
              <button type="button" className="dashboard-text-btn" onClick={() => navigate('/literature-survey')}>
                All
              </button>
            </div>
            {loadingRecent ? (
              <SkeletonList count={2} />
            ) : recentSurveys.length === 0 ? (
              <p className="dashboard-rail-empty">No surveys yet. Discover a topic to start one.</p>
            ) : (
              <ul className="dashboard-rail-list">
                {recentSurveys.map((survey, i) => (
                  <li key={i} className="dashboard-rail-survey">
                    <button
                      type="button"
                      className="dashboard-rail-survey-main"
                      onClick={() => navigate('/literature-survey', { state: { query: survey.query } })}
                    >
                      <span className="dashboard-rail-survey-query">{survey.query}</span>
                      <span className="dashboard-rail-survey-meta">
                        {survey.papers?.length || 0} papers
                      </span>
                    </button>
                    <button
                      type="button"
                      className="dashboard-rail-survey-delete"
                      onClick={(e) => deleteRecentSurvey(survey.query, e)}
                      aria-label="Delete survey"
                    >
                      <Trash2 size={12} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>

        {/* ── Right workspace: search + stream ── */}
        <div className="dashboard-workspace">
          <div className="dashboard-query">
            <div ref={inputWrap} className="dashboard-query-field">
              <Search size={16} className="dashboard-query-icon" />
              <input
                className="dashboard-query-input"
                placeholder="Search a field, method, or research question…"
                value={topic}
                onChange={e => handleInputChange(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') discover();
                  if (e.key === 'Escape') {
                    if (loading) stopDiscover();
                    else setShowSug(false);
                  }
                }}
                onFocus={() => suggestions.length && setShowSug(true)}
                aria-label="Discover research topics"
              />
              {(topic || hasSearched) && (
                <button
                  type="button"
                  className="dashboard-query-clear"
                  onClick={clearSearch}
                  aria-label="Clear search"
                  title="Clear search"
                >
                  <X size={14} />
                </button>
              )}
              {showSug && (
                <div className="dashboard-suggestions">
                  {suggestions.map((s, i) => (
                    <button key={i} type="button" onMouseDown={() => discover(s)} className="dashboard-suggestion">
                      <Search size={12} /> {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {loading ? (
              <button
                type="button"
                className="dashboard-query-btn dashboard-query-btn-stop"
                onClick={stopDiscover}
              >
                <Square size={12} fill="currentColor" /> Stop
              </button>
            ) : (
              <button
                type="button"
                className="dashboard-query-btn"
                onClick={() => discover()}
              >
                Discover
                <ArrowRight size={14} />
              </button>
            )}
          </div>

          {error && (
            <div className={`dashboard-error${error === 'Search stopped.' ? ' is-muted' : ''}`} role="alert">
              <span className="dashboard-error-text"><X size={14} /> {error}</span>
              <button type="button" className="dashboard-error-dismiss" onClick={() => setError('')} aria-label="Dismiss">
                Dismiss
              </button>
            </div>
          )}

          {loading && (
            <div className="dashboard-stream-block">
              <p className="dashboard-stream-status"><Spinner size={15} /> Scanning literature…</p>
              <SkeletonList count={4} />
            </div>
          )}

          {showWelcome && (
            <section className="dashboard-stream-block">
              <div className="dashboard-stream-head">
                <h2 className="dashboard-stream-title">
                  <TrendingUp size={15} /> Trending domains
                </h2>
                <span className="dashboard-stream-tag">Start here</span>
              </div>
              <ol className="dashboard-stream-list">
                {TRENDING.map((item, idx) => (
                  <li key={item.title}>
                    <button type="button" className="dashboard-stream-row" onClick={() => discover(item.title)}>
                      <span className="dashboard-stream-idx">{String(idx + 1).padStart(2, '0')}</span>
                      <span className="dashboard-stream-main">
                        <span className="dashboard-stream-row-title">{item.title}</span>
                        <span className="dashboard-stream-row-meta">{item.field}</span>
                      </span>
                      <span className="dashboard-stream-delta">{item.delta}</span>
                    </button>
                  </li>
                ))}
              </ol>
            </section>
          )}

          {showResults && (
            <>
              <section className="dashboard-stream-block">
                <div className="dashboard-stream-head">
                  <h2 className="dashboard-stream-title">
                    Directions for <em>{topic}</em>
                  </h2>
                  <span className="dashboard-stream-tag">{results.length} ranked</span>
                </div>
                <ol className="dashboard-stream-list">
                  {results.map((t, i) => {
                    const score = impactScore(t.impact);
                    return (
                      <li key={i}>
                        <div
                          className={`dashboard-direction${i === 0 ? ' is-lead' : ''}`}
                          role="button"
                          tabIndex={0}
                          onClick={() => navigate('/literature-survey', { state: { query: t.title } })}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              navigate('/literature-survey', { state: { query: t.title } });
                            }
                          }}
                        >
                          <span className="dashboard-stream-idx">{String(i + 1).padStart(2, '0')}</span>
                          <div className="dashboard-direction-body">
                            <div className="dashboard-direction-top">
                              <span className="dashboard-direction-label">
                                {i === 0 ? 'Lead direction' : `Direction ${i + 1}`}
                              </span>
                              <span className={`dashboard-impact impact-${(t.impact || 'medium').toLowerCase().replace(/\s+/g, '-')}`}>
                                {t.impact}
                              </span>
                            </div>
                            <h3 className="dashboard-direction-title">{t.title}</h3>
                            <div className="dashboard-meter" aria-hidden="true">
                              {[1, 2, 3, 4].map((level) => (
                                <span key={level} className={`dashboard-meter-seg${level <= score ? ' is-on' : ''}`} />
                              ))}
                            </div>
                            <p className="dashboard-direction-hint">{impactHint(t.impact)}</p>
                            <div className="dashboard-direction-actions">
                              <span className="dashboard-action-primary">
                                Start survey <ArrowRight size={12} />
                              </span>
                              <a
                                href={`https://scholar.google.com/scholar?q=${encodeURIComponent(t.title)}`}
                                target="_blank"
                                rel="noreferrer"
                                className="dashboard-action-link"
                                onClick={(e) => e.stopPropagation()}
                              >
                                Scholar <ExternalLink size={11} />
                              </a>
                            </div>
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </section>

              <section className="dashboard-stream-block">
                <div className="dashboard-stream-head">
                  <h2 className="dashboard-stream-title">
                    <FileText size={15} /> Related papers
                  </h2>
                  <button type="button" className="dashboard-text-btn" onClick={() => navigate('/literature-survey', { state: { query: topic } })}>
                    Literature survey <ArrowUpRight size={13} />
                  </button>
                </div>
                {papersLoading && (
                  <p className="dashboard-stream-status"><Spinner size={15} /> Loading papers…</p>
                )}
                {!papersLoading && relatedPapers.length === 0 && (
                  <p className="dashboard-stream-status">No related papers found for this query.</p>
                )}
                {!papersLoading && relatedPapers.length > 0 && (
                  <>
                    <div className="dashboard-cite-list">
                      {relatedPapers.slice(0, visibleRelatedCount).map((paper, i) => (
                        <a
                          key={paper.id || `${paper.title}-${i}`}
                          href={paper.url || `https://scholar.google.com/scholar?q=${encodeURIComponent(paper.title)}`}
                          target="_blank"
                          rel="noreferrer"
                          className="dashboard-cite-row"
                        >
                          <span className="dashboard-cite-num">[{i + 1}]</span>
                          <span className="dashboard-cite-body">
                            <span className="dashboard-cite-title">{paper.title}</span>
                            <span className="dashboard-cite-meta">
                              {paper.authors || 'Authors unavailable'}
                              {paper.year ? ` · ${paper.year}` : ''}
                            </span>
                          </span>
                          <ExternalLink size={13} className="dashboard-cite-ext" />
                        </a>
                      ))}
                    </div>
                    {visibleRelatedCount < relatedPapers.length && (
                      <button
                        type="button"
                        className="dashboard-load-more"
                        onClick={() => setVisibleRelatedCount((n) => Math.min(n + RELATED_PAGE_SIZE, relatedPapers.length))}
                      >
                        Load more · {visibleRelatedCount}/{relatedPapers.length}
                      </button>
                    )}
                  </>
                )}
              </section>
            </>
          )}

          {activeCategory && (
            <section className="dashboard-stream-block">
              <div className="dashboard-stream-head">
                <h2 className="dashboard-stream-title">
                  <span className="dashboard-arxiv-code">{activeCategory.arxiv}</span>
                  Latest in {activeCategory.title}
                </h2>
                <button
                  type="button"
                  className="dashboard-text-btn"
                  onClick={() => { setActiveCategory(null); setCategoryPapers([]); }}
                >
                  <X size={13} /> Close feed
                </button>
              </div>
              {catLoading && (
                <p className="dashboard-stream-status"><Spinner size={15} /> Loading archive…</p>
              )}
              {!catLoading && categoryPapers.length === 0 && (
                <p className="dashboard-stream-status">No recent papers in this category.</p>
              )}
              {!catLoading && categoryPapers.length > 0 && (
                <div className="dashboard-cite-list">
                  {categoryPapers.map((p, i) => (
                    <article key={i} className="dashboard-feed-item">
                      <span className="dashboard-cite-num">[{String(i + 1).padStart(2, '0')}]</span>
                      <div className="dashboard-feed-body">
                        <h3 className="dashboard-cite-title">{p.title}</h3>
                        <p className="dashboard-cite-meta">{p.authors}</p>
                        {p.abstract && (
                          <p className="dashboard-feed-abstract">{p.abstract.substring(0, 160)}…</p>
                        )}
                        <div className="dashboard-feed-links">
                          {p.url && (
                            <a href={p.url} target="_blank" rel="noreferrer">Abstract</a>
                          )}
                          {p.pdf_url && (
                            <a href={p.pdf_url} target="_blank" rel="noreferrer">PDF</a>
                          )}
                          <a
                            href={`https://scholar.google.com/scholar?q=${encodeURIComponent(p.title)}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Scholar
                          </a>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          )}

          {!results.length && !loading && !activeCategory && hasSearched && !error && (
            <div className="dashboard-empty-search">
              <Search size={22} />
              <h3>No results for “{topic}”</h3>
              <p>Try a more specific field, or pick one from the left rail.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
