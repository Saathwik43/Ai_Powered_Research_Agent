import React, { useState, useEffect, useRef } from 'react';
import { UploadCloud, FileText, Table, Image as ImageIcon, Link as LinkIcon, Trash2, Plus, Sparkles, CheckCircle2, AlertCircle, RefreshCw } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Spinner } from './Loader';

export default function SourcesPanel({ topic }) {
  const { authFetch } = useAuth();
  const [sources, setSources] = useState([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [dragActive, setDragActive] = useState(false);
  const [urlInput, setUrlInput] = useState('');
  const [mode, setMode] = useState('file'); // 'file' | 'url'
  const fileInputRef = useRef(null);

  const fetchSources = async () => {
    if (!topic || !topic.trim()) {
      setSources([]);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/sources?topic=${encodeURIComponent(topic.trim())}`);
      if (!res.ok) throw new Error('Failed to fetch sources');
      const data = await res.json();
      setSources(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || 'Error loading sources');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSources();
  }, [topic]);

  const handleUploadFile = async (file) => {
    if (!file) return;
    if (!topic || !topic.trim()) {
      setError('Please set a research topic first before uploading sources.');
      return;
    }
    setUploading(true);
    setError('');
    const formData = new FormData();
    formData.append('file', file);
    formData.append('topic', topic.trim());

    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/sources/upload`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Upload failed');
      }
      await fetchSources();
    } catch (err) {
      setError(err.message || 'Error uploading file');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleUploadUrl = async (e) => {
    e.preventDefault();
    if (!urlInput.trim()) return;
    if (!topic || !topic.trim()) {
      setError('Please set a research topic first before adding sources.');
      return;
    }
    setUploading(true);
    setError('');
    const formData = new FormData();
    formData.append('url', urlInput.trim());
    formData.append('topic', topic.trim());

    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/sources/upload`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'URL ingestion failed');
      }
      setUrlInput('');
      await fetchSources();
    } catch (err) {
      setError(err.message || 'Error adding URL source');
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (sourceId) => {
    try {
      const res = await authFetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/sources/${sourceId}`, {
        method: 'DELETE',
      });
      if (!res.ok) throw new Error('Failed to delete source');
      setSources(prev => prev.filter(s => (s.id || s._id) !== sourceId));
    } catch (err) {
      setError(err.message || 'Error deleting source');
    }
  };

  const getSourceIcon = (type, filename) => {
    const t = (type || '').toLowerCase();
    const f = (filename || '').toLowerCase();
    if (t === 'url' || f.startsWith('http')) return <LinkIcon size={18} style={{ color: 'var(--primary)' }} />;
    if (t.includes('csv') || t.includes('json') || f.endsWith('.csv') || f.endsWith('.json')) return <Table size={18} style={{ color: '#10b981' }} />;
    if (t.startsWith('image/') || f.match(/\.(png|jpg|jpeg|webp)$/)) return <ImageIcon size={18} style={{ color: '#ec4899' }} />;
    return <FileText size={18} style={{ color: 'var(--primary)' }} />;
  };

  const getCleanTypeLabel = (type, filename) => {
    const t = (type || '').toLowerCase();
    const f = (filename || '').toLowerCase();
    if (t === 'url' || f.startsWith('http')) return 'URL Link';
    if (f.endsWith('.csv') || t.includes('csv')) return 'CSV Data';
    if (f.endsWith('.json') || t.includes('json')) return 'JSON Data';
    if (f.endsWith('.pdf') || t.includes('pdf')) return 'PDF Document';
    if (t.startsWith('image/')) return 'OCR Image';
    return 'Text File';
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleUploadFile(e.dataTransfer.files[0]);
    }
  };

  return (
    <div className="sources-panel-container" style={{ width: '100%', minHeight: '420px', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      {/* Panel Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--space-3)', paddingBottom: 'var(--space-3)', borderBottom: '1px solid var(--border)' }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <Sparkles size={16} style={{ color: 'var(--primary)' }} /> Ground Truth Sources
          </h3>
          <p style={{ margin: '4px 0 0 0', fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
            Upload raw experiment data (CSV, JSON, PDF, Images) or Web URLs. Numbers in these files are strictly preserved as ground truth.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
          <button
            onClick={() => setMode('file')}
            style={{
              padding: 'var(--space-1) var(--space-3)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border)',
              background: mode === 'file' ? 'var(--primary)' : 'var(--bg-input)',
              color: mode === 'file' ? 'var(--on-primary)' : 'var(--text)',
              fontSize: 'var(--fs-xs)',
              fontWeight: mode === 'file' ? 600 : 400,
              cursor: 'pointer',
              transition: 'var(--transition)'
            }}
          >
            Upload File
          </button>
          <button
            onClick={() => setMode('url')}
            style={{
              padding: 'var(--space-1) var(--space-3)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border)',
              background: mode === 'url' ? 'var(--primary)' : 'var(--bg-input)',
              color: mode === 'url' ? 'var(--on-primary)' : 'var(--text)',
              fontSize: 'var(--fs-xs)',
              fontWeight: mode === 'url' ? 600 : 400,
              cursor: 'pointer',
              transition: 'var(--transition)'
            }}
          >
            Add Web URL
          </button>
        </div>
      </div>

      {error && (
        <div style={{ padding: 'var(--space-3)', background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.2)', borderRadius: 'var(--radius-md)', color: 'var(--danger)', fontSize: 'var(--fs-xs)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
          <AlertCircle size={14} /> {error}
        </div>
      )}

      {/* Upload Box / Input */}
      {mode === 'file' ? (
        <div
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
          style={{
            border: `2px dashed ${dragActive ? 'var(--primary)' : 'var(--border)'}`,
            borderRadius: 'var(--radius-lg)',
            padding: 'var(--space-6) var(--space-4)',
            textAlign: 'center',
            background: dragActive ? 'rgba(0, 87, 255, 0.05)' : 'var(--bg-input)',
            cursor: uploading ? 'not-allowed' : 'pointer',
            transition: 'all 0.2s ease',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 'var(--space-2)'
          }}
        >
          <input
            type="file"
            ref={fileInputRef}
            onChange={(e) => e.target.files?.[0] && handleUploadFile(e.target.files[0])}
            accept=".pdf,.csv,.json,.txt,.md,.png,.jpg,.jpeg"
            style={{ display: 'none' }}
            disabled={uploading}
          />
          {uploading ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', color: 'var(--primary)' }}>
              <Spinner size={20} /> Processing & Extracting Text...
            </div>
          ) : (
            <>
              <UploadCloud size={32} style={{ color: 'var(--primary)', opacity: 0.8 }} />
              <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' }}>
                Drag & drop experiment file here, or <span style={{ color: 'var(--primary)' }}>browse</span>
              </div>
              <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
                Supports PDF, CSV, JSON, TXT, MD, PNG, JPG (OCR)
              </div>
            </>
          )}
        </div>
      ) : (
        <form onSubmit={handleUploadUrl} style={{ display: 'flex', gap: 'var(--space-2)' }}>
          <input
            type="url"
            placeholder="https://example.com/experiment-results or paper URL"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            disabled={uploading}
            style={{
              flex: 1,
              padding: 'var(--space-3)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border)',
              background: 'var(--bg-input)',
              color: 'var(--text)',
              fontSize: 'var(--fs-sm)',
              outline: 'none'
            }}
          />
          <button
            type="submit"
            disabled={uploading || !urlInput.trim()}
            className="btn btn-primary"
            style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', padding: '0 var(--space-4)' }}
          >
            {uploading ? <Spinner size={14} /> : <Plus size={14} />} Add Link
          </button>
        </form>
      )}

      {/* Sources List Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 'var(--space-2)' }}>
        <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-subtle)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Uploaded Sources ({sources.length})
        </span>
        <button onClick={fetchSources} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '4px', fontSize: 'var(--fs-2xs)' }}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Sources Grid / List */}
      {loading ? (
        <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--text-muted)' }}>
          <Spinner size={24} /> Loading sources...
        </div>
      ) : sources.length === 0 ? (
        <div style={{ padding: 'var(--space-6)', textAlign: 'center', background: 'var(--bg-input)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}>
          <p style={{ margin: 0, fontSize: 'var(--fs-sm)' }}>No ground truth sources attached for topic <strong>"{topic || 'Unspecified'}"</strong>.</p>
          <p style={{ margin: 'var(--space-1) 0 0 0', fontSize: 'var(--fs-xs)', opacity: 0.8 }}>Upload your CSV results or experimental notes above to inject un-paraphrased facts into manuscript generation.</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 280px), 1fr))', gap: 'var(--space-3)' }}>
          {sources.map((source) => {
            const sid = source.id || source._id;
            return (
              <div
                key={sid}
                style={{
                  background: 'var(--bg-card)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-md)',
                  padding: 'var(--space-3)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 'var(--space-2)',
                  position: 'relative',
                  transition: 'border-color 0.2s ease, box-shadow 0.2s ease'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 'var(--space-2)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', minWidth: 0 }}>
                    {getSourceIcon(source.type, source.filename)}
                    <span style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={source.filename}>
                      {source.filename}
                    </span>
                  </div>
                  <button
                    onClick={() => handleDelete(sid)}
                    title="Delete source"
                    style={{
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      color: 'var(--text-subtle)',
                      padding: '2px',
                      borderRadius: 'var(--radius-sm)',
                      display: 'flex',
                      alignItems: 'center',
                      transition: 'color 0.2s ease'
                    }}
                    onMouseEnter={e => e.currentTarget.style.color = 'var(--danger)'}
                    onMouseLeave={e => e.currentTarget.style.color = 'var(--text-subtle)'}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>

                <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 'var(--fs-2xs)', background: 'rgba(0, 87, 255, 0.08)', color: 'var(--primary)', padding: '2px 6px', borderRadius: '4px', fontWeight: 600 }}>
                    {getCleanTypeLabel(source.type, source.filename)}
                  </span>
                  <span style={{ fontSize: 'var(--fs-2xs)', background: 'rgba(16, 185, 129, 0.08)', color: '#10b981', padding: '2px 6px', borderRadius: '4px', fontWeight: 600, display: 'inline-flex', alignItems: 'center', gap: '2px' }}>
                    <CheckCircle2 size={10} /> Ground Truth
                  </span>
                </div>

                {source.raw_text && (
                  <div style={{
                    fontSize: 'var(--fs-2xs)',
                    color: 'var(--text-muted)',
                    background: 'var(--bg-input)',
                    padding: 'var(--space-2)',
                    borderRadius: 'var(--radius-sm)',
                    maxHeight: '60px',
                    overflow: 'hidden',
                    fontFamily: source.type?.includes('csv') || source.type?.includes('json') ? 'monospace' : 'inherit',
                    lineHeight: 1.4
                  }}>
                    {source.raw_text.slice(0, 140)}{source.raw_text.length > 140 ? '...' : ''}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
