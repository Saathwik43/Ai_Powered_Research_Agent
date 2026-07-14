import React, { useEffect, useState } from 'react';
import { CheckCircle, Circle, Save, FileText, Wand2, FolderOpen, X, Search, Sparkles, Send, BookOpen, Bold, Italic, Strikethrough, Link, List, ListOrdered, CheckSquare, Table, Quote, Code, Undo, Redo, Heading1, Heading2, Heading3, Printer } from 'lucide-react';
import './ManuscriptBuilder.css';
import './PaperPreview.css';
import { useAuth } from '../context/AuthContext';
import { Spinner, SkeletonText } from '../components/Loader';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { ghcolors } from 'react-syntax-highlighter/dist/esm/styles/prism';
import 'katex/dist/katex.min.css';
import { MODELS } from '../constants/models';
import { diffWords } from 'diff';
import Mermaid from '../components/Mermaid';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

const TableOrChart = ({ node, children, ...props }) => {
  const [showChart, setShowChart] = useState(false);
  
  // Attempt to parse table data from AST
  let headers = [];
  let rows = [];
  let hasNumericData = false;

  try {
    const thead = node.children.find(c => c.tagName === 'thead');
    const tbody = node.children.find(c => c.tagName === 'tbody');
    
    if (thead && tbody) {
      // Parse headers
      const trHead = thead.children.find(c => c.tagName === 'tr');
      if (trHead) {
        headers = trHead.children.filter(c => c.tagName === 'th').map(th => {
          return th.children[0]?.value || '';
        });
      }
      
      // Parse rows
      const trs = tbody.children.filter(c => c.tagName === 'tr');
      rows = trs.map(tr => {
        const tds = tr.children.filter(c => c.tagName === 'td');
        const rowData = {};
        tds.forEach((td, i) => {
          const val = td.children[0]?.value || '';
          if (i > 0 && !isNaN(parseFloat(val))) {
            rowData[headers[i] || `col_${i}`] = parseFloat(val);
            hasNumericData = true;
          } else {
            rowData[headers[i] || `col_${i}`] = val;
          }
        });
        return rowData;
      });
    }
  } catch (e) {
    // Silently fallback to table on parse error
  }

  if (hasNumericData && headers.length >= 2 && rows.length > 0) {
    const xKey = headers[0];
    const yKey = headers.find((h, i) => i > 0 && !isNaN(rows[0][h])) || headers[1];

    return (
      <div className="table-chart-container" style={{ margin: '1.5rem 0' }}>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '0.5rem' }}>
          <button 
            onClick={() => setShowChart(!showChart)}
            className="btn btn-ghost"
            style={{ fontSize: '0.75rem', padding: '0.25rem 0.5rem', height: 'auto' }}
          >
            {showChart ? 'View as Table' : 'View as Chart'}
          </button>
        </div>
        
        {showChart ? (
          <div style={{ width: '100%', height: 300, background: 'var(--bg-card)', padding: '1rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)' }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <XAxis dataKey={xKey} tick={{fontSize: 12}} />
                <YAxis tick={{fontSize: 12}} />
                <Tooltip cursor={{fill: 'var(--primary-light)'}} contentStyle={{ borderRadius: 'var(--radius-md)', border: 'none', boxShadow: 'var(--shadow-md)' }} />
                <Bar dataKey={yKey} fill="var(--primary)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table {...props}>{children}</table>
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{ overflowX: 'auto', margin: '1.5rem 0' }}>
      <table {...props}>{children}</table>
    </div>
  );
};

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
  const [editHistory, setEditHistory] = useState({});
  const [pendingEdit, setPendingEdit] = useState(null);
  const [generating, setGenerating] = useState(false);
  const [saveStatus, setSaveStatus] = useState('');
  const [printPending, setPrintPending] = useState(false);
  const [showLoad, setShowLoad]     = useState(false);
  const [viewMode, setViewMode]     = useState('write');
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
  const [revisePanelOpen, setRevisePanelOpen] = useState(false);
  const [refsOpen, setRefsOpen] = useState(false);
  
  // Phase B additions
  const [citationStyle, setCitationStyle] = useState('ieee');
  const [selectedModelId, setSelectedModelId] = useState('groq-default');
  const [manuscriptRefs, setManuscriptRefs] = useState(null);
  const [rateLimitWait, setRateLimitWait] = useState(null);
  const [autoMode, setAutoMode] = useState(true);
  const [autoStatus, setAutoStatus] = useState('');
  
  const [gapAnalysis, setGapAnalysis] = useState(null);
  const [customContext, setCustomContext] = useState('');

  const done = STEPS.filter(s => content[s.id]?.trim()).map(s => s.id);

  const generate = async () => {
    if (!topic.trim()) return;
    setGenerating(true);
    setGenerateError('');
    setUnverifiedWarning('');
    setUnverifiedNumbers([]);
    setRateLimitWait(null);
    setContent(prev => ({ ...prev, [active]: '' })); // Clear old content
    
    // If the active section is 'references', we don't need the LLM to generate it!
    // We already have the compiled references in `manuscriptRefs`.
    if (active === 'references') {
      if (manuscriptRefs && Object.keys(manuscriptRefs).length > 0) {
        let refsText = '';
        Object.keys(manuscriptRefs).sort((a, b) => parseInt(a) - parseInt(b)).forEach(key => {
          refsText += `${key}. ${manuscriptRefs[key]}\n\n`;
        });
        setContent(prev => ({ ...prev, [active]: refsText.trim() }));
      } else {
        setContent(prev => ({ ...prev, [active]: '*No references have been cited in the generated manuscript yet.*' }));
      }
      setGenerating(false);
      return;
    }
    
    try {
      const payloadContext = customContext.trim() || 'Use latest research trends and cite recent advancements.';
      const selectedModel = MODELS.find(m => m.id === selectedModelId) || MODELS[0];
      setAutoStatus('');
      
      const payload = { 
        topic, 
        section: active, 
        context: payloadContext, 
        citation_style: citationStyle, 
        mode: autoMode ? 'auto' : 'manual'
      };
      
      if (!autoMode) {
        payload.provider = selectedModel.provider;
        payload.model = selectedModel.model;
      }

      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript/stream`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      
      if (!res.ok) {
        if (res.status === 429) {
            setGenerateError("Rate limit exceeded. Please wait before generating again.");
        } else if (res.status === 503) {
            setGenerateError("AI generation is temporarily unavailable. Please try again shortly.");
        } else {
            const errorData = await res.json().catch(() => ({}));
            setGenerateError(errorData.detail || 'Failed to generate section. Please try again.');
        }
        setGenerating(false);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { done: readerDone, value } = await reader.read();
        if (readerDone) break;
        
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const chunkStr = buffer.slice(0, boundary).trim();
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf("\n\n");
          
          if (chunkStr.startsWith("data: ")) {
            const dataStr = chunkStr.slice(6);
            if (dataStr === "[DONE]") continue;
            try {
              const data = JSON.parse(dataStr);
              if (data.type === "chunk") {
                setContent(prev => ({ ...prev, [active]: (prev[active] || "") + data.text }));
              } else if (data.type === "provider_active") {
                setAutoStatus(data.continuing ? `Resuming with ${data.provider}...` : `Generating with ${data.provider}...`);
              } else if (data.type === "provider_status") {
                setAutoStatus(data.message);
                setTimeout(() => setAutoStatus(''), 2500);
              } else if (data.type === "metadata") {
                if (data.formatted_references) setManuscriptRefs(data.formatted_references);
                if (data.unverified_citations) setUnverifiedWarning('Warning: The generated text contains citations that could not be verified against the provided context. Please verify them independently.');
                if (data.unverified_numbers && data.unverified_numbers.length > 0) setUnverifiedNumbers(data.unverified_numbers);
                if (data.gap_analysis) setGapAnalysis(data.gap_analysis);
                else if (active === 'lit_review' || active === 'literature_review') setGapAnalysis(null);
              } else if (data.type === "stopped") {
                setAutoStatus('');
                if (data.reason === "rate_limit") {
                  const waitSecs = data.retry_after_seconds;
                  if (waitSecs) {
                     setRateLimitWait(waitSecs);
                     setContent(prev => ({ ...prev, [active]: (prev[active] || "") + `\n\n*[Generation paused — rate limit reached. You can resume in ~${waitSecs}s.]*` }));
                  } else {
                     setContent(prev => ({ ...prev, [active]: (prev[active] || "") + `\n\n*[Generation paused — rate limit reached. Try again shortly.]*` }));
                  }
                } else if (data.reason === "error") {
                  setContent(prev => ({ ...prev, [active]: (prev[active] || "") + `\n\n*[Generation stopped: ${data.message}]*` }));
                }
              } else if (data.type === "done") {
                setAutoStatus('');
                setGenerating(false);
              }
            } catch (err) {
              console.error("Error parsing stream chunk:", err, chunkStr);
            }
          }
        }
      }
    } catch (e) {
      console.error(e);
      setGenerateError('Network error. Please try again.');
    }
    finally { setGenerating(false); }
  };

  // Countdown timer effect
  useEffect(() => {
    if (rateLimitWait === null || rateLimitWait <= 0) return;
    const timer = setInterval(() => {
      setRateLimitWait(prev => {
        if (prev <= 1) {
          clearInterval(timer);
          return null;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [rateLimitWait]);

  // Markdown formatting helper
  const insertMarkdown = (prefix, suffix = '') => {
    const textarea = document.getElementById('manuscript-textarea');
    if (!textarea) return;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const text = textarea.value;
    const selected = text.substring(start, end);
    
    // We use execCommand so the native Undo/Redo stack isn't broken
    textarea.focus();
    const replacement = `${prefix}${selected}${suffix}`;
    
    // If the browser supports execCommand for insertText
    if (document.queryCommandSupported('insertText')) {
      document.execCommand('insertText', false, replacement);
    } else {
      // Fallback for older browsers (will break undo stack)
      const newText = text.substring(0, start) + replacement + text.substring(end);
      setContent(prev => ({ ...prev, [active]: newText }));
    }
    
    // Set selection back
    setTimeout(() => {
      textarea.setSelectionRange(start + prefix.length, start + prefix.length + selected.length);
    }, 0);
  };

  const handleFormat = (e, prefix, suffix = '') => {
    e.preventDefault(); // Prevent button click from stealing focus
    insertMarkdown(prefix, suffix);
  };

  const handleUndo = (e) => {
    e.preventDefault();
    document.execCommand('undo');
  };

  const handleRedo = (e) => {
    e.preventDefault();
    document.execCommand('redo');
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
      setPendingEdit(data.content);
      setEditPrompt('');
    } catch (e) {
      setEditError('Network error. Please try again.');
    } finally {
      setEditing(false);
    }
  };

  const acceptEdit = () => {
    setEditHistory(prev => ({ 
      ...prev, 
      [active]: [...(prev[active] || []).slice(-4), content[active]] 
    }));
    setContent(prev => ({ ...prev, [active]: pendingEdit }));
    setPendingEdit(null);
    setRevisePanelOpen(false);
  };

  const undoLastEdit = () => {
    const hist = editHistory[active] || [];
    if (!hist.length) return;
    const previous = hist[hist.length - 1];
    setContent(prev => ({ ...prev, [active]: previous }));
    setEditHistory(prev => ({ ...prev, [active]: hist.slice(0, -1) }));
  };

  const rejectEdit = () => {
    setPendingEdit(null);
    setRevisePanelOpen(false);
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

  const exportMarkdown = () => {
    if (!topic || !Object.keys(content).length) return;
    const md = [`# ${topic}\n`, ...STEPS.filter(s => content[s.id]).flatMap(s => [`\n## ${s.label}\n`, content[s.id]])].join('\n');
    const blob = new Blob([md], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${topic.replace(/\s+/g, '-')}.md`; a.click();
    URL.revokeObjectURL(url);
  };

  const exportPDF = () => {
    if (!topic || !Object.keys(content).length) return;
    setViewMode('paper');
    setPrintPending(true);
  };

  useEffect(() => {
    if (printPending && viewMode === 'paper') {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => { 
          window.print(); 
          setPrintPending(false); 
        });
      });
    }
  }, [printPending, viewMode]);

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
        <div className="responsive-actions" style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <div style={{ padding: '0.4rem 0.8rem', background: 'var(--bg-card-alt)', borderRadius: '6px', fontSize: '0.85rem', fontWeight: '500', color: 'var(--text-muted)', border: '1px solid var(--border-color)' }}>
            {citationStyle.toUpperCase()} Format
          </div>
          <button className="btn btn-secondary" onClick={() => { setShowLoad(true); setLoadError(''); setDraftFilter(''); }}>
            <FolderOpen size={14} /> Load Draft
          </button>
          <button className="btn btn-secondary" onClick={exportMarkdown} disabled={!Object.keys(content).length}>
            <FileText size={14} /> .md
          </button>
          <button 
            className="btn btn-primary" 
            onClick={exportPDF} 
            disabled={!Object.keys(content).length}
            title="Export as PDF (uncheck 'Headers and footers' in the print dialog for a clean file)"
          >
            <Printer size={14} /> PDF
          </button>
        </div>
      </div>

      <div className="manuscript-layout" style={{ display: 'flex', gap: '1.25rem', alignItems: 'flex-start', flexWrap: 'wrap' }}>

        {/* Sidebar stepper */}
        <div className="manuscript-outline-panel" style={{ flex: '0 0 210px', minWidth: '180px', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '1.25rem', position: 'sticky', top: '1.5rem' }}>
          <p style={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.09em', color: 'var(--text-subtle)', marginBottom: '0.875rem' }}>Sections</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
            {STEPS.map(step => {
              const isDone   = done.includes(step.id);
              const isActive = active === step.id;
              return (
                <div key={step.id} onClick={() => setActive(step.id)}
                  className={isActive ? 'manuscript-step-active' : ''}
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

          <div style={{ marginTop: '1.5rem', paddingTop: '1.25rem', borderTop: '1px solid var(--border)' }}>
            <p style={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.09em', color: 'var(--text-subtle)', marginBottom: '0.875rem' }}>Configuration</p>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <input
                placeholder="Enter research topic..."
                value={topic}
                onChange={e => setTopic(e.target.value)}
                style={{ padding: '0.6rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', background: 'var(--bg-input)', color: 'var(--text)', fontSize: '0.85rem', width: '100%' }}
              />
              <select 
                value={citationStyle} 
                onChange={e => setCitationStyle(e.target.value)}
                style={{ padding: '0.6rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', background: 'var(--bg-input)', color: 'var(--text)', fontSize: '0.85rem', width: '100%' }}
              >
                <option value="ieee">IEEE Citation Format</option>
                <option value="apa">APA Citation Format</option>
                <option value="chicago">Chicago Style</option>
                <option value="oxford">Oxford Style</option>
              </select>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.5rem', padding: '0.75rem', background: 'var(--bg-input)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem', cursor: 'pointer' }}>
                  <input type="radio" checked={autoMode} onChange={() => setAutoMode(true)} />
                  Auto (recommended) - reliable generation
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem', cursor: 'pointer' }}>
                  <input type="radio" checked={!autoMode} onChange={() => setAutoMode(false)} />
                  Choose specific model
                </label>
                
                {!autoMode && (
                  <select 
                    value={selectedModelId} 
                    onChange={e => setSelectedModelId(e.target.value)}
                    style={{ padding: '0.6rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: '0.85rem', width: '100%', marginTop: '0.5rem' }}
                  >
                    {Array.from(new Set(MODELS.map(m => m.group))).map(group => (
                      <optgroup key={group} label={group}>
                        {MODELS.filter(m => m.group === group).map(m => (
                          <option key={m.id} value={m.id}>{m.label}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Editor */}
        <div style={{ flex: 1, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '1.5rem', minWidth: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem', flexWrap: 'wrap', gap: '0.5rem' }}>
            <h2 style={{ margin: 0, fontSize: '1.1rem' }}>{currentStep?.label}</h2>
            <div className="responsive-actions" style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
              {autoStatus && <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>{autoStatus}</span>}
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <button className="btn btn-secondary" onClick={generate} disabled={generating || !topic.trim() || rateLimitWait > 0}>
                  {generating ? <Spinner size={14} /> : <><Sparkles size={14} /> {rateLimitWait ? `Wait ${rateLimitWait}s` : 'Generate'}</>}
                </button>
                <button className="btn btn-ghost" onClick={save} disabled={!topic || !Object.keys(content).length || saveStatus === 'saving'}>
                  <Save size={14} /> {saveStatus === 'saving' ? 'Saving...' : saveStatus === 'saved' ? 'Saved' : 'Save'}
                </button>
              </div>
            </div>
          </div>

          {/* Topic and settings were moved to the sidebar */}

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
                  {gapAnalysis.consensus && gapAnalysis.consensus.length > 0 && (
                  <div>
                    <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.9rem', color: 'var(--success)' }}>Consensus</h4>
                    <ul style={{ margin: 0, paddingLeft: '1.5rem', fontSize: '0.88rem', color: 'var(--text)' }}>
                      {(gapAnalysis.consensus || []).map((item, i) => (
                        <li key={i} style={{ marginBottom: '0.25rem' }}>
                          {item.claim} <span style={{color: 'var(--text-subtle)'}}>[{item.supporting_papers?.join(', ')}]</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  )}
                  {gapAnalysis.conflicts && gapAnalysis.conflicts.length > 0 && (
                  <div>
                    <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.9rem', color: 'var(--warning)' }}>Conflicts</h4>
                    <ul style={{ margin: 0, paddingLeft: '1.5rem', fontSize: '0.88rem', color: 'var(--text)' }}>
                      {(gapAnalysis.conflicts || []).map((item, i) => (
                        <li key={i} style={{ marginBottom: '0.25rem' }}>
                          {item.claim_a} <strong>vs</strong> {item.claim_b} <span style={{color: 'var(--text-subtle)'}}>[{item.papers?.join(', ')}]</span><br/>
                          <span style={{fontSize: '0.8rem', color: 'var(--text-subtle)'}}>{item.note}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  )}
                  <div>
                    <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.9rem', color: 'var(--warning)' }}>Remaining Gaps</h4>
                    <ul style={{ margin: 0, paddingLeft: '1.5rem', fontSize: '0.88rem', color: 'var(--text)' }}>
                      {(gapAnalysis.gaps || []).map((item, i) => (
                        <li key={i} style={{ marginBottom: '0.25rem' }}>
                          {item.description} <span style={{color: 'var(--text-subtle)'}}>[{item.informed_by?.join(', ')}]</span>
                        </li>
                      ))}
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

          {generating && (
            <div style={{ marginTop: '2rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1.5rem', background: 'rgba(0, 87, 255, 0.04)', border: '1px solid rgba(0, 87, 255, 0.1)', padding: '1.25rem', borderRadius: 'var(--radius-lg)' }}>
                <div style={{ animation: 'spin 3s linear infinite' }}>
                  <Sparkles size={24} style={{ color: 'var(--primary)' }} />
                </div>
                <div>
                  <h2 style={{ fontSize: '1.1rem', fontWeight: 600, margin: '0 0 0.35rem 0', color: 'var(--primary)' }}>
                    Generating your manuscript...
                  </h2>
                  <div style={{ margin: 0, fontSize: '0.85rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <Spinner size={12} /> Synthesizing evidence and structuring content...
                  </div>
                </div>
              </div>
              <SkeletonText lines={12} />
            </div>
          )}

          {pendingEdit ? (
            <div className="manuscript-diff-view">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem', marginBottom: '1rem' }}>
                <h3 style={{ margin: 0, fontSize: '1rem', color: 'var(--primary)' }}>Review AI Revisions</h3>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <button className="btn btn-ghost" onClick={rejectEdit} style={{ color: 'var(--danger)' }}><X size={16} /> Discard</button>
                  <button className="btn btn-primary" onClick={acceptEdit}><CheckCircle size={16} /> Accept Changes</button>
                </div>
              </div>
              <div className="manuscript-diff-content">
                {diffWords(content[active] || '', pendingEdit).map((part, i) => (
                  part.added ? <ins key={i}>{part.value}</ins> :
                  part.removed ? <del key={i}>{part.value}</del> :
                  <span key={i}>{part.value}</span>
                ))}
              </div>
            </div>
          ) : !generating && (
            <div className="manuscript-editor-surface" key={active}>
              <div className="manuscript-toolbar">
                <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
                  <div style={{ display: 'flex', gap: '0.25rem' }}>
                    {['write', 'preview', 'paper'].map(mode => (
                      <button
                        key={mode}
                        onClick={() => setViewMode(mode)}
                        style={{ background: 'none', border: 'none', padding: '0.25rem 0.5rem', borderBottom: viewMode === mode ? '2px solid var(--primary)' : '2px solid transparent', color: viewMode === mode ? 'var(--primary)' : 'var(--text-subtle)', fontWeight: viewMode === mode ? 600 : 400, cursor: 'pointer', transition: 'var(--transition)', fontSize: '0.88rem' }}
                      >
                        {mode === 'write' ? 'Write' : mode === 'preview' ? 'Preview' : 'Paper Preview'}
                      </button>
                    ))}
                  </div>
                  
                  {/* Rich Text Toolbar (Only in Write Mode) */}
                  {viewMode === 'write' && (
                    <div className="manuscript-format-toolbar">
                      <div className="format-group">
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '# ')} title="Heading 1"><Heading1 size={15} /></button>
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '## ')} title="Heading 2"><Heading2 size={15} /></button>
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '### ')} title="Heading 3"><Heading3 size={15} /></button>
                      </div>
                      <div className="format-group">
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '**', '**')} title="Bold"><Bold size={15} /></button>
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '*', '*')} title="Italic"><Italic size={15} /></button>
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '~~', '~~')} title="Strikethrough"><Strikethrough size={15} /></button>
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '[', '](url)')} title="Link"><Link size={15} /></button>
                      </div>
                      <div className="format-group">
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '- ')} title="Bulleted List"><List size={15} /></button>
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '1. ')} title="Numbered List"><ListOrdered size={15} /></button>
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '- [ ] ')} title="Checklist"><CheckSquare size={15} /></button>
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '\n| Column 1 | Column 2 |\n| -------- | -------- |\n| Text     | Text     |\n')} title="Table"><Table size={15} /></button>
                      </div>
                      <div className="format-group">
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '> ')} title="Blockquote"><Quote size={15} /></button>
                        <button className="format-btn" onMouseDown={(e) => handleFormat(e, '```\n', '\n```')} title="Code Block"><Code size={15} /></button>
                      </div>
                      <div className="format-group">
                        <button className="format-btn" onMouseDown={handleUndo} title="Undo"><Undo size={15} /></button>
                        <button className="format-btn" onMouseDown={handleRedo} title="Redo"><Redo size={15} /></button>
                      </div>
                    </div>
                  )}
                </div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-subtle)', fontWeight: 500, minWidth: '70px', textAlign: 'right' }}>
                  {content[active] ? content[active].trim().split(/\s+/).length : 0} words
                </div>
              </div>
              
              {viewMode === 'write' ? (
                <textarea
                  id="manuscript-textarea"
                  placeholder={`Write your ${currentStep?.label.toLowerCase()} here, or click Generate for AI assistance...\nUse LaTeX for math (e.g. $E = mc^2$ for inline, $$x^2$$ for block).`}
                  value={(content[active] || '') + (generating ? '▋' : '')}
                  onChange={e => setContent(prev => ({ ...prev, [active]: e.target.value }))}
                  disabled={generating}
                  style={{ width: '100%', minHeight: '420px', padding: '1rem', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', color: 'var(--text)', fontFamily: 'inherit', fontSize: '0.93rem', resize: 'vertical', outline: 'none', lineHeight: 1.75, transition: 'var(--transition)', boxSizing: 'border-box', opacity: generating ? 0.8 : 1 }}
                  onFocus={e => { e.target.style.borderColor = 'var(--border-focus)'; e.target.style.boxShadow = '0 0 0 3px var(--primary-light)'; }}
                  onBlur={e => { e.target.style.borderColor = 'var(--border)'; e.target.style.boxShadow = 'none'; }}
                />
              ) : viewMode === 'preview' ? (
                <div className="pdf-markdown-body" style={{ width: '100%', minHeight: '420px', padding: '1rem', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', color: 'var(--text)', overflowY: 'auto', boxSizing: 'border-box' }}>
                  {content[active] ? (
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm, remarkMath]}
                      rehypePlugins={[rehypeKatex]}
                      components={{
                        table: TableOrChart,
                        code({ node, inline, className, children, ...props }) {
                          const match = /language-(\w+)/.exec(className || '');
                          const language = match ? match[1].toLowerCase() : '';
                          const contentStr = String(children).replace(/\n$/, '');
                          
                          if (!inline && (language === 'mermaid' || language === 'graph' || contentStr.trim().startsWith('graph ') || contentStr.trim().startsWith('pie ') || contentStr.trim().startsWith('sequenceDiagram'))) {
                            return <Mermaid chart={contentStr} />;
                          }
                          return !inline && match ? (
                            <SyntaxHighlighter style={ghcolors} language={match[1]} PreTag="div" {...props}>
                              {contentStr}
                            </SyntaxHighlighter>
                          ) : (
                            <code className={className} {...props}>{children}</code>
                          );
                        }
                      }}
                    >
                      {(content[active] || '') + (generating ? ' ▋' : '')}
                    </ReactMarkdown>
                  ) : (
                    <p style={{ color: 'var(--text-subtle)', fontStyle: 'italic', margin: 0 }}>Nothing to preview.</p>
                  )}
                </div>
              ) : (
                /* ─── Paper Preview Mode ─── */
                <>
                  {/* Screen Version: Only Active Section */}
                  <div className={`paper-preview format-${citationStyle} paper-preview-screen`}>
                    <div className="paper-header">
                      <div className="paper-section-label">{currentStep?.label}</div>
                      <h1 className="paper-title">{topic || 'Untitled Paper'}</h1>
                    </div>
                    <div className="paper-body">
                      {content[active] ? (
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm, remarkMath]}
                          rehypePlugins={[rehypeKatex]}
                          components={{
                            table: TableOrChart,
                            code({ node, inline, className, children, ...props }) {
                              const match = /language-(\w+)/.exec(className || '');
                              const language = match ? match[1].toLowerCase() : '';
                              const contentStr = String(children).replace(/\n$/, '');
                              
                              if (!inline && (language === 'mermaid' || language === 'graph' || language === 'xychart-beta' || contentStr.trim().startsWith('graph ') || contentStr.trim().startsWith('pie ') || contentStr.trim().startsWith('sequenceDiagram') || contentStr.trim().startsWith('xychart-beta'))) {
                                return <Mermaid chart={contentStr} />;
                              }
                              return !inline && match ? (
                                <SyntaxHighlighter style={ghcolors} language={match[1]} PreTag="div" {...props}>
                                  {contentStr}
                                </SyntaxHighlighter>
                              ) : (
                                <code className={className} {...props}>{children}</code>
                              );
                            }
                          }}
                        >
                          {content[active]}
                        </ReactMarkdown>
                      ) : (
                        <p style={{ color: '#999', fontStyle: 'italic', textAlign: 'center', marginTop: '3rem' }}>
                          No content to preview for this section.
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Print Version: Full Paper (Hidden on screen, visible when printing) */}
                  <div className={`paper-preview format-${citationStyle} paper-preview-print`}>
                    <div className="paper-header">
                      <h1 className="paper-title">{topic || 'Untitled Paper'}</h1>
                    </div>
                    <div className="paper-body">
                      {STEPS.map(step => content[step.id] ? (
                        <div key={step.id} className="paper-section" style={{ marginBottom: '2.5rem' }}>
                          {step.id !== 'abstract' && (
                            <h2 style={{ textTransform: 'uppercase', fontSize: '1.2rem', marginBottom: '1rem', borderBottom: '1px solid #eee', paddingBottom: '0.5rem' }}>
                              {step.label}
                            </h2>
                          )}
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm, remarkMath]}
                            rehypePlugins={[rehypeKatex]}
                            components={{
                              table: TableOrChart,
                              code({ node, inline, className, children, ...props }) {
                                const match = /language-(\w+)/.exec(className || '');
                                const language = match ? match[1].toLowerCase() : '';
                                const contentStr = String(children).replace(/\n$/, '');
                                
                                if (!inline && (language === 'mermaid' || language === 'graph' || language === 'xychart-beta' || contentStr.trim().startsWith('graph ') || contentStr.trim().startsWith('pie ') || contentStr.trim().startsWith('sequenceDiagram') || contentStr.trim().startsWith('xychart-beta'))) {
                                  return <Mermaid chart={contentStr} />;
                                }
                                return !inline && match ? (
                                  <SyntaxHighlighter style={ghcolors} language={match[1]} PreTag="div" {...props}>
                                    {contentStr}
                                  </SyntaxHighlighter>
                                ) : (
                                  <code className={className} {...props}>{children}</code>
                                );
                              }
                            }}
                          >
                            {content[step.id]}
                          </ReactMarkdown>
                        </div>
                      ) : null)}
                    </div>
                  </div>
                </>
              )}

              {content[active] && !generating && (
                <div style={{ position: 'absolute', bottom: '1.5rem', right: '1.5rem', display: 'flex', gap: '0.75rem' }}>
                  {editHistory[active] && editHistory[active].length > 0 && (
                    <button 
                      className="btn btn-secondary"
                      onClick={undoLastEdit}
                      style={{ borderRadius: '50px', padding: '0.75rem 1.25rem', boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}
                    >
                      <Undo size={16} /> Undo AI Edit
                    </button>
                  )}
                  <button 
                    className="btn btn-primary"
                    onClick={() => setRevisePanelOpen(true)}
                    style={{ borderRadius: '50px', padding: '0.75rem 1.25rem', boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}
                  >
                    <Sparkles size={16} /> AI Revise
                  </button>
                </div>
              )}

              <div className="manuscript-revise-panel-container">
                <div className={`manuscript-revise-panel ${revisePanelOpen ? 'open' : ''}`}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                    <p style={{ margin: 0, fontSize: '0.9rem', fontWeight: 600, color: 'var(--primary)' }}><Sparkles size={14} style={{ display: 'inline', verticalAlign: 'text-bottom' }}/> Revise Section</p>
                    <button onClick={() => setRevisePanelOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-subtle)' }}><X size={16} /></button>
                  </div>
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
                      {editing ? <Spinner size={14} /> : <Send size={14} />} Apply Revision
                    </button>
                  </div>
                  {editError && <div style={{ color: 'var(--danger)', fontSize: '0.8rem', marginTop: '0.5rem' }}>{editError}</div>}
                </div>
              </div>
            </div>
          )}

          {/* References Drawer & Toggle */}
          {manuscriptRefs && Object.keys(manuscriptRefs).length > 0 && (
            <>
              <button 
                className="manuscript-refs-toggle" 
                onClick={() => setRefsOpen(!refsOpen)}
              >
                <BookOpen size={16} /> References ({Object.keys(manuscriptRefs).length})
              </button>
              
              <div className={`manuscript-refs-drawer ${refsOpen ? 'open' : ''}`}>
                <div className="manuscript-refs-drawer-header">
                  <h3 style={{ margin: 0, fontSize: '1.05rem', color: 'var(--text)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <BookOpen size={16} color="var(--primary)" /> References
                  </h3>
                  <button onClick={() => setRefsOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-subtle)' }}><X size={16} /></button>
                </div>
                <div className="manuscript-refs-drawer-content">
                  <ol style={{ margin: 0, paddingLeft: '1.5rem', fontSize: '0.88rem', color: 'var(--text-subtle)', lineHeight: 1.6 }}>
                    {Object.entries(manuscriptRefs).map(([idx, refString]) => (
                      <li key={idx} style={{ marginBottom: '0.5rem' }}>{refString}</li>
                    ))}
                  </ol>
                </div>
              </div>
              
              {/* Mobile overlay backdrop */}
              {refsOpen && (
                <div 
                  style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 35 }} 
                  onClick={() => setRefsOpen(false)}
                  className="mobile-overlay"
                />
              )}
            </>
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
