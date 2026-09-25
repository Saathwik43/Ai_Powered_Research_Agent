/**
 * Formatting for the daily AI wallet.
 *
 * The unit is **tokens** — the same unit the quota is enforced in and the same
 * unit the admin tables show. The sidebar used to divide them by 5,000 and call
 * the result "messages left", which was a chatbot metaphor this product never
 * matched: one PDF analysis can cost four of those imaginary messages and one
 * literature brief a fraction of one.
 *
 * Pure functions, no React — so the labels can be checked without a DOM.
 */

// Order is the order they stack in the bar and list in the popover. `other`
// last, because it is the catch-all.
export const USAGE_FEATURES = [
  { key: 'literature', label: 'Literature', className: 'is-literature' },
  { key: 'pdf_analysis', label: 'PDF analysis', className: 'is-pdf' },
  { key: 'manuscript', label: 'Manuscript', className: 'is-manuscript' },
  { key: 'other', label: 'Other', className: 'is-other' },
];

/**
 * `1.2K`, `31.2K`, `250K`, `1.4M` — the compact chip next to the bar.
 *
 * One decimal is enough to see the number move during a session; past 100K a
 * decimal is noise, and `250.0K` reads worse than `250K`.
 */
export function compactTokens(value) {
  const n = Math.max(0, Math.round(Number(value) || 0));
  if (n < 1000) return String(n);
  if (n < 1_000_000) {
    const k = n / 1000;
    return `${k >= 100 ? Math.round(k) : Math.round(k * 10) / 10}K`;
  }
  const m = n / 1_000_000;
  return `${m >= 100 ? Math.round(m) : Math.round(m * 10) / 10}M`;
}

/** `31,200` — the popover, where there is room for the real number. */
export function fullTokens(value) {
  return Math.max(0, Math.round(Number(value) || 0)).toLocaleString();
}

/**
 * Whole-percent *used*, never "full": this is a wallet, not a context window.
 *
 * Derived from `used / quota` rather than from the server's `pct_used`, which
 * is already rounded to one decimal — rounding 12.48 to 12.5 and then to a
 * whole number gives 13% for a bar that is visibly 12% full. `pct_used` is the
 * fallback for a response that carries no quota.
 */
export function usagePct(usage) {
  if (!usage) return 0;
  const clamp = (n) => Math.max(0, Math.min(100, Math.round(n)));
  const quota = Number(usage.quota);
  if (Number.isFinite(quota)) {
    // A quota of 0 locks the account: nothing left, so the bar reads full.
    if (quota <= 0) return 100;
    return clamp((Number(usage.used) || 0) / quota * 100);
  }
  const server = Number(usage.pct_used);
  return Number.isFinite(server) ? clamp(server) : 0;
}

/**
 * The bar's coloured runs, widest-meaning-first in `USAGE_FEATURES` order.
 *
 * Widths are a share of the *quota*, not of the spend, so the filled part of
 * the track equals `pct_used` and an empty day is an empty bar. A day that
 * somehow overshot the quota is scaled back to exactly 100% rather than
 * overflowing the track.
 */
export function usageSegments(usage) {
  const quota = Number(usage?.quota) || 0;
  const byFeature = usage?.by_feature || {};
  if (quota <= 0) return [];

  const segments = USAGE_FEATURES
    .map((feature) => ({
      ...feature,
      tokens: Math.max(0, Number(byFeature[feature.key]) || 0),
    }))
    .filter((segment) => segment.tokens > 0)
    .map((segment) => ({ ...segment, pct: (segment.tokens / quota) * 100 }));

  const total = segments.reduce((sum, segment) => sum + segment.pct, 0);
  if (total <= 100) return segments;
  return segments.map((segment) => ({ ...segment, pct: (segment.pct / total) * 100 }));
}

/**
 * Every bucket, zeros included — the popover lists all four so the reader can
 * see that PDF analysis cost nothing today, not just that it is missing.
 */
export function usageBreakdown(usage) {
  const byFeature = usage?.by_feature || {};
  return USAGE_FEATURES.map((feature) => ({
    ...feature,
    tokens: Math.max(0, Number(byFeature[feature.key]) || 0),
  }));
}
