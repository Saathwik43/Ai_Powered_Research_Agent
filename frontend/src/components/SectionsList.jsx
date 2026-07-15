import React from 'react';
import { CheckCircle, Circle, Edit2 } from 'lucide-react';

export default function SectionsList({ sections, activeSectionId, onSelectSection, doneIds, generating }) {
  const progressPercent = sections.length ? (doneIds.length / sections.length) * 100 : 0;

  return (
    <div className="manuscript-outline-panel" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '1.25rem' }}>
      <p style={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.09em', color: 'var(--text-subtle)', marginBottom: '0.875rem' }}>Sections</p>
      
      <div style={{ height: '4px', borderRadius: '99px', background: 'var(--border)', marginBottom: '1rem', overflow: 'hidden' }}>
        <div style={{ height: '100%', background: 'var(--success)', width: `${progressPercent}%`, transition: 'width 0.3s ease' }}></div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
        {sections.map(step => {
          const isDone = doneIds.includes(step.id);
          const isActive = activeSectionId === step.id;
          const isGenerating = isActive && generating;

          let icon;
          let statusLabel = '';
          
          if (isGenerating) {
            icon = <Edit2 size={15} color="var(--primary)" />;
            statusLabel = "Writing…";
          } else if (isDone) {
            icon = <CheckCircle size={15} color="var(--success)" />;
            statusLabel = "Done";
          } else {
            icon = <Circle size={15} color="var(--text-subtle)" style={{ strokeDasharray: '4 2' }} />;
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
                padding: '0.6rem 0.75rem', 
                borderRadius: 'var(--radius-md)', 
                cursor: 'pointer', 
                fontSize: '0.88rem', 
                fontWeight: isActive ? 700 : 500, 
                color: isActive ? 'var(--primary)' : isDone ? 'var(--text)' : 'var(--text-muted)', 
                background: isGenerating ? 'var(--primary-light)' : (isActive ? 'var(--bg-elevated)' : 'transparent'), 
                border: isGenerating ? '1px solid var(--primary)' : `1px solid ${isActive ? 'rgba(0,87,255,0.22)' : 'transparent'}`, 
                transition: 'var(--transition)' 
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                {icon}
                <span>{step.label}</span>
              </div>
              <span style={{ fontSize: '0.65rem', color: isGenerating ? 'var(--primary)' : 'var(--text-subtle)', fontWeight: 600 }}>
                {statusLabel}
              </span>
            </div>
          );
        })}
      </div>
      <div style={{ marginTop: '1rem', paddingTop: '0.875rem', borderTop: '1px solid var(--border)', fontSize: '0.75rem', color: 'var(--text-subtle)', textAlign: 'center' }}>
        {doneIds.length} of {sections.length} written
      </div>
    </div>
  );
}
