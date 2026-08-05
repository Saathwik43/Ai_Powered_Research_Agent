import React, { useState, useEffect } from 'react';
import { Search, Star, ExternalLink, X, CheckCircle, BookMarked, Sparkles } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Spinner, SkeletonList } from '../components/Loader';
import './VenueRecommendations.css';

const matchColor = m => m >= 90 ? 'var(--success)' : m >= 75 ? 'var(--primary)' : m >= 60 ? 'var(--warning)' : 'var(--text-muted)';

function GuidelinesModal({ g, onClose }) {
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = prev;
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  return (
    <div className="venue-modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="venue-modal animate-scale-in"
        role="dialog"
        aria-modal="true"
        aria-labelledby="venue-modal-title"
        onClick={e => e.stopPropagation()}
      >
        <div className="venue-modal-header">
          <div>
            <h2 id="venue-modal-title">{g.venue}</h2>
            <p className="venue-modal-kicker">Submission Guidelines</p>
          </div>
          <button type="button" className="venue-modal-close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="venue-modal-score">
          <span className="venue-modal-score-value">{g.alignment_score}%</span>
          <div>
            <div className="venue-modal-score-label">Match Score</div>
            <div className="venue-modal-score-notes">{g.alignment_notes}</div>
          </div>
        </div>

        <div className="venue-modal-tiles">
          {[['Word Limit', g.word_limit], ['Citation Style', g.citation_style], ['Submission', g.submission_format]].map(([label, val]) => (
            <div key={label} className="venue-modal-tile">
              <div className="venue-modal-tile-label">{label}</div>
              <div className="venue-modal-tile-value">{val}</div>
            </div>
          ))}
        </div>

        {g.sections_required?.length > 0 && (
          <div className="venue-modal-block">
            <p className="venue-modal-section-label">Required Sections</p>
            <div className="venue-modal-chips">
              {g.sections_required.map((s, i) => (
                <span key={i} className="venue-modal-chip">{s}</span>
              ))}
            </div>
          </div>
        )}

        {g.key_requirements?.length > 0 && (
          <div className="venue-modal-block">
            <p className="venue-modal-section-label">Requirements</p>
            {g.key_requirements.map((r, i) => (
              <div key={i} className="venue-modal-list-item">
                <CheckCircle size={14} color="var(--success)" />
                <span>{r}</span>
              </div>
            ))}
          </div>
        )}

        {g.formatting_tips?.length > 0 && (
          <div className="venue-modal-block">
            <p className="venue-modal-section-label">Formatting Tips</p>
            {g.formatting_tips.map((tip, i) => (
              <div key={i} className="venue-modal-list-item">
                <Star size={13} color="var(--warning)" fill="var(--warning)" />
                <span>{tip}</span>
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
    <div className="animate-fade-in venue-page">
      <header className="venue-masthead">
        <h1>Venue Recommendations</h1>
        <p className="text-muted">Find the best journals and conferences for your manuscript.</p>
      </header>

      <div className="venue-form">
        <div className="venue-field">
          <label htmlFor="venue-domain" className="venue-field-label">Research domain</label>
          <div className="venue-field-control">
            <Search size={15} className="venue-field-icon" aria-hidden="true" />
            <input
              id="venue-domain"
              placeholder="e.g. AI in Healthcare"
              value={domain}
              onChange={e => setDomain(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && recommend()}
            />
          </div>
        </div>

        <div className="venue-field">
          <label htmlFor="venue-abstract" className="venue-field-label">
            Abstract <span className="venue-field-optional">(optional)</span>
          </label>
          <textarea
            id="venue-abstract"
            className="venue-abstract"
            placeholder="Paste your abstract for more accurate matching…"
            value={abstract}
            onChange={e => setAbstract(e.target.value)}
          />
        </div>

        <button
          type="button"
          className="btn btn-primary venue-submit"
          onClick={recommend}
          disabled={loading || !domain.trim()}
        >
          {loading ? <Spinner size={16} /> : <><Search size={14} /> Find Venues</>}
        </button>

        {error && (
          <div className="venue-error" role="alert">
            <X size={15} /> {error}
          </div>
        )}
      </div>

      {loading && (
        <div className="venue-loading">
          <h2 className="venue-section-title">
            <Sparkles size={18} /> Analyzing topic and finding venues…
          </h2>
          <SkeletonList count={3} />
        </div>
      )}

      {!loading && (
        <div className="venue-results">
          {venues.length === 0 && !hasSearched && (
            <div className="empty-state venue-empty">
              <BookMarked size={38} />
              Enter your research domain to get venue recommendations.
            </div>
          )}

          {venues.length === 0 && hasSearched && !error && (
            <div className="venue-empty-card">
              <Search size={32} />
              <h3>No venues found for &ldquo;{domain}&rdquo;</h3>
              <p>Try a different research domain.</p>
            </div>
          )}

          <div className="venue-results-grid">
            {venues.map((v, i) => (
              <div key={v.id || i} className="venue-card animate-card-in" style={{ animationDelay: `${i * 0.07}s` }}>
                <div className="venue-card-head">
                  <div className="venue-card-titles">
                    <h3>{v.name}</h3>
                    <span className="venue-card-type">{v.type}</span>
                  </div>
                  <div className="venue-card-match" style={{ color: matchColor(v.match) }}>
                    <Star size={14} fill={matchColor(v.match)} color={matchColor(v.match)} />
                    {v.match}%
                  </div>
                </div>

                <div className="venue-card-meta">
                  <span><strong>Impact:</strong> {v.impact}</span>
                  <span><strong>Scope:</strong> {v.scope}</span>
                </div>

                <div className="venue-card-actions">
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => viewGuidelines(v)}
                    disabled={guideLoading === (v.id || v.name)}
                  >
                    {guideLoading === (v.id || v.name) ? <Spinner size={16} /> : 'View Guidelines'}
                  </button>
                  <button
                    type="button"
                    className="btn btn-icon"
                    onClick={() => window.open(`https://scholar.google.com/scholar?q=${encodeURIComponent(v.name)}`, '_blank')}
                    title="Search on Google Scholar"
                  >
                    <ExternalLink size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {guidelines && <GuidelinesModal g={guidelines} onClose={() => setGuidelines(null)} />}
    </div>
  );
}
