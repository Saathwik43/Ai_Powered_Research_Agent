/** Shared client-side search heuristics for Dashboard + Literature Survey. */

export const SEARCH_MAX_LEN = 200;
export const SEARCH_MIN_ALPHA = 3;
const ACRONYM_MAX_LEN = 6;

const INJECTION_PATTERNS = [
  /ignore all previous instructions/i,
  /system prompt/i,
  /bypass/i,
  /drop table/i,
  /exec\(/i,
  /eval\(/i,
];

const TOKEN_RE = /[A-Za-z]+/g;
const VOWEL_RE = /[aeiouy]/i;
const CONSONANT_RUN_RE = /[bcdfghjklmnpqrstvwxz]{5,}/i;
const CHAR_REPEAT_RE = /(.)\1{4,}/;

/** Short all-caps tokens (LLM, NLP, SVM, TCP) are legitimate query terms. */
function isAcronym(token) {
  return token.length >= 2 && token.length <= ACRONYM_MAX_LEN && token === token.toUpperCase();
}

/**
 * True when a single token looks like a real word or a known-shape acronym.
 * Checked per token, never on the whitespace-stripped query: concatenating
 * words invents consonant runs across word boundaries ("CNN classification"
 * -> "CNNcl") and rejects ordinary acronym-heavy research queries.
 */
function isWordlike(token) {
  if (token.length < 2) return false;
  if (isAcronym(token)) return true;
  if (CHAR_REPEAT_RE.test(token)) return false;
  if (!VOWEL_RE.test(token)) return false;
  if (CONSONANT_RUN_RE.test(token)) return false;
  return true;
}

/**
 * Pure grammar / closed-class words. Mirrors GRAMMAR_STOPS in
 * backend/core/query_key.py — the two must stay identical, or the client's
 * in-flight dedupe guard and the server's cache will disagree on which
 * queries are the same query.
 */
const GRAMMAR_STOPS = new Set([
  'a', 'about', 'above', 'after', 'again', 'against', 'all', 'am', 'an', 'and', 'any', 'are', 'as', 'at',
  'be', 'because', 'been', 'before', 'being', 'below', 'between', 'both', 'but', 'by', 'can', 'cannot',
  'could', 'did', 'do', 'does', 'doing', 'down', 'during', 'each', 'few', 'for', 'from', 'further',
  'had', 'has', 'have', 'having', 'he', 'her', 'here', 'hers', 'herself', 'him', 'himself', 'his', 'how',
  'if', 'in', 'into', 'is', 'it', 'its', 'itself', 'let', 'me', 'more', 'most', 'my', 'myself',
  'no', 'nor', 'not', 'of', 'off', 'on', 'once', 'only', 'or', 'other', 'ought', 'our', 'ours',
  'ourselves', 'out', 'over', 'own', 'same', 'she', 'should', 'so', 'some', 'such', 'than', 'that',
  'the', 'their', 'theirs', 'them', 'themselves', 'then', 'there', 'these', 'they', 'this', 'those',
  'through', 'to', 'too', 'under', 'until', 'up', 'very', 'was', 'we', 'were', 'what', 'when', 'where',
  'which', 'while', 'who', 'whom', 'why', 'will', 'with', 'would', 'you', 'your', 'yours', 'yourself',
]);

/** Plural folding only — see the `stem` docstring in core/query_key.py. */
function stem(word) {
  const w = word.toLowerCase();
  if (w.length > 4 && w.endsWith('ies')) return `${w.slice(0, -3)}y`;
  if (w.length > 3 && w.endsWith('s') && !w.endsWith('ss')) return w.slice(0, -1);
  return w;
}

/**
 * Cache identity for a query. Port of canonical_key() in
 * backend/core/query_key.py: NFKC → casefold → strip punctuation → drop
 * grammar words → stem → dedupe → sort.
 *
 * Identity only. Never send this to the API or show it to the user — it
 * discards word order and inflection.
 */
export function normalizeSearchQuery(q) {
  const display = String(q || '').trim().replace(/\s+/g, ' ');
  const folded = display.normalize('NFKC').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  if (!folded) return '';

  const words = folded.split(' ');
  // Length is not a filter: "vitamin d", "e coli" and "k means" each depend
  // on their one-letter token.
  let content = words.filter(w => !GRAMMAR_STOPS.has(w)).map(stem);
  // An all-grammar query ("what is the") still needs a distinct key.
  if (!content.length) content = words.map(stem);

  return [...new Set(content)].sort().join(' ');
}

/**
 * Validate a research search query before hitting the API.
 * Mirrors backend guardrails (Layer A/B) so users get instant feedback.
 * @returns {{ ok: true, query: string } | { ok: false, code: string, message: string }}
 */
export function validateSearchQuery(raw) {
  const query = String(raw || '').trim().replace(/\s+/g, ' ');

  if (!query) {
    return {
      ok: false,
      code: 'empty',
      message: 'Enter a research topic or keywords to search.',
    };
  }

  if (query.length > SEARCH_MAX_LEN) {
    return {
      ok: false,
      code: 'too_long',
      message: `Keep your query under ${SEARCH_MAX_LEN} characters.`,
    };
  }

  const tokens = query.match(TOKEN_RE) || [];
  const alphaLen = tokens.reduce((n, t) => n + t.length, 0);
  if (alphaLen < SEARCH_MIN_ALPHA) {
    return {
      ok: false,
      code: 'too_short',
      message: 'Use at least 3 letters so we can match research topics.',
    };
  }

  // One real-looking token is enough — the backend's semantic layer is the
  // authority on meaning; this only filters obvious keyboard mash.
  if (!tokens.some(isWordlike)) {
    return {
      ok: false,
      code: 'gibberish',
      message: `"${query}" doesn't look like a research topic. Try a specific field or subject area.`,
    };
  }

  for (const pattern of INJECTION_PATTERNS) {
    if (pattern.test(query)) {
      return {
        ok: false,
        code: 'blocked',
        message: 'That query looks unsafe. Rephrase your research topic.',
      };
    }
  }

  return { ok: true, query };
}

export function isAbortError(err) {
  return (
    err?.name === 'AbortError' ||
    err?.code === 20 ||
    (typeof DOMException !== 'undefined' && err instanceof DOMException && err.name === 'AbortError')
  );
}
