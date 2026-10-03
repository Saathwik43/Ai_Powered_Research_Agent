import React, { useCallback, useEffect, useMemo, useState, useRef } from 'react';
import { CheckCircle, Circle, Save, FileText, Wand2, FolderOpen, X, Search, Sparkles, Send, BookOpen, Bold, Italic, Strikethrough, Link, List, ListOrdered, CheckSquare, Table, Quote, Code, Undo, Redo, Heading1, Heading2, Heading3, Printer, ExternalLink, Plus, Pin, History, RotateCcw , PanelLeft, PanelRight, Settings2, Download, AlertTriangle, Pin as PinIcon } from 'lucide-react';
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
import CodeHighlight from '../components/CodeHighlight';
import 'katex/dist/katex.min.css';
import { MODELS } from '../constants/models';
import { diffWords } from 'diff';
import Mermaid from '../components/Mermaid';
import {
  isMermaidBlock,
  parseCodeLanguage,
  codeChildrenToText,
  findMermaidBlocks,
} from '../utils/mermaidChart';
import { normalizeLatexDelimiters, KATEX_REHYPE_OPTIONS } from '../utils/latexMath';
import SourcesPanel from '../components/SourcesPanel';
import { SavedItemMenu, RenameField } from '../components/SavedItemActions';
import TopBar from '../components/TopBar';
import { Button } from '../components/ui/button';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/ui/tabs';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '../components/ui/sheet';
import { Popover, PopoverContent, PopoverTrigger } from '../components/ui/popover';
import {
  Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue,
} from '../components/ui/select';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger,
} from '../components/ui/dropdown-menu';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '../components/ui/alert-dialog';
import { splitPinned, displayName, orderPinned } from '../lib/savedItems';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

