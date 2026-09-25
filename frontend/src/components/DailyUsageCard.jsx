import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  compactTokens,
  fullTokens,
  usageBreakdown,
  usagePct,
  usageSegments,
} from '../utils/usageFormat';

// The quota resets at midnight UTC and is spent by background work as well as
// by clicks, so a card fetched once on mount goes stale within a session. Poll
// while the sidebar is mounted, and catch up immediately when the tab is
// brought back — a user who left for an hour should not see an hour-old number.
const REFRESH_MS = 45_000;

/**
 * The daily AI wallet.
 *
 * This is a **budget**, in tokens, against a UTC-day quota — not a context
 * window and not a message count. Hence "used", never "full", and no
 * conversion into "PDFs left": one analysis can cost twenty times another.
 */
export default function DailyUsageCard() {
  const { api, user } = useAuth();
  const [usage, setUsage] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const cardRef = useRef(null);
  // Guards against a slow poll landing after a newer one, and against two
  // requests overlapping when the tab regains focus mid-interval.
  const inFlight = useRef(false);

  const fetchUsage = useCallback(async () => {
    if (!user || inFlight.current) return;
    inFlight.current = true;
    try {
      const res = await api.raw('/api/user/usage');
      if (res.ok) setUsage(await res.json());
    } catch (err) {
      // Keep the last good numbers on screen. A wallet that blanks out on one
      // failed poll is worse than one that is a minute behind.
      console.error('Failed to fetch usage:', err);
    } finally {
      inFlight.current = false;
    }
  }, [api, user]);

  useEffect(() => {
    if (!user) return undefined;
    fetchUsage();
    const timer = setInterval(fetchUsage, REFRESH_MS);
    const onFocus = () => fetchUsage();
    window.addEventListener('focus', onFocus);
    return () => {
      clearInterval(timer);
      window.removeEventListener('focus', onFocus);
    };
  }, [user, fetchUsage]);

  // Dismiss the popover the way every other popover on the page behaves.
  useEffect(() => {
    if (!expanded) return undefined;
    const onPointerDown = (e) => {
      if (cardRef.current && !cardRef.current.contains(e.target)) setExpanded(false);
    };
    const onKeyDown = (e) => {
      if (e.key === 'Escape') setExpanded(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [expanded]);

  if (!usage) return null;

  const pct = usagePct(usage);
  const segments = usageSegments(usage);
  const breakdown = usageBreakdown(usage);
  const spentNothing = !(Number(usage.used) > 0);

  return (
    <div className="sidebar-usage-card" ref={cardRef}>
      <button
        type="button"
        className="sidebar-usage-trigger"
        onClick={() => setExpanded((open) => !open)}
        aria-expanded={expanded}
        aria-label={`Today's AI: ${pct}% of the daily token budget used. Show breakdown.`}
      >
        <div className="sidebar-usage-title">Today&rsquo;s AI</div>
        <div className="sidebar-usage-row">
          <span className="sidebar-usage-pct">{pct}% used</span>
          <span className="sidebar-usage-tokens">
            ~{compactTokens(usage.used)} / {compactTokens(usage.quota)}
          </span>
        </div>
        <div
          className="sidebar-usage-track"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          {segments.length > 0 ? (
            segments.map((segment) => (
              <span
                key={segment.key}
                className={`sidebar-usage-seg ${segment.className}`}
                style={{ width: `${segment.pct}%` }}
              />
            ))
          ) : (
            // Before Phase 2's tagging landed every log was `general`, so a
            // single untagged run is a normal state, not a bug.
            <span className="sidebar-usage-seg is-other" style={{ width: `${pct}%` }} />
          )}
        </div>
        <div className="sidebar-usage-reset">Resets in {usage.reset_in}</div>
      </button>

      {expanded && (
        <div className="sidebar-usage-popover" role="dialog" aria-label="Today's AI usage">
          <div className="sidebar-usage-pop-head">
            <span className="sidebar-usage-pct">{pct}% used</span>
            <span className="sidebar-usage-tokens">
              {fullTokens(usage.used)} / {fullTokens(usage.quota)}
            </span>
          </div>
          {spentNothing ? (
            <p className="sidebar-usage-empty">No AI usage today</p>
          ) : (
            <ul className="sidebar-usage-list">
              {breakdown.map((feature) => (
                <li key={feature.key}>
                  <span className={`sidebar-usage-swatch ${feature.className}`} />
                  <span className="sidebar-usage-list-label">{feature.label}</span>
                  <span className="sidebar-usage-list-value">{fullTokens(feature.tokens)}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="sidebar-usage-hint">
            Paper analysis often uses 8&ndash;20K &middot; a section draft 3&ndash;8K
          </p>
        </div>
      )}
    </div>
  );
}
