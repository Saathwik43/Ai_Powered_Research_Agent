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

export function normalizeSearchQuery(q) {
  return String(q || '')
    .trim()
    .replace(/\s+/g, ' ')
    .toLowerCase();
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
