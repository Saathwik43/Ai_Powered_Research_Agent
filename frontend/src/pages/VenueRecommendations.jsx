import React, { useState } from 'react';
import { Search, Star, ExternalLink, X, CheckCircle, BookMarked, Sparkles } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Spinner, SkeletonList } from '../components/Loader';
import './VenueRecommendations.css';

const matchColor = m => m >= 90 ? 'var(--success)' : m >= 75 ? 'var(--primary)' : m >= 60 ? 'var(--warning)' : 'var(--text-muted)';

function GuidelinesModal({ g, onClose }) {
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: 'var(--space-4)', backdropFilter: 'blur(5px)' }} onClick={onClose}>
      <div className="animate-scale-in" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', padding: 'var(--space-6)', maxWidth: '580px', width: '100%', maxHeight: '85vh', overflowY: 'auto' }} onClick={e => e.stopPropagation()}>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--space-5)' }}>
          <div>
            <h2 style={{ margin: '0 0 var(--space-1)' }}>{g.venue}</h2>
            <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--text-muted)' }}>Submission Guidelines</p>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-subtle)', display: 'flex', padding: 'var(--space-1)' }}><X size={18} /></button>
        </div>

        {/* Alignment score */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', background: 'var(--primary-light)', border: '1px solid rgba(0,87,255,0.18)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-4) var(--space-4)', marginBottom: 'var(--space-5)' }}>
          <span style={{ fontSize: 'var(--fs-xl)', fontWeight: 800, color: 'var(--primary)', lineHeight: 1 }}>{g.alignment_score}%</span>
          <div>
            <div style={{ fontWeight: 600, color: 'var(--primary)', fontSize: 'var(--fs-sm)' }}>Match Score</div>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginTop: 'var(--space-1)' }}>{g.alignment_notes}</div>
          </div>
        </div>

        {/* Info tiles */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 'var(--space-3)', marginBottom: 'var(--space-5)' }}>
          {[['Word Limit', g.word_limit], ['Citation Style', g.citation_style], ['Submission', g.submission_format]].map(([label, val]) => (
            <div key={label} style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', padding: 'var(--space-3)' }}>
              <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--text-subtle)', marginBottom: 'var(--space-1)' }}>{label}</div>
              <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 500 }}>{val}</div>
            </div>
          ))}
        </div>

        {/* Sections */}
        {g.sections_required?.length > 0 && (
          <div style={{ marginBottom: 'var(--space-4)' }}>
            <p style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-subtle)', marginBottom: 'var(--space-2)' }}>Required Sections</p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
              {g.sections_required.map((s, i) => (
                <span key={i} style={{ background: 'var(--primary-light)', color: 'var(--primary)', padding: 'var(--space-1) var(--space-3)', borderRadius: '999px', fontSize: 'var(--fs-xs)', fontWeight: 600 }}>{s}</span>
              ))}
            </div>
          </div>
        )}

        {/* Requirements */}
        {g.key_requirements?.length > 0 && (
          <div style={{ marginBottom: 'var(--space-4)' }}>
            <p style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-subtle)', marginBottom: 'var(--space-2)' }}>Requirements</p>
            {g.key_requirements.map((r, i) => (
              <div key={i} style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'flex-start', fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginBottom: 'var(--space-2)' }}>
                <CheckCircle size={14} color="var(--success)" style={{ flexShrink: 0, marginTop: 'var(--space-1)' }} />{r}
              </div>
            ))}
          </div>
        )}

        {/* Tips */}
        {g.formatting_tips?.length > 0 && (
          <div>
            <p style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-subtle)', marginBottom: 'var(--space-2)' }}>Formatting Tips</p>
            {g.formatting_tips.map((tip, i) => (
              <div key={i} style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'flex-start', fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginBottom: 'var(--space-2)' }}>
                <Star size={13} color="var(--warning)" fill="var(--warning)" style={{ flexShrink: 0, marginTop: 'var(--space-1)' }} />{tip}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function VenueRecommendations() {
  const { authFetch } = useAuth();
  const [domain, setDomain]     = useState('');
  const [abstract, setAbstract] = useState('');
  const [venues, setVenues]     = useState([]);
  const [loading, setLoading]   = useState(false);
  const [guidelines, setGuidelines]   = useState(null);
  const [guideLoading, setGuideLoading] = useState(null);
  const [error, setError] = useState('');
  const [hasSearched, setHasSearched] = useState(false);

  const recommend = async () => {
    if (!domain.trim()) return;
    setLoading(true); setVenues([]); setError(''); setHasSearched(true);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/venues`, { method: 'POST', body: JSON.stringify({ abstract, domain }) });
      if (res.status === 429 || res.status === 503) {
        if (res.status === 503) {
          try {
            const data = await res.json();
            if (data?.detail?.verification_unavailable) {
              setError('Verification temporarily unavailable, please try again shortly.');
              setVenues([]);
              return;
            }
          } catch(e) {}
        }
        setError('Rate limit exceeded. Please wait a minute before trying again.');
        return;
      }
      if (!res.ok) {
        setError('Failed to find venues. Please try again.');
        return;
      }
      const data = await res.json();
      
      if (data.coherence_check === 'failed') {
        setError(`"${domain}" doesn't look like a research domain. Try a specific field or subject area.`);
        setVenues([]);
        return;
      }
      
      setVenues(data.data || []);
    } catch (e) {
      console.error(e);
      setError('Network error. Please try again.');
    }
    finally { setLoading(false); }
  };

  const viewGuidelines = async (venue) => {
    const key = venue.id || venue.name;
    setGuideLoading(key);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/guidelines`, { method: 'POST', body: JSON.stringify({ manuscript: { abstract, domain }, venue: { name: venue.name, type: venue.type, scope: venue.scope } }) });
      const data = await res.json();
      setGuidelines(data.data);
    } catch (e) { console.error(e); }
    finally { setGuideLoading(null); }
  };

  return (
    <div className="animate-fade-in">
      <div style={{ marginBottom: 'var(--space-6)' }}>
        <h1>Venue Recommendations</h1>
        <p className="text-muted">Find the best journals and conferences for your manuscript.</p>
      </div>

      {/* Input panel */}
      <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-5)', marginBottom: 'var(--space-5)' }}>
        <div className="responsive-row" style={{ display: 'flex', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
          <div style={{ position: 'relative', flex: 1 }}>
            <Search size={15} style={{ position: 'absolute', left: '1rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-subtle)', pointerEvents: 'none' }} />
            <input placeholder="Research domain (e.g. AI in Healthcare)..." value={domain} onChange={e => setDomain(e.target.value)} onKeyDown={e => e.key === 'Enter' && recommend()} style={{ paddingLeft: 'var(--space-7)' }} />
          </div>
        </div>
        <div className="responsive-row" style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'flex-start' }}>
          <textarea placeholder="Paste your abstract for more accurate matching (optional)..." value={abstract} onChange={e => setAbstract(e.target.value)} style={{ flex: 1, minHeight: '88px', resize: 'vertical' }} />
          <button className="btn btn-primary responsive-fit" onClick={recommend} disabled={loading || !domain.trim()} style={{ minWidth: '130px', height: '88px' }}>
            {loading ? <Spinner size={16} /> : <><Search size={14} /> Find Venues</>}
          </button>
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
            <Sparkles size={18} style={{ color: 'var(--primary)' }} /> Analyzing topic and finding venues...
          </h2>
          <SkeletonList count={3} />
        </div>
      )}

      {/* Venue grid */}
      {!loading && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 'var(--space-4)' }}>
          {venues.length === 0 && !hasSearched && (
            <div className="empty-state" style={{ gridColumn: '1 / -1' }}>
              <BookMarked size={38} style={{ margin: '0 auto var(--space-3)', color: 'var(--text-subtle)', display: 'block' }} />
              Enter your research domain to get venue recommendations.
            </div>
          )}
          
          {venues.length === 0 && hasSearched && !error && (
            <div style={{ gridColumn: '1 / -1', textAlign: 'center', padding: 'var(--space-7) var(--space-5)', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)' }}>
              <Search size={32} style={{ color: 'var(--text-subtle)', marginBottom: 'var(--space-4)', opacity: 0.5 }} />
              <h3 style={{ margin: '0 0 var(--space-2)', fontSize: 'var(--fs-md)', color: 'var(--text)' }}>No venues found for "{domain}"</h3>
              <p style={{ margin: 0, color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>Try a different research domain.</p>
            </div>
          )}
          {venues.map((v, i) => (
            <div key={v.id || i} className="venue-card animate-card-in" style={{ animationDelay: `${i * 0.07}s` }}>
              {/* Name + match */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--space-3)' }}>
                <div style={{ flex: 1, minWidth: 0, paddingRight: 'var(--space-3)' }}>
                  <h3 style={{ margin: '0 0 var(--space-1)', fontSize: 'var(--fs-sm)', lineHeight: 1.35 }}>{v.name}</h3>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-subtle)', background: 'var(--bg-elevated)', border: '1px solid var(--border)', padding: 'var(--space-1) var(--space-2)', borderRadius: '999px' }}>{v.type}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-1)', fontWeight: 700, fontSize: 'var(--fs-base)', color: matchColor(v.match), flexShrink: 0 }}>
                  <Star size={14} fill={matchColor(v.match)} color={matchColor(v.match)} />{v.match}%
                </div>
              </div>

              {/* Details */}
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginBottom: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
                <span><span style={{ color: 'var(--text-subtle)', fontWeight: 600 }}>Impact:</span> {v.impact}</span>
                <span><span style={{ color: 'var(--text-subtle)', fontWeight: 600 }}>Scope:</span> {v.scope}</span>
              </div>

              {/* Actions */}
              <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                <button className="btn btn-primary" style={{ flex: 1, fontSize: 'var(--fs-sm)' }} onClick={() => viewGuidelines(v)} disabled={guideLoading === (v.id || v.name)}>
                  {guideLoading === (v.id || v.name) ? <Spinner size={16} /> : 'View Guidelines'}
                </button>
                <button className="btn btn-icon" onClick={() => window.open(`https://scholar.google.com/scholar?q=${encodeURIComponent(v.name)}`, '_blank')} title="Search on Google Scholar">
                  <ExternalLink size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {guidelines && <GuidelinesModal g={guidelines} onClose={() => setGuidelines(null)} />}
    </div>
  );
}
