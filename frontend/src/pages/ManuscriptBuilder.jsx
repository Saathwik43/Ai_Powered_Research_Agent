import React, { useEffect, useState, useRef } from 'react';
import { CheckCircle, Circle, Save, FileText, Wand2, FolderOpen, X, Search, Sparkles, Send, BookOpen, Bold, Italic, Strikethrough, Link, List, ListOrdered, CheckSquare, Table, Quote, Code, Undo, Redo, Heading1, Heading2, Heading3, Printer, ChevronDown, ExternalLink, Plus } from 'lucide-react';
import './ManuscriptBuilder.css';
import './PaperPreview.css';
import { useAuth } from '../context/AuthContext';
import { useAppContext } from '../context/AppContext';
import { Spinner, SkeletonText } from '../components/Loader';
import SectionsList from '../components/SectionsList';
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

function extractText(node) {
  if (!node) return '';
  if (node.type === 'text') return node.value || '';
  if (node.children) return node.children.map(extractText).join('');
  return '';
}

const TableOrChart = ({ node, children, ...props }) => {
  // Attempt to parse table data from AST
  let headers = [];
  let rows = [];
  let hasNumericData = false;
  let graphTitle = '';

  try {
    const thead = node.children.find(c => c.tagName === 'thead');
    const tbody = node.children.find(c => c.tagName === 'tbody');
    
    if (thead && tbody) {
      // Parse headers
      const trHead = thead.children.find(c => c.tagName === 'tr');
      if (trHead) {
        headers = trHead.children.filter(c => c.tagName === 'th').map(th => extractText(th).trim());
      }
      
      // Parse rows
      const trs = tbody.children.filter(c => c.tagName === 'tr');
      rows = trs.map(tr => {
        const tds = tr.children.filter(c => c.tagName === 'td');
        const rowData = {};
        tds.forEach((td, i) => {
          const val = extractText(td).trim();
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

    // Detect if this table is meant to be a graph:
    // 1. Check preceding sibling text for "Graph N:" pattern
    if (node.position) {
      const parent = node;  // node is the <table> element in the AST
      // Check if any header cell text starts with "Graph"
      const firstHeader = (headers[0] || '').toLowerCase();
      if (/^graph\s*\d/i.test(firstHeader) || /^chart\s*\d/i.test(firstHeader) || /^figure\s*\d/i.test(firstHeader)) {
        graphTitle = headers[0];
      }
    }
  } catch (e) {
    console.debug('TableOrChart parse failed, falling back to table:', e);
  }

  // If this is a graph (detected by caption/header) AND has chart-able data → render as chart
  const isGraph = graphTitle !== '';
  if (isGraph && hasNumericData && headers.length >= 2 && rows.length > 0) {
    const xKey = headers[0];
    const yKey = headers.find((h, i) => i > 0 && !isNaN(rows[0][h])) || headers[1];

    return (
      <div className="table-chart-container" style={{ margin: 'var(--space-5) 0' }}>
        {graphTitle && (
          <div style={{ textAlign: 'center', fontWeight: '600', fontSize: 'var(--fs-sm)', marginBottom: 'var(--space-2)', color: 'var(--text-primary)' }}>
            {graphTitle}
          </div>
        )}
        <div style={{ width: '100%', height: 300, background: 'var(--bg-card)', padding: 'var(--space-4)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)' }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <XAxis dataKey={xKey} tick={{fontSize: 12}} />
              <YAxis tick={{fontSize: 12}} />
              <Tooltip cursor={{fill: 'var(--primary-light)'}} contentStyle={{ borderRadius: 'var(--radius-md)', border: 'none', boxShadow: 'var(--shadow-md)' }} />
              <Bar dataKey={yKey} fill="var(--primary)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    );
  }

  // Otherwise: always render as a plain table
  return (
    <div style={{ overflowX: 'auto', margin: 'var(--space-5) 0' }}>
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
  const { manuscriptState } = useAppContext();
  const {
    active, setActive,
    topic, setTopic,
    content, setContent,
    generating, setGenerating,
    editHistory, setEditHistory,
    manuscriptRefs, setManuscriptRefs,
    lastSavedContentRef
  } = manuscriptState;

  const [pendingEdit, setPendingEdit] = useState(null);
  const [saveStatus, setSaveStatus] = useState('');
  const [printPending, setPrintPending] = useState(false);
  const [showLoad,     setShowLoad]     = useState(false);
  const [showNewPaperConfirm, setShowNewPaperConfirm] = useState(false);
  const [viewMode,     setViewMode]     = useState('write');
  const [drafts,       setDrafts]       = useState([]);
  const [draftFilter,  setDraftFilter]  = useState('');
  const [draftLoading, setDraftLoading] = useState(false);
  const [loadError,    setLoadError]    = useState('');
  const [editPrompt,   setEditPrompt]   = useState('');
  const [editing,      setEditing]      = useState(false);
  const [editError,    setEditError]    = useState('');
  const [generateError, setGenerateError] = useState('');
  const [unverifiedWarning, setUnverifiedWarning] = useState('');
  const [unverifiedNumbers, setUnverifiedNumbers] = useState([]);
  const [revisePanelOpen, setRevisePanelOpen] = useState(false);
  const [refsOpen, setRefsOpen] = useState(false);
  
  // Phase B additions
  const [citationStyle, setCitationStyle] = useState('ieee');
  const [selectedModelId, setSelectedModelId] = useState('groq-default');
  const [rateLimitWait, setRateLimitWait] = useState(null);
  const [autoMode, setAutoMode] = useState(true);
  const [autoStatus, setAutoStatus] = useState('');
  
  const [gapAnalysis, setGapAnalysis] = useState(null);
  const [gapPanelOpen, setGapPanelOpen] = useState(false);
  const [gapTab, setGapTab] = useState('consensus');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [customContext, setCustomContext] = useState('');
  const [streamSources, setStreamSources] = useState([]);
  const [sourcesResolved, setSourcesResolved] = useState(false);
  
  const abortControllerRef = useRef(null);

  const processForUnverified = (text) => {
    if (!text || !unverifiedNumbers || !unverifiedNumbers.length) return text;
    let processed = text;
    unverifiedNumbers.forEach(num => {
      // Split and join is a safe way to replace all occurrences without regex escaping issues
      processed = processed.split(num).join(`[${num}](#unverified-stat)`);
    });
    // Fix double wrapping if any
    processed = processed.split('](#unverified-stat)](#unverified-stat)').join('](#unverified-stat)').split('[[').join('[');
    return processed;
  };

  const done = STEPS.filter(s => content[s.id]?.trim()).map(s => s.id);

  const generate = async () => {
    if (!topic.trim()) return;
    setGenerating(true);
    setGenerateError('');
    setUnverifiedWarning('');
    setUnverifiedNumbers([]);
    setRateLimitWait(null);
    setStreamSources([]);
    setSourcesResolved(false);
    setContent(prev => ({ ...prev, [active]: '' })); // Clear old content
    
    abortControllerRef.current = new AbortController();
    
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
        signal: abortControllerRef.current.signal
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
              } else if (data.type === "sources_list") {
                setStreamSources(data.sources || []);
                setSourcesResolved(true);
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
      if (e.name === 'AbortError') {
        setContent(prev => ({ ...prev, [active]: (prev[active] || "") + `\n\n*[Generation stopped by user]*` }));
      } else {
        console.error(e);
        setGenerateError('Network error. Please try again.');
      }
    }
    finally { setGenerating(false); }
  };

  const stopGeneration = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
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
      const payload = { 
        topic, 
        content,
        gap_analysis: gapAnalysis,
        manuscript_refs: manuscriptRefs,
        citation_style: citationStyle
      };
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript/save`, { 
        method: 'POST', 
        body: JSON.stringify(payload) 
      });
      setSaveStatus(res.ok ? 'saved' : 'error');
      if (res.ok) setTimeout(() => setSaveStatus(''), 3000);
    } catch { setSaveStatus('error'); }
  };

  // Autosave Effect
  useEffect(() => {
    if (!topic || !Object.keys(content).length) return;
    
    // Only autosave if the content actually changed since last save
    const currentContentStr = JSON.stringify(content);
    const lastSavedStr = JSON.stringify(lastSavedContentRef.current);
    if (currentContentStr === lastSavedStr) return;
    
    const timeoutId = setTimeout(() => {
      save();
      lastSavedContentRef.current = content;
    }, 5000);
    
    return () => clearTimeout(timeoutId);
  }, [content, topic]);

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

  const handleNewPaper = () => {
    if (Object.keys(content).length > 0) {
      setShowNewPaperConfirm(true);
      return;
    }
    confirmNewPaper();
  };

  const confirmNewPaper = () => {
    setContent({});
    setTopic('');
    setGapAnalysis(null);
    setManuscriptRefs(null);
    setStreamSources([]);
    setActive('abstract');
    setUnverifiedWarning('');
    setUnverifiedNumbers([]);
    lastSavedContentRef.current = {};
    setShowNewPaperConfirm(false);
  };

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
        if (data.data.gap_analysis) {
          setGapAnalysis(data.data.gap_analysis);
          setGapPanelOpen(false); // keep it closed on initial load
        }
        if (data.data.manuscript_refs) {
          setManuscriptRefs(data.data.manuscript_refs);
        }
        if (data.data.citation_style) {
          setCitationStyle(data.data.citation_style);
        }
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
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--space-6)', flexWrap: 'wrap', gap: 'var(--space-4)' }}>
        <div>
          <h1>Manuscript Builder</h1>
          <p className="text-muted">Write your research paper section by section with AI assistance.</p>
          <div style={{ marginTop: 'var(--space-3)', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <span style={{ fontSize: 'var(--fs-base)' }}>💡</span>
            <span><strong>Note:</strong> AI can make mistakes, review before proceeding.</span>
          </div>
        </div>
        <div className="responsive-actions" style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
          <div style={{ padding: 'var(--space-2) var(--space-3)', background: 'var(--bg-card-alt)', borderRadius: '6px', fontSize: 'var(--fs-sm)', fontWeight: '500', color: 'var(--text-muted)', border: '1px solid var(--border-color)' }}>
            {citationStyle.toUpperCase()} Format
          </div>
          <button className="btn btn-secondary" onClick={handleNewPaper}>
            <Plus size={14} /> New Paper
          </button>
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

      <div className="manuscript-layout" style={{ display: 'flex', gap: 'var(--space-4)', alignItems: 'flex-start', flexWrap: 'wrap' }}>

        {/* Sidebar Container */}
        <div className="sidebar" style={{ flex: '0 0 220px', minWidth: '220px', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', position: 'sticky', top: '1.5rem' }}>
          
          <SectionsList 
            sections={STEPS} 
            activeSectionId={active} 
            onSelectSection={setActive} 
            doneIds={done} 
            generating={generating} 
          />

          {/* Configuration Block */}
          <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-4)' }}>
            
            {/* Topic Input - Always Visible */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
              <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Research Topic</span>
              <input
                placeholder="Enter research topic..."
                value={topic}
                onChange={e => setTopic(e.target.value)}
                style={{ padding: 'var(--space-2)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', background: 'var(--bg-input)', color: 'var(--text)', fontSize: 'var(--fs-sm)', width: '100%', outline: 'none' }}
              />
            </div>

            {/* Settings Accordion */}
            <div style={{ borderTop: '1px solid var(--border)', paddingTop: 'var(--space-3)', marginTop: 'var(--space-4)' }}>
              <button
                onClick={() => setSettingsOpen(!settingsOpen)}
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
              >
                <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Settings</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                  {!settingsOpen && (
                    <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--primary)', fontWeight: 500 }}>
                      {citationStyle.toUpperCase()} · {autoMode ? 'Auto' : 'Specific'}
                    </span>
                  )}
                  <ChevronDown size={14} style={{ color: 'var(--text-muted)', transform: settingsOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s ease' }} />
                </div>
              </button>

              {settingsOpen && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', marginTop: 'var(--space-4)' }}>
                  
                  {/* Citation Format */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                    <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-subtle)' }}>Citation Format</span>
                    <select 
                      value={citationStyle} 
                      onChange={e => setCitationStyle(e.target.value)}
                      style={{ padding: 'var(--space-2)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', background: 'var(--bg-input)', color: 'var(--text)', fontSize: 'var(--fs-sm)', width: '100%' }}
                    >
                      <option value="ieee">IEEE Citation Format</option>
                      <option value="apa">APA Citation Format</option>
                      <option value="chicago">Chicago Style</option>
                      <option value="oxford">Oxford Style</option>
                    </select>
                  </div>

                  {/* Model Selection */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                    <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-subtle)' }}>Generation Model</span>
                    
                    <div style={{ display: 'flex', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
                      <button
                        onClick={() => setAutoMode(true)}
                        style={{ flex: 1, padding: 'var(--space-2)', background: autoMode ? 'var(--primary)' : 'transparent', color: autoMode ? 'white' : 'var(--text)', border: 'none', cursor: 'pointer', fontSize: 'var(--fs-xs)', fontWeight: autoMode ? 600 : 400, transition: 'background-color var(--transition), color var(--transition)' }}
                      >
                        Auto
                      </button>
                      <button
                        onClick={() => setAutoMode(false)}
                        style={{ flex: 1, padding: 'var(--space-2)', background: !autoMode ? 'var(--primary)' : 'transparent', color: !autoMode ? 'white' : 'var(--text)', border: 'none', cursor: 'pointer', fontSize: 'var(--fs-xs)', fontWeight: !autoMode ? 600 : 400, transition: 'background-color var(--transition), color var(--transition)' }}
                      >
                        Specific
                      </button>
                    </div>

                    {!autoMode && (
                      <select 
                        value={selectedModelId} 
                        onChange={e => setSelectedModelId(e.target.value)}
                        style={{ padding: 'var(--space-2)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', background: 'var(--bg-input)', color: 'var(--text)', fontSize: 'var(--fs-sm)', width: '100%', marginTop: 'var(--space-1)' }}
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
              )}
            </div>
          </div>
        </div>

        {/* Editor */}
        <div style={{ flex: 1, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-5)', minWidth: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-4)', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
            <h2 style={{ margin: 0, fontSize: 'var(--fs-md)' }}>{currentStep?.label}</h2>
            <div className="responsive-actions" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)' }}>
              {autoStatus && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)' }}>{autoStatus}</span>}
              <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                {generating ? (
                  <button className="btn btn-secondary" onClick={stopGeneration} style={{ background: 'var(--danger)', color: 'white', borderColor: 'var(--danger)' }}>
                    <Spinner size={14} /> Stop
                  </button>
                ) : (
                  <button className="btn btn-secondary" onClick={generate} disabled={!topic.trim() || rateLimitWait > 0}>
                    <Sparkles size={14} /> {rateLimitWait ? `Wait ${rateLimitWait}s` : 'Generate'}
                  </button>
                )}
                <button className="btn btn-ghost" onClick={save} disabled={!topic || !Object.keys(content).length || saveStatus === 'saving'}>
                  <Save size={14} /> {saveStatus === 'saving' ? 'Saving...' : saveStatus === 'saved' ? 'Saved' : 'Save'}
                </button>
              </div>
            </div>
          </div>

          {/* Source Cards Strip */}
          {sourcesResolved && (
            <div style={{ marginBottom: 'var(--space-4)' }}>
              {streamSources.length > 0 ? (
                <>
                  <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-subtle)', marginBottom: 'var(--space-2)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                    <BookOpen size={12} /> {streamSources.length} Sources Used
                  </div>
                  <div style={{ display: 'flex', gap: 'var(--space-2)', overflowX: 'auto', paddingBottom: 'var(--space-2)' }}>
                    {streamSources.map((src, i) => (
                      <div
                        key={src.index}
                        className="source-card"
                        style={{
                          animationDelay: `${i * 100}ms`,
                          minWidth: '200px', maxWidth: '260px', padding: 'var(--space-3)',
                          background: 'var(--bg-elevated)', border: '1px solid var(--border)',
                          borderRadius: 'var(--radius-md)', fontSize: 'var(--fs-xs)',
                          display: 'flex', flexDirection: 'column', gap: 'var(--space-1)',
                          transition: 'border-color var(--transition), box-shadow var(--transition)',
                          cursor: 'default', flexShrink: 0,
                        }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--primary)'; e.currentTarget.style.boxShadow = '0 2px 8px rgba(43,94,168,0.1)'; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = 'none'; }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-1)' }}>
                      <span style={{ background: 'var(--primary)', color: 'white', borderRadius: '50%', width: '18px', height: '18px', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 'var(--fs-2xs)', fontWeight: 700, flexShrink: 0 }}>{src.index}</span>
                      <span style={{ fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{src.title}</span>
                    </div>
                    <div style={{ color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {src.authors} {src.year && `(${src.year})`}
                    </div>
                    {src.url && (
                      <a href={src.url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--primary)', display: 'inline-flex', alignItems: 'center', gap: '2px', fontSize: 'var(--fs-2xs)', textDecoration: 'none', marginTop: 'auto' }}>
                        <ExternalLink size={10} /> View source
                      </a>
                    )}
                  </div>
                ))}
              </div>
              </>
              ) : (
                <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', fontStyle: 'italic', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                  <BookOpen size={14} /> No specific sources cited — generated from general context.
                </div>
              )}
            </div>
          )}

          {/* Gap Analysis Panel */}
          {gapAnalysis && (
            <div style={{ marginBottom: 'var(--space-5)', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 'var(--space-5)' }}>
              <button onClick={() => setGapPanelOpen(o => !o)} style={{display:'flex', alignItems:'center', gap:'var(--space-2)', width:'100%', background:'none', border:'none', cursor:'pointer', fontSize:'var(--fs-md)', fontWeight:600}}>
                <Search size={16} color="var(--primary)" /> Research Gaps Analysis
                <span style={{fontSize:'var(--fs-sm)', color:'var(--text-subtle)', fontWeight:400}}>
                  ({gapAnalysis.conflicts?.length || 0} conflicts, {gapAnalysis.gaps?.length || 0} gaps)
                </span>
                <ChevronDown size={16} style={{marginLeft:'auto', transform: gapPanelOpen ? 'rotate(180deg)' : 'none', transition:'transform 150ms ease'}} />
              </button>
              
              <div className={`gap-panel-body${gapPanelOpen ? ' open' : ''}`}>
                <div className="inner">
                  <div style={{ paddingTop: gapPanelOpen ? 'var(--space-4)' : '0', transition: 'padding-top var(--transition)' }}>
                  {gapAnalysis.status === 'insufficient_literature' ? (
                    <div style={{ textAlign: 'center', padding: 'var(--space-4)', color: 'var(--text-muted)' }}>
                      <p style={{ margin: 0 }}>{gapAnalysis.message}</p>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', borderBottom: '1px solid var(--border)', paddingBottom: 'var(--space-2)', position: 'relative' }}>
                        {['consensus', 'conflicts', 'gaps'].map(tab => (
                          <button
                            key={tab}
                            onClick={() => setGapTab(tab)}
                            style={{ background: 'none', border: 'none', padding: 'var(--space-1) var(--space-3)', color: gapTab === tab ? 'var(--primary)' : 'var(--text-subtle)', fontWeight: gapTab === tab ? 600 : 400, cursor: 'pointer', transition: 'color var(--transition)', fontSize: 'var(--fs-sm)', textTransform: 'capitalize', textAlign: 'center' }}
                          >
                            {tab}
                          </button>
                        ))}
                        <div style={{
                          position: 'absolute', bottom: 0, height: '2px', background: 'var(--primary)',
                          transition: 'transform var(--transition)',
                          width: 'calc(100% / 3)',
                          transform: `translateX(${['consensus', 'conflicts', 'gaps'].indexOf(gapTab) * 100}%)`
                        }} />
                      </div>

                      {gapTab === 'consensus' && gapAnalysis.consensus && gapAnalysis.consensus.length > 0 && (
                        <div>
                          <ul style={{ margin: 0, paddingLeft: 'var(--space-5)', fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>
                            {(gapAnalysis.consensus || []).map((item, i) => (
                              <li key={i} style={{ marginBottom: 'var(--space-1)' }}>
                                {item.claim} <span style={{color: 'var(--text-subtle)'}}>[{item.supporting_papers?.join(', ')}]</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      
                      {gapTab === 'conflicts' && gapAnalysis.conflicts && gapAnalysis.conflicts.length > 0 && (
                        <div>
                          <ul style={{ margin: 0, paddingLeft: 'var(--space-5)', fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>
                            {(gapAnalysis.conflicts || []).map((item, i) => (
                              <li key={i} style={{ marginBottom: 'var(--space-1)' }}>
                                {item.claim_a} <strong>vs</strong> {item.claim_b} <span style={{color: 'var(--text-subtle)'}}>[{item.papers?.join(', ')}]</span><br/>
                                <span style={{fontSize: 'var(--fs-sm)', color: 'var(--text-subtle)'}}>{item.note}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {gapTab === 'gaps' && gapAnalysis.gaps && gapAnalysis.gaps.length > 0 && (
                        <div>
                          <ul style={{ margin: 0, paddingLeft: 'var(--space-5)', fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>
                            {(gapAnalysis.gaps || []).map((item, i) => (
                              <li key={i} style={{ marginBottom: 'var(--space-1)' }}>
                                {item.description} <span style={{color: 'var(--text-subtle)'}}>[{item.informed_by?.join(', ')}]</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      <div style={{ background: 'rgba(0, 87, 255, 0.05)', padding: 'var(--space-4)', borderRadius: 'var(--radius-md)', border: '1px solid rgba(0, 87, 255, 0.15)' }}>
                        <h4 style={{ margin: '0 0 var(--space-2) 0', fontSize: 'var(--fs-sm)', color: 'var(--primary)' }}>Suggested Direction</h4>
                        <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>{gapAnalysis.suggested_direction}</p>
                        <button className="btn btn-primary" style={{marginTop:'var(--space-3)'}}
                          onClick={() => { setTopic(gapAnalysis.suggested_direction); generate(); }}>
                          Use this direction →
                        </button>
                        {gapAnalysis.vagueness_warning && (
                          <div style={{ marginTop: 'var(--space-2)', padding: 'var(--space-2)', background: 'rgba(255, 152, 0, 0.1)', color: 'var(--warning)', borderRadius: '4px', fontSize: 'var(--fs-sm)' }}>
                            {gapAnalysis.vagueness_warning}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {generateError && (
            <div style={{ marginTop: 'var(--space-4)', marginBottom: 'var(--space-4)', padding: 'var(--space-3) var(--space-4)', background: 'rgba(229,28,35,0.08)', border: '1px solid rgba(229,28,35,0.2)', borderRadius: 'var(--radius-md)', color: 'var(--danger)', fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
              <X size={15} /> {generateError}
            </div>
          )}
          {/* Unverified Stats Toast */}
          {(unverifiedWarning || unverifiedNumbers.length > 0) && (
            <div style={{
              position: 'fixed', bottom: 'var(--space-5)', right: 'var(--space-5)',
              zIndex: 100, maxWidth: '360px', width: '100%',
              background: 'var(--bg-card)', border: '1px solid rgba(229,28,35,0.25)',
              borderRadius: 'var(--radius-lg)',
              boxShadow: '0 8px 32px rgba(0,0,0,0.12), 0 2px 8px rgba(229,28,35,0.08)',
              padding: 'var(--space-4)',
              animation: 'slideUp 0.3s ease-out both',
            }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 'var(--space-3)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', minWidth: 0 }}>
                  <span style={{ fontSize: 'var(--fs-md)', flexShrink: 0 }}>⚠️</span>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>
                      {unverifiedNumbers.length} stat{unverifiedNumbers.length !== 1 ? 's' : ''} may need verification
                    </div>
                    <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginTop: '2px' }}>
                      Not found in source papers
                    </div>
                  </div>
                </div>
                <button
                  onClick={() => { setUnverifiedWarning(''); setUnverifiedNumbers([]); }}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px', color: 'var(--text-subtle)', flexShrink: 0 }}
                >
                  <X size={14} />
                </button>
              </div>
              {unverifiedNumbers.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-1)', marginTop: 'var(--space-3)' }}>
                  {unverifiedNumbers.map((num, i) => (
                    <span key={i} style={{
                      padding: '2px 8px', borderRadius: '999px',
                      background: 'rgba(229,28,35,0.08)', color: 'var(--danger)',
                      fontSize: 'var(--fs-xs)', fontWeight: 600, fontFamily: "'IBM Plex Mono', monospace",
                      border: '1px solid rgba(229,28,35,0.15)',
                    }}>{num}</span>
                  ))}
                </div>
              )}
            </div>
          )}

          {generating && (
            <div style={{ marginTop: 'var(--space-6)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', marginBottom: 'var(--space-5)', background: 'rgba(0, 87, 255, 0.04)', border: '1px solid rgba(0, 87, 255, 0.1)', padding: 'var(--space-4)', borderRadius: 'var(--radius-lg)' }}>
                <div style={{ animation: 'spin 3s linear infinite' }}>
                  <Sparkles size={24} style={{ color: 'var(--primary)' }} />
                </div>
                <div>
                  <h2 style={{ fontSize: 'var(--fs-md)', fontWeight: 600, margin: '0 0 var(--space-1) 0', color: 'var(--primary)' }}>
                    Generating your manuscript...
                  </h2>
                  <div style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                    <Spinner size={12} /> Synthesizing evidence and structuring content...
                  </div>
                </div>
              </div>
              <SkeletonText lines={12} />
            </div>
          )}

          {pendingEdit ? (
            <div className="manuscript-diff-view">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 'var(--space-4)', marginBottom: 'var(--space-4)' }}>
                <h3 style={{ margin: 0, fontSize: 'var(--fs-base)', color: 'var(--primary)' }}>Review AI Revisions</h3>
                <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
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
                <div style={{ display: 'flex', gap: 'var(--space-4)', alignItems: 'center' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', position: 'relative' }}>
                    {['write', 'preview', 'paper'].map(mode => (
                      <button
                        key={mode}
                        onClick={() => setViewMode(mode)}
                        style={{ background: 'none', border: 'none', padding: 'var(--space-1) var(--space-2)', color: viewMode === mode ? 'var(--primary)' : 'var(--text-subtle)', fontWeight: viewMode === mode ? 600 : 400, cursor: 'pointer', transition: 'color var(--transition)', fontSize: 'var(--fs-sm)', textAlign: 'center' }}
                      >
                        {mode === 'write' ? 'Write' : mode === 'preview' ? 'Preview' : 'Paper Preview'}
                      </button>
                    ))}
                    <div style={{
                      position: 'absolute', bottom: 0, height: '2px', background: 'var(--primary)',
                      transition: 'transform var(--transition)',
                      width: 'calc(100% / 3)',
                      transform: `translateX(${['write', 'preview', 'paper'].indexOf(viewMode) * 100}%)`
                    }} />
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
                <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-subtle)', fontWeight: 500, minWidth: '70px', textAlign: 'right' }}>
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
                  style={{ width: '100%', minHeight: '420px', padding: 'var(--space-4)', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', color: 'var(--text)', fontFamily: 'inherit', fontSize: 'var(--fs-sm)', resize: 'vertical', outline: 'none', lineHeight: 1.75, transition: 'var(--transition)', boxSizing: 'border-box', opacity: generating ? 0.8 : 1 }}
                  onFocus={e => { e.target.style.borderColor = 'var(--border-focus)'; e.target.style.boxShadow = '0 0 0 3px var(--primary-light)'; }}
                  onBlur={e => { e.target.style.borderColor = 'var(--border)'; e.target.style.boxShadow = 'none'; }}
                />
              ) : viewMode === 'preview' ? (
                <div className="pdf-markdown-body" style={{ width: '100%', minHeight: '420px', padding: 'var(--space-4)', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', color: 'var(--text)', overflowY: 'auto', boxSizing: 'border-box' }}>
                  {content[active] ? (
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm, remarkMath]}
                      rehypePlugins={[rehypeKatex]}
                      components={{
                        table: TableOrChart,
                        a: ({ node, href, children, ...props }) => {
                          if (href === '#unverified-stat') {
                            return <span className="unverified-stat" title="Not found in source papers">{children}</span>;
                          }
                          return <a href={href} {...props}>{children}</a>;
                        },
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
                      {processForUnverified((content[active] || '') + (generating ? ' <span class="write-cursor">▋</span>' : ''))}
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
                            a: ({ node, href, children, ...props }) => {
                              if (href === '#unverified-stat') {
                                return <span className="unverified-stat" title="Not found in source papers">{children}</span>;
                              }
                              return <a href={href} {...props}>{children}</a>;
                            },
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
                          {processForUnverified(content[active])}
                        </ReactMarkdown>
                      ) : (
                        <p style={{ color: '#999', fontStyle: 'italic', textAlign: 'center', marginTop: 'var(--space-7)' }}>
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
                        <div key={step.id} className="paper-section" style={{ marginBottom: 'var(--space-6)' }}>
                          {step.id !== 'abstract' && (
                            <h2 style={{ textTransform: 'uppercase', fontSize: 'var(--fs-md)', marginBottom: 'var(--space-4)', borderBottom: '1px solid #eee', paddingBottom: 'var(--space-2)' }}>
                              {step.label}
                            </h2>
                          )}
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm, remarkMath]}
                            rehypePlugins={[rehypeKatex]}
                            components={{
                              table: TableOrChart,
                              a: ({ node, href, children, ...props }) => {
                                if (href === '#unverified-stat') {
                                  return <span className="unverified-stat" title="Not found in source papers">{children}</span>;
                                }
                                return <a href={href} {...props}>{children}</a>;
                              },
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
                            {processForUnverified(content[step.id])}
                          </ReactMarkdown>
                        </div>
                      ) : null)}
                    </div>
                  </div>
                </>
              )}

              {content[active] && !generating && (
                <div style={{ position: 'absolute', bottom: '1.5rem', right: '1.5rem', display: 'flex', gap: 'var(--space-3)' }}>
                  {editHistory[active] && editHistory[active].length > 0 && (
                    <button 
                      className="btn btn-secondary"
                      onClick={undoLastEdit}
                      style={{ borderRadius: '50px', padding: 'var(--space-3) var(--space-4)', boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}
                    >
                      <Undo size={16} /> Undo AI Edit
                    </button>
                  )}
                  <button 
                    className="btn btn-primary"
                    onClick={() => setRevisePanelOpen(true)}
                    style={{ borderRadius: '50px', padding: 'var(--space-3) var(--space-4)', boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}
                  >
                    <Sparkles size={16} /> AI Revise
                  </button>
                </div>
              )}

              <div className="manuscript-revise-panel-container">
                <div className={`manuscript-revise-panel ${revisePanelOpen ? 'open' : ''}`}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-3)' }}>
                    <p style={{ margin: 0, fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--primary)' }}><Sparkles size={14} style={{ display: 'inline', verticalAlign: 'text-bottom' }}/> Revise Section</p>
                    <button onClick={() => setRevisePanelOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-subtle)' }}><X size={16} /></button>
                  </div>
                  <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
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
                  {editError && <div style={{ color: 'var(--danger)', fontSize: 'var(--fs-sm)', marginTop: 'var(--space-2)' }}>{editError}</div>}
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
                  <h3 style={{ margin: 0, fontSize: 'var(--fs-md)', color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                    <BookOpen size={16} color="var(--primary)" /> References
                  </h3>
                  <button onClick={() => setRefsOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-subtle)' }}><X size={16} /></button>
                </div>
                <div className="manuscript-refs-drawer-content">
                  <ol style={{ margin: 0, paddingLeft: 'var(--space-5)', fontSize: 'var(--fs-sm)', color: 'var(--text-subtle)', lineHeight: 1.6 }}>
                    {Object.entries(manuscriptRefs).map(([idx, refString]) => (
                      <li key={idx} style={{ marginBottom: 'var(--space-2)' }}>{refString}</li>
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

      {/* New Paper Confirm Modal */}
      {showNewPaperConfirm && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, backdropFilter: 'blur(4px)' }} onClick={() => setShowNewPaperConfirm(false)}>
          <div className="animate-scale-in" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', padding: 'var(--space-6)', width: '100%', maxWidth: '400px' }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-4)' }}>
              <h3 style={{ margin: 0, color: 'var(--danger)' }}>Start New Paper?</h3>
              <button onClick={() => setShowNewPaperConfirm(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-subtle)', display: 'flex' }}><X size={17} /></button>
            </div>
            <p style={{ fontSize: 'var(--fs-sm)', marginBottom: 'var(--space-6)' }}>Are you sure you want to start a new paper? Any unsaved changes in your current manuscript will be lost.</p>
            <div style={{ display: 'flex', gap: 'var(--space-3)', justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" onClick={() => setShowNewPaperConfirm(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={confirmNewPaper} style={{ background: 'var(--danger)' }}>Yes, Start Fresh</button>
            </div>
          </div>
        </div>
      )}

      {/* Load modal */}
      {showLoad && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, backdropFilter: 'blur(4px)' }} onClick={() => setShowLoad(false)}>
          <div className="animate-scale-in" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', padding: 'var(--space-6)', width: '100%', maxWidth: '400px' }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-4)' }}>
              <h3 style={{ margin: 0 }}>Load Draft</h3>
              <button onClick={() => setShowLoad(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-subtle)', display: 'flex' }}><X size={17} /></button>
            </div>
            <p style={{ fontSize: 'var(--fs-sm)', marginBottom: 'var(--space-3)' }}>Choose one of your saved manuscript drafts.</p>
            <div style={{ position: 'relative', marginBottom: 'var(--space-4)' }}>
              <Search size={15} style={{ position: 'absolute', left: '0.9rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-subtle)', pointerEvents: 'none' }} />
              <input placeholder="Filter saved drafts..." value={draftFilter} onChange={e => setDraftFilter(e.target.value)} style={{ paddingLeft: 'var(--space-6)' }} />
            </div>
            {loadError && <p style={{ color: 'var(--danger)', fontSize: 'var(--fs-sm)', marginBottom: 'var(--space-3)' }}>{loadError}</p>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', maxHeight: '280px', overflowY: 'auto', marginBottom: 'var(--space-4)' }}>
              {draftLoading && <p style={{ margin: 0, color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>Loading drafts...</p>}
              {!draftLoading && visibleDrafts.length === 0 && (
                <div className="empty-state" style={{ padding: 'var(--space-4)', fontSize: 'var(--fs-sm)' }}>
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
                  <span style={{ color: 'var(--text-subtle)', fontSize: 'var(--fs-xs)', fontWeight: 700 }}>Load</span>
                </button>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 'var(--space-3)', justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" onClick={() => setShowLoad(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
