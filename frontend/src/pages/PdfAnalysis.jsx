import React, { useState, useRef, useEffect } from 'react';
import {
  UploadCloud, FileText, Send, Sparkles, AlertCircle,
  ChevronLeft, ChevronRight, Bot, User, Paperclip, X,
  CheckCircle, AlertTriangle, ArrowRight, Zap, BookOpen, History,
  Minus, Plus
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Spinner, TypingDots } from '../components/Loader';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { ghcolors } from 'react-syntax-highlighter/dist/esm/styles/prism';
import Mermaid from '../components/Mermaid';
import 'katex/dist/katex.min.css';
import './PdfAnalysis.css';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

const SUGGESTIONS = [
  { label: "Main Contribution", prompt: "What's the main contribution of this paper?", icon: "🎯" },
  { label: "Limitations", prompt: "What are the key limitations and weaknesses?", icon: "⚠️" },
  { label: "Research Gaps", prompt: "Identify research gaps and future directions.", icon: "🔬" },
  { label: "Methodology", prompt: "Explain the methodology used in this paper.", icon: "📐" },
  { label: "Key Findings", prompt: "Summarize the key findings and results.", icon: "📊" },
  { label: "Follow-up Work", prompt: "Suggest potential follow-up research directions.", icon: "🚀" },
];

function TypingIndicator() {
  return (
    <div style={{ padding: 'var(--space-1) 0' }}>
      <TypingDots />
    </div>
  );
}

