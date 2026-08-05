/** Shared client-side search heuristics for Dashboard + Literature Survey. */

export const SEARCH_MAX_LEN = 200;
export const SEARCH_MIN_ALPHA = 3;

const INJECTION_PATTERNS = [
  /ignore all previous instructions/i,
  /system prompt/i,
  /bypass/i,
  /drop table/i,
  /exec\(/i,
  /eval\(/i,
];

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

  const alpha = query.replace(/[^a-zA-Z]/g, '');
  if (alpha.length < SEARCH_MIN_ALPHA) {
    return {
      ok: false,
      code: 'too_short',
      message: 'Use at least 3 letters so we can match research topics.',
    };
  }

  const noVowels = !/[aeiouy]/i.test(alpha);
  const consonantRun = /[bcdfghjklmnpqrstvwxz]{5,}/i.test(alpha);
  const charRepeats = /(.)\1{4,}/i.test(alpha);
  if (noVowels || consonantRun || charRepeats) {
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
