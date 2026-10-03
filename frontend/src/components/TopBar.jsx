import React from 'react';
import { ChevronRight } from 'lucide-react';

/**
 * Thin workspace context bar: which section you are in, and what is currently
 * loaded (topic, survey, draft). Chrome only — page actions belong in
 * PageHeader's `actions`, not here.
 *
 *   crumbs  — [{ label, to? }] section path; the last entry reads as current
 *   context — short mono chip for the active object, when one is set
 *   actions — small icon controls that act on the whole workspace
 */
export default function TopBar({ crumbs = [], context, actions }) {
  return (
    <div className="topbar">
      <nav className="topbar-crumbs" aria-label="Workspace location">
        {crumbs.map((crumb, i) => {
          const last = i === crumbs.length - 1;
          return (
            <React.Fragment key={`${crumb.label}-${i}`}>
              {i > 0 && (
                <ChevronRight size={12} className="topbar-crumb-sep" aria-hidden="true" />
              )}
              <span className={`topbar-crumb${last ? ' topbar-crumb-current' : ''}`}>
                {crumb.label}
              </span>
            </React.Fragment>
          );
        })}
      </nav>

      {context && <span className="topbar-context" title={context}>{context}</span>}

      <span className="topbar-spacer" />

      {actions && <div className="topbar-actions">{actions}</div>}
    </div>
  );
}
