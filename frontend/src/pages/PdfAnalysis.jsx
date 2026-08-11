import React, { useState, useRef, useEffect, useMemo, useCallback, memo } from 'react';
import {
  UploadCloud, FileText, Send, AlertCircle,
  ChevronLeft, ChevronRight, Bot, User, Paperclip, X,
  CheckCircle, AlertTriangle, ArrowRight, History,
  Minus, Plus, BookOpen, MessageSquare, Layers, Search
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Spinner, TypingDots } from '../components/Loader';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import CodeHighlight from '../components/CodeHighlight';
import Mermaid from '../components/Mermaid';
import {
  isMermaidBlock,
  extractMermaidCharts,
  parseCodeLanguage,
  codeChildrenToText,
} from '../utils/mermaidChart';
import 'katex/dist/katex.min.css';
import './PdfAnalysis.css';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

const REMARK_PLUGINS = [remarkGfm, remarkMath];
const REHYPE_PLUGINS = [[rehypeKatex, { strict: false, throwOnError: false, errorColor: 'inherit' }]];

const SUGGESTIONS = [
  { label: 'Main contribution', prompt: "What's the main contribution of this paper?" },
  { label: 'Limitations', prompt: 'What are the key limitations and weaknesses?' },
  { label: 'Research gaps', prompt: 'Identify research gaps and future directions.' },
  { label: 'Methodology', prompt: 'Explain the methodology used in this paper.' },
  { label: 'Key findings', prompt: 'Summarize the key findings and results.' },
  { label: 'Follow-up work', prompt: 'Suggest potential follow-up research directions.' },
];

const MODES = [
  { id: 'read', label: 'Read', Icon: BookOpen },
  { id: 'ask', label: 'Ask', Icon: MessageSquare },
  { id: 'findings', label: 'Findings', Icon: Layers },
];

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
          {(msg.content || '').replace(/\[?(?:Page|Pg\.?)\s*(\d+)\]?/gi, '[Page $1](#page-$1)')}
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

