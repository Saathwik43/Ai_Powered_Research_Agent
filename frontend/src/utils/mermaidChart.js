/** Shared Mermaid helpers for PDF chat + manuscript rendering. */

export const MERMAID_LANGS = [
  'mermaid',
  'flowchart',
  'graph',
  'xychart-beta',
  'xychart',
  'gantt',
  'classdiagram',
  'pie',
  'sequencediagram',
  'statediagram',
  'statediagram-v2',
  'erdiagram',
  'mindmap',
  'timeline',
  'journey',
  'quadrantchart',
  'gitgraph',
  'sankey-beta',
  'block-beta',
  'requirementdiagram',
  'c4context',
];

const DIAGRAM_STARTERS = [
  'flowchart',
  'graph',
  'sequenceDiagram',
  'classDiagram',
  'stateDiagram-v2',
  'stateDiagram',
  'erDiagram',
  'gantt',
  'pie',
  'journey',
  'mindmap',
  'timeline',
  'quadrantChart',
  'xychart-beta',
  'xychart',
  'gitGraph',
  'C4Context',
  'sankey-beta',
  'requirementDiagram',
  'block-beta',
];

export function parseCodeLanguage(className = '') {
  const match = /language-([^\s]+)/.exec(className || '');
  return match ? match[1].toLowerCase() : '';
}

export function codeChildrenToText(children) {
  if (children == null) return '';
  if (typeof children === 'string' || typeof children === 'number') {
    return String(children).replace(/\n$/, '');
  }
  if (Array.isArray(children)) {
    return children
      .map((c) => (typeof c === 'string' || typeof c === 'number' ? String(c) : ''))
      .join('')
      .replace(/\n$/, '');
  }
  return String(children).replace(/\n$/, '');
}

export function isMermaidBlock(language, contentStr) {
  const lang = (language || '').toLowerCase();
  const t = (contentStr || '').trim();
  if (!t) return false;
  if (MERMAID_LANGS.includes(lang) || lang.startsWith('mermaid')) return true;
  return DIAGRAM_STARTERS.some(
    (k) => t.startsWith(k) || t.startsWith(`${k} `) || t.startsWith(`${k}\n`) || t.startsWith(`${k}-`)
  );
}

export function extractMermaidCharts(content = '') {
  const charts = [];
  const fenceLang = MERMAID_LANGS.map((l) => l.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
  const fenced = new RegExp('```(?:' + fenceLang + ')?\\s*([\\s\\S]*?)```', 'gi');
  let m;
  while ((m = fenced.exec(content)) !== null) {
    const body = m[1].trim();
    if (body && isMermaidBlock('', body)) charts.push(body);
  }
  return charts;
}

function startsWithDiagram(chart) {
  return DIAGRAM_STARTERS.some(
    (k) => chart.startsWith(k) || chart.startsWith(`${k} `) || chart.startsWith(`${k}\n`) || chart.startsWith(`${k}-`)
  );
}

export function sanitizeMermaidChart(raw) {
  let chart = String(raw || '')
    .replace(/^\s*```(?:[^\n`]*)\s*/i, '')
    .replace(/\s*```\s*$/i, '')
    .replace(/\u201c|\u201d/g, '"')
    .replace(/\u2018|\u2019/g, "'")
    .replace(/\u00a0/g, ' ')
    .replace(/\r\n/g, '\n')
    .trim();

  if (!chart) return '';

  chart = chart.replace(/^mermaid\s*\n/i, '');
  chart = chart.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/__([^_]+)__/g, '$1');
  chart = chart.replace(/\$([^$\n]+)\$/g, '$1');
  chart = chart.replace(/^pie\s*\n\s*title\s+/i, 'pie title ');

  if (/^xychart\b/i.test(chart) && !/^xychart-beta\b/i.test(chart)) {
    chart = chart.replace(/^xychart\b/i, 'xychart-beta');
  }

  if (!startsWithDiagram(chart)) {
    if (/\bx-axis\b/i.test(chart) || /\by-axis\b/i.test(chart) || /^\s*(bar|line)\s+\[/m.test(chart)) {
      chart = `xychart-beta\n${chart}`;
    } else if (/^\s*(participant|actor|->>|-->>)/m.test(chart)) {
      chart = `sequenceDiagram\n${chart}`;
    } else if (/-->|---|-\.-|==>/.test(chart)) {
      chart = `flowchart TD\n${chart}`;
    } else if (/^\s*"[^"]+"\s*:\s*[\d.]+/m.test(chart)) {
      chart = `pie title Distribution\n${chart}`;
    }
  }

  // Prefer vertical flowcharts for readability in chat
  chart = chart.replace(/^graph(\s+)/i, 'flowchart$1');
  chart = chart.replace(/^flowchart\s+LR\b/i, 'flowchart TD');
  chart = chart.replace(/^flowchart\s+RL\b/i, 'flowchart TD');

  return chart.trim();
}

export function normalizeMermaidSvg(svg) {
  if (!svg) return svg;
  return svg
    .replace(/\sstyle="([^"]*)"/, (_, style) => {
      const cleaned = style
        .replace(/max-width:\s*[^;]+;?/gi, '')
        .replace(/height:\s*auto;?/gi, '')
        .trim();
      return cleaned ? ` style="${cleaned}"` : '';
    })
    .replace(/\sstyle=""/g, '')
    .replace(/\swidth="100%"/i, '')
    .replace(/\sheight="100%"/i, '');
}
