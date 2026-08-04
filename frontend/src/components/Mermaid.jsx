import React, { useEffect, useId, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { Maximize2, X, ZoomIn, ZoomOut, RotateCcw } from 'lucide-react';
import {
  sanitizeMermaidChart,
  normalizeMermaidSvg,
} from '../utils/mermaidChart';

const svgCache = new Map();
let mermaidModule = null;
let initialized = false;
let renderChain = Promise.resolve();

async function loadMermaid() {
  if (!mermaidModule) {
    mermaidModule = (await import('mermaid')).default;
  }
  return mermaidModule;
}

async function ensureMermaidInit() {
  const mermaid = await loadMermaid();
  if (initialized) return mermaid;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: 'loose',
    suppressErrorRendering: true,
    flowchart: {
      htmlLabels: false,
      curve: 'basis',
      padding: 16,
      nodeSpacing: 50,
      rankSpacing: 55,
      useMaxWidth: false,
    },
    sequence: { useMaxWidth: false, actorMargin: 50 },
    gantt: { useMaxWidth: false },
    pie: { useMaxWidth: false, textPosition: 0.75 },
    class: { useMaxWidth: false },
    state: { useMaxWidth: false },
    er: { useMaxWidth: false },
    journey: { useMaxWidth: false },
    timeline: { useMaxWidth: false },
    mindmap: { useMaxWidth: false },
    quadrantChart: { useMaxWidth: false },
    xyChart: { useMaxWidth: false },
    theme: 'base',
    themeVariables: {
      fontFamily: "'IBM Plex Sans', 'Source Serif 4', Georgia, sans-serif",
      fontSize: '14px',
      primaryColor: '#E8F0FA',
      primaryTextColor: '#0F1115',
      primaryBorderColor: '#2B5EA8',
      secondaryColor: '#F7E8DE',
      secondaryTextColor: '#0F1115',
      secondaryBorderColor: '#C9622A',
      tertiaryColor: '#E4F0E9',
      tertiaryTextColor: '#0F1115',
      tertiaryBorderColor: '#4F8F6B',
      lineColor: '#374151',
      textColor: '#0F1115',
      mainBkg: '#FFFFFF',
      nodeBorder: '#2B5EA8',
      clusterBkg: '#FAFAF8',
      clusterBorder: '#C9C4B8',
      titleColor: '#0F1115',
      edgeLabelBackground: '#FFFFFF',
      pie1: '#2B5EA8',
      pie2: '#C9622A',
      pie3: '#2E7D32',
      pie4: '#D97706',
      pie5: '#6D28D9',
      pie6: '#0284C7',
      pie7: '#DC2626',
      xyChart: {
        backgroundColor: '#FFFFFF',
        titleColor: '#0F1115',
        xAxisTitleColor: '#0F1115',
        xAxisLabelColor: '#0F1115',
        xAxisLineColor: '#0F1115',
        yAxisTitleColor: '#0F1115',
        yAxisLabelColor: '#0F1115',
        yAxisLineColor: '#0F1115',
        plotColorPalette: '#2B5EA8, #C9622A, #2E7D32, #D97706, #6D28D9, #0284C7, #DC2626',
      },
    },
  });
  initialized = true;
  return mermaid;
}

function enqueueRender(task) {
  const next = renderChain.then(task, task);
  renderChain = next.catch(() => {});
  return next;
}

function cleanupStrayMermaid(id) {
  document.querySelectorAll(`[id^="${id}"]`).forEach((el) => el.remove());
}

