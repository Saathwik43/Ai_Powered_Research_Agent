import React, { useState } from 'react';
import { Search, Download, ExternalLink, Save, BookOpen, FileText, X } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Player } from '@lottiefiles/react-lottie-player';
import loadingAnimation from '../assets/groovyWalk.json';
export default function LiteratureSurvey() {
  const { authFetch } = useAuth();
  const [query, setQuery]         = useState('');
  const [papers, setPapers]       = useState([]);
  const [loading, setLoading]     = useState(false);
  const [saveStatus, setSaveStatus] = useState('');
  const [searchError, setSearchError] = useState('');
  const [hasSearched, setHasSearched] = useState(false);
  const [lastQuery, setLastQuery] = useState('');

  const search = async (q = query) => {
    if (!q.trim()) return;
    setLoading(true); setSaveStatus(''); setSearchError('');
    setHasSearched(true); setLastQuery(q);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/literature?query=${encodeURIComponent(q)}&limit=12`);
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
    finally { setLoading(false); }
  };

  const exportSurvey = () => {
    if (!papers.length) return;
    const blob = new Blob([JSON.stringify({ query, papers, exportedAt: new Date().toISOString() }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `survey-${query.replace(/\s+/g, '-')}.json`; a.click();
    URL.revokeObjectURL(url);
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

  return (
    <div className="animate-fade-in">
      <div style={{ marginBottom: '2rem' }}>
        <h1>Literature Survey</h1>
        <p className="text-muted">Search research papers from multiple academic sources in one place.</p>
      </div>

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
          {loading ? <><Spin /> Searching...</> : <><Search size={14} /> Search</>}
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
            <span style={{ fontWeight: 600, fontSize: '0.93rem' }}>{papers.length} results</span>
            <span style={{ color: 'var(--text-subtle)', fontSize: '0.83rem' }}>for "{query}"</span>
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
              {p.citations > 0 && (
                <span style={{ flexShrink: 0, fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', background: 'var(--bg-elevated)', border: '1px solid var(--border)', padding: '0.18rem 0.55rem', borderRadius: '999px', whiteSpace: 'nowrap' }}>
                  {p.citations.toLocaleString()} citations
                </span>
              )}
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
      </div>
    </div>
  );
}

const Spin = () => (
  <span style={{ width: 28, height: 28, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', verticalAlign: 'middle', marginRight: '0.35rem' }}>
    <Player autoplay loop src={loadingAnimation} style={{ height: '100%', width: '100%' }} />
  </span>
);
