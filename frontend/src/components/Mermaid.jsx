import React, { useEffect, useRef } from 'react';
import mermaid from 'mermaid';

mermaid.initialize({
  startOnLoad: false,
  theme: 'default',
  securityLevel: 'loose',
});

export default function Mermaid({ chart }) {
  const containerRef = useRef(null);

  useEffect(() => {
    let isMounted = true;
    
    if (containerRef.current && chart) {
      const id = `mermaid-${Math.random().toString(36).substring(2, 9)}`;
      mermaid.render(id, chart)
        .then(({ svg }) => {
          if (isMounted && containerRef.current) {
            containerRef.current.innerHTML = svg;
          }
        })
        .catch(e => {
          console.error('Mermaid render error:', e);
          if (isMounted && containerRef.current) {
            containerRef.current.innerHTML = `<pre style="color: red; padding: 1rem; border: 1px solid red; border-radius: 4px;">${e.message || 'Syntax error'}</pre>`;
          }
        });
    }
    
    return () => {
      isMounted = false;
    };
  }, [chart]);

  return <div ref={containerRef} className="mermaid-chart" style={{ display: 'flex', justifyContent: 'center', margin: '1rem 0' }} />;
}
