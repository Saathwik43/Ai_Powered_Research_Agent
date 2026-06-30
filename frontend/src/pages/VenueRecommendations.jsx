import React, { useState } from 'react';
import { Search, Star, ExternalLink, X, CheckCircle, BookMarked } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Player } from '@lottiefiles/react-lottie-player';
import loadingAnimation from '../assets/groovyWalk.json';
const matchColor = m => m >= 90 ? 'var(--success)' : m >= 75 ? 'var(--primary)' : m >= 60 ? 'var(--warning)' : 'var(--text-muted)';

function GuidelinesModal({ g, onClose }) {
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: '1rem', backdropFilter: 'blur(5px)' }} onClick={onClose}>
      <div className="animate-scale-in" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', padding: '2rem', maxWidth: '580px', width: '100%', maxHeight: '85vh', overflowY: 'auto' }} onClick={e => e.stopPropagation()}>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.5rem' }}>
          <div>
            <h2 style={{ margin: '0 0 0.25rem' }}>{g.venue}</h2>
            <p style={{ margin: 0, fontSize: '0.83rem', color: 'var(--text-muted)' }}>Submission Guidelines</p>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-subtle)', display: 'flex', padding: '0.25rem' }}><X size={18} /></button>
        </div>

        {/* Alignment score */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', background: 'var(--primary-light)', border: '1px solid rgba(0,87,255,0.18)', borderRadius: 'var(--radius-lg)', padding: '1rem 1.25rem', marginBottom: '1.5rem' }}>
          <span style={{ fontSize: '2rem', fontWeight: 800, color: 'var(--primary)', lineHeight: 1 }}>{g.alignment_score}%</span>
          <div>
            <div style={{ fontWeight: 600, color: 'var(--primary)', fontSize: '0.88rem' }}>Match Score</div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.15rem' }}>{g.alignment_notes}</div>
          </div>
        </div>

        {/* Info tiles */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: '0.65rem', marginBottom: '1.5rem' }}>
          {[['Word Limit', g.word_limit], ['Citation Style', g.citation_style], ['Submission', g.submission_format]].map(([label, val]) => (
            <div key={label} style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', padding: '0.75rem' }}>
              <div style={{ fontSize: '0.67rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--text-subtle)', marginBottom: '0.25rem' }}>{label}</div>
              <div style={{ fontSize: '0.85rem', fontWeight: 500 }}>{val}</div>
            </div>
          ))}
        </div>

        {/* Sections */}
        {g.sections_required?.length > 0 && (
          <div style={{ marginBottom: '1.25rem' }}>
            <p style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-subtle)', marginBottom: '0.6rem' }}>Required Sections</p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
              {g.sections_required.map((s, i) => (
                <span key={i} style={{ background: 'var(--primary-light)', color: 'var(--primary)', padding: '0.2rem 0.7rem', borderRadius: '999px', fontSize: '0.77rem', fontWeight: 600 }}>{s}</span>
              ))}
            </div>
          </div>
        )}

        {/* Requirements */}
        {g.key_requirements?.length > 0 && (
          <div style={{ marginBottom: '1.25rem' }}>
            <p style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-subtle)', marginBottom: '0.6rem' }}>Requirements</p>
            {g.key_requirements.map((r, i) => (
              <div key={i} style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start', fontSize: '0.86rem', color: 'var(--text-muted)', marginBottom: '0.4rem' }}>
                <CheckCircle size={14} color="var(--success)" style={{ flexShrink: 0, marginTop: '0.15rem' }} />{r}
              </div>
            ))}
          </div>
        )}

        {/* Tips */}
        {g.formatting_tips?.length > 0 && (
          <div>
            <p style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-subtle)', marginBottom: '0.6rem' }}>Formatting Tips</p>
            {g.formatting_tips.map((tip, i) => (
              <div key={i} style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start', fontSize: '0.86rem', color: 'var(--text-muted)', marginBottom: '0.4rem' }}>
                <Star size={13} color="var(--warning)" fill="var(--warning)" style={{ flexShrink: 0, marginTop: '0.18rem' }} />{tip}
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

  const recommend = async () => {
    if (!domain.trim()) return;
    setLoading(true); setVenues([]);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/venues`, { method: 'POST', body: JSON.stringify({ abstract, domain }) });
      const data = await res.json();
      setVenues(data.data || []);
    } catch (e) { console.error(e); }
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
      <div style={{ marginBottom: '2rem' }}>
        <h1>Venue Recommendations</h1>
        <p className="text-muted">Find the best journals and conferences for your manuscript.</p>
      </div>

      {/* Input panel */}
      <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '1.5rem', marginBottom: '1.75rem' }}>
        <div className="responsive-row" style={{ display: 'flex', gap: '0.75rem', marginBottom: '0.875rem' }}>
          <div style={{ position: 'relative', flex: 1 }}>
            <Search size={15} style={{ position: 'absolute', left: '1rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-subtle)', pointerEvents: 'none' }} />
            <input placeholder="Research domain (e.g. AI in Healthcare)..." value={domain} onChange={e => setDomain(e.target.value)} onKeyDown={e => e.key === 'Enter' && recommend()} style={{ paddingLeft: '2.6rem' }} />
          </div>
        </div>
        <div className="responsive-row" style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
          <textarea placeholder="Paste your abstract for more accurate matching (optional)..." value={abstract} onChange={e => setAbstract(e.target.value)} style={{ flex: 1, minHeight: '88px', resize: 'vertical' }} />
          <button className="btn btn-primary responsive-fit" onClick={recommend} disabled={loading || !domain.trim()} style={{ minWidth: '130px', height: '88px' }}>
            {loading ? <><Spin /> Finding...</> : <><Search size={14} /> Find Venues</>}
          </button>
        </div>
      </div>

      {/* Venue grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '1rem' }}>
        {venues.length === 0 && !loading && (
          <div className="empty-state" style={{ gridColumn: '1 / -1' }}>
            <BookMarked size={38} style={{ margin: '0 auto 0.875rem', color: 'var(--text-subtle)', display: 'block' }} />
            Enter your research domain to get venue recommendations.
          </div>
        )}
        {venues.map((v, i) => (
          <div key={v.id || i} className="animate-slide-up" style={{ animationDelay: `${i * 0.07}s`, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '1.5rem', transition: 'transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease' }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(0,87,255,0.32)'; e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = 'var(--shadow-glow)'; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.transform = ''; e.currentTarget.style.boxShadow = 'none'; }}
          >
            {/* Name + match */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.875rem' }}>
              <div style={{ flex: 1, minWidth: 0, paddingRight: '0.75rem' }}>
                <h3 style={{ margin: '0 0 0.35rem', fontSize: '0.97rem', lineHeight: 1.35 }}>{v.name}</h3>
                <span style={{ fontSize: '0.72rem', color: 'var(--text-subtle)', background: 'var(--bg-elevated)', border: '1px solid var(--border)', padding: '0.15rem 0.55rem', borderRadius: '999px' }}>{v.type}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontWeight: 700, fontSize: '1rem', color: matchColor(v.match), flexShrink: 0 }}>
                <Star size={14} fill={matchColor(v.match)} color={matchColor(v.match)} />{v.match}%
              </div>
            </div>

            {/* Details */}
            <div style={{ fontSize: '0.83rem', color: 'var(--text-muted)', marginBottom: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
              <span><span style={{ color: 'var(--text-subtle)', fontWeight: 600 }}>Impact:</span> {v.impact}</span>
              <span><span style={{ color: 'var(--text-subtle)', fontWeight: 600 }}>Scope:</span> {v.scope}</span>
            </div>

            {/* Actions */}
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn btn-primary" style={{ flex: 1, fontSize: '0.83rem' }} onClick={() => viewGuidelines(v)} disabled={guideLoading === (v.id || v.name)}>
                {guideLoading === (v.id || v.name) ? <><Spin /> Loading...</> : 'View Guidelines'}
              </button>
              <button className="btn btn-icon" onClick={() => window.open(`https://scholar.google.com/scholar?q=${encodeURIComponent(v.name)}`, '_blank')} title="Search on Google Scholar">
                <ExternalLink size={14} />
              </button>
            </div>
          </div>
        ))}
      </div>

      {guidelines && <GuidelinesModal g={guidelines} onClose={() => setGuidelines(null)} />}
    </div>
  );
}

const Spin = () => (
  <span style={{ width: 28, height: 28, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', verticalAlign: 'middle', marginRight: '0.35rem' }}>
    <Player autoplay loop src={loadingAnimation} style={{ height: '100%', width: '100%' }} />
  </span>
);
