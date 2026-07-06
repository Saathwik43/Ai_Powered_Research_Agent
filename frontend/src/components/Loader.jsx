import React from 'react';

// Perplexity-inspired subtle spinner
export function Spinner({ size = 24, label }) {
  return (
    <div className="flex items-center gap-2 text-text-subtle">
      <div 
        className="ui-spinner"
        style={{ width: size, height: size }}
      />
      {label && <span className="text-sm font-medium">{label}</span>}
    </div>
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

// A full card skeleton mimicking paper results
export function SkeletonCard({ className = '' }) {
  return (
    <div className={`glass-panel p-4 flex flex-col gap-3 ${className}`}>
      <div className="skeleton-shimmer h-6 w-3/4 rounded-sm" />
      <div className="skeleton-shimmer h-4 w-1/4 rounded-sm" />
      <div className="flex flex-col gap-2 mt-2">
        <div className="skeleton-shimmer h-4 w-full rounded-sm" />
        <div className="skeleton-shimmer h-4 w-5/6 rounded-sm" />
      </div>
      <div className="flex gap-2 mt-2">
        <div className="skeleton-shimmer h-8 w-20 rounded-md" />
        <div className="skeleton-shimmer h-8 w-20 rounded-md" />
      </div>
    </div>
  );
}

// Renders a list of skeleton cards for loading states
export function SkeletonList({ count = 3, className = '' }) {
  return (
    <div className={`flex flex-col gap-4 w-full ${className}`}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} style={{ animationDelay: `${i * 100}ms`, opacity: 0, animation: 'fadeInUp 0.5s ease forwards' }}>
          <SkeletonCard />
        </div>
      ))}
    </div>
  );
}
