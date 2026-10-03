import React from 'react';

/**
 * The paper's outline (SD-5): one plain row per section with a status dot,
 * not a stack of bordered cards with uppercase pills. Status is also spoken —
 * the dot is decoration, the text beside it is the information.
 */
export default function SectionsList({ sections, activeSectionId, onSelectSection, doneIds, generating }) {
  const written = doneIds.length;

  return (
    <div className="manuscript-sections">
      <div className="manuscript-sections-head">
        <span className="manuscript-sections-title">Sections</span>
        <span className="manuscript-sections-progress">{written} of {sections.length} written</span>
      </div>

      <nav aria-label="Paper sections" className="manuscript-sections-list">
        {sections.map((step) => {
          const isDone = doneIds.includes(step.id);
          const isActive = activeSectionId === step.id;
          const isGenerating = isActive && generating;
          const status = isGenerating ? 'writing' : isDone ? 'written' : 'empty';
          const statusLabel = { writing: 'Writing…', written: 'Written', empty: 'Not started' }[status];

          return (
            // ME-1 (E5): a real button, so sections are reachable by keyboard.
            <button
              type="button"
              key={step.id}
              onClick={() => onSelectSection(step.id)}
              aria-current={isActive ? 'step' : undefined}
              className={`manuscript-section-row is-${status}${isActive ? ' is-active' : ''}`}
            >
              <span className="manuscript-section-dot" aria-hidden="true" />
              <span className="manuscript-section-name">{step.label}</span>
              <span className="manuscript-section-status">{statusLabel}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
