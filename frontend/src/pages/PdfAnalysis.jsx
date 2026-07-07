import React, { useState, useRef, useEffect } from 'react';
import {
  UploadCloud, FileText, Send, Sparkles, AlertCircle,
  ChevronLeft, ChevronRight, Bot, User, Paperclip, X,
  CheckCircle, AlertTriangle, ArrowRight, Zap, BookOpen
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Spinner, TypingDots } from '../components/Loader';
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
    <div style={{ padding: '0.25rem 0' }}>
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
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--danger)' }}>
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
    return <div>{msg.content}</div>;
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

      setMessages([{
        id: Date.now(),
        role: 'assistant',
        type: 'text',
        content: `✅ **"${selected.name}"** has been uploaded and processed successfully!\n\nI've extracted the text and I'm ready to answer your questions. Try one of the suggestions below, or ask me anything about this paper.`,
      }]);
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
      if (file) formData.append('file', file);

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
      runAnalysis();
    }
  };

  const reset = () => {
    setFile(null);
    setExtractedText('');
    setMessages([]);
    setError('');
  };

  if (isExtracting) {
    return (
      <div className="pdf-upload-screen">
        <Spinner size={48} />
        <h2 style={{ marginTop: '1.5rem', fontWeight: 600 }}>Extracting Document Text...</h2>
        <p style={{ color: 'var(--text-subtle)' }}>This may take a few seconds.</p>
      </div>
    );
  }

  if (!file && !extractedText) {
    return (
      <div className="pdf-upload-screen">
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <div style={{ display: 'inline-flex', padding: '1rem', background: 'var(--primary-light)', borderRadius: '50%', color: 'var(--primary)', marginBottom: '1rem' }}>
            <FileText size={48} />
          </div>
          <h1 style={{ fontSize: '2rem', fontWeight: 800, margin: '0 0 0.5rem' }}>Chat with your PDF</h1>
          <p style={{ color: 'var(--text-subtle)' }}>Upload a research paper to extract insights, find gaps, and summarize methodology.</p>
        </div>

        {error && (
          <div style={{ color: 'var(--danger)', marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
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
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf"
            style={{ display: 'none' }}
            onChange={e => handleFileChange(e.target.files?.[0])}
          />
          <UploadCloud size={48} className="pdf-dropzone-icon" />
          <h3 style={{ fontSize: '1.2rem', fontWeight: 600, margin: '0 0 0.5rem' }}>Drop your PDF here</h3>
          <p style={{ color: 'var(--text-subtle)', fontSize: '0.9rem', margin: 0 }}>or click to browse from your computer</p>
        </div>
      </div>
    );
  }

  return (
    <div className="pdf-chat-container">
      <div className="pdf-chat-header">
        <div className="pdf-chat-title">
          <Bot size={20} style={{ color: 'var(--primary)' }} />
          PDF Assistant
        </div>
        {file && (
          <div className="pdf-file-badge">
            <FileText size={14} />
            {file.name}
            <button onClick={reset} style={{ background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', marginLeft: '0.25rem', color: 'currentColor' }}>
              <X size={14} />
            </button>
          </div>
        )}
      </div>

      <div className="pdf-messages-area">
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
      </div>

      <div className="pdf-input-container">
        <div className="pdf-input-wrapper">
          <textarea 
            ref={inputRef}
            className="pdf-textarea"
            placeholder="Ask a question about this paper..."
            value={customPrompt}
            onChange={e => setCustomPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            disabled={isAnalyzing}
          />
          <button 
            className="pdf-send-btn"
            onClick={() => runAnalysis()}
            disabled={!customPrompt.trim() || isAnalyzing}
          >
            {isAnalyzing ? <Spinner size={16} /> : <Send size={16} />}
          </button>
        </div>
      </div>
    </div>
  );
}
