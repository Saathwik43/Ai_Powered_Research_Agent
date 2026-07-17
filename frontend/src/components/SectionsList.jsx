import React from 'react';

export default function SectionsList({ sections, activeSectionId, onSelectSection, doneIds, generating }) {
  const progressPercent = sections.length ? (doneIds.length / sections.length) * 100 : 0;

  return (
    <div className="manuscript-outline-panel" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '1.25rem' }}>
      <p style={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.09em', color: 'var(--text-subtle)', marginBottom: '0.875rem' }}>Sections</p>
      
      <div style={{ height: '4px', borderRadius: '99px', background: 'var(--border)', marginBottom: '1rem', overflow: 'hidden' }}>
        <div style={{ height: '100%', background: 'var(--success)', width: `${progressPercent}%`, transition: 'width 0.3s ease' }}></div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
        {sections.map((step, index) => {
          const isDone = doneIds.includes(step.id);
          const isActive = activeSectionId === step.id;
          const isGenerating = isActive && generating;
          
          let statusLabel = '';
          if (isGenerating) {
            statusLabel = "Writing…";
          } else if (isDone) {
            statusLabel = "Done";
          } else {
            statusLabel = isActive ? "Active" : "Queued";
          }

          const prevIsDone = index > 0 ? doneIds.includes(sections[index - 1].id) : true;
          const showDivider = prevIsDone && !isDone;

          return (
            <React.Fragment key={step.id}>
              {showDivider && (
                <div style={{ padding: '0.5rem 0', color: 'var(--border-strong)' }}>
                  <svg width="100%" height="8" preserveAspectRatio="none" viewBox="0 0 100 8" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M0,4 Q5,0 10,4 T20,4 T30,4 T40,4 T50,4 T60,4 T70,4 T80,4 T90,4 T100,4" />
                  </svg>
                </div>
              )}
              <div 
                onClick={() => onSelectSection(step.id)}
                style={{ 
                  display: 'flex', 
                  alignItems: 'center', 
                  justifyContent: 'space-between',
                  padding: '0.6rem 0.75rem', 
                  borderRadius: 'var(--radius-md)', 
                  cursor: 'pointer', 
                  fontSize: '0.88rem', 
                  fontWeight: isActive ? 700 : 500, 
                  color: isActive ? 'var(--primary)' : isDone ? 'var(--text)' : 'var(--text-muted)', 
                  background: isGenerating ? 'var(--primary-light)' : (isActive ? 'var(--bg-elevated)' : 'transparent'), 
                  border: isGenerating ? '1px solid var(--primary)' : `1px solid ${isActive ? 'var(--border-strong)' : 'transparent'}`, 
                  transition: 'var(--transition)' 
                }}
              >
                <span>{step.label}</span>
                <span style={{ fontSize: '0.65rem', color: isGenerating ? 'var(--primary)' : 'var(--text-subtle)', fontWeight: 600 }}>
                  {statusLabel}
                </span>
              </div>
            </React.Fragment>
          );
        })}
      </div>
      <div style={{ marginTop: '1rem', paddingTop: '0.875rem', borderTop: '1px solid var(--border)', fontSize: '0.75rem', color: 'var(--text-subtle)', textAlign: 'center' }}>
        {doneIds.length} of {sections.length} written
      </div>
    </div>
  );
}