function MarkdownCode({ className, children, ...props }) {
  const language = parseCodeLanguage(className);
  const contentStr = codeChildrenToText(children);
  const isBlock = Boolean(className) || contentStr.includes('\n');

  if (isBlock && isMermaidBlock(language, contentStr)) {
    return <Mermaid chart={contentStr} />;
  }
  return isBlock && language ? (
    <CodeHighlight language={language} {...props}>
      {contentStr}
    </CodeHighlight>
  ) : (
    <code className={className} {...props}>
      {children}
    </code>
  );
}

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
              <XAxis dataKey={xKey} tick={{ fontSize: 12, fill: 'var(--text-muted)' }} />
              <YAxis tick={{ fontSize: 12, fill: 'var(--text-muted)' }} />
              <Tooltip cursor={{fill: 'var(--primary-light)'}} contentStyle={{ borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', boxShadow: 'var(--shadow-md)' }} />
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

/**
 * Verification verdict for a pending revision, shown above the diff so the
 * warning is visible while the change can still be discarded. Revisions
 * previously bypassed verification entirely, leaving the section's *previous*
 * verdict on screen — so the UI read "verified" about text that no longer existed.
 */
function RevisionChecks({ flags }) {
  if (!flags) return null;

  const ungrounded = (flags.citationMap || []).filter(
    (c) => c.status && c.status !== 'grounded'
  );
  const issues = [];

  if (flags.truncated) {
    issues.push({
      key: 'truncated',
      tone: 'danger',
      text: 'This revision may have been cut off before it finished. Check the ending before accepting.',
    });
  }
  if (flags.unverifiedCitations) {
    issues.push({
      key: 'citations',
      tone: 'danger',
      text: 'Contains citations that could not be matched to the source list.',
    });
  }
  // Checked server-side against the revised text: a revision re-emits the whole
  // section, so a diagram can come back broken even when the prose is fine.
  if (flags.diagramErrors?.length) {
    const n = flags.diagramErrors.length;
    issues.push({
      key: 'diagrams',
      tone: 'danger',
      text: `${n} diagram${n > 1 ? 's' : ''} in this revision will not render.`,
      detail: flags.diagramErrors
        .slice(0, 3)
        .map((d) => (d.index ? `Diagram ${d.index}: ${d.error}` : d.error)),
    });
  }
  if (flags.diagramsDropped > 0) {
    const n = flags.diagramsDropped;
    issues.push({
      key: 'diagrams-dropped',
      tone: 'warn',
      text: `This revision has ${n} fewer diagram${n > 1 ? 's' : ''} than the current section.`,
    });
  }
  // Scoping failed open rather than blocking the revision — say so, because the
  // user asked for one excerpt and got the whole section back.
  if (flags.targetUnresolved) {
    issues.push({
      key: 'target-unresolved',
      tone: 'warn',
      text: 'The text you targeted was no longer found in the section, so the whole section was revised instead. Read the diff before accepting.',
    });
  }
  if (flags.targetOverrun) {
    issues.push({
      key: 'target-overrun',
      tone: 'danger',
      text: 'The model returned far more text than the excerpt it was asked to replace, so this revision may contain duplicated text.',
    });
  }
  if (ungrounded.length) {
    issues.push({
      key: 'grounding',
      tone: 'warn',
      text: `${ungrounded.length} sentence${ungrounded.length > 1 ? 's' : ''} not fully supported by the cited source${ungrounded.length > 1 ? 's' : ''}.`,
      detail: ungrounded.slice(0, 3).map((c) => `“${c.sentence}” — ${c.status}${c.note ? `: ${c.note}` : ''}`),
    });
  }
  if (flags.unverifiedNumbers?.length) {
    issues.push({
      key: 'numbers',
      tone: 'warn',
      text: `Numbers not found in the sources: ${flags.unverifiedNumbers.slice(0, 8).join(', ')}`,
    });
  }
  if (flags.uncitedClaims?.length) {
    issues.push({
      key: 'uncited',
      tone: 'warn',
      text: `${flags.uncitedClaims.length} factual-sounding claim${flags.uncitedClaims.length > 1 ? 's' : ''} carry no citation marker.`,
    });
  }

  if (!issues.length) {
    return (
      <div className="revision-checks ok">
        <CheckCircle size={14} />
        {flags.scoped
          ? 'Only the targeted excerpt was rewritten; the rest of the section is unchanged. Citations and figures check out.'
          : 'Citations and figures in this revision check out against the source list.'}
      </div>
    );
  }

  return (
    <div className="revision-checks">
      {issues.map((issue) => (
        <div key={issue.key} className={`revision-check ${issue.tone}`}>
          <span>{issue.text}</span>
          {issue.detail && (
            <ul>
              {issue.detail.map((d, i) => <li key={i}>{d}</li>)}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

// Per-viewer layout conveniences (outline / tools panel); never data.
function readPref(key, fallback) {
  try {
    const v = window.localStorage.getItem(key);
    return v === null ? fallback : v === 'true';
  } catch {
    return fallback;
  }
}

function writePref(key, value) {
  try { window.localStorage.setItem(key, String(value)); } catch { /* private mode */ }
}

function useMediaQuery(query) {
  const [matches, setMatches] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setMatches(mq.matches);
    mq.addEventListener('change', onChange);
    onChange();
    return () => mq.removeEventListener('change', onChange);
  }, [query]);
  return matches;
}

function savedAgo(ts, now) {
  if (!ts) return '';
  const secs = Math.max(0, Math.round((now - ts) / 1000));
  if (secs < 45) return 'just now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const d = new Date(ts);
  // Today: the time. Earlier: the date, or a July save reads as "10:12 AM".
  return d.toDateString() === new Date(now).toDateString()
    ? `at ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
    : `on ${d.toLocaleDateString([], { day: 'numeric', month: 'short', year: d.getFullYear() === new Date(now).getFullYear() ? undefined : 'numeric' })}`;
}

const CITATION_STYLES = [
  ['ieee', 'IEEE'], ['apa', 'APA'], ['chicago', 'Chicago'], ['oxford', 'Oxford'],
];

const LATEX_VENUES = [
  ['ieee', 'IEEE'], ['acm', 'ACM'], ['springer', 'Springer LNCS'], ['elsevier', 'Elsevier'],
];

// The format toolbar (ME-1): [label, icon, prefix, suffix]. One table so every
// button gets the same name, keyboard and selection handling.
const FORMAT_GROUPS = [
  [['Heading 1', Heading1, '# '], ['Heading 2', Heading2, '## '], ['Heading 3', Heading3, '### ']],
  [['Bold', Bold, '**', '**'], ['Italic', Italic, '*', '*'], ['Strikethrough', Strikethrough, '~~', '~~'], ['Link', Link, '[', '](url)']],
  [['Bulleted list', List, '- '], ['Numbered list', ListOrdered, '1. '], ['Checklist', CheckSquare, '- [ ] '],
    ['Table', Table, '\n| Column 1 | Column 2 |\n| -------- | -------- |\n| Text     | Text     |\n']],
  [['Blockquote', Quote, '> '], ['Code block', Code, '```\n', '\n```']],
];

const STEPS = [
  { id: 'abstract',    label: 'Abstract' },
  { id: 'lit_review',  label: 'Literature Review' },
  { id: 'methodology', label: 'Proposed Methodology' },
  { id: 'results',     label: 'Projected Outcomes' },
  { id: 'references',  label: 'References' },
];

export default function ManuscriptBuilder() {
  const { api } = useAuth();
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
  // Verification verdict for the *pending* revision, shown in the diff view so
  // the user sees it before deciding, not after the text has already landed.
  const [pendingEditFlags, setPendingEditFlags] = useState(null);
  const [saveStatus, setSaveStatus] = useState('');
  const [printPending, setPrintPending] = useState(false);
  const [showLoad,     setShowLoad]     = useState(false);
  const [showNewPaperConfirm, setShowNewPaperConfirm] = useState(false);
  const [viewMode,     setViewMode]     = useState('write');
  const [drafts,       setDrafts]       = useState([]);
  const [draftFilter,  setDraftFilter]  = useState('');
  const [draftLoading, setDraftLoading] = useState(false);
  const [loadError,    setLoadError]    = useState('');
  // ROW-2: draft whose name is being edited inline in the Load dialog.
  const [renamingDraftId, setRenamingDraftId] = useState(null);
  const [loadedDraftId, setLoadedDraftId] = useState(null);
  // Version history (1.10). Every save snapshots what it replaced, server-side.
  const [showHistory, setShowHistory] = useState(false);
  const [versions, setVersions] = useState([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [versionError, setVersionError] = useState('');
  const [restoringVersion, setRestoringVersion] = useState('');
  const [editPrompt,   setEditPrompt]   = useState('');
  const [editing,      setEditing]      = useState(false);
  const [editError,    setEditError]    = useState('');
  const [generateError, setGenerateError] = useState('');
  const [unverifiedWarning, setUnverifiedWarning] = useState('');
  const [unverifiedNumbers, setUnverifiedNumbers] = useState([]);
  // Toolbar edits made without execCommand, which the native undo cannot see.
  const fallbackUndoRef = useRef([]);
  // Revision scope. `editScope` is null (whole section), {kind:'selection'} or
  // {kind:'diagram', index}. Diagram spans are resolved against live content at
  // send time rather than stored, so editing the section cannot make them stale.
  const [editScope, setEditScope] = useState(null);
  const [editorSelection, setEditorSelection] = useState(null);
  
  // Phase B additions
  const [citationStyle, setCitationStyle] = useState('ieee');
  const [selectedModelId, setSelectedModelId] = useState('groq-default');
  const [rateLimitWait, setRateLimitWait] = useState(null);
  const [autoMode, setAutoMode] = useState(true);
  const [autoStatus, setAutoStatus] = useState('');
  
  const [gapAnalysis, setGapAnalysis] = useState(null);
  // SD-5 (Direction B): outline left, document centre, AI & evidence on demand
  // in a right panel that docks beside the document or floats as a sheet.
  const [outlineOpen, setOutlineOpen] = useState(() => readPref('ms-outline-open', true));
  const [toolsOpen, setToolsOpen] = useState(() => readPref('ms-tools-open', false));
  const [toolsDocked, setToolsDocked] = useState(() => readPref('ms-tools-docked', true));
  const [toolsTab, setToolsTab] = useState('revise');
  const canDock = useMediaQuery('(min-width: 1100px)');
  const [lastSavedAt, setLastSavedAt] = useState(null);
  const [nowTick, setNowTick] = useState(() => Date.now());
  const [customContext, setCustomContext] = useState('');
  const [streamSources, setStreamSources] = useState([]);
  const [sourcesResolved, setSourcesResolved] = useState(false);
  // Research pipeline stages (1.5), newest state per stage.
  const [researchStages, setResearchStages] = useState([]);
  const [researchReady, setResearchReady] = useState(false);
  
  const abortControllerRef = useRef(null);
  const streamBufferRef = useRef('');
  const streamRafRef = useRef(null);
  // Whether this sitting's topic came from the user typing it. The prewarm
  // below is billed work, so it needs an intent to generate — see there.
  const topicTypedRef = useRef(false);
  const [sectionTruncated, setSectionTruncated] = useState(false);

  const flushStreamBuffer = (sectionId) => {
    streamRafRef.current = null;
    const pending = streamBufferRef.current;
    if (!pending) return;
    streamBufferRef.current = '';
    setContent(prev => ({ ...prev, [sectionId]: (prev[sectionId] || '') + pending }));
  };

  const queueStreamText = (sectionId, text) => {
    if (!text) return;
    streamBufferRef.current += text;
    if (streamRafRef.current == null) {
      streamRafRef.current = requestAnimationFrame(() => flushStreamBuffer(sectionId));
    }
  };

  const settleStreamBuffer = (sectionId) => {
    if (streamRafRef.current != null) {
      cancelAnimationFrame(streamRafRef.current);
      streamRafRef.current = null;
    }
    flushStreamBuffer(sectionId);
  };

  const processForUnverified = (text, sectionLabel = '') => {
    if (!text) return '';
    // Banner-only for unverified stats — substring replace used to rewrite
    // chart/math source (C9).
    // A leading H1 is dropped only when it repeats the section's own name
    // (the model opens with "# Abstract", which the preview already labels).
    // It used to drop *any* first H1, so an author's own heading vanished
    // from every preview (ME-3, B4).
    const lead = text.match(/^#\s+([^\n]+)\n?/);
    if (lead && sectionLabel) {
      const norm = (s) => s.toLowerCase().replace(/[^a-z]/g, '');
      const heading = norm(lead[1]);
      const label = norm(sectionLabel);
      if (heading && (heading === label || label.includes(heading))) {
        return text.slice(lead[0].length).trim();
      }
    }
    return text.trim();
  };

  const formatPaperTitle = (text) => {
    if (!text) return 'Untitled Research Paper';
    let cleaned = text.trim();
    // Strip common prompt action prefixes
    cleaned = cleaned.replace(/^(investigate|analyze|study|develop|research|explore|evaluate|assess|design)\s+(the\s+)?(development\s+of\s+a\s+|use\s+of\s+a\s+|application\s+of\s+a\s+)?/i, '');
    cleaned = cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
    
    const words = cleaned.split(/\s+/);
    if (words.length > 10) {
      return words.slice(0, 9).join(' ') + '...';
    }
    return cleaned;
  };

  const done = STEPS.filter(s => content[s.id]?.trim()).map(s => s.id);

  // E11: the active section and the whole paper (References excluded — it is
  // a generated list, not prose the author is counting toward a limit).
  const countWords = (text) => (text && text.trim() ? text.trim().split(/\s+/).length : 0);
  const sectionWords = countWords(content[active]);
  const paperWords = STEPS.filter(s => s.id !== 'references')
    .reduce((sum, s) => sum + countWords(content[s.id]), 0);

  // Keeps "Saved n min ago" current without re-rendering every second.
  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);

  // E9: one polite live region, so a screen reader hears what sighted users
  // see change — generation starting and finishing, saves, provider status.
  const [announcement, setAnnouncement] = useState('');
  const wasGeneratingRef = useRef(false);
  useEffect(() => {
    const label = STEPS.find(s => s.id === active)?.label || 'Section';
    if (generating && !wasGeneratingRef.current) setAnnouncement(`Generating ${label}…`);
    if (!generating && wasGeneratingRef.current && !generateError) setAnnouncement(`${label} finished generating.`);
    wasGeneratingRef.current = generating;
  }, [generating, generateError, active]);
  useEffect(() => {
    if (saveStatus === 'saved') setAnnouncement('Draft saved.');
    else if (saveStatus === 'error') setAnnouncement('Save failed.');
  }, [saveStatus]);
  useEffect(() => {
    if (autoStatus) setAnnouncement(autoStatus);
  }, [autoStatus]);

  // Warm the research corpus as soon as the topic settles (1.5). The corpus is
  // the same for every section, so building it while the user is still picking
  // one takes the whole 8-source fan-out, the relevance pass and the full-text
  // fetches off the critical path of the first Generate. Debounced because this
  // fires on every keystroke of the topic field.
  // Only for a topic the user is *typing*, because this is billed work and not
  // every topic change is an intent to generate. `prepare_corpus` is tagged
  // `manuscript` in the usage tracker and pays for an LLM screening pass plus
  // per-paper evidence extraction, so firing it on `topic` alone charged a
  // reader for opening a finished draft (`load` sets the topic) or for merely
  // navigating back to this page (the topic lives in AppContext and outlives
  // the mount). Both are reads. Nothing is lost by waiting: Generate prepares
  // the corpus itself and reports the same stages inline.
  useEffect(() => {
    const trimmed = topic.trim();
    setResearchReady(false);
    if (trimmed.length < 4 || generating || !topicTypedRef.current) return undefined;

    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const body = await api.post('/api/manuscript/research/prepare', { topic: trimmed });
        if (!cancelled) setResearchReady(body?.data?.status === 'ready');
      } catch {
        // Best effort. A failed warm-up just means Generate does the work
        // itself, reporting the same stages inline.
      }
    }, 1200);

    return () => { cancelled = true; clearTimeout(timer); };
  }, [topic, generating, api]);

  const generate = async (opts = {}) => {
    // `opts.topic`: a caller that has just called setTopic() passes the new
    // value, because this closure still holds the old one (ME-3, B1).
    const topicText = (opts.topic ?? topic).trim();
    if (!topicText) return;
    const sectionId = active;
    const continueSection = Boolean(opts.continueSection) && Boolean((content[sectionId] || '').trim());
    setGenerating(true);
    setGenerateError('');
    setUnverifiedWarning('');
    setUnverifiedNumbers([]);
    setRateLimitWait(null);
    setStreamSources([]);
    setSourcesResolved(false);
    streamBufferRef.current = '';
    if (streamRafRef.current != null) {
      cancelAnimationFrame(streamRafRef.current);
      streamRafRef.current = null;
    }
    if (!continueSection) {
      setContent(prev => ({ ...prev, [sectionId]: '' }));
      setSectionTruncated(false);
    }
    
    abortControllerRef.current = new AbortController();
    
    // If the active section is 'references', we don't need the LLM to generate it!
    // We already have the compiled references in `manuscriptRefs`.
    if (sectionId === 'references') {
      if (manuscriptRefs && Object.keys(manuscriptRefs).length > 0) {
        let refsText = '';
        Object.keys(manuscriptRefs).sort((a, b) => parseInt(a) - parseInt(b)).forEach(key => {
          refsText += `${key}. ${manuscriptRefs[key]}\n\n`;
        });
        setContent(prev => ({ ...prev, [sectionId]: refsText.trim() }));
      } else {
        setContent(prev => ({ ...prev, [sectionId]: '*No references have been cited in the generated manuscript yet.*' }));
      }
      setGenerating(false);
      return;
    }
    
    try {
      let payloadContext = customContext.trim() || 'Use latest research trends and cite recent advancements.';
      if (continueSection) {
        payloadContext = `The existing section was cut off mid-generation. Continue writing from the last sentence. Do not repeat the existing text.\n\n<existing_section>\n${content[sectionId]}\n</existing_section>\n\n${payloadContext}`;
      }
      const selectedModel = MODELS.find(m => m.id === selectedModelId) || MODELS[0];
      setAutoStatus('');
      
      const payload = { 
        topic: topicText, 
        section: sectionId, 
        context: payloadContext, 
        citation_style: citationStyle, 
        mode: autoMode ? 'auto' : 'manual'
      };
      
      if (!autoMode) {
        payload.provider = selectedModel.provider;
        payload.model = selectedModel.model;
      }

      const res = await api.raw(`/api/manuscript/stream`, {
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
                queueStreamText(sectionId, data.text || '');
              } else if (data.type === "status") {
                // Research stages (1.5). They arrive while the pipeline runs,
                // so the wait before the first token is legible instead of a
                // blank screen that reads as a hung request.
                setResearchStages((prev) => {
                  const next = prev.filter((s) => s.stage !== data.stage);
                  return [...next, { stage: data.stage, status: data.status, detail: data.detail }];
                });
              } else if (data.type === "sources_list") {
                setResearchStages([]);
                setStreamSources(data.sources || []);
                setSourcesResolved(true);
              } else if (data.type === "provider_active") {
                setAutoStatus(data.continuing ? `Resuming with ${data.provider}...` : `Generating with ${data.provider}...`);
              } else if (data.type === "provider_status") {
                setAutoStatus(data.message);
                setTimeout(() => setAutoStatus(''), 2500);
              } else if (data.type === "metadata") {
                settleStreamBuffer(sectionId);
                if (data.formatted_references) setManuscriptRefs(data.formatted_references);
                if (data.unverified_citations) setUnverifiedWarning('Warning: The generated text contains citations that could not be verified against the provided context. Please verify them independently.');
                if (data.unverified_numbers && data.unverified_numbers.length > 0) setUnverifiedNumbers(data.unverified_numbers);
                if (data.gap_analysis) setGapAnalysis(data.gap_analysis);
                else if (sectionId === 'lit_review' || sectionId === 'literature_review') setGapAnalysis(null);
                const reason = String(data.finish_reason || '').toLowerCase();
                const cutOff = Boolean(data.truncated) || ['length', 'max_tokens', 'max_output_tokens'].includes(reason);
                setSectionTruncated(cutOff);
              } else if (data.type === "stopped") {
                settleStreamBuffer(sectionId);
                setAutoStatus('');
                if (data.reason === "rate_limit") {
                  const waitSecs = data.retry_after_seconds;
                  if (waitSecs) {
                     setRateLimitWait(waitSecs);
                     setContent(prev => ({ ...prev, [sectionId]: (prev[sectionId] || "") + `\n\n*[Generation paused — rate limit reached. You can resume in ~${waitSecs}s.]*` }));
                  } else {
                     setContent(prev => ({ ...prev, [sectionId]: (prev[sectionId] || "") + `\n\n*[Generation paused — rate limit reached. Try again shortly.]*` }));
                  }
                } else if (data.reason === "error") {
                  setContent(prev => ({ ...prev, [sectionId]: (prev[sectionId] || "") + `\n\n*[Generation stopped: ${data.message}]*` }));
                }
              } else if (data.type === "done") {
                settleStreamBuffer(sectionId);
                setAutoStatus('');
                const reason = String(data.finish_reason || '').toLowerCase();
                if (['length', 'max_tokens', 'max_output_tokens'].includes(reason)) {
                  setSectionTruncated(true);
                }
                setGenerating(false);
              }
            } catch (err) {
              console.error("Error parsing stream chunk:", err, chunkStr);
            }
          }
        }
      }
      settleStreamBuffer(sectionId);
    } catch (e) {
      settleStreamBuffer(sectionId);
      if (e.name === 'AbortError') {
        setContent(prev => ({ ...prev, [sectionId]: (prev[sectionId] || "") + `\n\n*[Generation stopped by user]*` }));
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

  useEffect(() => {
    setSectionTruncated(false);
  }, [active]);

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
    
    // execCommand keeps the native undo stack. Its *result* is what counts:
    // queryCommandSupported can say yes while the insert still fails, and
    // that used to fall through silently (ME-3, B5).
    const inserted = document.queryCommandSupported?.('insertText')
      && document.execCommand('insertText', false, replacement);
    if (!inserted) {
      // The native stack cannot see this edit, so keep our own for Undo.
      fallbackUndoRef.current = [...fallbackUndoRef.current.slice(-49), { section: active, text }];
      const newText = text.substring(0, start) + replacement + text.substring(end);
      setContent(prev => ({ ...prev, [active]: newText }));
    }
    
    // Set selection back
    setTimeout(() => {
      textarea.setSelectionRange(start + prefix.length, start + prefix.length + selected.length);
    }, 0);
  };

  // Mousedown on a toolbar button would move focus off the textarea and lose
  // its selection; the action itself runs on click (keyboard works too).
  const keepEditorSelection = (e) => e.preventDefault();

  const handleUndo = (e) => {
    e.preventDefault();
    const textarea = document.getElementById('manuscript-textarea');
    const before = textarea?.value;
    // From the keyboard, focus is on the button; native undo acts on the
    // focused editor, so hand focus back first.
    textarea?.focus();
    document.execCommand('undo');
    // Native undo changed nothing: step back through edits it never saw.
    if (textarea && textarea.value === before) {
      const stack = fallbackUndoRef.current;
      const last = stack[stack.length - 1];
      if (last && last.section === active) {
        fallbackUndoRef.current = stack.slice(0, -1);
        setContent(prev => ({ ...prev, [active]: last.text }));
      }
    }
  };

  const handleRedo = (e) => {
    e.preventDefault();
    document.getElementById('manuscript-textarea')?.focus();
    document.execCommand('redo');
  };

  // Diagram spans in the *live* section, recomputed as the text changes, so a
  // chosen "Diagram 2" can never point at a stale offset.
  const diagramBlocks = useMemo(
    () => findMermaidBlocks(content[active] || ''),
    [content, active]
  );

  // Switching sections invalidates every span, so start over at whole-section.
  useEffect(() => {
    setEditScope(null);
    setEditorSelection(null);
  }, [active]);

  useEffect(() => {
    if (editScope?.kind === 'diagram' && !diagramBlocks[editScope.index]) setEditScope(null);
    if (editScope?.kind === 'selection' && !editorSelection) setEditScope(null);
  }, [editScope, diagramBlocks, editorSelection]);

  /** The span to revise, resolved against current content, or null for whole-section. */
  const resolveEditTarget = () => {
    if (editScope?.kind === 'selection' && editorSelection) {
      return { ...editorSelection, kind: 'selection' };
    }
    if (editScope?.kind === 'diagram') {
      const block = diagramBlocks[editScope.index];
      if (block) return { text: block.body, start: block.start, end: block.end, kind: 'diagram' };
    }
    return null;
  };

  const applyEdit = async () => {
    if (!editPrompt.trim() || !content[active]) return;
    setEditing(true);
    setEditError('');
    setPendingEditFlags(null);
    const target = resolveEditTarget();
    const selectedModel = MODELS.find(m => m.id === selectedModelId) || MODELS[0];
    abortControllerRef.current = new AbortController();
    let previewBuf = '';
    let previewRaf = null;
    const flushPreview = () => {
      previewRaf = null;
      const t = previewBuf;
      previewBuf = '';
      if (t && !target) setPendingEdit(prev => (prev || '') + t);
    };
    const settlePreview = () => {
      if (previewRaf != null) {
        cancelAnimationFrame(previewRaf);
        previewRaf = null;
      }
      flushPreview();
    };
    try {
      const payload = {
        topic,
        section: active,
        current_content: content[active],
        instructions: editPrompt,
        citation_style: citationStyle,
        mode: autoMode ? 'auto' : 'manual',
        ...(target && {
          target_text: target.text,
          target_start: target.start,
          target_end: target.end,
          target_kind: target.kind,
        }),
      };
      if (!autoMode) {
        payload.provider = selectedModel.provider;
        payload.model = selectedModel.model;
      }
      if (!target) setPendingEdit('');
      const res = await api.raw(`/api/manuscript/edit/stream`, {
        method: 'POST',
        body: JSON.stringify(payload),
        signal: abortControllerRef.current.signal,
      });
      if (res.status === 503) {
        setEditError('AI revision is temporarily unavailable. Please try again in a moment.');
        return;
      }
      if (res.status === 429) {
        setEditError('Rate limit exceeded. Please wait a minute before trying again.');
        return;
      }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const detail = data.detail;
        const message = typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
            ? detail.map(d => d.msg || d).join(' ')
            : 'Failed to apply revision. Please try again.';
        setEditError(message);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      while (true) {
        const { done: readerDone, value } = await reader.read();
        if (readerDone) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.indexOf('\n\n');
        while (boundary !== -1) {
          const chunkStr = buffer.slice(0, boundary).trim();
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf('\n\n');
          if (!chunkStr.startsWith('data: ')) continue;
          const dataStr = chunkStr.slice(6);
          if (dataStr === '[DONE]') continue;
          let data;
          try {
            data = JSON.parse(dataStr);
          } catch (err) {
            console.error('Error parsing edit stream chunk:', err, chunkStr);
            continue;
          }
          if (data.type === 'chunk') {
            if (!target) {
              previewBuf += data.text || '';
              if (previewRaf == null) previewRaf = requestAnimationFrame(flushPreview);
            }
          } else if (data.type === 'metadata') {
            settlePreview();
            setPendingEdit(data.content);
            setPendingEditFlags({
              unverifiedNumbers: data.unverified_numbers || [],
              uncitedClaims: data.uncited_claims || [],
              citationMap: data.citation_map || [],
              unverifiedCitations: Boolean(data.unverified_citations),
              truncated: Boolean(data.truncated),
              diagramErrors: data.diagram_errors || [],
              diagramsDropped: data.diagrams_dropped || 0,
              targetUnresolved: Boolean(data.target_unresolved),
              targetOverrun: Boolean(data.target_overrun),
              scoped: Boolean(target) && !data.target_unresolved,
            });
            setEditPrompt('');
          } else if (data.type === 'stopped' && data.reason === 'error') {
            setEditError(data.message || 'Failed to apply revision. Please try again.');
          }
        }
      }
      settlePreview();
    } catch (e) {
      if (e.name !== 'AbortError') setEditError('Network error. Please try again.');
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
    // The accepted text is now the section, so its verdict replaces the one
    // left over from generation instead of sitting alongside it.
    if (pendingEditFlags) {
      setUnverifiedNumbers(pendingEditFlags.unverifiedNumbers);
      setUnverifiedWarning(
        pendingEditFlags.unverifiedCitations
          ? 'Warning: The revised text contains citations that could not be verified against the provided context. Please verify them independently.'
          : ''
      );
    }
    setPendingEdit(null);
    setPendingEditFlags(null);
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
    setPendingEditFlags(null);
  };

  const buildDraftSnapshot = useCallback(() => ({
    topic: topic.trim(),
    content,
    gap_analysis: gapAnalysis,
    manuscript_refs: manuscriptRefs,
    citation_style: citationStyle,
  }), [topic, content, gapAnalysis, manuscriptRefs, citationStyle]);

  const save = useCallback(async ({ silent } = {}) => {
    if (!topic.trim()) return false;
    const snapshot = buildDraftSnapshot();
    if (!silent) setSaveStatus('saving');
    try {
      const res = await api.raw(`/api/manuscript/save`, {
        method: 'POST',
        body: JSON.stringify(snapshot),
      });
      if (res.ok) {
        const body = await res.json().catch(() => ({}));
        if (body.id) setLoadedDraftId(body.id);
        // Only the payload that actually persisted is clean. In-flight edits stay dirty.
        lastSavedContentRef.current = snapshot;
        setLastSavedAt(Date.now());
        if (!silent) {
          setSaveStatus('saved');
          setTimeout(() => setSaveStatus(''), 3000);
        }
        return true;
      }
      if (!silent) setSaveStatus('error');
      return false;
    } catch {
      if (!silent) setSaveStatus('error');
      return false;
    }
  }, [topic, buildDraftSnapshot, api]);

  // Autosave — skip while streaming to avoid stringify thrash. Gap, refs and
  // citation style are in the dirty snapshot, so changing them schedules a save
  // and a failed request leaves the draft dirty instead of pretending it landed.
  useEffect(() => {
    if (generating) return;
    if (!topic.trim()) return;

    const currentSnapshot = JSON.stringify(buildDraftSnapshot());
    const lastSnapshot = JSON.stringify(lastSavedContentRef.current);
    if (currentSnapshot === lastSnapshot) return;

    const timeoutId = setTimeout(async () => {
      await save();
    }, 5000);

    return () => clearTimeout(timeoutId);
  }, [generating, topic, buildDraftSnapshot, save]);

  useEffect(() => {
    if (!showLoad) return;
    const fetchDrafts = async () => {
      setDraftLoading(true);
      setLoadError('');
      try {
        if (topic.trim()) {
          await save({ silent: true });
        }
        const res = await api.raw(`/api/manuscript/list`);
        const data = await res.json();
        setDrafts(Array.isArray(data.data) ? data.data : []);
      } catch {
        setLoadError('Could not load saved drafts.');
      } finally {
        setDraftLoading(false);
      }
    };
    fetchDrafts();
  }, [api, showLoad]);

  // ─── Version history (1.10) ──────────────────────────────────────────────
  //
  // The list is fetched when the panel opens rather than kept live: it only
  // changes when this tab saves, and a draft being written to every few seconds
  // would otherwise poll for a list nobody is looking at.

  const openHistory = useCallback(async () => {
    if (!topic.trim() && !loadedDraftId) return;
    setShowHistory(true);
    setVersionError('');
    setVersionsLoading(true);
    try {
      // Flush anything unsaved first, so the version the user is about to roll
      // back *from* is itself recoverable.
      await save({ silent: true });
      const params = loadedDraftId ? { draft_id: loadedDraftId } : { topic: topic.trim() };
      const data = await api.get('/api/manuscript/versions', { params });
      setVersions(Array.isArray(data.data) ? data.data : []);
    } catch (e) {
      setVersionError(e?.message || 'Could not load version history.');
      setVersions([]);
    } finally {
      setVersionsLoading(false);
    }
  }, [api, topic, loadedDraftId, save]);

  const restoreVersion = useCallback(async (version) => {
    setRestoringVersion(version.id);
    setVersionError('');
    try {
      const body = await api.post('/api/manuscript/restore', { version_id: version.id });
      const draft = body.data || {};
      const restoredContent = draft.content || {};
      const restoredGap = draft.gap_analysis || null;
      const restoredRefs = draft.manuscript_refs || null;
      const restoredStyle = draft.citation_style || 'ieee';
      setContent(restoredContent);
      setGapAnalysis(restoredGap);
      setManuscriptRefs(restoredRefs);
      setCitationStyle(restoredStyle);
      // The restore is already persisted, so mark the editor clean against it —
      // otherwise autosave would immediately write the restored text back as a
      // brand new version.
      lastSavedContentRef.current = {
        topic: (draft.topic || topic).trim(),
        content: restoredContent,
        gap_analysis: restoredGap,
        manuscript_refs: restoredRefs,
        citation_style: restoredStyle,
      };
      setShowHistory(false);
      setSaveStatus('saved');
      setTimeout(() => setSaveStatus(''), 3000);
    } catch (e) {
      setVersionError(e?.message || 'Could not restore that version.');
    } finally {
      setRestoringVersion('');
    }
  }, [api, topic, setContent, setManuscriptRefs, lastSavedContentRef]);

  const handleNewPaper = () => {
    if (Object.keys(content).length > 0) {
      setShowNewPaperConfirm(true);
      return;
    }
    confirmNewPaper();
  };

  const clearEditor = () => {
    setContent({});
    setTopic('');
    setGapAnalysis(null);
    setManuscriptRefs(null);
    setStreamSources([]);
    setActive('abstract');
    setUnverifiedWarning('');
    setUnverifiedNumbers([]);
    setLoadedDraftId(null);
    lastSavedContentRef.current = {};
    setLastSavedAt(null);
  };

  const confirmNewPaper = () => {
    clearEditor();
    setShowNewPaperConfirm(false);
  };

  const load = async (draft) => {
    const draftId = draft?.id;
    const rawTopic = typeof draft === 'string' ? draft : (draft?.topic || '');
    if (!draftId && rawTopic == null) return;
    setLoadError('');
    try {
      const params = new URLSearchParams();
      if (draftId) params.set('draft_id', draftId);
      else params.set('topic', rawTopic);
      const res = await api.raw(`/api/manuscript/load?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        const loaded = data.data || {};
        const loadedContent = loaded.content || {};
        const loadedTopic = loaded.topic || rawTopic || '';
        const loadedGap = loaded.gap_analysis || null;
        const loadedRefs = loaded.manuscript_refs || null;
        const loadedStyle = loaded.citation_style || 'ieee';
        setContent(loadedContent);
        setTopic(loadedTopic);
        setGapAnalysis(loadedGap);
        setManuscriptRefs(loadedRefs);
        setCitationStyle(loadedStyle);
        setLoadedDraftId(loaded.id || draftId || null);
        lastSavedContentRef.current = {
          topic: (loadedTopic || '').trim(),
          content: loadedContent,
          gap_analysis: loadedGap,
          manuscript_refs: loadedRefs,
          citation_style: loadedStyle,
        };
        setLastSavedAt(loaded.updated_at ? Date.parse(loaded.updated_at) : Date.now());
        setShowLoad(false);
        setDraftFilter('');
      } else { setLoadError('No draft found for this topic.'); }
    } catch { setLoadError('Could not connect.'); }
  };

  const isCurrentDraft = (draft) =>
    Boolean((loadedDraftId && draft?.id && loadedDraftId === draft.id)
      || (topic && draft?.topic && topic === draft.topic));

  /** Confirmed by SavedItemMenu's dialog; a throw keeps the dialog open with it. */
  const deleteDraft = async (draft) => {
    const draftId = draft?.id;
    const rawTopic = draft?.topic || '';
    if (!draftId && rawTopic == null) return;
    setLoadError('');
    const params = new URLSearchParams();
    if (draftId) params.set('draft_id', draftId);
    else params.set('topic', rawTopic);
    let res;
    try {
      res = await api.raw(`/api/manuscript/delete?${params.toString()}`, { method: 'DELETE' });
    } catch {
      throw new Error('Could not reach the server. Try again.');
    }
    // 404: already gone, which is what was asked for.
    if (!res.ok && res.status !== 404) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Could not delete draft.');
    }
    const wasCurrent = isCurrentDraft(draft);
    setDrafts(prev => prev.filter(d => (draftId ? d.id !== draftId : d.topic !== rawTopic)));
    if (wasCurrent) clearEditor();
  };

  /** Pin / rename (ROW-1), applied at once and rolled back if the server refuses. */
  const patchDraft = async (draft, changes) => {
    setLoadError('');
    const before = drafts;
    const apply = (patch) => setDrafts(prev =>
      orderPinned(prev.map(d => (d.id === draft.id ? { ...d, ...patch } : d))));
    apply(changes);
    try {
      const data = await api.patch(`/api/manuscript/drafts/${draft.id}`, changes);
      apply({ pinned: data.pinned, title: data.title });
    } catch {
      setDrafts(before);
      setLoadError('title' in changes ? 'That draft could not be renamed.' : 'That draft could not be pinned.');
    }
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

  const [latexVenue, setLatexVenue] = useState('ieee');
  const [latexExporting, setLatexExporting] = useState(false);
  const [latexError, setLatexError] = useState('');

  const exportLatex = async (venue = latexVenue) => {
    if (!topic.trim() || !Object.keys(content).length) return;
    setLatexVenue(venue);
    setLatexExporting(true);
    setLatexError('');
    try {
      // The server exports the *saved* draft, looked up by topic, so unsaved
      // edits (autosave waits 5s) used to be missing from the zip (ME-3, B2).
      const saved = await save({ silent: true });
      if (!saved) throw new Error('Could not save the draft before exporting. Try again.');
      const res = await api.raw(
        `/api/manuscript/export-latex`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ topic: topic.trim(), venue }),
        }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || 'Export failed.');
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${topic.replace(/\s+/g, '-')}-${venue}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setLatexError(e.message || 'Export failed.');
    } finally {
      setLatexExporting(false);
    }
  };

  useEffect(() => {
  if (printPending && viewMode === 'paper') {
    let attempts = 0;
    const waitForCharts = () => {
      // Recharts figures were never waited on, so window.print() could fire
      // while a .table-chart-container was still unpainted (audit C16).
      const nodes = document.querySelectorAll(
        '.paper-preview-print .mermaid-chart, .paper-preview-print .table-chart-container'
      );
      const allReady = Array.from(nodes).every(n => n.querySelector('svg'));
      // Never block the print dialog forever on a chart that will not render —
      // ~10s, then print what we have rather than leaving the button dead.
      if (allReady || ++attempts > 100) {
        // Scroll containers clip off-screen SVG when printing — reset scroll origin first
        document.querySelectorAll('.paper-preview-print .mermaid-chart-scroll').forEach((el) => {
          el.scrollTop = 0;
          el.scrollLeft = 0;
        });
        window.print();
        setPrintPending(false);
      } else {
        setTimeout(waitForCharts, 100);
      }
    };
    requestAnimationFrame(() => setTimeout(waitForCharts, 50));
  }
}, [printPending, viewMode]);

  const currentStep = STEPS.find(s => s.id === active);
  // A rename (ROW-1) is display-only; the topic stays the draft's key.
  const draftTitle = (draft) => displayName(draft, 'topic') || 'Untitled draft';
  const visibleDrafts = drafts.filter(draft =>
    draftTitle(draft).toLowerCase().includes(draftFilter.trim().toLowerCase())
  );
  const draftGroups = splitPinned(visibleDrafts);

  const refsCount = Object.keys(manuscriptRefs || {}).length;
  const gapsCount = gapAnalysis && gapAnalysis.status !== 'insufficient_literature'
    ? (gapAnalysis.gaps?.length || 0) : 0;
  const verifyCount = unverifiedNumbers.length + (unverifiedWarning ? 1 : 0);
  const hasContent = Object.values(content).some(t => (t || '').trim());
  const toolsAsPanel = toolsOpen && toolsDocked && canDock;
  const saveLabel = saveStatus === 'saving' ? 'Saving…'
    : saveStatus === 'error' ? 'Save failed'
      : lastSavedAt ? `Saved ${savedAgo(lastSavedAt, nowTick)}`
        : topic.trim() ? 'Not saved yet' : '';

  const openTools = (tab) => {
    setToolsTab(tab);
    setToolsOpen(true);
    writePref('ms-tools-open', true);
  };
  const closeTools = () => {
    setToolsOpen(false);
    writePref('ms-tools-open', false);
  };
  const toggleOutline = () => {
    setOutlineOpen(o => { writePref('ms-outline-open', !o); return !o; });
  };
  const toggleDock = () => {
    setToolsDocked(d => { writePref('ms-tools-docked', !d); return !d; });
  };

  const toolsBody = (
    <Tabs value={toolsTab} onValueChange={setToolsTab} className="manuscript-tools-tabs">
      <TabsList variant="line" className="manuscript-tools-tablist">
        <TabsTrigger value="revise">Revise</TabsTrigger>
        <TabsTrigger value="gaps">
          Gaps{gapsCount > 0 && <span className="manuscript-tools-count">{gapsCount}</span>}
        </TabsTrigger>
        <TabsTrigger value="sources">Sources</TabsTrigger>
        <TabsTrigger value="references">
          References{refsCount > 0 && <span className="manuscript-tools-count">{refsCount}</span>}
        </TabsTrigger>
      </TabsList>

      <TabsContent value="revise" className="manuscript-tools-pane">
        <p className="manuscript-tools-lede">
          Rewrite <strong>{currentStep?.label}</strong> with AI. You review every change before it is applied.
        </p>
        {/* Revision scope. Choosing anything but "Whole section" means the
            server rewrites only that span and splices the reply back, so the
            rest of the section is copied rather than regenerated. */}
        <div className="revise-scope" role="group" aria-labelledby="revise-scope-label">
          <span className="revise-scope-label" id="revise-scope-label">Revise:</span>
          <button
            type="button"
            aria-pressed={!editScope}
            className={`revise-scope-chip${editScope ? '' : ' active'}`}
            onClick={() => setEditScope(null)}
            disabled={editing || generating}
          >
            Whole section
          </button>
          <button
            type="button"
            aria-pressed={editScope?.kind === 'selection'}
            className={`revise-scope-chip${editScope?.kind === 'selection' ? ' active' : ''}`}
            onClick={() => setEditScope({ kind: 'selection' })}
            disabled={editing || generating || !editorSelection}
            title={
              editorSelection
                ? `“${editorSelection.text.slice(0, 80)}${editorSelection.text.length > 80 ? '…' : ''}”`
                : 'Select text in the editor first'
            }
          >
            Selected text
          </button>
          {diagramBlocks.map((block, i) => (
            <button
              key={i}
              type="button"
              aria-pressed={editScope?.kind === 'diagram' && editScope.index === i}
              className={`revise-scope-chip${editScope?.kind === 'diagram' && editScope.index === i ? ' active' : ''}`}
              onClick={() => setEditScope({ kind: 'diagram', index: i })}
              disabled={editing || generating}
              title={block.body.slice(0, 120)}
            >
              {diagramBlocks.length > 1 ? `Diagram ${i + 1}` : 'Diagram'}
            </button>
          ))}
        </div>
        <p className="revise-scope-hint">
          {editScope
            ? 'Only the targeted text is sent for rewriting — everything else is preserved exactly as written.'
            : 'The whole section is rewritten. Target a selection or a diagram to leave the rest untouched.'}
        </p>
        <div className="manuscript-revise-row">
          <textarea
            rows={3}
            placeholder={
              editScope?.kind === 'diagram'
                ? 'e.g. Fix the axis labels, use the numbers from [2]...'
                : 'e.g. Make this shorter, add bullet points, fix grammar...'
            }
            aria-label="Revision instructions"
            value={editPrompt}
            onChange={e => setEditPrompt(e.target.value)}
            disabled={editing || generating || !content[active]}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); applyEdit(); } }}
          />
          <Button onClick={applyEdit} disabled={editing || generating || !editPrompt.trim() || !content[active]}>
            {editing ? <Spinner size={14} /> : <Send size={14} aria-hidden="true" />} Apply revision
          </Button>
        </div>
        {!content[active] && (
          <p className="manuscript-tools-empty">Write or generate this section first, then revise it here.</p>
        )}
        {editError && <div className="manuscript-inline-error" role="alert">{editError}</div>}
      </TabsContent>

      <TabsContent value="gaps" className="manuscript-tools-pane">
        {!gapAnalysis ? (
          <div className="manuscript-tools-empty">
            <p>Where the literature agrees, conflicts and leaves gaps. It is produced when you generate the Literature Review.</p>
            {active !== 'lit_review' && (
              <Button variant="outline" size="sm" onClick={() => setActive('lit_review')}>Go to Literature Review</Button>
            )}
          </div>
        ) : gapAnalysis.status === 'insufficient_literature' ? (
          <p className="manuscript-tools-empty">{gapAnalysis.message}</p>
        ) : (
          <div className="manuscript-gaps">
            <p className="manuscript-tools-lede">From the Literature Review generation.</p>
            {[
              ['Consensus', gapAnalysis.consensus, (item) => (
                <>{item.claim} <span className="manuscript-gap-refs">[{item.supporting_papers?.join(', ')}]</span></>
              )],
              ['Conflicts', gapAnalysis.conflicts, (item) => (
                <>
                  {item.claim_a} <strong>vs</strong> {item.claim_b} <span className="manuscript-gap-refs">[{item.papers?.join(', ')}]</span>
                  {item.note && <span className="manuscript-gap-note">{item.note}</span>}
                </>
              )],
              ['Gaps', gapAnalysis.gaps, (item) => (
                <>{item.description} <span className="manuscript-gap-refs">[{item.informed_by?.join(', ')}]</span></>
              )],
            ].filter(([, items]) => items && items.length > 0).map(([title, items, render]) => (
              <section key={title} className="manuscript-gap-group">
                <h3>{title} <span className="manuscript-tools-count">{items.length}</span></h3>
                <ul>{items.map((item, i) => <li key={i}>{render(item)}</li>)}</ul>
              </section>
            ))}
            {gapAnalysis.suggested_direction && (
              <section className="manuscript-gap-direction">
                <h3>Suggested direction</h3>
                <p>{gapAnalysis.suggested_direction}</p>
                {gapAnalysis.vagueness_warning && (
                  <p className="manuscript-gap-warning">{gapAnalysis.vagueness_warning}</p>
                )}
                <Button
                  size="sm"
                  disabled={generating}
                  onClick={() => {
                    const direction = gapAnalysis.suggested_direction;
                    setTopic(direction);
                    generate({ topic: direction });
                  }}
                >
                  Use this direction
                </Button>
              </section>
            )}
          </div>
        )}
      </TabsContent>

      <TabsContent value="sources" className="manuscript-tools-pane">
        <section className="manuscript-sources-used">
          <h3>Cited in the last generation</h3>
          {!sourcesResolved ? (
            <p className="manuscript-tools-empty">Sources appear here after you generate a section.</p>
          ) : streamSources.length === 0 ? (
            <p className="manuscript-tools-empty">No specific sources were cited; it was written from general context.</p>
          ) : (
            <ol className="manuscript-source-list">
              {streamSources.map((src) => (
                <li key={src.index}>
                  <span className="manuscript-source-index">{src.index}</span>
                  <span className="manuscript-source-main">
                    <span className="manuscript-source-title">{src.title}</span>
                    <span className="manuscript-source-meta">{src.authors}{src.year ? ` (${src.year})` : ''}</span>
                  </span>
                  {src.url && (
                    <a href={src.url} target="_blank" rel="noopener noreferrer" aria-label={`Open source ${src.index}`}>
                      <ExternalLink size={13} aria-hidden="true" />
                    </a>
                  )}
                </li>
              ))}
            </ol>
          )}
        </section>
        <section className="manuscript-ground-truth">
          <SourcesPanel topic={topic} />
        </section>
      </TabsContent>

      <TabsContent value="references" className="manuscript-tools-pane">
        {refsCount === 0 ? (
          <p className="manuscript-tools-empty">References collect here as generated sections cite sources.</p>
        ) : (
          <ol className="manuscript-ref-list">
            {Object.entries(manuscriptRefs).map(([idx, refString]) => (
              <li key={idx} value={Number(idx) || undefined}>{refString}</li>
            ))}
          </ol>
        )}
      </TabsContent>
    </Tabs>
  );

  return (
    <div className="manuscript-page animate-fade-in">
      <TopBar crumbs={[{ label: 'Write' }, { label: 'Manuscript' }]} context={topic.trim() || undefined} />
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{announcement}</div>
      <h1 className="sr-only">Manuscript Builder</h1>

      {/* Command bar: draft actions and save state on the left; the evidence
          summary, settings, export, the tools panel and Generate on the right.
          The evidence counts are always visible, so nothing the paper rests on
          is hidden behind the on-demand panel. */}
      <div className="manuscript-commandbar">
        <div className="manuscript-commandbar-group">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={toggleOutline}
            aria-label={outlineOpen ? 'Hide outline' : 'Show outline'}
            aria-expanded={outlineOpen}
            aria-controls="manuscript-outline"
            title={outlineOpen ? 'Hide outline' : 'Show outline'}
          >
            <PanelLeft aria-hidden="true" />
          </Button>
          <Button variant="ghost" size="sm" onClick={handleNewPaper}>
            <Plus aria-hidden="true" /> New
          </Button>
          <Button variant="ghost" size="sm" onClick={() => { setShowLoad(true); setLoadError(''); setDraftFilter(''); setRenamingDraftId(null); }}>
            <FolderOpen aria-hidden="true" /> Open
          </Button>
          <Button variant="ghost" size="sm" onClick={openHistory} disabled={!topic.trim() && !loadedDraftId} title="Earlier versions of this draft">
            <History aria-hidden="true" /> History
          </Button>
          <span className="manuscript-commandbar-sep" aria-hidden="true" />
          {saveLabel && (
            <span className={`manuscript-save-state${saveStatus === 'error' ? ' is-error' : ''}`}>{saveLabel}</span>
          )}
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => { void save(); }}
            disabled={!topic.trim() || saveStatus === 'saving'}
            aria-label="Save now"
            title="Save now (drafts also save automatically)"
          >
            <Save aria-hidden="true" />
          </Button>
        </div>

        <div className="manuscript-commandbar-group">
          <div className="manuscript-evidence" role="group" aria-label="Evidence summary">
            <button type="button" className="manuscript-evidence-item" onClick={() => openTools('references')}>
              <BookOpen size={13} aria-hidden="true" /> {refsCount} {refsCount === 1 ? 'reference' : 'references'}
            </button>
            <button type="button" className="manuscript-evidence-item" onClick={() => openTools('gaps')}>
              <Search size={13} aria-hidden="true" /> {gapAnalysis ? `${gapsCount} ${gapsCount === 1 ? 'gap' : 'gaps'}` : 'No gap analysis'}
            </button>
            {verifyCount > 0 && (
              <button type="button" className="manuscript-evidence-item is-warn" onClick={() => document.getElementById('manuscript-verify')?.scrollIntoView({ block: 'center' })}>
                <AlertTriangle size={13} aria-hidden="true" /> {verifyCount} to verify
              </button>
            )}
          </div>

          <Popover>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                aria-label={`Paper settings: ${citationStyle.toUpperCase()} citations, ${autoMode ? 'automatic' : 'specific'} model`}
                title={`${citationStyle.toUpperCase()} citations · ${autoMode ? 'Auto' : 'Specific'} model`}
              >
                <Settings2 aria-hidden="true" /> {citationStyle.toUpperCase()}
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="manuscript-settings">
              <div className="manuscript-settings-field">
                <span id="manuscript-citation-label">Citation style</span>
                <Select value={citationStyle} onValueChange={setCitationStyle}>
                  <SelectTrigger aria-labelledby="manuscript-citation-label" className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CITATION_STYLES.map(([id, label]) => <SelectItem key={id} value={id}>{label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="manuscript-settings-field">
                <span id="manuscript-model-label">Generation model</span>
                <div className="manuscript-segmented" role="group" aria-labelledby="manuscript-model-label">
                  <button type="button" aria-pressed={autoMode} className={autoMode ? 'is-on' : ''} onClick={() => setAutoMode(true)}>Auto</button>
                  <button type="button" aria-pressed={!autoMode} className={!autoMode ? 'is-on' : ''} onClick={() => setAutoMode(false)}>Specific</button>
                </div>
                {!autoMode && (
                  <Select value={selectedModelId} onValueChange={setSelectedModelId}>
                    <SelectTrigger aria-label="Specific model" className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {Array.from(new Set(MODELS.map(m => m.group))).map(group => (
                        <SelectGroup key={group}>
                          <SelectLabel>{group}</SelectLabel>
                          {MODELS.filter(m => m.group === group).map(m => (
                            <SelectItem key={m.id} value={m.id}>{m.label}</SelectItem>
                          ))}
                        </SelectGroup>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </div>
            </PopoverContent>
          </Popover>

          <DropdownMenu modal={false}>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" disabled={!hasContent || latexExporting}>
                <Download aria-hidden="true" /> {latexExporting ? 'Exporting…' : 'Export'}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-auto min-w-44">
              <DropdownMenuItem onSelect={exportMarkdown}><FileText /> Markdown (.md)</DropdownMenuItem>
              <DropdownMenuSub>
                <DropdownMenuSubTrigger><FileText /> LaTeX (.zip)</DropdownMenuSubTrigger>
                <DropdownMenuSubContent>
                  {LATEX_VENUES.map(([id, label]) => (
                    <DropdownMenuItem key={id} onSelect={() => exportLatex(id)}>{label} template</DropdownMenuItem>
                  ))}
                </DropdownMenuSubContent>
              </DropdownMenuSub>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={exportPDF}><Printer /> Print or save as PDF</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <Button
            variant={toolsOpen ? 'secondary' : 'outline'}
            size="sm"
            onClick={() => (toolsOpen ? closeTools() : openTools(toolsTab))}
            aria-pressed={toolsOpen}
            aria-controls="manuscript-tools"
          >
            <PanelRight aria-hidden="true" /> AI &amp; evidence
          </Button>

          {generating ? (
            <Button variant="destructive" size="sm" onClick={stopGeneration}>
              <Spinner size={14} /> Stop
            </Button>
          ) : (
            <Button size="sm" onClick={() => generate({ continueSection: sectionTruncated })} disabled={!topic.trim() || rateLimitWait > 0}>
              <Sparkles aria-hidden="true" /> {rateLimitWait ? `Wait ${rateLimitWait}s` : sectionTruncated ? 'Continue' : 'Generate'}
            </Button>
          )}
        </div>
      </div>

      {latexError && <div className="manuscript-banner is-error" role="alert">{latexError}</div>}

      <div className={`manuscript-workspace${outlineOpen ? '' : ' is-outline-hidden'}${toolsAsPanel ? ' has-tools' : ''}`}>
        {outlineOpen && (
          <aside id="manuscript-outline" className="manuscript-outline" aria-label="Paper outline">
            <div className="manuscript-topic-field">
              <label htmlFor="manuscript-topic">Research topic</label>
              <textarea
                id="manuscript-topic"
                rows={3}
                placeholder="What is the paper about?"
                value={topic}
                onChange={e => { topicTypedRef.current = true; setTopic(e.target.value); }}
              />
              {!generating && researchReady && topic.trim() && (
                <span className="manuscript-research-ready" title="The literature for this topic is already searched, screened and read, so generating starts immediately.">
                  <CheckCircle size={12} aria-hidden="true" /> Research ready
                </span>
              )}
            </div>
            <SectionsList
              sections={STEPS}
              activeSectionId={active}
              onSelectSection={setActive}
              doneIds={done}
              generating={generating}
            />
          </aside>
        )}

        <main className="manuscript-document" aria-labelledby="manuscript-section-title">
          {/* SD-5b: title, mode switch and counts in one fixed row, so switching
              Write / Preview / Paper never moves anything. */}
          <div className="manuscript-doc-head">
            <h2 id="manuscript-section-title">{currentStep?.label}</h2>
            <div className="manuscript-mode-tabs" role="tablist" aria-label="Editor modes">
              {[
                { id: 'write', label: 'Write' },
                { id: 'preview', label: 'Preview' },
                { id: 'paper', label: 'Paper' },
              ].map((mode) => (
                <button
                  key={mode.id}
                  type="button"
                  role="tab"
                  id={`manuscript-tab-${mode.id}`}
                  aria-controls="manuscript-mode-panel"
                  data-mode={mode.id}
                  aria-selected={viewMode === mode.id}
                  className={`manuscript-mode-tab${viewMode === mode.id ? ' is-active' : ''}`}
                  onClick={() => setViewMode(mode.id)}
                >
                  {mode.label}
                </button>
              ))}
            </div>
            <div className="manuscript-word-count" title="This section · whole paper">
              {sectionWords} words
              <span className="manuscript-word-count-total"> · {paperWords} in paper</span>
            </div>
            {autoStatus && <span className="manuscript-auto-status">{autoStatus}</span>}
          </div>

          {/* Research pipeline stages (1.5): what Generate is doing before the
              first token, so the wait reads as work, not a hang. */}
          {researchStages.length > 0 && !sourcesResolved && (
            <div className="manuscript-research-stages">
              <div className="manuscript-research-stages-title">
                <Search size={12} aria-hidden="true" /> Preparing research
              </div>
              {researchStages.map((st) => (
                <div key={st.stage} className={`manuscript-research-stage is-${st.status}`}>
                  {st.status === 'running' ? <Spinner size={12} /> : <CheckCircle size={12} aria-hidden="true" />}
                  <span>{st.detail || st.stage}</span>
                </div>
              ))}
            </div>
          )}

          {generateError && (
            <div className="manuscript-banner is-error" role="alert">
              <AlertTriangle size={15} aria-hidden="true" /> {generateError}
            </div>
          )}
          {sectionTruncated && !generating && !generateError && (
            <div className="manuscript-banner is-warn">
              This section was cut off mid-sentence. Press Continue to keep writing from here.
            </div>
          )}
          {/* Verification checks sit in the document, next to the text they
              are about — they used to float bottom-right over the AI button. */}
          {verifyCount > 0 && !generating && (
            <div id="manuscript-verify" className="manuscript-banner is-warn manuscript-verify">
              <AlertTriangle size={15} aria-hidden="true" />
              <div className="manuscript-verify-body">
                <strong>
                  {unverifiedNumbers.length > 0
                    ? `${unverifiedNumbers.length} stat${unverifiedNumbers.length !== 1 ? 's' : ''} may need verification`
                    : 'Citations may need verification'}
                </strong>
                <span>
                  {unverifiedNumbers.length > 0
                    ? `Not found in source papers${unverifiedWarning ? '; some citations could not be verified either' : ''}.`
                    : 'Some citations could not be matched to the provided sources. Check them independently.'}
                </span>
                {unverifiedNumbers.length > 0 && (
                  <span className="manuscript-verify-values">
                    {unverifiedNumbers.map((num, i) => <code key={i}>{num}</code>)}
                  </span>
                )}
              </div>
              <Button variant="ghost" size="sm" onClick={() => { setUnverifiedWarning(''); setUnverifiedNumbers([]); }}>
                Dismiss
              </Button>
            </div>
          )}

          {generating ? (
            <div className="manuscript-generating" aria-busy="true">
              <p className="manuscript-generating-title"><Spinner size={14} /> Writing {currentStep?.label}…</p>
              <SkeletonText lines={12} />
            </div>
          ) : pendingEdit ? (
            <div className="manuscript-diff-view">
              <div className="manuscript-diff-head">
                <h3>Review AI revisions</h3>
                <div className="manuscript-diff-actions">
                  <Button variant="ghost" size="sm" onClick={rejectEdit}>Discard</Button>
                  <Button size="sm" onClick={acceptEdit}><CheckCircle aria-hidden="true" /> Accept changes</Button>
                </div>
              </div>
              <RevisionChecks flags={pendingEditFlags} />
              <div className="manuscript-diff-content">
                {diffWords(content[active] || '', pendingEdit).map((part, i) => (
                  part.added ? <ins key={i}>{part.value}</ins> :
                  part.removed ? <del key={i}>{part.value}</del> :
                  <span key={i}>{part.value}</span>
                ))}
              </div>
            </div>
          ) : (
            <div className={`manuscript-editor-surface is-${viewMode}`} key={active}>
              <div className="manuscript-toolbar">

                {viewMode === 'preview' && (
                  <p className="manuscript-bar-hint">Preview — how headings, maths, tables and charts will render.</p>
                )}
                {viewMode === 'paper' && (
                  <p className="manuscript-bar-hint">
                    {citationStyle.toUpperCase()} layout of this section. For the whole paper use Export › Print or save as PDF.
                  </p>
                )}
                {viewMode === 'write' && (
                  // ME-1 (E2, E4, E12): a real toolbar. Every button is named,
                  // acts on click so Enter/Space work, and only uses mousedown to
                  // keep the editor's selection from moving to the button.
                  <div
                    className="manuscript-format-toolbar"
                    role="toolbar"
                    aria-label="Formatting"
                    aria-controls="manuscript-textarea"
                  >
                    {FORMAT_GROUPS.map((group, g) => (
                      <div className="format-group" key={g}>
                        {group.map(([label, Icon, prefix, suffix = '']) => (
                          <button
                            key={label}
                            type="button"
                            className="format-btn"
                            title={label}
                            aria-label={label}
                            onMouseDown={keepEditorSelection}
                            onClick={() => insertMarkdown(prefix, suffix)}
                          >
                            <Icon size={15} aria-hidden="true" />
                          </button>
                        ))}
                      </div>
                    ))}
                    <div className="format-group">
                      <button type="button" className="format-btn" title="Undo" aria-label="Undo" onMouseDown={keepEditorSelection} onClick={handleUndo}>
                        <Undo size={15} aria-hidden="true" />
                      </button>
                      <button type="button" className="format-btn" title="Redo" aria-label="Redo" onMouseDown={keepEditorSelection} onClick={handleRedo}>
                        <Redo size={15} aria-hidden="true" />
                      </button>
                    </div>
                  </div>
                )}
              </div>
              
              {/* E8: the panel the mode tabs control. display: contents keeps
                  the existing layout rules, which target its children. */}
              <div
                id="manuscript-mode-panel"
                role="tabpanel"
                aria-labelledby={`manuscript-tab-${viewMode}`}
                className="manuscript-mode-panel"
              >
              {viewMode === 'write' ? (
                <textarea
                  id="manuscript-textarea"
                  aria-label={`${currentStep?.label} text`}
                  spellCheck
                  lang="en"
                  placeholder={`Write your ${currentStep?.label.toLowerCase()} here, or click Generate for AI assistance...\nMaths: $...$ inline, $$...$$ on its own line.`}
                  value={content[active] || ''}
                  onChange={e => setContent(prev => ({ ...prev, [active]: e.target.value }))}
                  // Captured so "AI Revise" can scope to it — clicking into the
                  // revise input drops the browser selection, so it is held here.
                  onSelect={e => {
                    const { selectionStart: from, selectionEnd: to, value } = e.target;
                    setEditorSelection(to > from ? { start: from, end: to, text: value.slice(from, to) } : null);
                  }}
                  disabled={generating}
                />
              ) : viewMode === 'preview' ? (
                <div className="pdf-markdown-body">
                  {content[active] ? (
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm, remarkMath]}
                      rehypePlugins={[[rehypeKatex, KATEX_REHYPE_OPTIONS]]}
                      components={{
                        table: TableOrChart,
                        a: ({ node, href, children, ...props }) => {
                          if (href === '#unverified-stat') {
                            return <span className="unverified-stat" title="Not found in source papers">{children}</span>;
                          }
                          return <a href={href} {...props}>{children}</a>;
                        },
                        code: MarkdownCode
                      }}
                    >
                      {normalizeLatexDelimiters(processForUnverified(content[active], currentStep?.label))}
                    </ReactMarkdown>
                  ) : (
                    <p className="manuscript-empty-note">Nothing to preview yet.</p>
                  )}
                </div>
              ) : viewMode === 'paper' ? (
                /* ─── Paper Preview Mode ─── */
                <>
                  {/* Screen Version: Only Active Section */}
                  <div className={`paper-preview format-${citationStyle} paper-preview-screen`}>
                    <div className="paper-header">
                      <div className="paper-section-label">{currentStep?.label}</div>
                       <h2 className="paper-title" title={topic}>{formatPaperTitle(topic)}</h2>
                    </div>
                    <div className="paper-body">
                      {content[active] ? (
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm, remarkMath]}
                          rehypePlugins={[[rehypeKatex, KATEX_REHYPE_OPTIONS]]}
                          components={{
                            table: TableOrChart,
                            a: ({ node, href, children, ...props }) => {
                              if (href === '#unverified-stat') {
                                return <span className="unverified-stat" title="Not found in source papers">{children}</span>;
                              }
                              return <a href={href} {...props}>{children}</a>;
                            },
                            code: MarkdownCode
                          }}
                        >
                          {normalizeLatexDelimiters(processForUnverified(content[active], currentStep?.label))}
                        </ReactMarkdown>
                      ) : (
                        <p className="manuscript-empty-note">No content to preview for this section.</p>
                      )}
                    </div>
                  </div>

                  {/* Print Version: Full Paper (Hidden on screen, visible when printing) */}
                  <div className={`paper-preview format-${citationStyle} paper-preview-print`}>
                    <div className="paper-header">
                      <h1 className="paper-title" title={topic}>{formatPaperTitle(topic)}</h1>
                    </div>
                    <div className="paper-body">
                      {STEPS.map(step => content[step.id] ? (
                        <div key={step.id} className="paper-section">
                          {step.id !== 'abstract' && (
                            <h2 className="paper-section-title">{step.label}</h2>
                          )}
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm, remarkMath]}
                            rehypePlugins={[[rehypeKatex, KATEX_REHYPE_OPTIONS]]}
                            components={{
                              table: TableOrChart,
                              a: ({ node, href, children, ...props }) => {
                                if (href === '#unverified-stat') {
                                  return <span className="unverified-stat" title="Not found in source papers">{children}</span>;
                                }
                                return <a href={href} {...props}>{children}</a>;
                              },
                              code: MarkdownCode
                            }}
                          >
                            {normalizeLatexDelimiters(processForUnverified(content[step.id], step.label))}
                          </ReactMarkdown>
                        </div>
                      ) : null)}
                    </div>
                  </div>
                </>
              ) : null}
              </div>

            </div>
          )}

          {content[active] && !generating && !pendingEdit && (
            <div className="manuscript-doc-footer">
              {editHistory[active] && editHistory[active].length > 0 && (
                <Button variant="ghost" size="sm" onClick={undoLastEdit}>
                  <Undo aria-hidden="true" /> Undo AI edit
                </Button>
              )}
              <Button variant="outline" size="sm" onClick={() => openTools('revise')}>
                <Sparkles aria-hidden="true" /> Revise with AI
              </Button>
            </div>
          )}
        </main>

        {toolsAsPanel && (
          <aside id="manuscript-tools" className="manuscript-tools" aria-label="AI and evidence">
            <div className="manuscript-tools-head">
              <h2>AI &amp; evidence</h2>
              <Button variant="ghost" size="icon-sm" onClick={toggleDock} aria-label="Float the panel over the document" title="Float the panel">
                <PinIcon aria-hidden="true" />
              </Button>
              <Button variant="ghost" size="icon-sm" onClick={closeTools} aria-label="Close AI and evidence panel" title="Close">
                <X aria-hidden="true" />
              </Button>
            </div>
            {toolsBody}
          </aside>
        )}
      </div>

      {/* Narrow screens, or when the panel is undocked: the same tools as a sheet. */}
      <Sheet open={toolsOpen && !toolsAsPanel} onOpenChange={(open) => (open ? openTools(toolsTab) : closeTools())}>
        <SheetContent side="right" className="manuscript-tools-sheet">
          <SheetHeader className="manuscript-tools-head">
            <SheetTitle>AI &amp; evidence</SheetTitle>
            <SheetDescription className="sr-only">Revise the section, read the gap analysis, sources and references.</SheetDescription>
            {canDock && (
              <Button variant="ghost" size="sm" onClick={() => { toggleDock(); }}>
                <PinIcon aria-hidden="true" /> Dock beside the document
              </Button>
            )}
          </SheetHeader>
          <div id={toolsAsPanel ? undefined : 'manuscript-tools'} className="manuscript-tools-sheet-body">{toolsBody}</div>
        </SheetContent>
      </Sheet>

      {/* New paper: the shared confirm, not a hand-built overlay. */}
      <AlertDialog open={showNewPaperConfirm} onOpenChange={setShowNewPaperConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Start a new paper?</AlertDialogTitle>
            <AlertDialogDescription>
              The editor will be cleared. Saved drafts stay in Open; changes since the last save are lost.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep editing</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmNewPaper}>Start new paper</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Version history (1.10) */}
      <Dialog open={showHistory} onOpenChange={(open) => { if (!open && !restoringVersion) setShowHistory(false); }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Version history</DialogTitle>
            <DialogDescription>
              Each entry is what this draft looked like before a save. Restoring one keeps your current
              text as a new entry, so a restore can be undone too.
            </DialogDescription>
          </DialogHeader>
          {versionError && <div className="manuscript-draft-error" role="alert">{versionError}</div>}
          <div className="manuscript-draft-list">
            {versionsLoading && <div className="manuscript-draft-meta">Loading history...</div>}
            {!versionsLoading && versions.length === 0 && !versionError && (
              <p className="manuscript-empty-note">No earlier versions yet — one is kept every time a save replaces existing text.</p>
            )}
            {!versionsLoading && versions.map((version) => {
              const summary = version.summary || {};
              const when = version.created_at ? new Date(version.created_at) : null;
              const sectionCount = (summary.sections || []).length;
              return (
                <div key={version.id} className="manuscript-draft-row">
                  <div className="manuscript-draft-main is-static">
                    <span className="manuscript-draft-title">
                      {when ? when.toLocaleString() : 'Earlier version'}
                      {version.reason === 'restore' && ' · before a restore'}
                    </span>
                    <span className="manuscript-draft-meta">
                      {sectionCount} section{sectionCount === 1 ? '' : 's'}
                      {typeof summary.total_chars === 'number' && ` · ${summary.total_chars.toLocaleString()} characters`}
                    </span>
                  </div>
                  <Button variant="outline" size="sm" disabled={Boolean(restoringVersion)} onClick={() => restoreVersion(version)}>
                    {restoringVersion === version.id ? <Spinner size={14} /> : <><RotateCcw aria-hidden="true" /> Restore</>}
                  </Button>
                </div>
              );
            })}
          </div>
        </DialogContent>
      </Dialog>

      {/* Load modal */}
      {/* Load draft (ROW-2): a shadcn Dialog, so the row menu and the delete
          confirm stack above it — the old z-index 1000 overlay hid them. */}
      <Dialog open={showLoad} onOpenChange={(open) => { if (!open) setShowLoad(false); }}>
        <DialogContent
          className="sm:max-w-lg"
          onEscapeKeyDown={(e) => { if (renamingDraftId) e.preventDefault(); }}
        >
          <DialogHeader>
            <DialogTitle>Open draft</DialogTitle>
            <DialogDescription>Choose one of your saved manuscript drafts.</DialogDescription>
          </DialogHeader>
          <div className="manuscript-draft-filter">
            <Search size={15} />
            <input aria-label="Filter saved drafts" placeholder="Filter saved drafts..." value={draftFilter} onChange={e => setDraftFilter(e.target.value)} />
          </div>
          {loadError && <div className="manuscript-draft-error" role="alert">{loadError}</div>}
          <div className="manuscript-draft-list">
            {draftLoading && <div className="manuscript-draft-meta">Loading drafts...</div>}
            {!draftLoading && visibleDrafts.length === 0 && (
              <div className="empty-state" style={{ padding: 'var(--space-4)', fontSize: 'var(--fs-sm)' }}>
                {drafts.length ? 'No drafts match your filter.' : 'No saved drafts yet.'}
              </div>
            )}
            {!draftLoading && [['Pinned', draftGroups.pinned], ['Drafts', draftGroups.rest]]
              .filter(([, rows]) => rows.length > 0)
              .map(([label, rows]) => (
                <section key={label} className="manuscript-draft-section" aria-label={label}>
                  {draftGroups.pinned.length > 0 && <h4 className="manuscript-draft-group">{label}</h4>}
                  {rows.map((draft) => {
                    const name = draftTitle(draft);
                    return (
                      <div key={draft.id || draft.topic} className="manuscript-draft-row group">
                        {renamingDraftId && renamingDraftId === draft.id ? (
                          <RenameField
                            className="manuscript-draft-rename"
                            initial={name}
                            label={`Rename ${name}`}
                            onSubmit={(value) => { setRenamingDraftId(null); patchDraft(draft, { title: value }); }}
                            onCancel={() => setRenamingDraftId(null)}
                          />
                        ) : (
                          <button
                            type="button"
                            className="manuscript-draft-main"
                            onClick={() => load(draft)}
                            title={draft.title ? `${name} (${draft.topic})` : name}
                          >
                            <span className="manuscript-draft-title">
                              {draft.pinned && <Pin size={12} aria-label="Pinned" />} {name}
                            </span>
                            {draft.updated_at && (
                              <span className="manuscript-draft-meta">
                                Updated {new Date(draft.updated_at).toLocaleDateString()}
                              </span>
                            )}
                          </button>
                        )}
                        <SavedItemMenu
                          name={name}
                          kind="draft"
                          pinned={draft.pinned === true}
                          onTogglePin={draft.id ? () => patchDraft(draft, { pinned: !draft.pinned }) : undefined}
                          onRename={draft.id ? () => setRenamingDraftId(draft.id) : undefined}
                          onDelete={() => deleteDraft(draft)}
                          deleteDetail={isCurrentDraft(draft)
                            ? 'and its version history will be removed, and the editor will be cleared because this is the paper you are editing.'
                            : 'and its version history will be removed.'}
                        />
                      </div>
                    );
                  })}
                </section>
              ))}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