function DiagramLightbox({ svgHtml, onClose }) {
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
      if (e.key === '+' || e.key === '=') setZoom((z) => Math.min(3, z + 0.2));
      if (e.key === '-') setZoom((z) => Math.max(0.4, z - 0.2));
    };
    document.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return createPortal(
    <div className="mermaid-lightbox" role="dialog" aria-modal="true" aria-label="Diagram preview">
      <button type="button" className="mermaid-lightbox-backdrop" onClick={onClose} aria-label="Close preview" />
      <div className="mermaid-lightbox-panel">
        <header className="mermaid-lightbox-toolbar">
          <span className="mermaid-lightbox-title">Diagram preview</span>
          <div className="mermaid-lightbox-actions">
            <button type="button" className="mermaid-lightbox-btn" onClick={() => setZoom((z) => Math.max(0.4, z - 0.2))} title="Zoom out">
              <ZoomOut size={16} />
            </button>
            <span className="mermaid-lightbox-zoom">{Math.round(zoom * 100)}%</span>
            <button type="button" className="mermaid-lightbox-btn" onClick={() => setZoom((z) => Math.min(3, z + 0.2))} title="Zoom in">
              <ZoomIn size={16} />
            </button>
            <button type="button" className="mermaid-lightbox-btn" onClick={() => setZoom(1)} title="Reset zoom">
              <RotateCcw size={15} />
            </button>
            <button type="button" className="mermaid-lightbox-btn" onClick={onClose} title="Close">
              <X size={16} />
            </button>
          </div>
        </header>
        <div className="mermaid-lightbox-canvas">
          <div
            className="mermaid-lightbox-svg"
            style={{ transform: `scale(${zoom})`, transformOrigin: 'top center' }}
            dangerouslySetInnerHTML={{ __html: svgHtml }}
          />
        </div>
      </div>
    </div>,
    document.body
  );
}

export default function Mermaid({ chart }) {
  const reactId = useId().replace(/:/g, '');
  const containerRef = useRef(null);
  const [error, setError] = useState(null);
  const [ready, setReady] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [svgHtml, setSvgHtml] = useState('');
  const lastValidSvgRef = useRef('');
  const renderTimeoutRef = useRef(null);

  const openPreview = useCallback(() => {
    if (lastValidSvgRef.current) setPreviewOpen(true);
  }, []);

  useEffect(() => {
    let isMounted = true;

    const cleanChart = sanitizeMermaidChart(chart);
    if (!cleanChart) return undefined;

    const cacheKey = cleanChart;
    if (svgCache.has(cacheKey)) {
      const cached = svgCache.get(cacheKey);
      lastValidSvgRef.current = cached;
      setSvgHtml(cached);
      if (containerRef.current) {
        containerRef.current.innerHTML = cached;
        setReady(true);
        setError(null);
      }
      return undefined;
    }

    if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current);

    renderTimeoutRef.current = setTimeout(() => {
      enqueueRender(async () => {
        if (!isMounted || !containerRef.current) return;
        const id = `mmd-${reactId}-${Math.random().toString(36).slice(2, 9)}`;

        try {
          const mermaid = await ensureMermaidInit();
          await mermaid.parse(cleanChart);
          const { svg } = await mermaid.render(id, cleanChart);
          const fixed = normalizeMermaidSvg(svg);
          if (!fixed || fixed.length < 40) throw new Error('Empty diagram SVG');

          if (isMounted && containerRef.current) {
            lastValidSvgRef.current = fixed;
            svgCache.set(cacheKey, fixed);
            setSvgHtml(fixed);
            containerRef.current.innerHTML = fixed;
            setReady(true);
            setError(null);
          }
        } catch (e) {
          cleanupStrayMermaid(id);
          if (isMounted && !lastValidSvgRef.current) {
            setError(e?.str || e?.message || 'Diagram could not be rendered');
            setReady(false);
          }
        }
      });
    }, 180);

    return () => {
      isMounted = false;
      if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current);
    };
  }, [chart, reactId]);

  if (error && !ready && !lastValidSvgRef.current) {
    return (
      <details className="mermaid-error-box">
        <summary>Diagram could not be rendered</summary>
        <pre>{error}</pre>
        <pre className="mermaid-error-source">{sanitizeMermaidChart(chart)}</pre>
      </details>
    );
  }

  return (
    <>
      <figure className={`mermaid-figure${ready || lastValidSvgRef.current ? ' is-ready' : ''}`}>
        <div
          className="mermaid-chart-scroll"
          onClick={openPreview}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              openPreview();
            }
          }}
          role="button"
          tabIndex={0}
          aria-label="Open diagram preview"
          title="Click to enlarge"
        >
          <div ref={containerRef} className="mermaid-chart" />
        </div>
        {(ready || lastValidSvgRef.current) && (
          <button type="button" className="mermaid-expand-btn" onClick={openPreview} title="Enlarge diagram">
            <Maximize2 size={14} />
            <span>Expand</span>
          </button>
        )}
      </figure>

      {previewOpen && svgHtml && (
        <DiagramLightbox svgHtml={svgHtml} onClose={() => setPreviewOpen(false)} />
      )}
    </>
  );
}
