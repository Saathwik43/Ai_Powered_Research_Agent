import React, { useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';

mermaid.initialize({
  startOnLoad: false,
  theme: 'default',
  securityLevel: 'loose',
  suppressErrorRendering: true,
});

export default function Mermaid({ chart }) {
  const containerRef = useRef(null);
  const [error, setError] = useState(null);
  const renderTimeoutRef = useRef(null);

  useEffect(() => {
    let isMounted = true;
    setError(null);

    if (!chart || !chart.trim()) return;

    // Debounce rendering during streaming — wait 300ms of stability
    // before attempting to render (avoids partial-syntax crashes)
    if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current);

    renderTimeoutRef.current = setTimeout(async () => {
      if (!isMounted || !containerRef.current) return;

      // Pre-validate syntax before rendering
      try {
        await mermaid.parse(chart);
      } catch (parseErr) {
        if (isMounted) {
          setError(parseErr?.message || 'Diagram syntax error');
          if (containerRef.current) containerRef.current.innerHTML = '';
        }
        return;
      }

      const id = `mermaid-${Math.random().toString(36).substring(2, 9)}`;
      try {
        const { svg } = await mermaid.render(id, chart);
        if (isMounted && containerRef.current) {
          containerRef.current.innerHTML = svg;
          setError(null);
        }
      } catch (e) {
        console.error('Mermaid render error:', e);
        if (isMounted) {
          setError(e?.message || 'Failed to render diagram');
          if (containerRef.current) containerRef.current.innerHTML = '';
        }
        // Clean up any stray error SVGs mermaid may have injected into <body>
        document.querySelectorAll(`[id="${id}"]`).forEach(el => el.remove());
        document.querySelectorAll('.error-icon, .mermaid-error').forEach(el => {
          if (!containerRef.current?.contains(el)) el.remove();
        });
      }
    }, 300);

    return () => {
      isMounted = false;
      if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current);
    };
  }, [chart]);

  if (error) {
    return (
      <details style={{
        margin: '1rem 0', padding: '0.75rem 1rem',
        background: 'rgba(229,28,35,0.06)', border: '1px solid rgba(229,28,35,0.2)',
        borderRadius: 'var(--radius-md)', fontSize: 'var(--fs-sm)', color: 'var(--text-muted)',
      }}>
        <summary style={{ cursor: 'pointer', fontWeight: 500, color: 'var(--danger)' }}>
          ⚠ Diagram could not be rendered
        </summary>
        <pre style={{ marginTop: '0.5rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 'var(--fs-xs)', color: 'var(--text-subtle)' }}>
          {error}
        </pre>
        <pre style={{ marginTop: '0.5rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 'var(--fs-xs)', color: 'var(--text-subtle)', background: 'var(--bg-elevated)', padding: '0.5rem', borderRadius: '4px' }}>
          {chart}
        </pre>
      </details>
    );
  }

  return <div ref={containerRef} className="mermaid-chart" style={{ display: 'flex', justifyContent: 'center', margin: '1rem 0' }} />;
}
