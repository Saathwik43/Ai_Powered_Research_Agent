import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { BookOpen, CheckCircle2, ChevronRight, Copy, Download, ExternalLink, FileText, Filter, List, Save, Search, Sparkles, User, X, Loader2, Bookmark, Unlock, ChevronDown, Trash2, Square } from 'lucide-react';
import { InteractiveHoverButton } from '@/components/ui/interactive-hover-button';
import { AnimatePresence, LayoutGroup, motion } from 'motion/react';
import './LiteratureSurvey.css';
import { useAuth } from '../context/AuthContext';
import { useAppContext } from '../context/AppContext';
import { Spinner, SkeletonList } from '../components/Loader';
import {
  validateSearchQuery,
  normalizeSearchQuery,
  isAbortError,
} from '../utils/searchHeuristics';

// Progressive fetch: classify one page at a time. Raising limit on Load more
// reuses search_all + relevance caches so only the new window slice is paid for.
const INITIAL_LIMIT = 15;
const MAX_LIMIT = 100;

export default function LiteratureSurvey() {
  const { authFetch } = useAuth();
  const { literatureState } = useAppContext();
  const {
    query, setQuery,
    papers, setPapers,
    loading, setLoading,
    activeTab, setActiveTab,
    searchError, setSearchError,
    hasSearched, setHasSearched,
    lastQuery, setLastQuery,
    filterYear, setFilterYear,
    filterSource, setFilterSource,
    visibleCount, setVisibleCount
  } = literatureState;

  const [loadingMore, setLoadingMore] = useState(false);
  const [saveStatus, setSaveStatus] = useState('');
  const [savedSurveys, setSavedSurveys] = useState([]);
  const [loadingSaved, setLoadingSaved] = useState(false);
  const [serverHasMore, setServerHasMore] = useState(false);
  const [fetchedLimit, setFetchedLimit] = useState(INITIAL_LIMIT);
  const abortRef = useRef(null);
  const inFlightQueryRef = useRef('');

  const PAGE_SIZE = 15;

  const paperKey = (p) =>
    p.doi || p.id || p.url || `${p.title}|${p.year}|${p.source}|${p.authors}`;

  useEffect(() => () => {
    abortRef.current?.abort();
  }, []);

  const fetchSavedSurveys = async () => {
    setLoadingSaved(true);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature/list`);
      if (res.ok) {
        const data = await res.json();
        setSavedSurveys(data.data || []);
      }
    } catch (e) {
      console.error("Failed to fetch saved surveys", e);
    } finally {
      setLoadingSaved(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'saved') {
      fetchSavedSurveys();
    }
  }, [activeTab]);

  const location = useLocation();
  const navigate = useNavigate();
  const autoSearchKeyRef = useRef('');

  // Explicit user stop. Owns both the abort and the resulting UI state, so the
  // aborted request's own catch/finally can stay silent (see search()).
  const stopSearch = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
      setSearchError('Search stopped.');
    }
    inFlightQueryRef.current = '';
    setLoading(false);
  }, [setLoading, setSearchError]);

  const clearSearch = useCallback(() => {
    stopSearch();
    setQuery('');
    setPapers([]);
    setSearchError('');
    setHasSearched(false);
    setLastQuery('');
    setFilterYear('All');
    setFilterSource('All');
    setVisibleCount(15);
    setSaveStatus('');
  }, [
    stopSearch, setQuery, setPapers, setSearchError, setHasSearched,
    setLastQuery, setFilterYear, setFilterSource, setVisibleCount,
  ]);

  const search = async (q = query, newSearch = true) => {
    const check = validateSearchQuery(q);
    if (!check.ok) {
      setSearchError(check.message);
      if (check.code === 'empty') setHasSearched(false);
      return;
    }

    const normalized = normalizeSearchQuery(check.query);
    // Read the abort ref, not `loading` — the state closure is a render behind
    // on rapid submits and would let the duplicate through.
    if (abortRef.current && inFlightQueryRef.current === normalized) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    inFlightQueryRef.current = normalized;
    // Only the run that still owns abortRef may touch shared UI state; a run
    // superseded by a newer search, or cancelled via stopSearch, stays silent.
    const isCurrent = () => abortRef.current === controller;

    setQuery(check.query);
    setLoading(true);
    if (newSearch) setPapers([]);
    setVisibleCount(15);
    setSaveStatus('');
    setSearchError('');
    setHasSearched(true);
    setLastQuery(check.query);
    setFilterYear('All');
    setFilterSource('All');
    setServerHasMore(false);
    setFetchedLimit(INITIAL_LIMIT);

    try {
      const res = await authFetch(
        `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature?query=${encodeURIComponent(check.query)}&limit=${INITIAL_LIMIT}`,
        { signal: controller.signal }
      );
      if (!isCurrent()) return;
      if (res.status === 429 || res.status === 503) {
        setSearchError('Rate limit exceeded. Please wait a minute before trying again.');
        setPapers([]);
        return;
      }
      if (!res.ok) {
        setSearchError('Failed to fetch literature. Please try again.');
        setPapers([]);
        return;
      }
      const data = await res.json();
      if (!isCurrent()) return;
      setPapers(data.data || []);
      setServerHasMore(Boolean(data.has_more));
      setFetchedLimit(data.limit || INITIAL_LIMIT);
    } catch (e) {
      // Superseded/cancelled runs report nothing — whoever aborted them owns
      // the UI now. Without this, an aborted run's error and loading reset
      // landed on top of the search that replaced it.
      if (!isCurrent()) return;
      if (isAbortError(e)) return;
      console.error(e);
      setSearchError('Network error. Please try again.');
      setPapers([]);
    } finally {
      if (isCurrent()) {
        abortRef.current = null;
        inFlightQueryRef.current = '';
        setLoading(false);
      }
    }
  };

  // Auto-run search when navigated here from Dashboard with a query in state.
  useEffect(() => {
    const incomingQuery = location.state?.query;
    if (!incomingQuery || !String(incomingQuery).trim()) return;

    const key = `${incomingQuery}::${location.key || ''}`;
    if (autoSearchKeyRef.current === key) return;
    autoSearchKeyRef.current = key;

    setActiveTab('search');
    setQuery(incomingQuery);
    void search(incomingQuery, true);
    // Clear nav state after starting search so refresh/back does not re-fire.
    navigate(location.pathname, { replace: true, state: {} });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state, location.key]);

  const loadMore = async () => {
    if (visibleCount < filteredPapers.length) {
      setVisibleCount(prev => Math.min(prev + PAGE_SIZE, filteredPapers.length));
      return;
    }
    if (!serverHasMore || loadingMore || !lastQuery) return;

    const nextLimit = Math.min(MAX_LIMIT, fetchedLimit + PAGE_SIZE);
    if (nextLimit <= fetchedLimit) return;

    setLoadingMore(true);
    setSearchError('');
    try {
      const res = await authFetch(
        `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature?query=${encodeURIComponent(lastQuery)}&limit=${nextLimit}`
      );
      if (res.status === 429 || res.status === 503) {
        setSearchError('Rate limit exceeded. Please wait a minute before trying again.');
        return;
      }
      if (!res.ok) {
        setSearchError('Failed to load more results. Please try again.');
        return;
      }
      const data = await res.json();
      setPapers(data.data || []);
      setServerHasMore(Boolean(data.has_more));
      setFetchedLimit(data.limit || nextLimit);
      setVisibleCount(prev => prev + PAGE_SIZE);
    } catch (e) {
      console.error(e);
      setSearchError('Network error while loading more. Please try again.');
    } finally {
      setLoadingMore(false);
    }
  };

  const filteredPapers = papers.filter(p => {
    if (filterYear !== 'All') {
      const year = p.year === 'Unknown' ? p.published : p.year;
      if (filterYear === 'Last 5 Years') {
        const y = parseInt(year);
        if (isNaN(y) || new Date().getFullYear() - y > 5) return false;
      } else if (String(year) !== filterYear) {
        return false;
      }
    }
    if (filterSource !== 'All') {
      if (p.source !== filterSource) return false;
    }
    return true;
  });

  const displayedPapers = filteredPapers.slice(0, visibleCount);
  const hasMoreFiltered = visibleCount < filteredPapers.length || serverHasMore;

  const exportSurveyToPDF = async (papersToExport, queryName) => {
    if (!papersToExport || !papersToExport.length) return;
    const [{ default: jsPDF }, autoTableMod] = await Promise.all([
      import('jspdf'),
      import('jspdf-autotable'),
    ]);
    const autoTable = autoTableMod.default;
    const doc = new jsPDF();
    doc.setFontSize(16);
    doc.text(`Literature Survey: ${queryName}`, 14, 22);
    doc.setFontSize(10);
    doc.text(`Generated on: ${new Date().toLocaleDateString()}`, 14, 30);
    
    const tableColumn = ["Title", "Authors", "Year", "Citations"];
    const tableRows = [];

    papersToExport.forEach(p => {
      const rowData = [
        p.title || 'N/A',
        p.authors || 'N/A',
        p.year === 'Unknown' ? (p.published || 'N/A') : (p.year || 'N/A'),
        p.citations || 0
      ];
      tableRows.push(rowData);
    });

    autoTable(doc, {
      startY: 35,
      head: [tableColumn],
      body: tableRows,
      styles: { fontSize: 8, cellPadding: 3 },
      headStyles: { fillColor: [41, 128, 185] },
      columnStyles: {
        0: { cellWidth: 80 },
        1: { cellWidth: 50 },
        2: { cellWidth: 20 },
        3: { cellWidth: 20 }
      }
    });

    doc.save(`survey-${queryName.replace(/\s+/g, '-')}.pdf`);
  };

  const exportSurvey = () => {
    exportSurveyToPDF(papers, query);
  };

  const saveSurvey = async () => {
    if (!query || !papers.length) return;
    setSaveStatus('saving');
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature/save`, { method: 'POST', body: JSON.stringify({ query, papers }) });
      setSaveStatus(res.ok ? 'saved' : 'error');
      if (res.ok) setTimeout(() => setSaveStatus(''), 3000);
    } catch { setSaveStatus('error'); }
  };

  const deleteSurvey = async (surveyQuery) => {
    if (!window.confirm(`Are you sure you want to delete the survey "${surveyQuery}"?`)) return;
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature/delete/${encodeURIComponent(surveyQuery)}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        fetchSavedSurveys();
      }
    } catch (e) {
      console.error("Failed to delete survey", e);
    }
  };

  return (
    <div className="animate-fade-in lit-page">
      <div className="lit-masthead">
        <h1>Literature Survey</h1>
        <p className="text-muted">Search research papers from multiple academic sources in one place.</p>
      </div>

      <LayoutGroup id="lit-tabs">
        <div className="lit-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'search'}
            className={`lit-tab${activeTab === 'search' ? ' is-active' : ''}`}
            onClick={() => setActiveTab('search')}
          >
            {activeTab === 'search' && (
              <motion.span
                layoutId="lit-tab-ink"
                className="lit-tab-ink"
                transition={{ type: 'spring', visualDuration: 0.22, bounce: 0.16 }}
                aria-hidden="true"
              />
            )}
            <Search size={15} /> Search
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'saved'}
            className={`lit-tab${activeTab === 'saved' ? ' is-active' : ''}`}
            onClick={() => setActiveTab('saved')}
          >
            {activeTab === 'saved' && (
              <motion.span
                layoutId="lit-tab-ink"
                className="lit-tab-ink"
                transition={{ type: 'spring', visualDuration: 0.22, bounce: 0.16 }}
                aria-hidden="true"
              />
            )}
            <Bookmark size={15} /> <span className="lit-tab-label-full">Saved </span>Surveys
          </button>
        </div>
      </LayoutGroup>

      {activeTab === 'search' ? (
        <>
      {/* Search desk */}
      <div className="lit-search-desk">
        <div className="lit-search-field">
          <Search size={15} />
          <input
            placeholder="Topic, keyword, or author…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') search();
              if (e.key === 'Escape' && loading) stopSearch();
            }}
            aria-label="Search literature"
          />
          {(query || hasSearched) && (
            <button
              type="button"
              className="lit-search-clear"
              onClick={clearSearch}
              aria-label="Clear search"
              title="Clear search"
            >
              <X size={14} />
            </button>
          )}
        </div>
        {loading ? (
          <button type="button" className="lit-search-stop" onClick={stopSearch} aria-label="Stop search" title="Stop">
            <Square size={14} fill="currentColor" />
          </button>
        ) : (
          <InteractiveHoverButton
            className="lit-search-go"
            text="Search"
            loading={false}
            onClick={() => search()}
          />
        )}
      </div>

      {searchError && (
        <div className={`lit-search-alert${searchError === 'Search stopped.' ? ' is-muted' : ''}`} role="alert">
          <span className="lit-search-alert-text"><X size={15} /> {searchError}</span>
          <button type="button" className="lit-search-alert-dismiss" onClick={() => setSearchError('')} aria-label="Dismiss">
            Dismiss
          </button>
        </div>
      )}

      {/* Toolbar and Filters */}
      {papers.length > 0 && (
        <div className="lit-filter-bar">
          <div className="lit-filter-top">
            <div className="lit-filter-summary">
              <BookOpen size={15} color="var(--primary)" />
              <span className="lit-filter-count">{filteredPapers.length} results</span>
              <span className="lit-filter-query">for &ldquo;{lastQuery}&rdquo;</span>
            </div>
            <div className="lit-filter-actions">
              <button className="btn btn-secondary" onClick={saveSurvey} disabled={saveStatus === 'saving'}>
                <Save size={14} /> {saveStatus === 'saving' ? 'Saving...' : saveStatus === 'saved' ? 'Saved' : 'Save'}
              </button>
              <button className="btn btn-secondary" onClick={exportSurvey}>
                <Download size={14} /> Export
              </button>
            </div>
          </div>

          <div className="lit-filter-row">
            <div className="lit-filter-field">
              <label htmlFor="lit-filter-year">Year</label>
              <select id="lit-filter-year" value={filterYear} onChange={e => { setFilterYear(e.target.value); setVisibleCount(15); }}>
                <option value="All">All Years</option>
                <option value="Last 5 Years">Last 5 Years</option>
                <option value="2026">2026</option>
                <option value="2025">2025</option>
                <option value="2024">2024</option>
                <option value="2023">2023</option>
                <option value="2022">2022</option>
                <option value="2021">2021</option>
              </select>
            </div>
            <div className="lit-filter-field">
              <label htmlFor="lit-filter-source">Source</label>
              <select id="lit-filter-source" value={filterSource} onChange={e => { setFilterSource(e.target.value); setVisibleCount(15); }}>
                <option value="All">All Sources</option>
                <option value="Semantic Scholar">Semantic Scholar</option>
                <option value="IEEE">IEEE</option>
                <option value="Springer">Springer</option>
                <option value="CORE">CORE</option>
                <option value="OpenAlex">OpenAlex</option>
                <option value="PubMed">PubMed</option>
                <option value="arXiv">arXiv</option>
                <option value="Crossref">Crossref</option>
                <option value="GitHub">GitHub</option>
              </select>
            </div>
          </div>
        </div>
      )}

      {/* Papers */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
        {loading && (
          <div style={{ marginTop: 'var(--space-6)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', marginBottom: 'var(--space-5)', background: 'rgba(0, 87, 255, 0.04)', border: '1px solid rgba(0, 87, 255, 0.1)', padding: 'var(--space-4)', borderRadius: 'var(--radius-lg)' }}>
              <div style={{ animation: 'spin 3s linear infinite' }}>
                <Sparkles size={24} style={{ color: 'var(--primary)' }} />
              </div>
              <div>
                <h2 style={{ fontSize: 'var(--fs-md)', fontWeight: 600, margin: '0 0 var(--space-1) 0', color: 'var(--primary)' }}>
                  Searching Literature...
                </h2>
                <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                  <Spinner size={12} /> Querying multiple academic libraries simultaneously. This deep search may take a few seconds...
                </p>
              </div>
            </div>
            
            <div style={{ width: '100%', height: '4px', background: 'var(--border)', borderRadius: '2px', overflow: 'hidden', marginBottom: 'var(--space-5)' }}>
              <div style={{ height: '100%', background: 'var(--primary)', width: '0%', animation: 'progressAnim 15s cubic-bezier(0.1, 0.8, 0.3, 1) forwards' }} />
            </div>
            <style>{`
              @keyframes progressAnim {
                0% { width: 0%; }
                100% { width: 95%; } 
              }
            `}</style>
            
            <div className="skeleton-card" />
            <div className="skeleton-card" style={{ animationDelay: '0.1s' }} />
            <div className="skeleton-card" style={{ animationDelay: '0.2s' }} />
            <div className="skeleton-card" style={{ animationDelay: '0.3s' }} />
          </div>
        )}

        {!loading && papers.length === 0 && !hasSearched && !searchError && (
          <div className="lit-empty">
            <BookOpen size={28} />
            Enter a topic to discover relevant research.
          </div>
        )}

        {!loading && papers.length === 0 && hasSearched && !searchError && (
          <div className="lit-empty">
            <BookOpen size={28} />
            No results for '{lastQuery}'. Try a different term.
          </div>
        )}

        {!loading && papers.length > 0 && filteredPapers.length === 0 && (
          <div className="lit-empty">
            <BookOpen size={28} />
            No papers match your selected filters.
          </div>
        )}

        <AnimatePresence mode="popLayout" initial={false}>
        {displayedPapers.map((p) => (
          <motion.article
            key={paperKey(p)}
            className="lit-result-card"
            layout
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{
              layout: { type: 'spring', visualDuration: 0.28, bounce: 0.16 },
              opacity: { duration: 0.18 },
              y: { duration: 0.2 },
            }}
          >
            <div className="lit-result-head">
              <h3 className="lit-result-title">{p.title}</h3>
              <div className="lit-result-actions">
                {p.oa_url && (
                  <a href={p.oa_url} target="_blank" rel="noreferrer" className="lit-badge lit-badge-oa"
                    title="Open Access — free full text available"
                  >
                    <Unlock size={11} /> Open Access
                  </a>
                )}
                {p.citations > 0 && (
                  <span className="lit-badge">
                    {p.citations.toLocaleString()} citations
                  </span>
                )}
              </div>
            </div>

            <p className="lit-result-meta">
              {p.authors}
              {p.year && p.year !== 'N/A' && <span> · {p.year}</span>}
              {p.published && p.year === 'Unknown' && <span> · {p.published}</span>}
            </p>

            {p.abstract && p.abstract !== 'No abstract available' && (
              <p className="lit-result-abstract">
                {p.abstract.substring(0, 240)}{p.abstract.length > 240 ? '...' : ''}
              </p>
            )}

            <div className="lit-result-links">
              {p.url && (
                <a href={p.url} target="_blank" rel="noreferrer" className="btn btn-ghost lit-link-btn">
                  <ExternalLink size={12} /> View
                </a>
              )}
              {p.pdf_url && p.pdf_url !== p.url && (
                <a href={p.pdf_url} target="_blank" rel="noreferrer" className="btn btn-ghost lit-link-btn">
                  <FileText size={12} /> PDF
                </a>
              )}
              {p.oa_url && p.oa_url !== p.url && p.oa_url !== p.pdf_url && (
                <a href={p.oa_url} target="_blank" rel="noreferrer" className="btn btn-ghost lit-link-btn lit-link-oa">
                  <Unlock size={12} /> Full Text (OA)
                </a>
              )}
              <a
                href={`https://scholar.google.com/scholar?q=${encodeURIComponent(p.title)}`}
                target="_blank"
                rel="noreferrer"
                className="btn btn-ghost lit-link-btn"
              >
                <Search size={12} /> Scholar
              </a>
            </div>
          </motion.article>
        ))}
        </AnimatePresence>

        {/* Load more button */}
        {hasMoreFiltered && !loading && (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-4) 0' }}>
            <button className="btn btn-secondary" onClick={loadMore} disabled={loadingMore}
              style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', padding: 'var(--space-3) var(--space-5)' }}
            >
              {loadingMore ? <Spinner size={16} /> : <><ChevronDown size={16} /> Load more results</>}
            </button>
          </div>
        )}
      </div>
        </>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          {loadingSaved ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-6)' }}><Spinner /></div>
          ) : savedSurveys.length === 0 ? (
            <div className="empty-state">
              <Bookmark size={38} style={{ margin: '0 auto var(--space-3)', color: 'var(--text-subtle)', display: 'block' }} />
              You haven't saved any surveys yet.
            </div>
          ) : (
            savedSurveys.map((survey, i) => (
              <div key={i} className="lit-result-card lit-saved-card animate-slide-up"
                style={{ animationDelay: `${i * 0.04}s` }}
              >
                <div className="lit-saved-copy">
                  <h3>{survey.query}</h3>
                  <p>{survey.papers?.length || 0} papers saved</p>
                </div>
                <div className="lit-result-actions lit-saved-actions">
                  <button className="btn btn-secondary" onClick={() => exportSurveyToPDF(survey.papers, survey.query)}>
                    <Download size={14} /> Download PDF
                  </button>
                  <button
                    type="button"
                    className="btn btn-icon lit-delete-btn"
                    onClick={() => deleteSurvey(survey.query)}
                    aria-label="Delete survey"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
