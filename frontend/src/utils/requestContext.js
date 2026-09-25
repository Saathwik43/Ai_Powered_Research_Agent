/**
 * What one analysis request put in front of the model.
 *
 * A different meter from the sidebar's daily wallet, and it must not be
 * confused with it. This one says **full** — it is window occupancy for a
 * single call, it resets every message, and it is an estimate. The wallet says
 * **used**, counts real provider tokens, and resets at midnight UTC.
 *
 * Pure functions, no React, so the arithmetic can be checked without a DOM.
 */

// Reader-facing names for the prompt's parts. `reply` is last because it is
// the only one that has not been sent yet — it is space held open.
export const CONTEXT_SEGMENTS = [
  { key: 'instructions', label: 'Instructions', className: 'is-instructions' },
  { key: 'paper', label: 'Paper', className: 'is-paper' },
  { key: 'conversation', label: 'Conversation', className: 'is-conversation' },
  { key: 'question', label: 'Your question', className: 'is-question' },
  { key: 'reply', label: 'Reply budget', className: 'is-reply' },
];

/**
 * Turn the backend's character counts into the panel's numbers.
 *
 * Characters come from the server because only it knows what was actually
 * assembled; the characters-to-tokens divisor comes from the server too, so
 * the estimate is defined in one place. Returns `null` when the request never
 * reached a model — a question answered straight from the paper's metadata has
 * no context to show, and an empty panel would imply one.
 */
export function describeRequestContext(context) {
  if (!context) return null;

  const perToken = Number(context.chars_per_token) || 4;
  const toTokens = (chars) => Math.round((Number(chars) || 0) / perToken);

  const tokens = {
    instructions: toTokens(context.instructions_chars),
    paper: toTokens(context.paper_chars),
    conversation: toTokens(context.conversation_chars),
    question: toTokens(context.question_chars),
    reply: Math.max(0, Math.round(Number(context.max_output_tokens) || 0)),
  };
  const used = Object.values(tokens).reduce((sum, n) => sum + n, 0);

  // The denominator is what this request was *allowed*, not some model's
  // advertised window: the paper's own char cap, plus every other part at the
  // size it was actually sent. So "% full" answers "how much of the room the
  // paper was given did it need", which is the question truncation turns on.
  const budget =
    toTokens(context.paper_char_cap)
    + tokens.instructions
    + tokens.conversation
    + tokens.question
    + tokens.reply;

  const share = (n) => (budget > 0 ? (n / budget) * 100 : 0);
  return {
    used,
    budget,
    pctFull: budget > 0 ? Math.max(0, Math.min(100, Math.round((used / budget) * 100))) : 0,
    segments: CONTEXT_SEGMENTS
      .map((segment) => ({ ...segment, tokens: tokens[segment.key] }))
      .filter((segment) => segment.tokens > 0)
      .map((segment) => ({ ...segment, pct: share(segment.tokens) })),
    // The paper did not fit and was cut. Worth saying out loud: an answer drawn
    // from part of a paper can be wrong by omission.
    truncated: Boolean(context.paper_truncated),
    // Cached content still occupies the window; it is the billing that changes.
    cached: Boolean(context.cached),
  };
}
