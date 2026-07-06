import React, { useEffect, useState } from 'react';
import { CheckCircle, Circle, Save, FileText, Wand2, FolderOpen, X, Search } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Player } from '@lottiefiles/react-lottie-player';
import loadingAnimation from '../assets/groovyWalk.json';
const STEPS = [
  { id: 'abstract',    label: 'Abstract' },
  { id: 'lit_review',  label: 'Literature Review' },
  { id: 'methodology', label: 'Methodology' },
  { id: 'results',     label: 'Results' },
  { id: 'references',  label: 'References' },
];

export default function ManuscriptBuilder() {
  const { authFetch } = useAuth();
  const [active, setActive]         = useState('abstract');
  const [topic, setTopic]           = useState('');
  const [content, setContent]       = useState({});
  const [generating, setGenerating] = useState(false);
  const [saveStatus, setSaveStatus] = useState('');
  const [showLoad, setShowLoad]     = useState(false);
  const [drafts, setDrafts]         = useState([]);
  const [draftFilter, setDraftFilter] = useState('');
  const [draftLoading, setDraftLoading] = useState(false);
  const [loadError, setLoadError]   = useState('');
  const [editPrompt, setEditPrompt] = useState('');
  const [editing, setEditing]       = useState(false);
  const [editError, setEditError]   = useState('');
  const [generateError, setGenerateError] = useState('');
  const [unverifiedWarning, setUnverifiedWarning] = useState('');
  const [unverifiedNumbers, setUnverifiedNumbers] = useState([]);
  
  // Phase B additions
  const [citationStyle, setCitationStyle] = useState('ieee');
  const [manuscriptRefs, setManuscriptRefs] = useState(null);
  
  const [gapAnalysis, setGapAnalysis] = useState(null);

  const done = STEPS.filter(s => content[s.id]?.trim()).map(s => s.id);

  const generate = async () => {
    if (!topic.trim()) return;
    setGenerating(true);
    setGenerateError('');
    setUnverifiedWarning('');
    setUnverifiedNumbers([]);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript`, {
        method: 'POST',
        body: JSON.stringify({ topic, section: active, context: 'Use latest research trends and cite recent advancements.', citation_style: citationStyle }),
      });
      if (res.status === 429 || res.status === 503) {
        if (res.status === 503) {
          try {
            const data = await res.json();
            if (data?.detail?.verification_unavailable) {
              setGenerateError('Verification temporarily unavailable, please try again shortly.');
              return;
            }
          } catch(e) {}
        }
      setGenerateError("Rate limit exceeded. Please wait a minute before generating again.");
      return;
      }
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        setGenerateError(errorData.detail || 'Failed to generate section. Please try again.');
        return;
      }
      const data = await res.json();
      setContent(prev => ({ ...prev, [active]: data.content }));
      if (data.formatted_references) {
        setManuscriptRefs(data.formatted_references);
      }
      if (data.unverified_citations) {
        setUnverifiedWarning('Warning: The generated text contains citations that could not be verified against the provided context. Please verify them independently.');
      }
      if (data.unverified_numbers && data.unverified_numbers.length > 0) {
        setUnverifiedNumbers(data.unverified_numbers);
      }
      
      if (data.gap_analysis) {
        setGapAnalysis(data.gap_analysis);
      } else if (active === 'lit_review' || active === 'literature_review') {
        setGapAnalysis(null);
      }
    } catch (e) {
      console.error(e);
      setGenerateError('Network error. Please try again.');
    }
    finally { setGenerating(false); }
  };



  const applyEdit = async () => {
    if (!editPrompt.trim() || !content[active]) return;
    setEditing(true);
    setEditError('');
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript/edit`, {
        method: 'POST',
        body: JSON.stringify({
          topic,
          section: active,
          current_content: content[active],
          instructions: editPrompt
        }),
      });
      if (res.status === 429 || res.status === 503) {
        if (res.status === 503) {
          try {
            const data = await res.json();
            if (data?.detail?.verification_unavailable) {
              setEditError('Verification temporarily unavailable, please try again shortly.');
              return;
            }
          } catch(e) {}
        }
        setEditError('Rate limit exceeded. Please wait a minute before trying again.');
        return;
      }
      if (!res.ok) {
        setEditError('Failed to apply revision. Please try again.');
        return;
      }
      const data = await res.json();
      setContent(prev => ({ ...prev, [active]: data.content }));
      setEditPrompt('');
    } catch (e) {
      setEditError('Network error. Please try again.');
    } finally {
      setEditing(false);
    }
  };

  const save = async () => {
    if (!topic || !Object.keys(content).length) return;
    setSaveStatus('saving');
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript/save`, { method: 'POST', body: JSON.stringify({ topic, content }) });
      setSaveStatus(res.ok ? 'saved' : 'error');
      if (res.ok) setTimeout(() => setSaveStatus(''), 3000);
    } catch { setSaveStatus('error'); }
  };

  useEffect(() => {
    if (!showLoad) return;
    const fetchDrafts = async () => {
      setDraftLoading(true);
      setLoadError('');
      try {
        const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript/list`);
        const data = await res.json();
        setDrafts(Array.isArray(data.data) ? data.data : []);
      } catch {
        setLoadError('Could not load saved drafts.');
      } finally {
        setDraftLoading(false);
      }
    };
    fetchDrafts();
  }, [authFetch, showLoad]);

  const load = async (draftTopic) => {
    const t = draftTopic.trim();
    if (!t) return;
    setLoadError('');
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript/load?topic=${encodeURIComponent(t)}`);
      if (res.ok) {
        const data = await res.json();
        setContent(data.data.content || {});
        setTopic(data.data.topic || t);
        setShowLoad(false);
        setDraftFilter('');
      } else { setLoadError('No draft found for this topic.'); }
    } catch { setLoadError('Could not connect.'); }
  };

  const exportDoc = () => {
    if (!topic || !Object.keys(content).length) return;
    const md = [`# ${topic}\n`, ...STEPS.filter(s => content[s.id]).flatMap(s => [`\n## ${s.label}\n`, content[s.id]])].join('\n');
    const blob = new Blob([md], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${topic.replace(/\s+/g, '-')}.md`; a.click();
    URL.revokeObjectURL(url);
  };

  const currentStep = STEPS.find(s => s.id === active);
  const visibleDrafts = drafts.filter(draft =>
    draft.topic?.toLowerCase().includes(draftFilter.trim().toLowerCase())
  );

  return (
    <div className="animate-fade-in">
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '2rem', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h1>Manuscript Builder</h1>
          <p className="text-muted">Write your research paper section by section with AI assistance.</p>
          <div style={{ marginTop: '0.75rem', color: 'var(--text-muted)', fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <span style={{ fontSize: '1rem' }}>💡</span>
            <span><strong>Note:</strong> AI can make mistakes, review before proceeding.</span>
          </div>
        </div>
        <div className="responsive-actions" style={{ display: 'flex', gap: '0.5rem' }}>
          <button className="btn btn-secondary" onClick={() => { setShowLoad(true); setLoadError(''); setDraftFilter(''); }}>
            <FolderOpen size={14} /> Load Draft
          </button>
          <button className="btn btn-primary" onClick={exportDoc} disabled={!Object.keys(content).length}>
            <FileText size={14} /> Export
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: '1.25rem', alignItems: 'flex-start', flexWrap: 'wrap' }}>

        {/* Sidebar stepper */}
        <div style={{ flex: '0 0 210px', minWidth: '180px', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '1.25rem', position: 'sticky', top: '1.5rem' }}>
          <p style={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.09em', color: 'var(--text-subtle)', marginBottom: '0.875rem' }}>Sections</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
            {STEPS.map(step => {
              const isDone   = done.includes(step.id);
              const isActive = active === step.id;
              return (
                <div key={step.id} onClick={() => setActive(step.id)}
                  style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', padding: '0.6rem 0.75rem', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: '0.88rem', fontWeight: isActive ? 700 : 500, color: isActive ? 'var(--primary)' : isDone ? 'var(--text)' : 'var(--text-muted)', background: isActive ? 'var(--primary-light)' : 'transparent', border: `1px solid ${isActive ? 'rgba(0,87,255,0.22)' : 'transparent'}`, transition: 'var(--transition)' }}
                >
                  {isDone
                    ? <CheckCircle size={15} color={isActive ? 'var(--primary)' : 'var(--success)'} />
                    : <Circle size={15} />}
                  {step.label}
                </div>
              );
            })}
          </div>
          <div style={{ marginTop: '1rem', paddingTop: '0.875rem', borderTop: '1px solid var(--border)', fontSize: '0.75rem', color: 'var(--text-subtle)', textAlign: 'center' }}>
            {done.length} of {STEPS.length} written
          </div>
        </div>

        {/* Editor */}
        <div style={{ flex: 1, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '1.5rem', minWidth: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem', flexWrap: 'wrap', gap: '0.5rem' }}>
            <h2 style={{ margin: 0, fontSize: '1.1rem' }}>{currentStep?.label}</h2>
            <div className="responsive-actions" style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn btn-secondary" onClick={generate} disabled={generating || !topic.trim()}>
                {generating ? <><Spin /> Writing...</> : <><Wand2 size={14} /> Generate</>}
              </button>
              <button className="btn btn-ghost" onClick={save} disabled={!topic || !Object.keys(content).length || saveStatus === 'saving'}>
                <Save size={14} /> {saveStatus === 'saving' ? 'Saving...' : saveStatus === 'saved' ? 'Saved' : 'Save'}
              </button>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
            <input
              placeholder="Enter your research topic..."
              value={topic}
              onChange={e => setTopic(e.target.value)}
              style={{ flex: 1, minWidth: '250px' }}
            />
            <select 
              value={citationStyle} 
              onChange={e => setCitationStyle(e.target.value)}
              style={{ padding: '0.6rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', background: 'var(--bg-input)', color: 'var(--text)', fontSize: '0.85rem' }}
            >
              <option value="ieee">IEEE</option>
              <option value="apa">APA</option>
              <option value="chicago">Chicago</option>
              <option value="oxford">Oxford</option>
            </select>
          </div>

          {/* Gap Analysis Panel */}
          {gapAnalysis && (
            <div style={{ marginBottom: '1.5rem', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '1.5rem' }}>
              <h3 style={{ margin: '0 0 1rem 0', fontSize: '1.05rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}><Search size={16} color="var(--primary)" /> Research Gaps Analysis</h3>
              
              {gapAnalysis.status === 'insufficient_literature' ? (
                <div style={{ textAlign: 'center', padding: '1rem', color: 'var(--text-muted)' }}>
                  <p style={{ margin: 0 }}>{gapAnalysis.message}</p>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
                  <div>
                    <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.9rem', color: 'var(--success)' }}>Well Covered</h4>
                    <ul style={{ margin: 0, paddingLeft: '1.5rem', fontSize: '0.88rem', color: 'var(--text)' }}>
                      {(gapAnalysis.well_covered || []).map((item, i) => <li key={i} style={{ marginBottom: '0.25rem' }}>{item}</li>)}
                    </ul>
                  </div>
                  <div>
                    <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.9rem', color: 'var(--warning)' }}>Remaining Gaps</h4>
                    <ul style={{ margin: 0, paddingLeft: '1.5rem', fontSize: '0.88rem', color: 'var(--text)' }}>
                      {(gapAnalysis.gaps || []).map((item, i) => <li key={i} style={{ marginBottom: '0.25rem' }}>{item}</li>)}
                    </ul>
                  </div>
                  <div style={{ background: 'rgba(0, 87, 255, 0.05)', padding: '1rem', borderRadius: 'var(--radius-md)', border: '1px solid rgba(0, 87, 255, 0.15)' }}>
                    <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.9rem', color: 'var(--primary)' }}>Suggested Direction</h4>
                    <p style={{ margin: 0, fontSize: '0.88rem', color: 'var(--text)' }}>{gapAnalysis.suggested_direction}</p>
                    {gapAnalysis.vagueness_warning && (
                      <p style={{ margin: '0.5rem 0 0 0', fontSize: '0.75rem', color: 'var(--warning)' }}>⚠️ This suggestion may be broad — consider refining.</p>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {generateError && (
            <div style={{ marginTop: '1rem', marginBottom: '1rem', padding: '0.85rem 1rem', background: 'rgba(229,28,35,0.08)', border: '1px solid rgba(229,28,35,0.2)', borderRadius: 'var(--radius-md)', color: 'var(--danger)', fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <X size={15} /> {generateError}
            </div>
          )}
          {unverifiedWarning && <p style={{ color: 'var(--warning)', fontSize: '0.85rem', marginBottom: '1rem', background: 'rgba(255,176,0,0.1)', padding: '0.75rem', borderRadius: 'var(--radius-md)' }}>{unverifiedWarning}</p>}
          {unverifiedNumbers.length > 0 && (
            <div style={{ color: 'var(--danger)', fontSize: '0.85rem', marginBottom: '1rem', background: 'rgba(229,28,35,0.08)', border: '1px solid rgba(229,28,35,0.2)', padding: '0.75rem', borderRadius: 'var(--radius-md)' }}>
              <p style={{ margin: '0 0 0.5rem 0', fontWeight: 600 }}>⚠️ Unverified Statistics Detected</p>
              <p style={{ margin: '0 0 0.5rem 0' }}>The following numbers were not found in the source papers and may be hallucinated:</p>
              <ul style={{ margin: 0, paddingLeft: '1.5rem' }}>
                {unverifiedNumbers.map((num, i) => <li key={i}><strong>{num}</strong></li>)}
              </ul>
            </div>
          )}

          <textarea
            placeholder={`Write your ${currentStep?.label.toLowerCase()} here, or click Generate for AI assistance...`}
            value={content[active] || ''}
            onChange={e => setContent(prev => ({ ...prev, [active]: e.target.value }))}
            style={{ width: '100%', minHeight: '420px', padding: '1rem', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', color: 'var(--text)', fontFamily: 'inherit', fontSize: '0.93rem', resize: 'vertical', outline: 'none', lineHeight: 1.75, transition: 'var(--transition)', boxSizing: 'border-box' }}
            onFocus={e => { e.target.style.borderColor = 'var(--border-focus)'; e.target.style.boxShadow = '0 0 0 3px var(--primary-light)'; }}
            onBlur={e => { e.target.style.borderColor = 'var(--border)'; e.target.style.boxShadow = 'none'; }}
          />
          
          {content[active] && (
            <div style={{ marginTop: '1rem', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', padding: '1.25rem' }}>
              <p style={{ margin: '0 0 0.75rem 0', fontSize: '0.85rem', fontWeight: 600 }}>Revise Section with AI</p>
              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                <input
                  placeholder="e.g. Make this shorter, add bullet points, fix grammar..."
                  value={editPrompt}
                  onChange={e => setEditPrompt(e.target.value)}
                  style={{ flex: 1, minWidth: '200px' }}
                  disabled={editing || generating}
                  onKeyDown={e => { if (e.key === 'Enter') applyEdit(); }}
                />
                <button className="btn btn-primary" onClick={applyEdit} disabled={editing || generating || !editPrompt.trim()}>
                  {editing ? <><Spin /> Revising...</> : 'Apply Revision'}
                </button>
              </div>
              {editError && <p style={{ color: 'var(--danger)', fontSize: '0.8rem', marginTop: '0.5rem', marginBottom: 0 }}>{editError}</p>}
            </div>
          )}
        </div>
      </div>

      {/* Load modal */}
      {showLoad && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, backdropFilter: 'blur(4px)' }} onClick={() => setShowLoad(false)}>
          <div className="animate-scale-in" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', padding: '2rem', width: '100%', maxWidth: '400px' }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
              <h3 style={{ margin: 0 }}>Load Draft</h3>
              <button onClick={() => setShowLoad(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-subtle)', display: 'flex' }}><X size={17} /></button>
            </div>
            <p style={{ fontSize: '0.85rem', marginBottom: '0.875rem' }}>Choose one of your saved manuscript drafts.</p>
            <div style={{ position: 'relative', marginBottom: '0.9rem' }}>
              <Search size={15} style={{ position: 'absolute', left: '0.9rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-subtle)', pointerEvents: 'none' }} />
              <input placeholder="Filter saved drafts..." value={draftFilter} onChange={e => setDraftFilter(e.target.value)} style={{ paddingLeft: '2.45rem' }} />
            </div>
            {loadError && <p style={{ color: 'var(--danger)', fontSize: '0.83rem', marginBottom: '0.75rem' }}>{loadError}</p>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.55rem', maxHeight: '280px', overflowY: 'auto', marginBottom: '1rem' }}>
              {draftLoading && <p style={{ margin: 0, color: 'var(--text-muted)', fontSize: '0.86rem' }}>Loading drafts...</p>}
              {!draftLoading && visibleDrafts.length === 0 && (
                <div className="empty-state" style={{ padding: '1.25rem', fontSize: '0.86rem' }}>
                  {drafts.length ? 'No drafts match your filter.' : 'No saved drafts yet.'}
                </div>
              )}
              {!draftLoading && visibleDrafts.map((draft) => (
                <button
                  key={draft.topic}
                  className="btn btn-secondary"
                  onClick={() => load(draft.topic)}
                  style={{ justifyContent: 'space-between', whiteSpace: 'normal', textAlign: 'left' }}
                >
                  <span>{draft.topic}</span>
                  <span style={{ color: 'var(--text-subtle)', fontSize: '0.72rem', fontWeight: 700 }}>Load</span>
                </button>
              ))}
            </div>
            <div style={{ display: 'flex', gap: '0.65rem', justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" onClick={() => setShowLoad(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const Spin = () => (
  <span style={{ width: 28, height: 28, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', verticalAlign: 'middle', marginRight: '0.35rem' }}>
    <Player autoplay loop src={loadingAnimation} style={{ height: '100%', width: '100%' }} />
  </span>
);
