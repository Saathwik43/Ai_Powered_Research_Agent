import React from 'react';

export default function SectionsList({ sections, activeSectionId, onSelectSection, doneIds, generating }) {
  const progressPercent = sections.length ? (doneIds.length / sections.length) * 100 : 0;

  return (
    <div className="manuscript-outline-panel" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '1rem', boxShadow: 'none' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.6rem' }}>
        <span style={{ fontSize: 'var(--fs-2xs)', fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-subtle)' }}>
          Sections
        </span>
        <span style={{ fontSize: 'var(--fs-2xs)', fontWeight: 700, color: 'var(--text-muted)' }}>
          {doneIds.length}/{sections.length} Written
        </span>
      </div>
      
      <div style={{ height: '4px', borderRadius: '999px', background: 'var(--border)', marginBottom: '0.85rem', overflow: 'hidden' }}>
        <div style={{ height: '100%', background: 'var(--success)', width: `${progressPercent}%`, transition: 'width 0.3s ease' }} />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
        {sections.map((step) => {
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

          return (
            <div 
              key={step.id}
              onClick={() => onSelectSection(step.id)}
              style={{ 
                display: 'flex', 
                alignItems: 'center', 
                justifyContent: 'space-between',
                padding: '0.55rem 0.75rem', 
                borderRadius: 'var(--radius-md)', 
                cursor: 'pointer', 
                fontSize: 'var(--fs-sm)', 
                fontWeight: isActive ? 700 : 500, 
                color: isActive ? 'var(--primary)' : isDone ? 'var(--text)' : 'var(--text-muted)', 
                background: isGenerating ? 'var(--primary-light)' : (isActive ? 'var(--bg-elevated)' : 'transparent'), 
                border: isGenerating ? '1px solid var(--primary)' : isActive ? '1px solid var(--primary)' : '1px solid var(--border)', 
                borderLeft: isActive ? '3px solid var(--primary)' : isDone ? '3px solid var(--success)' : '1px solid var(--border)',
                transition: 'var(--transition)' 
              }}
            >
              <span style={{ fontWeight: isActive ? 750 : 550 }}>{step.label}</span>
              <span style={{ 
                fontSize: '10px', 
                padding: '0.15rem 0.45rem',
                borderRadius: '999px',
                fontWeight: 700,
                textTransform: 'uppercase',
                letterSpacing: '0.04em',
                background: isGenerating ? 'var(--primary)' : isActive ? 'var(--primary-light)' : isDone ? 'rgba(79, 143, 107, 0.12)' : 'var(--bg-hover)',
                color: isGenerating ? 'var(--on-primary)' : isActive ? 'var(--primary)' : isDone ? 'var(--success)' : 'var(--text-subtle)'
              }}>
                {statusLabel}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
