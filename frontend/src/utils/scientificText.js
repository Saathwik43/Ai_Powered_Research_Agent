/**
 * Turn the _ / ^ markup models emit in titles and briefs (SnO_2, J_SC,
 * cm^−2) into tokens a React view can render as <sub>/<sup> without
 * changing the surrounding font or line box.
 *
 * $...$ / \(...\) is left as a math token for KaTeX. Bare snake_case
 * words are left alone.
 */

import { normalizeLatexDelimiters } from './latexMath';

const BASE_CHAR = /[\p{L}\p{N})\]%]/u;
const MATH_RE = /\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g;
const SUB_BODY = /^(?:\{([^}]+)\}|([+\u2212\u00B1-]?\d+|max|min|avg|rms|eff|th|[A-Z]{1,5}|[a-z]))/;
const SUP_BODY = /^(?:\{([^}]+)\}|([+\u2212\u00B1-]?\d+|[A-Za-z]|[*†‡]))/;

function matchScriptBody(text, start, isSup) {
  const rest = text.slice(start);
  if (!rest) return null;
  const m = rest.match(isSup ? SUP_BODY : SUB_BODY);
  if (!m) return null;
  const end = start + m[0].length;
  // `_of` in snake_case must stay literal; `_i` in x_i^2 is a real index.
  if (/^[A-Za-z]/.test(m[0]) && /[A-Za-z]/.test(text[end] || '')) {
    return null;
  }
  return { value: m[1] || m[2], end };
}

function parseScripts(text) {
  const tokens = [];
  let buf = '';
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if ((ch === '_' || ch === '^') && i > 0 && BASE_CHAR.test(text[i - 1])) {
      const body = matchScriptBody(text, i + 1, ch === '^');
      if (body) {
        if (buf) {
          tokens.push({ type: 'text', value: buf });
          buf = '';
        }
        tokens.push({ type: ch === '_' ? 'sub' : 'sup', value: body.value });
        i = body.end;
        continue;
      }
    }
    buf += ch;
    i += 1;
  }
  if (buf) tokens.push({ type: 'text', value: buf });
  return tokens;
}

export function parseScientificText(input) {
  const text = normalizeLatexDelimiters(String(input || ''));
  if (!text) return [];
  const tokens = [];
  MATH_RE.lastIndex = 0;
  let last = 0;
  let match;
  while ((match = MATH_RE.exec(text))) {
    if (match.index > last) {
      tokens.push(...parseScripts(text.slice(last, match.index)));
    }
    tokens.push({ type: 'math', value: (match[1] ?? match[2]).trim() });
    last = match.index + match[0].length;
  }
  if (last < text.length) tokens.push(...parseScripts(text.slice(last)));
  return tokens;
}
