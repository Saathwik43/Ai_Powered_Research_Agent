import React from 'react';

/**
 * The one masthead. Every page's title block goes through this component so the
 * kicker/title/lede rhythm and the rule beneath it are defined once, in
 * index.css (`.page-header*`), instead of being re-invented per page.
 *
 *   kicker  — mono eyebrow naming the desk ("Literature desk")
 *   title   — serif page title
 *   lede    — one sentence saying what the page is for
 *   facts   — mono operational figures, separated automatically
 *   actions — right-aligned controls on the title row
 */
export default function PageHeader({ kicker, title, lede, facts, actions, children }) {
  return (
    <header className="page-header">
      {kicker && <p className="page-header-kicker">{kicker}</p>}

      <div className="page-header-row">
        <h1 className="page-header-title">{title}</h1>
        {actions && <div className="page-header-actions">{actions}</div>}
      </div>

      {lede && <p className="page-header-lede">{lede}</p>}

      {facts?.length > 0 && (
        <div className="page-header-facts">
          {facts.map((fact, i) => (
            <span key={i}>{fact}</span>
          ))}
        </div>
      )}

      {children}
    </header>
  );
}
