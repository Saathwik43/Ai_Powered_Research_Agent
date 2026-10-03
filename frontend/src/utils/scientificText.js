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
// Multi-letter lowercase subscripts are a safelist, not a pattern, and that is
// deliberate: `[a-z]{2,5}` would also lift the tail of every snake_case
// identifier (`use_the_snake_case` -> `use_the_snake<sub>case</sub>`), and no
// look-around separates the two reliably. So the list carries the subscripts
// that actually turn up in physics and chemistry writing. Measured against the
// parser before this list existed: tau_rec, E_tot, alpha_abs, P_out and I_sat
// all rendered with a literal underscore.
const SUB_WORDS =
  'theo|calc|crit|loss|meas|surf|trap|bulk|cell|abs|ads|app|avg|det|exc|exp|' +
  'ext|gen|inj|int|max|min|net|obs|opt|out|rec|red|ref|rel|rms|sat|eff|' +
  'tot|th|bg|in|ox|ph';

// Greek is a script body in its own right - T_alpha, E_gamma, mu_beta written
// with the actual letters. It matches neither [A-Za-z] nor \d, so it used to
// render as a literal underscore as well.
const GREEK = '[\\u0370-\\u03FF\\u1F00-\\u1FFF]';

const SUB_BODY = new RegExp(
  '^(?:\\{([^}]+)\\}|([+\\u2212\\u00B1-]?\\d+|' + SUB_WORDS + '|[A-Z]{1,5}|[a-z]|' + GREEK + '))'
);
const SUP_BODY = new RegExp(
  '^(?:\\{([^}]+)\\}|([+\\u2212\\u00B1-]?\\d+|[A-Za-z]|' + GREEK + '|[*\\u2020\\u2021]))'
);

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
