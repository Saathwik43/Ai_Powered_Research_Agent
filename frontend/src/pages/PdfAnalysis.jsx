import React, { useState, useRef, useEffect } from 'react';
import {
  UploadCloud, FileText, Send, Sparkles, AlertCircle,
  ChevronLeft, ChevronRight, Bot, User, Paperclip, X,
  CheckCircle, AlertTriangle, ArrowRight, Zap, BookOpen
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Player } from '@lottiefiles/react-lottie-player';
import loadingAnimation from '../assets/groovyWalk.json';
import './PdfAnalysis.css';

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
    <div className="pdf-typing-indicator">
      <span /><span /><span />
    </div>
  );
}

function MessageBubble({ msg }) {
  const isUser = msg.role === 'user';

  const renderContent = () => {
    if (msg.isLoading) return <TypingIndicator />;
    if (msg.error) {
      return (
        <div className="pdf-msg-error">
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
    return <div className="pdf-msg-text">{msg.content}</div>;
  };

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
  const { authFetch } = useAuth();
  const [file, setFile] = useState(null);
  const [extractedText, setExtractedText] = useState('');
  const [isExtracting, setIsExtracting] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState('');
  const [customPrompt, setCustomPrompt] = useState('');
  const [messages, setMessages] = useState([]);
  const [docPanelOpen, setDocPanelOpen] = useState(true);
  const [isDragging, setIsDragging] = useState(false);

  const chatEndRef = useRef(null);
  const inputRef = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleFileChange = async (selected) => {
    if (!selected) return;
    if (selected.type !== 'application/pdf') {
      setError('Please upload a valid PDF file.');
      return;
    }

    setFile(selected);
    setError('');
    setExtractedText('');
    setMessages([]);
    setCustomPrompt('');
    setIsExtracting(true);

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

      // Welcome message after extraction
      setMessages([{
        id: Date.now(),
        role: 'assistant',
        type: 'text',
        content: `✅ **"${selected.name}"** has been uploaded and processed successfully!\n\nI've extracted the text and I'm ready to answer your questions. Try one of the suggestions below, or ask me anything about this paper.`,
      }]);
    } catch (err) {
      setError(err.message || 'Error extracting PDF text. Please try again.');
    } finally {
      setIsExtracting(false);
    }
  };

  const handleInputFileChange = (e) => handleFileChange(e.target.files?.[0]);

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    handleFileChange(e.dataTransfer.files?.[0]);
  };

  const runAnalysis = async (promptOverride = null) => {
    const finalPrompt = promptOverride !== null ? promptOverride : customPrompt;
    if (!extractedText) return;

    const userMsg = {
      id: Date.now(),
      role: 'user',
      type: 'text',
      content: finalPrompt || '🔍 Run default gap analysis',
    };
    const loadingMsg = {
      id: Date.now() + 1,
      role: 'assistant',
      type: 'text',
      isLoading: true,
      content: '',
    };

    setMessages(prev => [...prev, userMsg, loadingMsg]);
    setCustomPrompt('');
    setIsAnalyzing(true);
    setError('');

    try {
      const res = await authFetch(
        `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/manuscript/analyze-pdf`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: extractedText, custom_prompt: finalPrompt || null }),
        }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Failed to analyze PDF');
      }
      const data = await res.json();

      setMessages(prev => prev.map(m =>
        m.id === loadingMsg.id
          ? {
              ...m,
              isLoading: false,
              type: data.type,
              content: data.type === 'custom' ? data.content : '',
              data: data.type !== 'custom' ? data.data : undefined,
            }
          : m
      ));
    } catch (err) {
      setMessages(prev => prev.map(m =>
        m.id === loadingMsg.id
          ? { ...m, isLoading: false, error: true, content: err.message || 'Analysis failed. Please try again.' }
          : m
      ));
    } finally {
      setIsAnalyzing(false);
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (customPrompt.trim()) runAnalysis();
    }
  };

  // ── UPLOAD SCREEN ──────────────────────────────────────────
  if (!file && !isExtracting) {
    return (
      <div className="pdf-upload-screen animate-fade-in">
        <div className="pdf-upload-header">
          <div className="pdf-upload-icon">
            <BookOpen size={32} />
          </div>
          <h1>PDF Analysis</h1>
          <p>Upload a research paper to extract insights and have an AI-powered conversation about it.</p>
        </div>

        {error && (
          <div className="pdf-error-banner">
            <AlertCircle size={14} /> {error}
          </div>
        )}

        <div
          className={`pdf-dropzone ${isDragging ? 'dragging' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf"
            style={{ display: 'none' }}
            onChange={handleInputFileChange}
          />
          <UploadCloud size={40} className="pdf-dropzone-icon" />
          <p className="pdf-dropzone-title">Drop your PDF here</p>
          <p className="pdf-dropzone-sub">or click to browse files</p>
          <div className="pdf-dropzone-badge">PDF up to 10MB</div>
        </div>

        <div className="pdf-suggestion-preview">
          <p className="pdf-suggestion-preview-label">What you can ask:</p>
          <div className="pdf-suggestion-chips-preview">
            {SUGGESTIONS.map((s, i) => (
              <span key={i} className="pdf-chip-preview">
                {s.icon} {s.label}
              </span>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ── EXTRACTING SCREEN ──────────────────────────────────────
  if (isExtracting) {
    return (
      <div className="pdf-upload-screen animate-fade-in">
        <div className="pdf-extracting">
          <Player autoplay loop src={loadingAnimation} style={{ height: 120, width: 120 }} />
          <h2>Processing PDF…</h2>
          <p>Extracting text from <strong>{file?.name}</strong></p>
        </div>
      </div>
    );
  }

  // ── CHAT SCREEN ──────────────────────────────────────────
  return (
    <div className="pdf-chat-root animate-fade-in">

      {/* ── Document panel ── */}
      <div className={`pdf-doc-panel ${docPanelOpen ? 'open' : 'closed'}`}>
        <div className="pdf-doc-header">
          <div className="pdf-doc-title">
            <FileText size={15} />
            <span title={file?.name}>{file?.name}</span>
          </div>
          <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
            <label className="pdf-change-btn" title="Change PDF">
              <UploadCloud size={13} />
              <span>Change</span>
              <input type="file" accept="application/pdf" style={{ display: 'none' }} onChange={handleInputFileChange} />
            </label>
            <button className="pdf-panel-toggle" onClick={() => setDocPanelOpen(o => !o)} title="Collapse panel">
              <ChevronLeft size={15} />
            </button>
          </div>
        </div>
        <div className="pdf-doc-body">
          <pre className="pdf-extracted-text">{extractedText}</pre>
        </div>
      </div>

      {/* ── Collapsed tab ── */}
      {!docPanelOpen && (
        <button className="pdf-panel-reopen" onClick={() => setDocPanelOpen(true)} title="Open document panel">
          <ChevronRight size={15} />
          <FileText size={14} />
        </button>
      )}

      {/* ── Chat area ── */}
      <div className="pdf-chat-area">

        {/* Header */}
        <div className="pdf-chat-header">
          <div className="pdf-chat-header-left">
            <div className="pdf-chat-avatar-sm">
              <Sparkles size={14} />
            </div>
            <div>
              <div className="pdf-chat-model-name">Research AI</div>
              <div className="pdf-chat-model-sub">Powered by LLM analysis</div>
            </div>
          </div>
          {error && (
            <div className="pdf-error-inline">
              <AlertCircle size={13} /> {error}
            </div>
          )}
        </div>

        {/* Messages */}
        <div className="pdf-messages-scroll">
          {messages.length === 0 && (
            <div className="pdf-empty-chat">
              <Sparkles size={28} />
              <p>Ask anything about your paper</p>
            </div>
          )}
          {messages.map(msg => (
            <MessageBubble key={msg.id} msg={msg} />
          ))}
          <div ref={chatEndRef} />
        </div>

        {/* Suggestions */}
        {extractedText && !isAnalyzing && (
          <div className="pdf-suggestions-bar">
            {SUGGESTIONS.map((s, i) => (
              <button
                key={i}
                className="pdf-suggestion-pill"
                onClick={() => runAnalysis(s.prompt)}
                disabled={isAnalyzing}
              >
                <span>{s.icon}</span> {s.label}
              </button>
            ))}
          </div>
        )}

        {/* Input bar */}
        <div className="pdf-input-bar">
          <label className="pdf-attach-btn" title="Change PDF">
            <Paperclip size={17} />
            <input type="file" accept="application/pdf" style={{ display: 'none' }} onChange={handleInputFileChange} />
          </label>
          <textarea
            ref={inputRef}
            className="pdf-input-textarea"
            value={customPrompt}
            onChange={e => setCustomPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question about the paper…"
            disabled={isAnalyzing || !extractedText}
            rows={1}
          />
          <button
            className={`pdf-send-btn ${customPrompt.trim() ? 'active' : ''}`}
            onClick={() => runAnalysis()}
            disabled={isAnalyzing || !customPrompt.trim() || !extractedText}
            title="Send"
          >
            {isAnalyzing ? (
              <span className="pdf-send-spinner" />
            ) : (
              <Send size={16} />
            )}
          </button>
        </div>
        <p className="pdf-input-hint">Press Enter to send · Shift+Enter for newline</p>
      </div>
    </div>
  );
}
