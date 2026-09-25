import React, { useMemo } from 'react';
import katex from 'katex';
import { parseScientificText } from '../utils/scientificText';
import { KATEX_REHYPE_OPTIONS } from '../utils/latexMath';
import 'katex/dist/katex.min.css';

function KatexInline({ tex }) {
  const html = useMemo(() => {
    try {
      return katex.renderToString(tex, {
        ...KATEX_REHYPE_OPTIONS,
        displayMode: false,
        output: 'html',
      });
    } catch {
      return '';
    }
  }, [tex]);
  if (!html) return <>${tex}$</>;
  return <span className="sci-math" dangerouslySetInnerHTML={{ __html: html }} />;
}

function renderToken(token, index) {
  if (token.type === 'sub') return <sub key={index}>{token.value}</sub>;
  if (token.type === 'sup') return <sup key={index}>{token.value}</sup>;
  if (token.type === 'math') return <KatexInline key={index} tex={token.value} />;
  return <React.Fragment key={index}>{token.value}</React.Fragment>;
}

export default function ScientificText({ text, as: Comp = 'span', className }) {
  const tokens = useMemo(() => parseScientificText(text), [text]);
  return <Comp className={className}>{tokens.map(renderToken)}</Comp>;
}
