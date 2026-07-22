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
  const lastValidSvgRef = useRef('');
  const renderTimeoutRef = useRef(null);

  useEffect(() => {
    let isMounted = true;

    if (!chart || !chart.trim()) return;

    // Clean up wrapping markdown codeblock ticks if present
    let cleanChart = chart.replace(/^```(mermaid|xychart-beta|graph)?\n?/, '').replace(/\n?```$/, '').trim();
    if (!cleanChart) return;

    // If it doesn't start with a known mermaid keyword but starts with xychart-beta, ensure keyword
    if (!cleanChart.startsWith('xychart-beta') && !cleanChart.startsWith('graph') && !cleanChart.startsWith('pie') && !cleanChart.startsWith('sequenceDiagram') && !cleanChart.startsWith('gantt') && !cleanChart.startsWith('classDiagram')) {
      if (cleanChart.includes('x-axis') || cleanChart.includes('y-axis')) {
        cleanChart = 'xychart-beta\n' + cleanChart;
      }
    }

    if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current);

    // Debounce to allow streaming token buffer to stabilize (250ms)
    renderTimeoutRef.current = setTimeout(async () => {
      if (!isMounted || !containerRef.current) return;

      const id = `mermaid-${Math.random().toString(36).substring(2, 9)}`;
      
      try {
        // Attempt parse check first
        const isValid = await mermaid.parse(cleanChart).catch(() => false);
        if (!isValid) {
          // Incomplete syntax while streaming — silently keep last valid SVG or wait for next tokens
          return;
        }

        const { svg } = await mermaid.render(id, cleanChart);
        if (isMounted && containerRef.current) {
          lastValidSvgRef.current = svg;
          containerRef.current.innerHTML = svg;
          setError(null);
        }
      } catch (e) {
        // Clean up any stray error SVGs mermaid may have injected into <body>
        document.querySelectorAll(`[id="${id}"]`).forEach(el => el.remove());
        document.querySelectorAll('.error-icon, .mermaid-error').forEach(el => {
          if (!containerRef.current?.contains(el)) el.remove();
        });

        // Do NOT flash error box if we already rendered a valid SVG or syntax is still streaming
        if (isMounted && !lastValidSvgRef.current) {
          // Only log, avoid flashing error state during live stream
          console.debug('Mermaid incomplete syntax during stream');
        }
      }
    }, 250);

    return () => {
      isMounted = false;
      if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current);
    };
  }, [chart]);

  if (error && !lastValidSvgRef.current) {
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
      </details>
    );
  }

  return (
    <div
      ref={containerRef}
      className="mermaid-chart"
      style={{
        display: 'flex',
        justifyContent: 'center',
        margin: '1rem 0',
        minHeight: lastValidSvgRef.current ? 'auto' : '40px',
        alignItems: 'center'
      }}
    />
  );
}
