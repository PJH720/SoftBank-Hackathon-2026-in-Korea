import React from 'react';
import { sparkPath, starPath } from '../lib/mascotPaths';

export function Mascot({ mood = 'sparkly', size = 48, className = '' }) {
  const bodyD = starPath(24, 25.5, 19, 10);

  const renderFace = () => {
    switch (mood) {
      case 'sleepy':
        return (
          <>
            <path className="ms-l" d="M17.6 24.4q2 1.5 4 0M26.4 24.4q2 1.5 4 0M22.6 30.4h2.8" />
            <text className="ms-z" x="37" y="11">z</text>
            <text className="ms-z small" x="42" y="5">z</text>
          </>
        );
      case 'building':
        return (
          <>
            <ellipse className="ms-f" cx="19.6" cy="25" rx="1.6" ry="2.2" />
            <ellipse className="ms-f" cx="28.4" cy="25" rx="1.6" ry="2.2" />
            <path className="ms-l" d="M17.2 21.2l3.6 1.1M30.8 21.2l-3.6 1.1M22 30.6h4" />
          </>
        );
      case 'fixing':
        return (
          <>
            <circle className="ms-f" cx="19.6" cy="24.6" r="1.8" />
            <circle className="ms-f" cx="28.4" cy="24.6" r="1.8" />
            <path className="ms-l" d="M21.6 29.8q2.4 1.6 4.8 0" />
            <g transform="rotate(-24 31.5 29)">
              <rect className="ms-band" x="27.5" y="27.2" width="8" height="3.6" rx="1.6" />
              <path className="ms-bx" d="M30.6 28.2l1.8 1.6M32.4 28.2l-1.8 1.6" />
            </g>
          </>
        );
      case 'teary':
        return (
          <>
            <path className="ms-l" d="M17.8 25.4q1.8-1.6 3.6 0M26.6 25.4q1.8-1.6 3.6 0M21 31.2q3-2.2 6 0" />
            <path className="ms-tear" d="M19.3 27.4c-.9 1.4-1.3 2.4-.5 3.1.9.6 1.9-.2 1.7-1.2-.2-.6-.6-1.2-1.2-1.9Z" />
          </>
        );
      case 'sparkly':
      default:
        return (
          <>
            <path className="ms-f" d={sparkPath(19.6, 24.6, 2.6)} />
            <path className="ms-f" d={sparkPath(28.4, 24.6, 2.6)} />
            <path className="ms-l" d="M20.6 28.8q3.4 3.2 6.8 0" />
            <circle className="ms-cheek" cx="16.4" cy="28.6" r="2" />
            <circle className="ms-cheek" cx="31.6" cy="28.6" r="2" />
            <path className="ms-sp" d={sparkPath(42, 9, 3.4)} />
            <path className="ms-sp" d={sparkPath(6, 13, 2.4)} />
          </>
        );
    }
  };

  return (
    <svg
      className={`mascot mood-${mood} ${className}`}
      width={size}
      height={size}
      viewBox="0 0 48 48"
      aria-hidden="true"
    >
      <path className="ms-body" d={bodyD} />
      {renderFace()}
    </svg>
  );
}

export function Buddy({ status = 'ok', size = 20, className = '' }) {
  const d = starPath(12, 12.8, 9.4, 4.9);
  return (
    <svg
      className={`buddy st-${status} ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}

export function GuideBubble({ mood = 'sparkly', mascotSize = 52, children, extra = null, className = '' }) {
  return (
    <div className={`guide ${className}`}>
      <Mascot mood={mood} size={mascotSize} />
      <div className="bubble">
        {children}
        {extra}
      </div>
    </div>
  );
}