function MessageBubble({ msg }) {
  const isUser = msg.role === 'user';

  const renderContent = () => {
    if (msg.isLoading) return <TypingIndicator />;
    if (msg.error) {
      return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', color: 'var(--danger)' }}>
          <AlertCircle size={14} />
          {msg.content}
        </div>
      );
    }
    if (msg.type === 'gap_analysis' && msg.data) {
      return (
        <div className="pdf-gap-result">
          {msg.data.well_covered?.length > 0 && (
            <div className="pdf-gap-section">
              <div className="pdf-gap-section-label success">
                <CheckCircle size={13} /> Well Covered
              </div>
              <ul>
                {msg.data.well_covered.map((item, i) => <li key={i}>{item}</li>)}
              </ul>
            </div>
          )}
          {msg.data.gaps?.length > 0 && (
            <div className="pdf-gap-section">
              <div className="pdf-gap-section-label danger">
                <AlertTriangle size={13} /> Identified Gaps
              </div>
              <ul>
                {msg.data.gaps.map((item, i) => <li key={i}>{item}</li>)}
              </ul>
            </div>
          )}
          {msg.data.suggested_direction && (
            <div className="pdf-gap-direction">
              <div className="pdf-gap-section-label accent">
                <ArrowRight size={13} /> Suggested Direction
              </div>
              <p>{msg.data.suggested_direction}</p>
            </div>
          )}
        </div>
      );
    }
    return (
      <div className="pdf-markdown-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex]}
          components={{
            a: ({ node, href, children, ...props }) => {
              if (href?.startsWith('#page-')) {
                const pageNum = href.replace('#page-', '');
                return (
                  <a href={href} className="citation-pill" data-ref={`p.${pageNum}`} {...props}>
                    {children}
                  </a>
                );
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
                <SyntaxHighlighter
                  style={ghcolors}
                  language={match[1]}
                  PreTag="div"
                  {...props}
                >
                  {contentStr}
                </SyntaxHighlighter>
              ) : (
                <code className={className} {...props}>
                  {children}
                </code>
              );
            }
          }}
        >
          {msg.content.replace(/\[?(?:Page|Pg\.?)\s*(\d+)\]?/gi, '[Page $1](#page-$1)')}
        </ReactMarkdown>
      </div>
    );
  };

  // TODO: Add citation-click-to-page-jump hook here in the future
  return (
    <div className={`pdf-message ${isUser ? 'user' : 'assistant'}`}>
      {!isUser && (
        <div className="pdf-avatar assistant-avatar">
          <Bot size={15} />
        </div>
      )}
      <div className="pdf-bubble">
        {renderContent()}
      </div>
      {isUser && (
        <div className="pdf-avatar user-avatar">
          <User size={15} />
        </div>
      )}
    </div>
  );
}

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

  const [numPages, setNumPages] = useState(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [zoom, setZoom] = useState(1.2);

  // Chat History States
  const [chatList, setChatList] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [loadingChats, setLoadingChats] = useState(false);

  const chatEndRef = useRef(null);
  const inputRef = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    fetchChatList();
  }, []);

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
        setFile({ name: chat.filename }); // dummy file object for display
        setExtractedText(chat.text);
        setStructure(chat.structure);
        setMessages(chat.messages || []);
        setError('');
      }
    } catch (e) {
      console.error("Failed to load chat", e);
    }
  };

  const deleteChat = async (chatId, e) => {
    e.stopPropagation();
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/pdf-chats/${chatId}`, { method: 'DELETE' });
      if (res.ok) {
        if (activeChatId === chatId) {
          reset();
        }
        fetchChatList();
      }
    } catch (err) {
      console.error("Failed to delete chat", err);
    }
  };

  const saveChatState = async (newMessages, currentFile, text, struct) => {
    if (!text || newMessages.length === 0) return;
    try {
      const payload = {
        chat_id: activeChatId,
        filename: currentFile?.name || "Unknown PDF",
        text: text,
        structure: struct,
        messages: newMessages
      };
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/pdf-chats/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        const data = await res.json();
        if (!activeChatId) {
          setActiveChatId(data.chat_id);
        }
        fetchChatList();
      }
    } catch (e) {
      console.error("Failed to save chat", e);
    }
  };

  const handleFileChange = async (selected) => {
    if (!selected) return;
    if (selected.type !== 'application/pdf') {
      setError('Please upload a valid PDF file.');
      return;
    }

    setFile(selected);
    setError('');
    setExtractedText('');
    setStructure(null);
    setMessages([]);
    setCustomPrompt('');
    setIsExtracting(true);
    setActiveChatId(null);

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

      const initMsgs = [{
        id: Date.now(),
        role: 'assistant',
        type: 'text',
        content: `✅ **"${selected.name}"** has been uploaded and processed successfully!\n\nI've extracted the text and I'm ready to answer your questions. Try one of the suggestions below, or ask me anything about this paper.`,
      }];
      setMessages(initMsgs);
      await saveChatState(initMsgs, selected, data.text, data.structure);
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

    const userMsg = {
      id: Date.now(),
      role: 'user',
      type: 'text',
      content: finalPrompt,
    };
    const loadingMsg = {
      id: Date.now() + 1,
      role: 'assistant',
      type: 'text',
      isLoading: true,
      content: '',
    };

    setMessages(prev => [...prev, userMsg, loadingMsg]);
    if (promptOverride === null) setCustomPrompt('');
    setIsAnalyzing(true);

    try {
      const formData = new FormData();
      formData.append('text', extractedText);
      if (finalPrompt) formData.append('custom_prompt', finalPrompt);
      if (structure) formData.append('structure', JSON.stringify(structure));
      if (activeChatId) formData.append('chat_id', activeChatId);

      const history = messages
        .filter(m => m.type === 'text' && !m.isLoading)
        .map(m => ({ role: m.role, content: m.content }));
      
      formData.append('history', JSON.stringify(history));

      const res = await authFetch(
        `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript/analyze-pdf`,
        {
          method: 'POST',
          body: formData,
        }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Failed to analyze PDF');
      }
      const data = await res.json();

      setMessages(prev => {
        const updated = prev.map(m =>
          m.id === loadingMsg.id
            ? {
                ...m,
                isLoading: false,
                type: data.type,
                content: data.type === 'custom' ? data.content : '',
                data: data.type !== 'custom' ? data.data : undefined,
              }
            : m
        );
        saveChatState(updated, file, extractedText, structure);
        return updated;
      });
    } catch (err) {
      setMessages(prev => {
        const updated = prev.map(m =>
          m.id === loadingMsg.id
            ? { ...m, isLoading: false, error: true, content: err.message || 'Analysis failed. Please try again.' }
            : m
        );
        saveChatState(updated, file, extractedText, structure);
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
    setExtractedText('');
    setStructure(null);
    setMessages([]);
    setError('');
    setActiveChatId(null);
  };

  const renderContent = () => {
    return (
      <div className="pdf-split-view">
        {/* PDF Viewer Pane */}
        {(file || extractedText) && (
          <div className="pdf-viewer-pane">
            {file.size ? (
               <div className="pdf-viewer-scroll">
                 <Document 
                    file={file} 
                    onLoadSuccess={({ numPages }) => { setNumPages(numPages); setPageNumber(1); }} 
                    loading={<div style={{padding: 'var(--space-6)', textAlign: 'center'}}><Spinner size={24} /></div>}
                 >
                   <div style={{ transform: `scale(${1 / pixelRatio})`, transformOrigin: 'top left', width: `${100 * pixelRatio}%` }}>
                     <Page 
                        pageNumber={pageNumber} 
                        renderTextLayer={true} 
                        renderAnnotationLayer={true} 
                        scale={zoom * pixelRatio} 
                     />
                   </div>
                 </Document>
               </div>
            ) : (
               <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-subtle)' }}>
                 <p>Preview unavailable for loaded chats.<br/>(PDF file not stored in browser)</p>
               </div>
            )}
            {file.size && numPages && (
              <div className="pdf-viewer-controls">
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                  <button className="btn btn-secondary btn-icon" title="Zoom Out" onClick={() => setZoom(z => Math.max(0.5, z - 0.2))}><Minus size={16} /></button>
                  <span style={{ minWidth: '3.5rem', textAlign: 'center' }}>{Math.round(zoom * 100)}%</span>
                  <button className="btn btn-secondary btn-icon" title="Zoom In" onClick={() => setZoom(z => Math.min(3.0, z + 0.2))}><Plus size={16} /></button>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                  <button className="btn btn-secondary btn-icon" disabled={pageNumber <= 1} onClick={() => setPageNumber(p => p - 1)}><ChevronLeft size={16} /></button>
                  <span>Page {pageNumber} of {numPages}</span>
                  <button className="btn btn-secondary btn-icon" disabled={pageNumber >= numPages} onClick={() => setPageNumber(p => p + 1)}><ChevronRight size={16} /></button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Chat Pane */}
        <div className="pdf-chat-container">
        <div className="pdf-chat-header">
          <div className="pdf-chat-title" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
            <button 
              className={`pdf-history-toggle ${!historyCollapsed ? 'desktop-hide' : ''}`}
              onClick={() => setHistoryCollapsed(false)}
              title="Open Chat History"
            >
              <History size={20} />
            </button>
            <Bot size={20} style={{ color: 'var(--primary)', flexShrink: 0 }} />
            <span className="pdf-chat-title-text">PDF Assistant</span>
          </div>
          {file && (
            <div className="pdf-file-badge">
              <FileText size={14} />
              {file.name}
              <button onClick={() => setFile(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', marginLeft: 'var(--space-1)', color: 'currentColor' }}>
                <X size={14} />
              </button>
            </div>
          )}
        </div>

        <div className="pdf-messages-area">
          {isExtracting ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-subtle)' }}>
              <Spinner size={48} />
              <h2 style={{ marginTop: 'var(--space-5)', fontWeight: 600, color: 'var(--text)' }}>Extracting Document Text...</h2>
              <p>This may take a few seconds.</p>
            </div>
          ) : (!file && !extractedText) ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: 1, width: '100%' }}>
              <div style={{ textAlign: 'center', marginBottom: 'var(--space-5)' }}>
                <div style={{ display: 'inline-flex', padding: 'var(--space-3)', background: 'var(--primary-light)', borderRadius: '50%', color: 'var(--primary)', marginBottom: 'var(--space-3)' }}>
                  <FileText size={32} />
                </div>
                <h1 style={{ fontSize: 'var(--fs-lg)', fontWeight: 800, margin: '0 0 var(--space-2)' }}>Chat with your PDF</h1>
                <p style={{ color: 'var(--text-subtle)', fontSize: 'var(--fs-sm)', maxWidth: '350px', margin: '0 auto' }}>Upload a research paper to extract insights, find gaps, and summarize methodology.</p>
              </div>

              {error && (
                <div style={{ color: 'var(--danger)', marginBottom: 'var(--space-4)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                  <AlertCircle size={16} /> {error}
                </div>
              )}

              <div
                className={`pdf-dropzone ${isDragging ? 'dragging' : ''}`}
                onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setIsDragging(false);
                  const files = e.dataTransfer.files;
                  if (files.length) handleFileChange(files[0]);
                }}
                onClick={() => fileInputRef.current?.click()}
                style={{ maxWidth: '400px', padding: 'var(--space-6) var(--space-5)' }}
              >
                <UploadCloud size={32} className="pdf-dropzone-icon" style={{ marginBottom: 'var(--space-3)' }} />
                <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 600, margin: '0 0 var(--space-1)' }}>Drop your PDF here</h3>
                <p style={{ color: 'var(--text-subtle)', fontSize: 'var(--fs-sm)', margin: 0 }}>or click to browse from your computer</p>
              </div>
            </div>
          ) : (
            <>
              {messages.map(msg => (
                <MessageBubble key={msg.id} msg={msg} />
              ))}
              
              {messages.length === 1 && (
                <div className="pdf-suggestions">
                  {SUGGESTIONS.map(s => (
                    <button key={s.label} className="pdf-suggestion-chip" onClick={() => runAnalysis(s.prompt)}>
                      <span>{s.icon}</span>
                      <span style={{ fontWeight: 500 }}>{s.label}</span>
                    </button>
                  ))}
                </div>
              )}
              <div ref={chatEndRef} style={{ height: 10 }} />
            </>
          )}
        </div>

        <div className="pdf-input-container">
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf"
            style={{ display: 'none' }}
            onChange={e => handleFileChange(e.target.files?.[0])}
          />
          <div className="pdf-input-wrapper">
            <button 
               className="btn btn-icon"
               style={{ border: 'none', background: 'transparent', color: 'var(--text-subtle)' }}
               title="Attach PDF"
               onClick={() => fileInputRef.current?.click()}
            >
              <Paperclip size={20} />
            </button>
            <textarea 
              ref={inputRef}
              className="pdf-textarea"
              placeholder={(!file && !extractedText) ? "Please upload a PDF first..." : "Ask a question about this paper..."}
              value={customPrompt}
              onChange={e => setCustomPrompt(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={1}
              disabled={isAnalyzing || (!file && !extractedText) || isExtracting}
            />
            <button 
              className="pdf-send-btn"
              onClick={() => runAnalysis()}
              disabled={!customPrompt.trim() || isAnalyzing || (!file && !extractedText) || isExtracting}
            >
              {isAnalyzing ? <Spinner size={16} /> : <Send size={16} />}
            </button>
          </div>
        </div>
      </div>
      </div>
    );
  };

  return (
    <div className="animate-fade-in pdf-analysis-layout">
      {/* Mobile Backdrop Overlay */}
      <div 
        className={`pdf-sidebar-overlay ${!historyCollapsed ? 'visible' : ''}`}
        onClick={() => setHistoryCollapsed(true)}
      ></div>

      {/* History Sidebar */}
      <div className={`pdf-history-sidebar ${historyCollapsed ? 'collapsed' : ''}`}>
        <div className="pdf-history-header">
          <button className="btn btn-primary" onClick={reset} style={{ padding: 'var(--space-2) var(--space-3)', fontSize: 'var(--fs-sm)' }}>
            + New Chat
          </button>
          <button 
            className="pdf-history-toggle" 
            onClick={() => setHistoryCollapsed(true)}
          >
            <ChevronLeft size={18}/>
          </button>
        </div>
        
        {!historyCollapsed && (
          <div className="pdf-history-list">
            {loadingChats ? (
              <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-4)' }}><Spinner size={16}/></div>
            ) : chatList.length === 0 ? (
              <div style={{ padding: 'var(--space-4)', textAlign: 'center', color: 'var(--text-subtle)', fontSize: 'var(--fs-sm)' }}>No past chats found.</div>
            ) : (
              chatList.map(chat => (
                <div 
                  key={chat.chat_id} 
                  className={`pdf-history-item ${activeChatId === chat.chat_id ? 'active' : ''}`}
                  onClick={() => loadChat(chat.chat_id)}
                >
                  <div className="pdf-history-item-content">
                    <FileText size={14} style={{ color: activeChatId === chat.chat_id ? 'var(--primary)' : 'var(--text-subtle)' }} />
                    <span className="pdf-history-item-text">{chat.filename}</span>
                  </div>
                  <button className="pdf-history-item-delete" onClick={(e) => deleteChat(chat.chat_id, e)}>
                    <X size={14} />
                  </button>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {renderContent()}
    </div>
  );
}
