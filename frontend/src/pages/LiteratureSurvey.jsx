import React, { useState, useEffect } from 'react';
import { Search, Download, ExternalLink, Save, BookOpen, FileText, X, Bookmark, Unlock, ChevronDown, Sparkles, Trash2 } from 'lucide-react';
import jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import { useAuth } from '../context/AuthContext';
import { Spinner, SkeletonList } from '../components/Loader';

export default function LiteratureSurvey() {
  const { authFetch } = useAuth();
  const [query, setQuery]         = useState('');
  const [papers, setPapers]       = useState([]);
  const [loading, setLoading]     = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [saveStatus, setSaveStatus] = useState('');
  const [searchError, setSearchError] = useState('');
  const [hasSearched, setHasSearched] = useState(false);
  const [lastQuery, setLastQuery] = useState('');
  const [activeTab, setActiveTab] = useState('search');
  const [savedSurveys, setSavedSurveys] = useState([]);
  const [loadingSaved, setLoadingSaved] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [totalResults, setTotalResults] = useState(0);

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
    const currentOffset = newSearch ? 0 : offset;
    if (newSearch) {
      setLoading(true);
      setPapers([]);
      setOffset(0);
      setHasMore(false);
      setTotalResults(0);
    } else {
      setLoadingMore(true);
    }
    setSaveStatus(''); setSearchError('');
    setHasSearched(true); setLastQuery(q);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature?query=${encodeURIComponent(q)}&limit=${PAGE_SIZE}&offset=${currentOffset}`);
      if (res.status === 429 || res.status === 503) {
        setSearchError('Rate limit exceeded. Please wait a minute before trying again.');
        if (newSearch) setPapers([]);
        return;
      }
      if (!res.ok) {
        setSearchError('Failed to fetch literature. Please try again.');
        if (newSearch) setPapers([]);
        return;
      }
      const data = await res.json();
      const newPapers = data.data || [];
      if (newSearch) {
        setPapers(newPapers);
      } else {
        setPapers(prev => [...prev, ...newPapers]);
      }
      setHasMore(data.has_more || false);
      setTotalResults(data.total || 0);
      setOffset(currentOffset + newPapers.length);
    } catch (e) {
      console.error(e);
      setSearchError('Network error. Please try again.');
      if (newSearch) setPapers([]);
    }
    finally {
      setLoading(false);
      setLoadingMore(false);
    }
  };

  const loadMore = () => {
    search(lastQuery, false);
  };

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
      <div style={{ marginBottom: '2rem' }}>
        <h1>Literature Survey</h1>
        <p className="text-muted">Search research papers from multiple academic sources in one place.</p>
      </div>

      <div style={{ display: 'flex', gap: '1rem', borderBottom: '1px solid var(--border)', marginBottom: '1.75rem' }}>
        <button 
          onClick={() => setActiveTab('search')} 
          style={{ background: 'none', border: 'none', padding: '0.75rem 1rem', cursor: 'pointer', borderBottom: activeTab === 'search' ? '2px solid var(--primary)' : '2px solid transparent', color: activeTab === 'search' ? 'var(--primary)' : 'var(--text-muted)', fontWeight: activeTab === 'search' ? 600 : 400, display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <Search size={16} /> Search
        </button>
        <button 
          onClick={() => setActiveTab('saved')} 
          style={{ background: 'none', border: 'none', padding: '0.75rem 1rem', cursor: 'pointer', borderBottom: activeTab === 'saved' ? '2px solid var(--primary)' : '2px solid transparent', color: activeTab === 'saved' ? 'var(--primary)' : 'var(--text-muted)', fontWeight: activeTab === 'saved' ? 600 : 400, display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <Bookmark size={16} /> Saved Surveys
        </button>
      </div>

      {activeTab === 'search' ? (
        <>
      {/* Search */}
      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1.75rem', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: '220px' }}>
          <Search size={15} style={{ position: 'absolute', left: '1rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-subtle)', pointerEvents: 'none' }} />
          <input
            placeholder="Search by topic, keyword, or author..."
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && search()}
            style={{ paddingLeft: '2.6rem' }}
          />
        </div>
        <button className="btn btn-primary" onClick={() => search()} disabled={loading}>
          {loading ? <Spinner size={16} /> : <><Search size={14} /> Search</>}
        </button>
      </div>

      {searchError && (
        <div style={{ marginBottom: '1.75rem', padding: '0.85rem 1rem', background: 'rgba(229,28,35,0.08)', border: '1px solid rgba(229,28,35,0.2)', borderRadius: 'var(--radius-md)', color: 'var(--danger)', fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <X size={15} /> {searchError}
        </div>
      )}

      {/* Toolbar */}
      {papers.length > 0 && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem', flexWrap: 'wrap', gap: '0.75rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <BookOpen size={15} color="var(--primary)" />
            <span style={{ fontWeight: 600, fontSize: '0.93rem' }}>{papers.length}{totalResults > papers.length ? ` of ${totalResults}` : ''} results</span>
            <span style={{ color: 'var(--text-subtle)', fontSize: '0.83rem' }}>for "{lastQuery}"</span>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button className="btn btn-secondary" onClick={saveSurvey} disabled={saveStatus === 'saving'}>
              <Save size={14} /> {saveStatus === 'saving' ? 'Saving...' : saveStatus === 'saved' ? 'Saved' : 'Save'}
            </button>
            <button className="btn btn-secondary" onClick={exportSurvey}>
              <Download size={14} /> Export
            </button>
          </div>
        </div>
      )}

      {/* Papers */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {loading && (
          <div style={{ marginTop: '2rem' }}>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 600, margin: '0 0 1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Sparkles size={18} style={{ color: 'var(--primary)' }} /> Searching literature...
            </h2>
            <SkeletonList count={3} />
          </div>
        )}

        {!loading && papers.length === 0 && !hasSearched && !searchError && (
          <div className="empty-state">
            <BookOpen size={38} style={{ margin: '0 auto 0.875rem', color: 'var(--text-subtle)', display: 'block' }} />
            Enter a topic to discover relevant research.
          </div>
        )}

        {!loading && papers.length === 0 && hasSearched && !searchError && (
          <div className="empty-state">
            <BookOpen size={38} style={{ margin: '0 auto 0.875rem', color: 'var(--text-subtle)', display: 'block' }} />
            No results found for '{lastQuery}'. Try a different search term.
          </div>
        )}

        {papers.map((p, i) => (
          <div key={p.id || i} className="animate-slide-up"
            style={{ animationDelay: `${i * 0.03}s`, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '1.25rem 1.5rem', transition: 'transform 0.18s ease, border-color 0.18s ease' }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(0,87,255,0.28)'; e.currentTarget.style.transform = 'translateY(-1px)'; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.transform = ''; }}
          >
            {/* Title + citations */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', marginBottom: '0.4rem' }}>
              <h3 style={{ margin: 0, fontSize: '0.97rem', fontWeight: 600, lineHeight: 1.45, flex: 1, color: 'var(--text)' }}>{p.title}</h3>
              <div style={{ display: 'flex', gap: '0.4rem', flexShrink: 0 }}>
                {p.oa_url && (
                  <a href={p.oa_url} target="_blank" rel="noreferrer" style={{ fontSize: '0.72rem', fontWeight: 600, color: '#16a34a', background: 'rgba(22,163,74,0.08)', border: '1px solid rgba(22,163,74,0.22)', padding: '0.18rem 0.55rem', borderRadius: '999px', whiteSpace: 'nowrap', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
                    title="Open Access — free full text available"
                  >
                    <Unlock size={11} /> Open Access
                  </a>
                )}
                {p.citations > 0 && (
                  <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', background: 'var(--bg-elevated)', border: '1px solid var(--border)', padding: '0.18rem 0.55rem', borderRadius: '999px', whiteSpace: 'nowrap' }}>
                    {p.citations.toLocaleString()} citations
                  </span>
                )}
              </div>
            </div>

            {/* Authors + year */}
            <p style={{ margin: '0 0 0.65rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              {p.authors}
              {p.year && p.year !== 'N/A' && <span style={{ color: 'var(--text-subtle)' }}> · {p.year}</span>}
              {p.published && p.year === 'Unknown' && <span style={{ color: 'var(--text-subtle)' }}> · {p.published}</span>}
            </p>

            {/* Abstract */}
            {p.abstract && p.abstract !== 'No abstract available' && (
              <p style={{ margin: '0 0 0.875rem', fontSize: '0.84rem', color: 'var(--text-subtle)', lineHeight: 1.6 }}>
                {p.abstract.substring(0, 240)}{p.abstract.length > 240 ? '...' : ''}
              </p>
            )}

            {/* Links */}
            <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
              {p.url && (
                <a href={p.url} target="_blank" rel="noreferrer" className="btn btn-ghost" style={{ fontSize: '0.79rem', padding: '0.3rem 0.65rem', textDecoration: 'none' }}>
                  <ExternalLink size={12} /> View
                </a>
              )}
              {p.pdf_url && p.pdf_url !== p.url && (
                <a href={p.pdf_url} target="_blank" rel="noreferrer" className="btn btn-ghost" style={{ fontSize: '0.79rem', padding: '0.3rem 0.65rem', textDecoration: 'none' }}>
                  <FileText size={12} /> PDF
                </a>
              )}
              {p.oa_url && p.oa_url !== p.url && p.oa_url !== p.pdf_url && (
                <a href={p.oa_url} target="_blank" rel="noreferrer" className="btn btn-ghost" style={{ fontSize: '0.79rem', padding: '0.3rem 0.65rem', textDecoration: 'none', color: '#16a34a' }}>
                  <Unlock size={12} /> Full Text (OA)
                </a>
              )}
              <a
                href={`https://scholar.google.com/scholar?q=${encodeURIComponent(p.title)}`}
                target="_blank"
                rel="noreferrer"
                className="btn btn-ghost"
                style={{ fontSize: '0.79rem', padding: '0.3rem 0.65rem', textDecoration: 'none' }}
              >
                <Search size={12} /> Scholar
              </a>
            </div>
          </div>
        ))}

        {/* Load more button */}
        {hasMore && !loading && (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '1rem 0' }}>
            <button className="btn btn-secondary" onClick={loadMore} disabled={loadingMore}
              style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.65rem 1.5rem' }}
            >
              {loadingMore ? <Spinner size={16} /> : <><ChevronDown size={16} /> Load more results</>}
            </button>
          </div>
        )}
      </div>
        </>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {loadingSaved ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '2rem' }}><Spinner /></div>
          ) : savedSurveys.length === 0 ? (
            <div className="empty-state">
              <Bookmark size={38} style={{ margin: '0 auto 0.875rem', color: 'var(--text-subtle)', display: 'block' }} />
              You haven't saved any surveys yet.
            </div>
          ) : (
            savedSurveys.map((survey, i) => (
              <div key={i} className="animate-slide-up"
                style={{ animationDelay: `${i * 0.03}s`, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '1.25rem 1.5rem', transition: 'transform 0.18s ease, border-color 0.18s ease', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}
              >
                <div>
                  <h3 style={{ margin: '0 0 0.25rem', fontSize: '1.05rem', fontWeight: 600, color: 'var(--text)' }}>{survey.query}</h3>
                  <p style={{ margin: 0, fontSize: '0.85rem', color: 'var(--text-muted)' }}>{survey.papers?.length || 0} papers saved</p>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
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
