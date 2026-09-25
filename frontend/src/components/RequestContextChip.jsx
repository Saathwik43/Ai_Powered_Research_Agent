import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Gauge } from 'lucide-react';
import { compactTokens } from '../utils/usageFormat';
import { describeRequestContext } from '../utils/requestContext';

/**
 * "This request" — how full the model's window was for the last analysis.
 *
 * Deliberately a chip beside the composer rather than anything in the sidebar:
 * it is per-request, per-page, and has nothing to do with the daily budget. A
 * reader who wants the split opens it; everyone else sees one short line.
 */
export default function RequestContextChip({ context }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKeyDown = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const report = describeRequestContext(context);
  // No model call was made — a metadata answer, or nothing asked yet.
  if (!report) return null;

  return (
    <div className="pdf-context-chip-wrap" ref={wrapRef}>
      <button
        type="button"
        className={`pdf-suggestion-chip pdf-context-chip${report.truncated ? ' is-truncated' : ''}`}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        title="What the last request put in front of the model"
      >
        {report.truncated ? <AlertTriangle size={13} /> : <Gauge size={13} />}
        {report.pctFull}% full
      </button>

      {open && (
        <div className="pdf-context-popover" role="dialog" aria-label="This request">
          <div className="pdf-context-head">
            <span className="pdf-context-title">This request</span>
            <span className="pdf-context-tokens">
              ~{compactTokens(report.used)} / ~{compactTokens(report.budget)}
            </span>
          </div>

          <div className="pdf-context-track">
            {report.segments.map((segment) => (
              <span
                key={segment.key}
                className={`pdf-context-seg ${segment.className}`}
                style={{ width: `${segment.pct}%` }}
              />
            ))}
          </div>

          <ul className="pdf-context-list">
            {report.segments.map((segment) => (
              <li key={segment.key}>
                <span className={`pdf-context-swatch ${segment.className}`} />
                <span className="pdf-context-label">
                  {segment.label}
                  {segment.key === 'paper' && report.cached ? (
                    <span className="pdf-context-tag">cached</span>
                  ) : null}
                </span>
                <span className="pdf-context-value">~{compactTokens(segment.tokens)}</span>
              </li>
            ))}
          </ul>

          {report.truncated && (
            <p className="pdf-context-warn">
              <AlertTriangle size={12} /> Paper text truncated to fit. Answers may miss
              what was cut.
            </p>
          )}
          <p className="pdf-context-note">
            Token counts are estimated from text length, not a tokenizer.
            {report.cached ? ' Cached text still fills the window; it is the billing that changes.' : ''}
          </p>
        </div>
      )}
    </div>
  );
}
