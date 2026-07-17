import React, { useState, useEffect } from 'react';
import { BookOpen, CheckCircle2, ChevronRight, Copy, Download, ExternalLink, FileText, Filter, List, RefreshCw, Save, Search, Sparkles, User, X, Loader2, Bookmark, Unlock, ChevronDown, Trash2 } from 'lucide-react';
import { InteractiveHoverButton } from '@/components/ui/interactive-hover-button';
import './LiteratureSurvey.css';
import jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import { useAuth } from '../context/AuthContext';
import { useAppContext } from '../context/AppContext';
import { Spinner, SkeletonList } from '../components/Loader';

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

  const PAGE_SIZE = 15;

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

  const search = async (q = query, newSearch = true) => {
    if (!q.trim()) return;
    setLoading(true);
    setPapers([]);
    setVisibleCount(15);
    setSaveStatus(''); setSearchError('');
    setHasSearched(true); setLastQuery(q);
    setFilterYear('All'); setFilterSource('All');
    
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature?query=${encodeURIComponent(q)}`);
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
      setPapers(data.data || []);
    } catch (e) {
      console.error(e);
      setSearchError('Network error. Please try again.');
      setPapers([]);
    }
    finally {
      setLoading(false);
    }
  };

  const loadMore = () => {
    setVisibleCount(prev => prev + PAGE_SIZE);
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
  const hasMoreFiltered = visibleCount < filteredPapers.length;

  const exportSurveyToPDF = (papersToExport, queryName) => {
    if (!papersToExport || !papersToExport.length) return;
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
    <div className="animate-fade-in">
      <div style={{ marginBottom: 'var(--space-6)' }}>
        <h1>Literature Survey</h1>
        <p className="text-muted">Search research papers from multiple academic sources in one place.</p>
      </div>

      <div className="lit-tabs">
        <button 
          onClick={() => setActiveTab('search')} 
          style={{ background: 'none', border: 'none', padding: 'var(--space-3) var(--space-4)', cursor: 'pointer', color: activeTab === 'search' ? 'var(--primary)' : 'var(--text-muted)', fontWeight: activeTab === 'search' ? 600 : 400, display: 'flex', alignItems: 'center', gap: 'var(--space-2)', zIndex: 1 }}
        >
          <Search size={16} /> Search
        </button>
        <button 
          onClick={() => setActiveTab('saved')} 
          style={{ background: 'none', border: 'none', padding: 'var(--space-3) var(--space-4)', cursor: 'pointer', color: activeTab === 'saved' ? 'var(--primary)' : 'var(--text-muted)', fontWeight: activeTab === 'saved' ? 600 : 400, display: 'flex', alignItems: 'center', gap: 'var(--space-2)', zIndex: 1 }}
        >
          <Bookmark size={16} /> Saved Surveys
        </button>
        <div className="lit-tab-indicator" style={{ width: '50%', left: activeTab === 'search' ? '0%' : '50%' }} />
      </div>

      {activeTab === 'search' ? (
        <>
      {/* Search */}
      <div className="lit-search-row" style={{ display: 'flex', gap: 'var(--space-3)', marginBottom: 'var(--space-5)', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: '220px' }}>
          <Search size={15} style={{ position: 'absolute', left: '1rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-subtle)', pointerEvents: 'none' }} />
          <input
            placeholder="Search by topic, keyword, or author..."
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && search()}
            style={{ paddingLeft: 'var(--space-7)' }}
          />
        </div>
        <InteractiveHoverButton 
          text={loading ? "Searching..." : "Search"} 
          onClick={() => search()} 
          disabled={loading} 
        />
      </div>
      

      {searchError && (
        <div style={{ marginBottom: 'var(--space-5)', padding: 'var(--space-3) var(--space-4)', background: 'rgba(229,28,35,0.08)', border: '1px solid rgba(229,28,35,0.2)', borderRadius: 'var(--radius-md)', color: 'var(--danger)', fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
          <X size={15} /> {searchError}
        </div>
      )}

      {/* Toolbar and Filters */}
      {papers.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', marginBottom: 'var(--space-4)', background: 'var(--bg-card)', padding: 'var(--space-4)', borderRadius: 'var(--radius-lg)', border: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--space-3)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
              <BookOpen size={15} color="var(--primary)" />
              <span style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}>{filteredPapers.length} results</span>
              <span style={{ color: 'var(--text-subtle)', fontSize: 'var(--fs-sm)' }}>for "{lastQuery}"</span>
            </div>
            <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
              <button className="btn btn-secondary" onClick={saveSurvey} disabled={saveStatus === 'saving'}>
                <Save size={14} /> {saveStatus === 'saving' ? 'Saving...' : saveStatus === 'saved' ? 'Saved' : 'Save'}
              </button>
              <button className="btn btn-secondary" onClick={exportSurvey}>
                <Download size={14} /> Export
              </button>
            </div>
          </div>
          
          <div className="lit-filter-row" style={{ display: 'flex', gap: 'var(--space-4)', flexWrap: 'wrap', borderTop: '1px solid var(--border)', paddingTop: 'var(--space-4)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
              <label style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-muted)' }}>Year:</label>
              <select value={filterYear} onChange={e => { setFilterYear(e.target.value); setVisibleCount(15); }} style={{ padding: 'var(--space-2)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)', background: 'var(--bg-input)', color: 'var(--text)', fontSize: 'var(--fs-sm)' }}>
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
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
              <label style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-muted)' }}>Source:</label>
              <select value={filterSource} onChange={e => { setFilterSource(e.target.value); setVisibleCount(15); }} style={{ padding: 'var(--space-2)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)', background: 'var(--bg-input)', color: 'var(--text)', fontSize: 'var(--fs-sm)' }}>
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
          <div className="empty-state">
            <BookOpen size={38} style={{ margin: '0 auto var(--space-3)', color: 'var(--text-subtle)', display: 'block' }} />
            Enter a topic to discover relevant research.
          </div>
        )}

        {!loading && papers.length === 0 && hasSearched && !searchError && (
          <div className="empty-state">
            <BookOpen size={38} style={{ margin: '0 auto var(--space-3)', color: 'var(--text-subtle)', display: 'block' }} />
            No results found for '{lastQuery}'. Try a different search term.
          </div>
        )}

        {!loading && papers.length > 0 && filteredPapers.length === 0 && (
          <div className="empty-state">
            <BookOpen size={38} style={{ margin: '0 auto var(--space-3)', color: 'var(--text-subtle)', display: 'block' }} />
            No papers match your selected filters.
          </div>
        )}

        {displayedPapers.map((p, i) => (
          <div key={p.id || i} className="lit-result-card animate-slide-up"
            style={{ animationDelay: `${(i % 15) * 0.04}s`, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-4) var(--space-5)', transition: 'transform 0.18s ease, border-color 0.18s ease' }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(0,87,255,0.28)'; e.currentTarget.style.transform = 'translateY(-1px)'; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.transform = ''; }}
          >
            {/* Title + citations */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 'var(--space-4)', marginBottom: 'var(--space-2)', width: '100%' }}>
              <h3 style={{ margin: 0, fontSize: 'var(--fs-sm)', fontWeight: 600, lineHeight: 1.45, flex: 1, color: 'var(--text)' }}>{p.title}</h3>
              <div className="action-buttons" style={{ display: 'flex', gap: 'var(--space-2)', flexShrink: 0 }}>
                {p.oa_url && (
                  <a href={p.oa_url} target="_blank" rel="noreferrer" style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: '#16a34a', background: 'rgba(22,163,74,0.08)', border: '1px solid rgba(22,163,74,0.22)', padding: 'var(--space-1) var(--space-2)', borderRadius: '999px', whiteSpace: 'nowrap', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 'var(--space-1)' }}
                    title="Open Access — free full text available"
                  >
                    <Unlock size={11} /> Open Access
                  </a>
                )}
                {p.citations > 0 && (
                  <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-muted)', background: 'var(--bg-elevated)', border: '1px solid var(--border)', padding: 'var(--space-1) var(--space-2)', borderRadius: '999px', whiteSpace: 'nowrap' }}>
                    {p.citations.toLocaleString()} citations
                  </span>
                )}
              </div>
            </div>

            {/* Authors + year */}
            <p style={{ margin: '0 0 var(--space-3)', fontSize: 'var(--fs-sm)', color: 'var(--text-muted)' }}>
              {p.authors}
              {p.year && p.year !== 'N/A' && <span style={{ color: 'var(--text-subtle)' }}> · {p.year}</span>}
              {p.published && p.year === 'Unknown' && <span style={{ color: 'var(--text-subtle)' }}> · {p.published}</span>}
            </p>

            {/* Abstract */}
            {p.abstract && p.abstract !== 'No abstract available' && (
              <p style={{ margin: '0 0 var(--space-3)', fontSize: 'var(--fs-sm)', color: 'var(--text-subtle)', lineHeight: 1.6 }}>
                {p.abstract.substring(0, 240)}{p.abstract.length > 240 ? '...' : ''}
              </p>
            )}

            {/* Links */}
            <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
              {p.url && (
                <a href={p.url} target="_blank" rel="noreferrer" className="btn btn-ghost" style={{ fontSize: 'var(--fs-sm)', padding: 'var(--space-1) var(--space-3)', textDecoration: 'none' }}>
                  <ExternalLink size={12} /> View
                </a>
              )}
              {p.pdf_url && p.pdf_url !== p.url && (
                <a href={p.pdf_url} target="_blank" rel="noreferrer" className="btn btn-ghost" style={{ fontSize: 'var(--fs-sm)', padding: 'var(--space-1) var(--space-3)', textDecoration: 'none' }}>
                  <FileText size={12} /> PDF
                </a>
              )}
              {p.oa_url && p.oa_url !== p.url && p.oa_url !== p.pdf_url && (
                <a href={p.oa_url} target="_blank" rel="noreferrer" className="btn btn-ghost" style={{ fontSize: 'var(--fs-sm)', padding: 'var(--space-1) var(--space-3)', textDecoration: 'none', color: '#16a34a' }}>
                  <Unlock size={12} /> Full Text (OA)
                </a>
              )}
              <a
                href={`https://scholar.google.com/scholar?q=${encodeURIComponent(p.title)}`}
                target="_blank"
                rel="noreferrer"
                className="btn btn-ghost"
                style={{ fontSize: 'var(--fs-sm)', padding: 'var(--space-1) var(--space-3)', textDecoration: 'none' }}
              >
                <Search size={12} /> Scholar
              </a>
            </div>
          </div>
        ))}

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
              <div key={i} className="lit-result-card animate-slide-up"
                style={{ animationDelay: `${i * 0.04}s`, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-4) var(--space-5)', transition: 'transform 0.18s ease, border-color 0.18s ease', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--space-4)' }}
              >
                <div>
                  <h3 style={{ margin: '0 0 var(--space-1)', fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>{survey.query}</h3>
                  <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--text-muted)' }}>{survey.papers?.length || 0} papers saved</p>
                </div>
                <div className="action-buttons" style={{ display: 'flex', gap: 'var(--space-2)' }}>
                  <button className="btn btn-secondary" onClick={() => exportSurveyToPDF(survey.papers, survey.query)}>
                    <Download size={14} /> Download PDF
                  </button>
                  <button 
                    className="btn btn-icon" 
                    onClick={() => deleteSurvey(survey.query)} 
                    style={{ color: 'var(--danger)', background: 'rgba(229, 28, 35, 0.1)', border: 'none' }}
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
