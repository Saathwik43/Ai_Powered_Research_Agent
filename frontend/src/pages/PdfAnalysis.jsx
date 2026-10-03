import React, { useState, useRef, useEffect, useMemo, useCallback, memo } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  FileText, Send, AlertCircle, Plus, Pin,
  ChevronLeft, Bot, User, Paperclip, X, RotateCw,
  CheckCircle, AlertTriangle, ArrowRight, History,
  BookOpen, MessageSquare, Layers, Search, Image as ImageIcon
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Spinner, TypingDots } from '../components/Loader';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import CodeHighlight from '../components/CodeHighlight';
import PaperFigure, {
  PaperFiguresProvider,
  canonicalFigureLabel,
  useHasFigure,
} from '../components/PaperFigure';
import {
  parseCodeLanguage,
  codeChildrenToText,
} from '../utils/mermaidChart';
import { normalizeLatexDelimiters, KATEX_REHYPE_OPTIONS } from '../utils/latexMath';
import RequestContextChip from '../components/RequestContextChip';
import { Badge } from '../components/ui/badge';
import { SavedItemMenu, RenameField } from '../components/SavedItemActions';
import { splitPinned, displayName, orderPinned } from '../lib/savedItems';
import 'katex/dist/katex.min.css';
import './PdfAnalysis.css';
// Also configures the pdf.js worker that PaperFigure's getDocument relies on.
import PdfReader from '../components/PdfReader';

const REMARK_PLUGINS = [remarkGfm, remarkMath];
const REHYPE_PLUGINS = [[rehypeKatex, KATEX_REHYPE_OPTIONS]];

// `kind: 'gaps'` runs the backend's structured gap-analysis branch, which
// returns { type: 'structured', data } and renders into Findings. Everything
// else is free-text chat. Asking for gaps in prose returns a normal `custom`
// message, which the Findings tab filters out -- so this needs its own trigger.
const GAP_ANALYSIS_LABEL = 'Analyse research gaps in this paper';

// `[Figure 3]`, `[Table 2]`, `[Fig. 4]` -- the citation contract the backend
// system prompt asks for. Rewritten to links so the markdown renderer can hand
// them to <PaperFigure/>, the same trick `[Page 3]` already uses.
const FIGURE_CITATION_RE =
  /\[\s*(Fig(?:ure)?\.?|Table|Chart|Scheme)\s*([0-9]+|[IVXLC]+)\s*\]/gi;

function linkFigureCitations(text, hasFigure) {
  return text.replace(FIGURE_CITATION_RE, (match, word, number) => {
    const label = canonicalFigureLabel(word, number);
    // A label this paper does not have stays literal text. The model is told
    // only listed labels exist; when it invents one anyway, a citation that
    // quietly does nothing beats a card for a figure that is not there.
    if (!label || !hasFigure(label)) return match;
    return `[${label}](#fig-${encodeURIComponent(label)})`;
  });
}

const SUGGESTIONS = [
  { label: 'Main contribution', prompt: "What's the main contribution of this paper?" },
  { label: 'Limitations', prompt: 'What are the key limitations and weaknesses?' },
  { label: 'Research gaps', kind: 'gaps' },
  { label: 'Methodology', prompt: 'Explain the methodology used in this paper.' },
  { label: 'Key findings', prompt: 'Summarize the key findings and results.' },
  { label: 'Follow-up work', prompt: 'Suggest potential follow-up research directions.' },
];

// The landing shows the four questions people actually open a paper with.
const HOME_SUGGESTIONS = SUGGESTIONS.slice(0, 4);
const RECENT_LIMIT = 5;

const MODES = [
  { id: 'read', label: 'Read', Icon: BookOpen },
  { id: 'ask', label: 'Ask', Icon: MessageSquare },
  { id: 'findings', label: 'Findings', Icon: Layers },
];

