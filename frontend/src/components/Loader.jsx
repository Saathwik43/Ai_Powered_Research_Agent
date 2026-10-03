import React from 'react';

// One spinner for the whole app: a hairline ring on --line, capped by --focus.
export function Spinner({ size = 24, label }) {
  return (
    <span className="inline-flex items-center gap-2 align-middle text-muted-foreground">
      <span
        className="ui-spinner"
        style={{ width: size, height: size }}
      />
      {label && <span className="text-sm font-medium">{label}</span>}
    </span>
  );
}

// Typing indicator for chat (3 dots)
export function TypingDots() {
  return (
    <div className="typing-dots">
      <span />
      <span />
      <span />
    </div>
  );
}

// Single block of shimmering text
export function SkeletonText({ lines = 3, className = '' }) {
  return (
    <div className={`flex flex-col gap-2 w-full ${className}`}>
      {Array.from({ length: lines }).map((_, i) => (
        <div 
          key={i} 
          className="skeleton-shimmer rounded-sm"
          style={{ 
            height: '1rem',
            width: i === lines - 1 ? '60%' : '100%' 
          }}
        />
      ))}
    </div>
  );
}

// Stands in for a result *row*, so it carries the row's rule rather than a
// panel frame — a skeleton that looks like a card teaches the wrong shape.
export function SkeletonCard({ className = '' }) {
  return (
    <div
      className={`flex flex-col gap-2 ${className}`}
      style={{
        padding: 'var(--space-3) 0',
        borderBottom: '1px solid var(--line)',
      }}
    >
      <div className="skeleton-shimmer h-4 w-3/4" />
      <div className="skeleton-shimmer h-4 w-1/4" />
    </div>
  );
}

// Renders a list of skeleton cards for loading states
export function SkeletonList({ count = 3, className = '' }) {
  return (
    <div className={`flex flex-col w-full ${className}`}>
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}