export default function PdfAnalysis() {
  const pixelRatio = window.devicePixelRatio || 1;
  const { authFetch } = useAuth();
  const [file, setFile] = useState(null);
  const [extractedText, setExtractedText] = useState('');
  const [structure, setStructure] = useState(null);
  const [isExtracting, setIsExtracting] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState('');
  const [customPrompt, setCustomPrompt] = useState('');
  const [messages, setMessages] = useState([]);
  const [isDragging, setIsDragging] = useState(false);
  const [fileId, setFileId] = useState(null);
  const [pdfBlobUrl, setPdfBlobUrl] = useState(null);
  const [numPages, setNumPages] = useState(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [zoom, setZoom] = useState(1.2);
  const [mode, setMode] = useState('ask');
  const [chatList, setChatList] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [historyCollapsed, setHistoryCollapsed] = useState(true);
  const [loadingChats, setLoadingChats] = useState(false);

  const chatEndRef = useRef(null);
  const inputRef = useRef(null);
  const fileInputRef = useRef(null);
  const viewerScrollRef = useRef(null);

  const hasPaper = Boolean(file || extractedText);

  const findings = useMemo(() => {
    const gaps = messages.filter(isGapMessage);
    const diagrams = [];
    messages.forEach((m) => {
      if (m.role === 'assistant' && m.content && !m.isLoading) {
        extractMermaidCharts(m.content).forEach((chart, i) => {
          diagrams.push({ id: `${m.id}-${i}`, chart, fromMessageId: m.id });
        });
      }
    });
    return { gaps, diagrams };
  }, [messages]);

  useEffect(() => {
    let currentBlobUrl = null;
    if (fileId && (!file || !file?.size)) {
      const loadPdf = async () => {
        try {
          const res = await authFetch(
            `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript/pdf/${fileId}`
          );
          if (res.ok) {
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            currentBlobUrl = url;
            setPdfBlobUrl(url);
          }
        } catch (err) {
          console.error('Failed to load PDF preview', err);
        }
      };
      loadPdf();
    } else {
      setPdfBlobUrl(null);
    }
    return () => {
      if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
    };
  }, [fileId, file, authFetch]);

  useEffect(() => {
    if (mode === 'ask') chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, mode]);

  useEffect(() => {
    fetchChatList();
  }, []);

  const jumpToPage = useCallback((page) => {
    setPageNumber(page);
    setMode('read');
    requestAnimationFrame(() => {
      viewerScrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }, []);

  const markdownComponents = useMemo(() => ({
    a: ({ href, children, ...props }) => {
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

      if (isBlock && isMermaidBlock(language, contentStr)) {
        return (
          <div className="pdf-figure-block">
            <Mermaid chart={contentStr} />
          </div>
        );
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
    },
  }), [jumpToPage]);

  const fetchChatList = async () => {
    setLoadingChats(true);
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/pdf-chats/list`);
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

  const loadChat = async (chatId) => {
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/pdf-chats/${chatId}`);
      if (res.ok) {
        const data = await res.json();
        const chat = data.data;
        setActiveChatId(chat.chat_id);
        setFile({ name: chat.filename });
        setFileId(chat.file_id || null);
        setExtractedText(chat.text);
        setStructure(chat.structure);
        setMessages(chat.messages || []);
        setError('');
        setMode('ask');
        setHistoryCollapsed(true);

        if (chat.file_id) {
          try {
            const pdfRes = await authFetch(
              `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript/pdf/${chat.file_id}`
            );
            if (pdfRes.ok) {
              const blob = await pdfRes.blob();
              setPdfBlobUrl((prev) => {
                if (prev) URL.revokeObjectURL(prev);
                return URL.createObjectURL(blob);
              });
            }
          } catch (pdfErr) {
            console.error('Failed to restore PDF preview', pdfErr);
          }
        }
      }
    } catch (e) {
      console.error('Failed to load chat', e);
    }
  };

  const deleteChat = async (chatId, e) => {
    e.stopPropagation();
    try {
      const res = await authFetch(
        `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/pdf-chats/${chatId}`,
        { method: 'DELETE' }
      );
      if (res.ok) {
        if (activeChatId === chatId) reset();
        fetchChatList();
      }
    } catch (err) {
      console.error('Failed to delete chat', err);
    }
  };

  const saveChatState = async (newMessages, currentFile, text, struct, currentFileId = fileId) => {
    if (!text || newMessages.length === 0) return;
    try {
      const payload = {
        chat_id: activeChatId,
        filename: currentFile?.name || 'Unknown PDF',
        text,
        structure: struct,
        messages: newMessages,
        file_id: currentFileId,
      };
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/pdf-chats/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        if (!activeChatId) setActiveChatId(data.chat_id);
        fetchChatList();
      }
    } catch (e) {
      console.error('Failed to save chat', e);
    }
  };

  const handleFileChange = async (selected) => {
    if (!selected) return;
    if (selected.type !== 'application/pdf') {
      setError('Please upload a valid PDF file.');
      return;
    }

    setFile(selected);
    setFileId(null);
    setPdfBlobUrl(null);
    setError('');
    setExtractedText('');
    setStructure(null);
    setMessages([]);
    setCustomPrompt('');
    setIsExtracting(true);
    setActiveChatId(null);
    setMode('ask');

    const formData = new FormData();
    formData.append('file', selected);

    try {
      const res = await authFetch(
        `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript/extract-pdf`,
        { method: 'POST', body: formData }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Failed to extract PDF');
      }
      const data = await res.json();
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
      setMessages(initMsgs);
      await saveChatState(initMsgs, selected, data.text, data.structure, data.file_id);
    } catch (err) {
      setError(err.message || 'Error extracting PDF text. Please try again.');
      setFile(null);
    } finally {
      setIsExtracting(false);
    }
  };

  const runAnalysis = async (promptOverride = null) => {
    const finalPrompt = promptOverride !== null ? promptOverride : customPrompt;
    if (!extractedText || !finalPrompt.trim()) return;

    const userMsg = { id: Date.now(), role: 'user', type: 'text', content: finalPrompt };
    const loadingMsg = { id: Date.now() + 1, role: 'assistant', type: 'text', isLoading: true, content: '' };

    setMessages((prev) => [...prev, userMsg, loadingMsg]);
    if (promptOverride === null) setCustomPrompt('');
    setIsAnalyzing(true);
    setMode('ask');

    try {
      const formData = new FormData();
      formData.append('text', extractedText);
      if (finalPrompt) formData.append('custom_prompt', finalPrompt);
      if (structure) formData.append('structure', JSON.stringify(structure));
      if (activeChatId) formData.append('chat_id', activeChatId);
      formData.append('history', JSON.stringify(historyPayload(messages)));

      const res = await authFetch(
        `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript/analyze-pdf`,
        { method: 'POST', body: formData }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Failed to analyze PDF');
      }
      const data = await res.json();
      const nextType = data.type === 'structured' ? 'structured' : data.type;

      setMessages((prev) => {
        const updated = prev.map((m) =>
          m.id === loadingMsg.id
            ? {
                ...m,
                isLoading: false,
                type: nextType,
                content: data.type === 'custom' ? data.content : '',
                data: data.type === 'structured' || data.type === 'gap_analysis' ? data.data : undefined,
              }
            : m
        );
        saveChatState(updated, file, extractedText, structure, fileId);
        return updated;
      });
      if (data.type === 'structured') setMode('findings');
    } catch (err) {
      setMessages((prev) => {
        const updated = prev.map((m) =>
          m.id === loadingMsg.id
            ? { ...m, isLoading: false, error: true, content: err.message || 'Analysis failed. Please try again.' }
            : m
        );
        saveChatState(updated, file, extractedText, structure, fileId);
        return updated;
      });
    } finally {
      setIsAnalyzing(false);
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      runAnalysis();
    }
  };

  const reset = () => {
    setFile(null);
    setFileId(null);
    setPdfBlobUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    setExtractedText('');
    setStructure(null);
    setMessages([]);
    setError('');
    setActiveChatId(null);
    setMode('ask');
    setPageNumber(1);
    setNumPages(null);
  };

  const structureHeadings = structure?.sections || structure?.headings || structure?.toc || [];

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
              chatList.map((chat) => (
                <div
                  key={chat.chat_id}
                  className={`pdf-history-item ${activeChatId === chat.chat_id ? 'active' : ''}`}
                  onClick={() => loadChat(chat.chat_id)}
                >
                  <div className="pdf-history-item-content">
                    <FileText size={14} />
                    <span className="pdf-history-item-text">{chat.filename}</span>
                  </div>
                  <button type="button" className="pdf-history-item-delete" onClick={(e) => deleteChat(chat.chat_id, e)}>
                    <X size={14} />
                  </button>
                </div>
              ))
            )}
          </div>
        )}
      </aside>

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
              {file?.name && <span className="pdf-studio-file">{file.name}</span>}
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
                  {id === 'findings' && (findings.gaps.length > 0 || findings.diagrams.length > 0) && (
                    <span className="pdf-mode-count">{findings.gaps.length + findings.diagrams.length}</span>
                  )}
                </button>
              ))}
            </nav>
          )}

          <div className="pdf-studio-bar-right">
            <button type="button" className="pdf-attach-btn" onClick={() => fileInputRef.current?.click()} title="Upload PDF">
              <Paperclip size={15} />
              {hasPaper ? 'Replace' : 'Upload'}
            </button>
            {hasPaper && (
              <button type="button" className="pdf-attach-btn danger" onClick={reset} title="Clear session">
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
          onChange={(e) => handleFileChange(e.target.files?.[0])}
        />

        <div className="pdf-studio-body">
          {isExtracting ? (
            <div className="pdf-empty-stage">
              <Spinner size={40} />
              <h2>Extracting document…</h2>
              <p>Parsing text and structure. This may take a few seconds.</p>
            </div>
          ) : !hasPaper ? (
            <div className="pdf-empty-stage">
              <div className="pdf-empty-icon">
                <FileText size={28} />
              </div>
              <h1>Analysis Studio</h1>
              <p>Upload a paper, then move between Read, Ask, and Findings.</p>
              {error && (
                <div className="pdf-error-banner">
                  <AlertCircle size={15} /> {error}
                </div>
              )}
              <div
                className={`pdf-dropzone ${isDragging ? 'dragging' : ''}`}
                onDragOver={(e) => {
                  e.preventDefault();
                  setIsDragging(true);
                }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setIsDragging(false);
                  if (e.dataTransfer.files.length) handleFileChange(e.dataTransfer.files[0]);
                }}
                onClick={() => fileInputRef.current?.click()}
              >
                <UploadCloud size={28} className="pdf-dropzone-icon" />
                <h3>Drop your PDF here</h3>
                <p>or click to browse</p>
              </div>
            </div>
          ) : (
            <>
              {/* READ */}
              {mode === 'read' && (
                <section className="pdf-mode-panel pdf-read-panel">
                  <div className="pdf-read-grid">
                    <div className="pdf-viewer-pane">
                      {file?.size || pdfBlobUrl ? (
                        <div className="pdf-viewer-scroll" ref={viewerScrollRef}>
                          <Document
                            file={file?.size ? file : pdfBlobUrl}
                            onLoadSuccess={({ numPages: n }) => {
                              setNumPages(n);
                              setPageNumber((p) => Math.min(p, n));
                            }}
                            loading={
                              <div className="pdf-viewer-loading">
                                <Spinner size={24} />
                              </div>
                            }
                          >
                            <div
                              style={{
                                transform: `scale(${1 / pixelRatio})`,
                                transformOrigin: 'top left',
                                width: `${100 * pixelRatio}%`,
                              }}
                            >
                              <Page
                                pageNumber={pageNumber}
                                renderTextLayer
                                renderAnnotationLayer
                                scale={zoom * pixelRatio}
                              />
                            </div>
                          </Document>
                        </div>
                      ) : (
                        <div className="pdf-viewer-missing">
                          <p>Preview unavailable.{fileId ? ' Loading PDF…' : ''}</p>
                        </div>
                      )}
                      {(file?.size || pdfBlobUrl) && numPages && (
                        <div className="pdf-viewer-controls">
                          <div className="pdf-control-cluster">
                            <button type="button" className="pdf-control-btn" onClick={() => setZoom((z) => Math.max(0.5, z - 0.2))}>
                              <Minus size={15} />
                            </button>
                            <span className="pdf-control-label">{Math.round(zoom * 100)}%</span>
                            <button type="button" className="pdf-control-btn" onClick={() => setZoom((z) => Math.min(3, z + 0.2))}>
                              <Plus size={15} />
                            </button>
                          </div>
                          <div className="pdf-control-divider" />
                          <div className="pdf-control-cluster">
                            <button
                              type="button"
                              className="pdf-control-btn"
                              disabled={pageNumber <= 1}
                              onClick={() => setPageNumber((p) => p - 1)}
                            >
                              <ChevronLeft size={15} />
                            </button>
                            <span className="pdf-control-label">
                              Page {pageNumber} / {numPages}
                            </span>
                            <button
                              type="button"
                              className="pdf-control-btn"
                              disabled={pageNumber >= numPages}
                              onClick={() => setPageNumber((p) => p + 1)}
                            >
                              <ChevronRight size={15} />
                            </button>
                          </div>
                        </div>
                      )}
                    </div>

                    {Array.isArray(structureHeadings) && structureHeadings.length > 0 && (
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
              )}

              {/* ASK */}
              {mode === 'ask' && (
                <section className="pdf-mode-panel pdf-ask-panel">
                  <div className="pdf-messages-area">
                    {messages.map((msg) => (
                      <MemoMessageBubble key={msg.id} msg={msg} markdownComponents={markdownComponents} />
                    ))}
                    {messages.length === 1 && (
                      <div className="pdf-suggestions">
                        {SUGGESTIONS.map((s) => (
                          <button key={s.label} type="button" className="pdf-suggestion-chip" onClick={() => runAnalysis(s.prompt)}>
                            <Search size={13} />
                            {s.label}
                          </button>
                        ))}
                      </div>
                    )}
                    <div ref={chatEndRef} />
                  </div>
                  <div className="pdf-input-container">
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
                        onClick={() => runAnalysis()}
                        disabled={!customPrompt.trim() || isAnalyzing || isExtracting}
                      >
                        {isAnalyzing ? <Spinner size={16} /> : <Send size={16} />}
                      </button>
                    </div>
                  </div>
                </section>
              )}

              {/* FINDINGS */}
              {mode === 'findings' && (
                <section className="pdf-mode-panel pdf-findings-panel">
                  {findings.gaps.length === 0 && findings.diagrams.length === 0 ? (
                    <div className="pdf-empty-stage compact">
                      <Layers size={28} />
                      <h2>No findings yet</h2>
                      <p>Ask for research gaps or a methodology diagram — results land here.</p>
                      <button type="button" className="pdf-new-chat-btn" onClick={() => setMode('ask')}>
                        Go to Ask
                      </button>
                    </div>
                  ) : (
                    <div className="pdf-findings-stack">
                      {findings.gaps.map((msg) => (
                        <article key={msg.id} className="pdf-finding-card">
                          <header>
                            <AlertTriangle size={15} />
                            Gap analysis
                          </header>
                          <GapPanel data={msg.data} />
                        </article>
                      ))}
                      {findings.diagrams.map((d) => (
                        <article key={d.id} className="pdf-finding-card">
                          <header>
                            <Layers size={15} />
                            Diagram
                          </header>
                          <div className="pdf-figure-block">
                            <Mermaid chart={d.chart} />
                          </div>
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
    </div>
  );
}