function titleCaseSection(key) {
  return String(key)
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Backend sections are `{name: body}` dicts; headings/toc may still be arrays. */
function headingsFromStructure(structure) {
  if (!structure) return [];
  const sections = structure.sections;
  if (sections && typeof sections === 'object' && !Array.isArray(sections)) {
    return Object.keys(sections)
      .filter((key) => key && key !== 'full_text')
      .map((key) => ({ title: titleCaseSection(key) }));
  }
  if (Array.isArray(sections)) return sections;
  if (Array.isArray(structure.headings)) return structure.headings;
  if (Array.isArray(structure.toc)) return structure.toc;
  return [];
}

function isGapMessage(msg) {
  return (
    !msg.isLoading &&
    !msg.error &&
    msg.data &&
    (msg.type === 'structured' || msg.type === 'gap_analysis')
  );
}

function historyPayload(messages) {
  return messages
    .filter((m) => !m.isLoading && !m.error)
    .map((m) => {
      if (isGapMessage(m)) {
        const gaps = (m.data.gaps || []).join('; ');
        const covered = (m.data.well_covered || []).join('; ');
        const direction = m.data.suggested_direction || '';
        return {
          role: m.role,
          content: `[Gap analysis]\nWell covered: ${covered}\nGaps: ${gaps}\nSuggested: ${direction}`,
        };
      }
      return { role: m.role, content: m.content || '' };
    })
    .filter((m) => m.content.trim());
}

function TypingIndicator() {
  return (
    <div className="pdf-typing">
      <TypingDots />
    </div>
  );
}

function GapPanel({ data }) {
  if (!data) return null;
  return (
    <div className="pdf-gap-result">
      {data.well_covered?.length > 0 && (
        <div className="pdf-gap-section">
          <div className="pdf-gap-section-label success">
            <CheckCircle size={13} /> Well covered
          </div>
          <ul>
            {data.well_covered.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {data.gaps?.length > 0 && (
        <div className="pdf-gap-section">
          <div className="pdf-gap-section-label danger">
            <AlertTriangle size={13} /> Identified gaps
          </div>
          <ul>
            {data.gaps.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {data.suggested_direction && (
        <div className="pdf-gap-direction">
          <div className="pdf-gap-section-label accent">
            <ArrowRight size={13} /> Suggested direction
          </div>
          <p>{data.suggested_direction}</p>
        </div>
      )}
    </div>
  );
}

function MessageBubble({ msg, markdownComponents }) {
  const isUser = msg.role === 'user';
  const hasFigure = useHasFigure();

  const renderContent = () => {
    if (msg.isLoading) return <TypingIndicator />;
    if (msg.error) {
      return (
        <div className="pdf-error-inline">
          <AlertCircle size={14} />
          {msg.content}
        </div>
      );
    }
    if (isGapMessage(msg)) {
      return <GapPanel data={msg.data} />;
    }
    return (
      <div className="pdf-markdown-body">
        <ReactMarkdown
          remarkPlugins={REMARK_PLUGINS}
          rehypePlugins={REHYPE_PLUGINS}
          components={markdownComponents}
        >
          {normalizeLatexDelimiters(
            linkFigureCitations(msg.content || '', hasFigure)
              .replace(/\[?(?:Page|Pg\.?)\s*(\d+)\]?/gi, '[Page $1](#page-$1)')
          )}
        </ReactMarkdown>
      </div>
    );
  };

  return (
    <div className={`pdf-message ${isUser ? 'user' : 'assistant'}`}>
      {!isUser && (
        <div className="pdf-avatar assistant-avatar">
          <Bot size={15} />
        </div>
      )}
      <div className="pdf-bubble">{renderContent()}</div>
      {isUser && (
        <div className="pdf-avatar user-avatar">
          <User size={15} />
        </div>
      )}
    </div>
  );
}

const MemoMessageBubble = memo(MessageBubble);

function formatSavedAt(iso, { short = false } = {}) {
  const date = iso ? new Date(iso) : null;
  if (!date || Number.isNaN(date.getTime())) return '';
  // The rail is narrow: drop the year when it is this year.
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, short && sameYear
    ? { day: 'numeric', month: 'short' }
    : { day: 'numeric', month: 'short', year: 'numeric' });
}

export default function PdfAnalysis() {
  const { api } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  // { name } always; `size` only on a File picked in this session.
  const [file, setFile] = useState(null);
  const [extractedText, setExtractedText] = useState('');
  const [structure, setStructure] = useState(null);
  const [isExtracting, setIsExtracting] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState('');
  const [customPrompt, setCustomPrompt] = useState('');
  const [messages, setMessages] = useState([]);
  const [isDragging, setIsDragging] = useState(false);
  // Landing composer (PDF-5): a PDF staged in the composer but not uploaded
  // yet, and whether the staged question is the structured gap analysis.
  const [draftFile, setDraftFile] = useState(null);
  const [draftKind, setDraftKind] = useState('ask');
  const [fileId, setFileId] = useState(null);
  // The PDF bytes the reader and figure crops open: the picked File, or a Blob
  // fetched for a saved chat. Never a blob: URL -- connect-src has no blob:,
  // so pdf.js could not read one back and every restored chat failed (PDF-2).
  const [pdfData, setPdfData] = useState(null);
  const [pdfStatus, setPdfStatus] = useState('idle'); // idle | loading | ready | error
  const [pdfRetry, setPdfRetry] = useState(0);
  const [jump, setJump] = useState(null);
  const [mode, setMode] = useState('ask');
  const [chatList, setChatList] = useState([]);
  const [activeChatId, setActiveChatIdState] = useState(null);
  const [historyCollapsed, setHistoryCollapsed] = useState(true);
  const [loadingChats, setLoadingChats] = useState(false);
  // Chat whose name is being edited inline, in the rail or the landing list.
  const [renamingChatId, setRenamingChatId] = useState(null);
  const [openingChat, setOpeningChat] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  // What the last analysis actually sent to the model. Not persisted with the
  // chat: it describes one request, and replaying a saved conversation would
  // show the sizes of a call that is not the one about to be made.
  const [requestContext, setRequestContext] = useState(null);

  const chatEndRef = useRef(null);
  const inputRef = useRef(null);
  const fileInputRef = useRef(null);
  // One object per open paper, holding its chat id. Saves carry the object
  // they were made for and read the id from it, never from a render's
  // closure: a closure still held the previous chat's id while a new upload
  // saved, so the new paper was written over the old chat (PDF-3). A save
  // that finishes after the user moved on still lands on its own chat.
  const sessionRef = useRef({ chatId: null });
  // One save at a time: a second save issued before the first returned its
  // id would insert a duplicate chat.
  const saveQueueRef = useRef(Promise.resolve());
  // Set where a save is wanted; the messages effect below performs it once
  // the turn has settled. Keeps the POST out of setState updaters, which
  // React may run twice.
  const pendingSaveRef = useRef(false);
  const didInitRef = useRef(false);
  const dragDepthRef = useRef(0);

  /** Start a new paper session; `chatId` is null until the first save returns. */
  const beginSession = useCallback((chatId = null, firstTurn = null) => {
    // `firstTurn`: a question asked on the landing before the paper existed,
    // sent by the effect below once the paper is ready.
    sessionRef.current = { chatId, firstTurn };
    pendingSaveRef.current = false;
    setActiveChatIdState(chatId);
  }, []);

  const hasPaper = Boolean(file || extractedText);
  const railGroups = useMemo(() => splitPinned(chatList), [chatList]);
  const activeChat = chatList.find((c) => c.chat_id === activeChatId) || null;

  // Gap analyses only. Generated diagrams used to land here too, but the model
  // is no longer allowed to draw any (RP-12): the paper's own figures are the
  // pictures in this studio now.
  const findings = useMemo(() => messages.filter(isGapMessage), [messages]);

  const paperFigures = useMemo(
    () => (Array.isArray(structure?.figures) ? structure.figures : []),
    [structure],
  );

  // The only place a saved chat's PDF is downloaded. loadChat used to fetch it
  // as well, so every restore pulled the file twice.
  useEffect(() => {
    if (file?.size) {
      setPdfData(file);
      setPdfStatus('ready');
      return undefined;
    }
    setPdfData(null);
    if (!fileId) {
      setPdfStatus('idle');
      return undefined;
    }
    const controller = new AbortController();
    setPdfStatus('loading');
    (async () => {
      try {
        const res = await api.raw(`/api/manuscript/pdf/${fileId}`, { signal: controller.signal });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        if (controller.signal.aborted) return;
        setPdfData(blob);
        setPdfStatus('ready');
      } catch (err) {
        if (controller.signal.aborted) return;
        console.error('Failed to load PDF preview', err);
        setPdfStatus('error');
      }
    })();
    return () => controller.abort();
  }, [fileId, file, api, pdfRetry]);

  useEffect(() => {
    if (mode === 'ask') chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, mode]);

  const jumpToPage = useCallback((page) => {
    if (!Number.isFinite(page) || page < 1) return;
    setJump((prev) => ({ page, seq: (prev?.seq || 0) + 1 }));
    setMode('read');
  }, []);

  const markdownComponents = useMemo(() => ({
    a: ({ href, children, ...props }) => {
      if (href?.startsWith('#fig-')) {
        return <PaperFigure label={decodeURIComponent(href.slice('#fig-'.length))} />;
      }
      if (href?.startsWith('#page-')) {
        const pageNum = Number(href.replace('#page-', ''));
        return (
          <button
            type="button"
            className="citation-pill"
            data-ref={`p.${pageNum}`}
            onClick={(e) => {
              e.preventDefault();
              if (!Number.isNaN(pageNum)) jumpToPage(pageNum);
            }}
          >
            {children}
          </button>
        );
      }
      return (
        <a href={href} target="_blank" rel="noreferrer" {...props}>
          {children}
        </a>
      );
    },
    code({ className, children, ...props }) {
      const language = parseCodeLanguage(className);
      const contentStr = codeChildrenToText(children);
      const isBlock = Boolean(className) || contentStr.includes('\n');

      return isBlock && language ? (
        <CodeHighlight language={language} {...props}>
          {contentStr}
        </CodeHighlight>
      ) : (
        <code className={className} {...props}>
          {children}
        </code>
      );
    },
  }), [jumpToPage]);

  const fetchChatList = async () => {
    setLoadingChats(true);
    try {
      const res = await api.raw(`/api/pdf-chats/list`);
      if (res.ok) {
        const data = await res.json();
        setChatList(data.data || []);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingChats(false);
    }
  };

  const loadChat = async (chatId, { firstTurn = null } = {}) => {
    setOpeningChat(true);
    setHistoryCollapsed(true);
    try {
      const res = await api.raw(`/api/pdf-chats/${chatId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const chat = data.data;
      beginSession(chat.chat_id, firstTurn);
      setFile({ name: chat.filename });
      setFileId(chat.file_id || null);
      setExtractedText(chat.text);
      setStructure(chat.structure);
      setMessages(chat.messages || []);
      setError('');
      setSaveFailed(false);
      setMode('ask');
      setJump(null);
      // Belongs to the turn that produced it, not to the conversation.
      setRequestContext(null);
    } catch (e) {
      console.error('Failed to load chat', e);
      reset();
      setError('That paper could not be opened. It may have been deleted.');
    } finally {
      setOpeningChat(false);
    }
  };

  /** Throws a user-facing message; SavedItemMenu keeps its dialog open with it. */
  const deleteChat = async (chatId) => {
    let res;
    try {
      res = await api.raw(`/api/pdf-chats/${chatId}`, { method: 'DELETE' });
    } catch {
      throw new Error('Could not reach the server. Try again.');
    }
    // 404: already gone, which is what was asked for.
    if (!res.ok && res.status !== 404) throw new Error('That paper could not be deleted. Try again.');
    if (sessionRef.current.chatId === chatId) reset();
    setChatList((prev) => prev.filter((c) => c.chat_id !== chatId));
    fetchChatList();
  };

  /** Pin / rename (ROW-1), applied at once and rolled back if the server refuses. */
  const patchChat = async (chatId, changes) => {
    const before = chatList;
    const apply = (patch) => setChatList((prev) =>
      orderPinned(prev.map((c) => (c.chat_id === chatId ? { ...c, ...patch } : c)), 'chat_id'));
    apply(changes);
    try {
      const res = await api.raw(`/api/pdf-chats/${chatId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changes),
      });
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      apply({ pinned: data.pinned, title: data.title });
    } catch {
      setChatList(before);
      setError('title' in changes
        ? 'That paper could not be renamed. Try again.'
        : 'That paper could not be pinned. Try again.');
    }
  };

  /** One row for the rail and the landing list: open, ⋯ menu, inline rename. */
  const renderChatRow = (chat, { longDate = false } = {}) => {
    const name = displayName(chat, 'filename') || 'Untitled PDF';
    const when = formatSavedAt(chat.updated_at || chat.created_at, { short: !longDate });
    const active = activeChatId === chat.chat_id;
    return (
      <div className={`pdf-history-item group ${active ? 'active' : ''}`}>
        {renamingChatId === chat.chat_id ? (
          <span className="pdf-history-item-content pdf-history-item-editing">
            <FileText size={14} />
            <RenameField
              className="pdf-history-rename"
              initial={name}
              label={`Rename ${name}`}
              onSubmit={(value) => { setRenamingChatId(null); patchChat(chat.chat_id, { title: value }); }}
              onCancel={() => setRenamingChatId(null)}
            />
          </span>
        ) : (
          <button
            type="button"
            className="pdf-history-item-main"
            onClick={() => loadChat(chat.chat_id)}
            aria-current={active ? 'true' : undefined}
            title={chat.title ? `${name} (${chat.filename})` : name}
          >
            <span className="pdf-history-item-content">
              {chat.pinned ? <Pin size={14} aria-label="Pinned" /> : <FileText size={14} />}
              <span className="pdf-history-item-text">{name}</span>
            </span>
            {when && <em>{when}</em>}
          </button>
        )}
        <SavedItemMenu
          name={name}
          kind="paper"
          pinned={chat.pinned === true}
          onTogglePin={() => patchChat(chat.chat_id, { pinned: !chat.pinned })}
          onRename={() => setRenamingChatId(chat.chat_id)}
          onDelete={() => deleteChat(chat.chat_id)}
          deleteDetail="and its conversation will be removed from your account."
        />
      </div>
    );
  };

  const saveChatState = (snapshot) => {
    const { session } = snapshot;
    const run = async () => {
      if (!snapshot.text || snapshot.messages.length === 0) return;
      try {
        const res = await api.raw(`/api/pdf-chats/save`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chat_id: session.chatId,
            filename: snapshot.filename || 'Unknown PDF',
            text: snapshot.text,
            structure: snapshot.structure,
            messages: snapshot.messages,
            file_id: snapshot.fileId,
          }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!session.chatId) session.chatId = data.chat_id;
        if (session === sessionRef.current) {
          setActiveChatIdState(session.chatId);
          setSaveFailed(false);
        }
        fetchChatList();
      } catch (e) {
        console.error('Failed to save chat', e);
        if (session === sessionRef.current) setSaveFailed(true);
      }
    };
    saveQueueRef.current = saveQueueRef.current.then(run);
    return saveQueueRef.current;
  };

  useEffect(() => {
    if (!pendingSaveRef.current) return;
    if (messages.some((m) => m.isLoading)) return;
    pendingSaveRef.current = false;
    saveChatState({
      session: sessionRef.current,
      filename: file?.name,
      text: extractedText,
      structure,
      messages,
      fileId,
    });
    // Runs when a turn settles; the other values are read as they are then.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);

  const retrySave = () => {
    pendingSaveRef.current = true;
    setMessages((prev) => [...prev]);
  };

  const handleFileChange = async (selected, firstTurn = null) => {
    if (!selected) return;
    if (selected.type !== 'application/pdf') {
      setError('Please upload a valid PDF file.');
      return;
    }

    beginSession(null, firstTurn);
    const session = sessionRef.current;
    setFile(selected);
    setFileId(null);
    setError('');
    setSaveFailed(false);
    setExtractedText('');
    setStructure(null);
    setMessages([]);
    setCustomPrompt('');
    setIsExtracting(true);
    setRequestContext(null);
    setJump(null);
    setMode('ask');

    const formData = new FormData();
    formData.append('file', selected);

    try {
      const res = await api.raw(
        `/api/manuscript/extract-pdf`,
        { method: 'POST', body: formData }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Failed to extract PDF');
      }
      const data = await res.json();
      // Another paper was opened while this one was being extracted.
      if (session !== sessionRef.current) return;
      // Same bytes as a paper already in the account (PDF-4): open that chat,
      // with its history, rather than starting a duplicate.
      if (data.existing_chat_id) {
        setIsExtracting(false);
        await loadChat(data.existing_chat_id, { firstTurn: session.firstTurn });
        return;
      }
      setExtractedText(data.text);
      setStructure(data.structure);
      setFileId(data.file_id);

      const initMsgs = [
        {
          id: Date.now(),
          role: 'assistant',
          type: 'text',
          content: `**"${selected.name}"** is ready.\n\nUse **Ask** for questions, **Read** to browse pages, and **Findings** for gap analyses and diagrams.`,
        },
      ];
      pendingSaveRef.current = true;
      setMessages(initMsgs);
    } catch (err) {
      if (session !== sessionRef.current) return;
      setError(err.message || 'Error extracting PDF text. Please try again.');
      setFile(null);
    } finally {
      if (session === sessionRef.current) setIsExtracting(false);
    }
  };

  /**
   * kind 'ask'  -> free-text question, backend returns { type: 'custom' }
   * kind 'gaps' -> omits custom_prompt entirely, which is how the backend
   *                selects its structured gap-analysis branch and returns
   *                { type: 'structured', data } for the Findings tab.
   */
  const submitAnalysis = async ({ prompt = '', kind = 'ask' } = {}) => {
    const isGaps = kind === 'gaps';
    const finalPrompt = isGaps ? '' : prompt;
    const session = sessionRef.current;
    if (!session.chatId && !extractedText) return;
    if (!isGaps && !finalPrompt.trim()) return;

    const userMsg = {
      id: Date.now(),
      role: 'user',
      type: 'text',
      content: isGaps ? GAP_ANALYSIS_LABEL : finalPrompt,
    };
    const loadingMsg = { id: Date.now() + 1, role: 'assistant', type: 'text', isLoading: true, content: '' };

    setMessages((prev) => [...prev, userMsg, loadingMsg]);
    setIsAnalyzing(true);
    setMode('ask');

    try {
      const formData = new FormData();
      // Follow-up turns: chat id only. The server hydrates text/structure/history
      // from pdf_chats. First turn (no id yet) still sends the extracted payload.
      if (session.chatId) {
        formData.append('chat_id', session.chatId);
      } else {
        formData.append('text', extractedText);
        if (structure) formData.append('structure', JSON.stringify(structure));
        formData.append('history', JSON.stringify(historyPayload(messages)));
      }
      // Deliberately omitted for 'gaps' -- its absence is the branch selector.
      if (!isGaps) formData.append('custom_prompt', finalPrompt);

      const res = await api.raw(
        `/api/manuscript/analyze-pdf`,
        { method: 'POST', body: formData }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Failed to analyze PDF');
      }
      const data = await res.json();
      const nextType = data.type === 'structured' ? 'structured' : data.type;
      // Absent when the question was answered from the paper's metadata without
      // a model call — clear rather than leave the previous turn's sizes up.
      // The user opened another paper while this answer was in flight.
      if (session !== sessionRef.current) return;
      setRequestContext(data.context || null);

      pendingSaveRef.current = true;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === loadingMsg.id
            ? {
                ...m,
                isLoading: false,
                type: nextType,
                content: data.type === 'custom' ? data.content : '',
                data: data.type === 'structured' || data.type === 'gap_analysis' ? data.data : undefined,
              }
            : m
        )
      );
      if (data.type === 'structured') setMode('findings');
    } catch (err) {
      if (session !== sessionRef.current) return;
      pendingSaveRef.current = true;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === loadingMsg.id
            ? { ...m, isLoading: false, error: true, content: err.message || 'Analysis failed. Please try again.' }
            : m
        )
      );
    } finally {
      setIsAnalyzing(false);
      inputRef.current?.focus();
    }
  };

  /** Send whatever is in the composer, then clear it. */
  const sendComposer = () => {
    const prompt = customPrompt;
    if (!prompt.trim()) return;
    setCustomPrompt('');
    submitAnalysis({ prompt });
  };

  const runGapAnalysis = () => submitAnalysis({ kind: 'gaps' });

  const runSuggestion = (suggestion) =>
    suggestion.kind === 'gaps'
      ? runGapAnalysis()
      : submitAnalysis({ prompt: suggestion.prompt });

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendComposer();
    }
  };

  // A question asked on the landing goes out as soon as the paper is ready --
  // after extraction, or after the existing chat for the same bytes opened.
  useEffect(() => {
    const turn = sessionRef.current.firstTurn;
    if (!turn || isExtracting || openingChat || isAnalyzing || !extractedText) return;
    sessionRef.current.firstTurn = null;
    submitAnalysis(turn);
    // submitAnalysis is re-created every render; this follows readiness only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isExtracting, openingChat, isAnalyzing, extractedText]);

  /** Landing: stage a PDF in the composer. Nothing uploads until Send. */
  const stageFile = (selected) => {
    if (!selected) return;
    if (selected.type !== 'application/pdf') {
      setError('That file is not a PDF.');
      return;
    }
    setError('');
    setDraftFile(selected);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  const pickFile = (selected) => {
    if (hasPaper) handleFileChange(selected);
    else stageFile(selected);
  };

  const sendFromHome = () => {
    if (!draftFile) {
      fileInputRef.current?.click();
      return;
    }
    const prompt = customPrompt.trim();
    const turn = draftKind === 'gaps' ? { kind: 'gaps' } : prompt ? { prompt } : null;
    const staged = draftFile;
    setDraftFile(null);
    setDraftKind('ask');
    setCustomPrompt('');
    handleFileChange(staged, turn);
  };

  const runHomeSuggestion = (suggestion) => {
    setDraftKind(suggestion.kind === 'gaps' ? 'gaps' : 'ask');
    setCustomPrompt(suggestion.kind === 'gaps' ? GAP_ANALYSIS_LABEL : suggestion.prompt);
    if (draftFile) requestAnimationFrame(() => inputRef.current?.focus());
    else fileInputRef.current?.click();
  };

  const homeDragProps = {
    onDragEnter: (e) => {
      if (!e.dataTransfer?.types?.includes('Files')) return;
      e.preventDefault();
      dragDepthRef.current += 1;
      setIsDragging(true);
    },
    onDragOver: (e) => {
      if (e.dataTransfer?.types?.includes('Files')) e.preventDefault();
    },
    onDragLeave: () => {
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
      if (dragDepthRef.current === 0) setIsDragging(false);
    },
    onDrop: (e) => {
      e.preventDefault();
      dragDepthRef.current = 0;
      setIsDragging(false);
      stageFile(e.dataTransfer.files?.[0]);
    },
  };

  function reset() {
    beginSession();
    setDraftFile(null);
    setDraftKind('ask');
    setCustomPrompt('');
    setFile(null);
    setFileId(null);
    setExtractedText('');
    setStructure(null);
    setMessages([]);
    setError('');
    setSaveFailed(false);
    setIsExtracting(false);
    setRequestContext(null);
    setJump(null);
    setMode('ask');
  }

  // Keep the open chat in the URL, so a reload -- or a link opened on another
  // device -- comes back to the same paper.
  useEffect(() => {
    if (!didInitRef.current) return;
    const current = searchParams.get('chat');
    if ((activeChatId || null) === (current || null)) return;
    const next = new URLSearchParams(searchParams);
    if (activeChatId) next.set('chat', activeChatId);
    else next.delete('chat');
    setSearchParams(next, { replace: true });
    // searchParams is read, not watched: this follows the chat, not the URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeChatId]);

  useEffect(() => {
    if (didInitRef.current) return;
    didInitRef.current = true;
    fetchChatList();
    const fromUrl = searchParams.get('chat');
    if (fromUrl) loadChat(fromUrl);
    // Once per mount; the guard also absorbs StrictMode's second run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const structureHeadings = useMemo(() => headingsFromStructure(structure), [structure]);

  return (
    <div className="pdf-analysis-layout">
      <div
        className={`pdf-sidebar-overlay ${!historyCollapsed ? 'visible' : ''}`}
        onClick={() => setHistoryCollapsed(true)}
      />

      <aside className={`pdf-history-sidebar ${historyCollapsed ? 'collapsed' : ''}`}>
        <div className="pdf-history-header">
          <button type="button" className="pdf-new-chat-btn" onClick={reset}>
            + New
          </button>
          <button type="button" className="pdf-history-toggle" onClick={() => setHistoryCollapsed(true)} title="Close history">
            <ChevronLeft size={18} />
          </button>
        </div>
        {!historyCollapsed && (
          <div className="pdf-history-list">
            {loadingChats ? (
              <div className="pdf-history-empty">
                <Spinner size={16} />
              </div>
            ) : chatList.length === 0 ? (
              <div className="pdf-history-empty">No past chats.</div>
            ) : (
              [['Pinned', railGroups.pinned], ['Recent', railGroups.rest]]
                .filter(([, rows]) => rows.length > 0)
                .map(([label, rows]) => (
                  <section key={label} className="pdf-history-group" aria-label={label}>
                    {railGroups.pinned.length > 0 && <h3 className="pdf-history-group-label">{label}</h3>}
                    {rows.map((chat) => (
                      <React.Fragment key={chat.chat_id}>{renderChatRow(chat)}</React.Fragment>
                    ))}
                  </section>
                ))
            )}
          </div>
        )}
      </aside>

      <PaperFiguresProvider
        file={pdfData}
        figures={paperFigures}
        onJumpToPage={jumpToPage}
      >
      <div className="pdf-studio">
        <header className="pdf-studio-bar">
          <div className="pdf-studio-bar-left">
            <button
              type="button"
              className="pdf-history-toggle"
              onClick={() => setHistoryCollapsed(false)}
              title="Chat history"
            >
              <History size={17} />
            </button>
            <div className="pdf-studio-title">
              <span>Analysis Studio</span>
              {(activeChat || file?.name) && (
                <span className="pdf-studio-file">
                  {activeChat ? displayName(activeChat, 'filename') : file.name}
                </span>
              )}
            </div>
          </div>

          {hasPaper && (
            <nav className="pdf-mode-tabs" aria-label="Studio modes">
              {MODES.map(({ id, label, Icon }) => (
                <button
                  key={id}
                  type="button"
                  className={`pdf-mode-tab ${mode === id ? 'is-active' : ''}`}
                  onClick={() => setMode(id)}
                >
                  <Icon size={14} />
                  {label}
                  {id === 'findings' && findings.length > 0 && (
                    <span className="pdf-mode-count">{findings.length}</span>
                  )}
                </button>
              ))}
            </nav>
          )}

          <div className="pdf-studio-bar-right">
            {saveFailed && (
              <button
                type="button"
                className="pdf-attach-btn is-error"
                onClick={retrySave}
                title="This chat was not saved to your account. Retry."
              >
                <AlertCircle size={15} />
                Not saved · Retry
              </button>
            )}
            {hasPaper && (
              <button type="button" className="pdf-attach-btn" onClick={() => fileInputRef.current?.click()} title="Replace PDF">
                <Paperclip size={15} />
                Replace
              </button>
            )}
            {hasPaper && (
              <button
                type="button"
                className="pdf-attach-btn"
                onClick={reset}
                title="Close paper"
                aria-label="Close paper"
              >
                <X size={15} />
              </button>
            )}
          </div>
        </header>

        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf"
          style={{ display: 'none' }}
          onChange={(e) => {
            pickFile(e.target.files?.[0]);
            // Cleared so picking the same file again still fires a change.
            e.target.value = '';
          }}
        />

        <div className="pdf-studio-body">
          {isExtracting || openingChat ? (
            <div className="pdf-empty-stage">
              <Spinner size={40} />
              {openingChat ? (
                <h2>Opening paper…</h2>
              ) : (
                <>
                  <h2>Extracting document…</h2>
                  <p>
                    Parsing text and structure. This may take a few seconds.
                    {sessionRef.current.firstTurn ? ' Your question is sent as soon as it is ready.' : ''}
                  </p>
                </>
              )}
            </div>
          ) : !hasPaper ? (
            <div className="pdf-empty-stage pdf-home" {...homeDragProps}>
              <h1 className="pdf-home-title">Which paper are we reading?</h1>

              <div className="pdf-home-column">
                {error && (
                  <div className="pdf-error-banner" role="alert">
                    <AlertCircle size={15} /> {error}
                  </div>
                )}

                <div className="pdf-input-wrapper pdf-home-composer">
                  {draftFile && (
                    <Badge variant="secondary" className="pdf-home-file">
                      <FileText />
                      <span>{draftFile.name}</span>
                      <button
                        type="button"
                        onClick={() => setDraftFile(null)}
                        aria-label={`Remove ${draftFile.name}`}
                      >
                        <X size={12} />
                      </button>
                    </Badge>
                  )}
                  <textarea
                    ref={inputRef}
                    className="pdf-textarea"
                    placeholder={
                      draftFile
                        ? 'Ask anything about this paper, or just send to open it'
                        : 'Attach a PDF, then ask anything about it'
                    }
                    value={customPrompt}
                    onChange={(e) => {
                      setCustomPrompt(e.target.value);
                      if (draftKind === 'gaps') setDraftKind('ask');
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        if (draftFile) sendFromHome();
                      }
                    }}
                    rows={2}
                  />
                  <div className="pdf-home-composer-row">
                    <button
                      type="button"
                      className="pdf-attach-btn"
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <Plus size={15} />
                      {draftFile ? 'Change PDF' : 'Attach PDF'}
                    </button>
                    <button
                      type="button"
                      className="pdf-send-btn"
                      onClick={sendFromHome}
                      disabled={!draftFile}
                      aria-label="Upload and send"
                    >
                      <Send size={16} />
                    </button>
                  </div>
                </div>

                <div className="pdf-home-suggestions">
                  {HOME_SUGGESTIONS.map((s) => (
                    <button
                      key={s.label}
                      type="button"
                      className="pdf-suggestion-chip"
                      onClick={() => runHomeSuggestion(s)}
                    >
                      {s.kind === 'gaps' ? <Layers size={13} /> : <Search size={13} />}
                      {s.label}
                    </button>
                  ))}
                </div>

                {[
                  ['pinned', 'Pinned', railGroups.pinned],
                  ['recent', 'Recent', railGroups.rest.slice(0, RECENT_LIMIT)],
                ].filter(([, , rows]) => rows.length > 0).map(([key, label, rows]) => (
                  <section key={key} className="pdf-recent" aria-labelledby={`pdf-${key}-title`}>
                    <div className="pdf-recent-head">
                      <h3 id={`pdf-${key}-title`}>{label}</h3>
                      {key === 'recent' && railGroups.rest.length > RECENT_LIMIT && (
                        <button type="button" onClick={() => setHistoryCollapsed(false)}>
                          View all
                        </button>
                      )}
                    </div>
                    <ul>
                      {rows.map((chat) => (
                        <li key={chat.chat_id}>{renderChatRow(chat, { longDate: true })}</li>
                      ))}
                    </ul>
                  </section>
                ))}
              </div>

              {isDragging && (
                <div className="pdf-home-drop" aria-hidden="true">
                  <FileText size={22} />
                  Drop the PDF to attach it
                </div>
              )}
            </div>
          ) : (
            <>
              {/* READ -- kept mounted while hidden, so switching modes keeps the
                  parsed document and the reading position. */}
              <section className="pdf-mode-panel pdf-read-panel" hidden={mode !== 'read'}>
                  <div className="pdf-read-grid">
                    {pdfStatus === 'ready' && pdfData ? (
                      <PdfReader file={pdfData} active={mode === 'read'} jump={jump} />
                    ) : (
                      <div className="pdf-viewer-pane">
                        {pdfStatus === 'loading' ? (
                          <div className="pdf-viewer-loading">
                            <Spinner size={24} />
                          </div>
                        ) : pdfStatus === 'error' ? (
                          <div className="pdf-viewer-missing">
                            <p>The PDF could not be downloaded.</p>
                            <button
                              type="button"
                              className="pdf-attach-btn"
                              onClick={() => setPdfRetry((n) => n + 1)}
                            >
                              <RotateCw size={15} />
                              Retry
                            </button>
                          </div>
                        ) : (
                          <div className="pdf-viewer-missing">
                            <p>No PDF was stored with this chat, so there is nothing to show here.</p>
                          </div>
                        )}
                      </div>
                    )}

                    {structureHeadings.length > 0 && (
                      <aside className="pdf-toc-rail">
                        <h3>Structure</h3>
                        <ul>
                          {structureHeadings.slice(0, 40).map((s, i) => {
                            const title = typeof s === 'string' ? s : s.title || s.heading || s.name || `Section ${i + 1}`;
                            const page = typeof s === 'object' ? s.page || s.page_number : null;
                            return (
                              <li key={i}>
                                <button
                                  type="button"
                                  onClick={() => page && jumpToPage(Number(page))}
                                  disabled={!page}
                                >
                                  <span>{title}</span>
                                  {page && <em>p.{page}</em>}
                                </button>
                              </li>
                            );
                          })}
                        </ul>
                      </aside>
                    )}
                  </div>
              </section>

              {/* ASK */}
              {mode === 'ask' && (
                <section className="pdf-mode-panel pdf-ask-panel">
                 <div className="pdf-ask-grid">
                  <div className="pdf-ask-main">
                  <div className="pdf-messages-area">
                    {messages.map((msg) => (
                      <MemoMessageBubble key={msg.id} msg={msg} markdownComponents={markdownComponents} />
                    ))}
                    {messages.length === 1 && (
                      <div className="pdf-suggestions">
                        {SUGGESTIONS.map((s) => (
                          <button
                            key={s.label}
                            type="button"
                            className="pdf-suggestion-chip"
                            disabled={isAnalyzing}
                            onClick={() => runSuggestion(s)}
                          >
                            {s.kind === 'gaps' ? <Layers size={13} /> : <Search size={13} />}
                            {s.label}
                          </button>
                        ))}
                      </div>
                    )}
                    <div ref={chatEndRef} />
                  </div>
                  <div className="pdf-input-container">
                    <div className="pdf-composer-actions">
                      <button
                        type="button"
                        className="pdf-suggestion-chip"
                        onClick={runGapAnalysis}
                        disabled={isAnalyzing || isExtracting}
                        title="Run a structured gap analysis — results appear under Findings"
                      >
                        <Layers size={13} />
                        Analyse gaps
                      </button>
                      <RequestContextChip context={requestContext} />
                    </div>
                    <div className="pdf-input-wrapper">
                      <textarea
                        ref={inputRef}
                        className="pdf-textarea"
                        placeholder="Ask about methods, gaps, results…"
                        value={customPrompt}
                        onChange={(e) => setCustomPrompt(e.target.value)}
                        onKeyDown={handleKeyDown}
                        rows={1}
                        disabled={isAnalyzing || isExtracting}
                      />
                      <button
                        type="button"
                        className="pdf-send-btn"
                        onClick={sendComposer}
                        disabled={!customPrompt.trim() || isAnalyzing || isExtracting}
                      >
                        {isAnalyzing ? <Spinner size={16} /> : <Send size={16} />}
                      </button>
                    </div>
                  </div>
                  </div>

                  {paperFigures.length > 0 && (
                    <aside className="pdf-figure-rail">
                      <h3>Figures in this paper</h3>
                      <ul>
                        {paperFigures.map((figure) => (
                          <li key={figure.label}>
                            <button
                              type="button"
                              onClick={() => jumpToPage(Number(figure.page))}
                              title={figure.caption || figure.label}
                            >
                              <span>
                                <ImageIcon size={12} />
                                {figure.label}
                              </span>
                              <em>p.{figure.page}</em>
                            </button>
                          </li>
                        ))}
                      </ul>
                    </aside>
                  )}
                 </div>
                </section>
              )}

              {/* FINDINGS */}
              {mode === 'findings' && (
                <section className="pdf-mode-panel pdf-findings-panel">
                  {findings.length === 0 ? (
                    <div className="pdf-empty-stage compact">
                      <Layers size={28} />
                      <h2>No findings yet</h2>
                      <p>Run a gap analysis — results land here.</p>
                      <div className="pdf-empty-actions">
                        <button
                          type="button"
                          className="pdf-new-chat-btn"
                          onClick={runGapAnalysis}
                          disabled={isAnalyzing || isExtracting}
                        >
                          {isAnalyzing ? 'Analysing…' : 'Analyse gaps'}
                        </button>
                        <button type="button" className="pdf-new-chat-btn" onClick={() => setMode('ask')}>
                          Go to Ask
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="pdf-findings-stack">
                      {findings.map((msg) => (
                        <article key={msg.id} className="pdf-finding-card">
                          <header>
                            <AlertTriangle size={15} />
                            Gap analysis
                          </header>
                          <GapPanel data={msg.data} />
                        </article>
                      ))}
                    </div>
                  )}
                </section>
              )}
            </>
          )}
        </div>
      </div>
      </PaperFiguresProvider>
    </div>
  );
}
