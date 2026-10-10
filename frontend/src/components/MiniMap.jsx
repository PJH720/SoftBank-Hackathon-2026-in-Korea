import React from 'react';

const PLACE_LABEL = {
  local: 'Local Docker',
  cloudrun: 'Cloud Run',
  node: 'Node Pool',
  function: 'Functions',
};

export default function MiniMap({ project, status = 'ok', className = '' }) {
  const targets = Array.isArray(project?.targets) && project.targets.length > 0 ? project.targets : ['local'];
  const n = targets.length;
  const bh = n > 2 ? 22 : 26;
  const gap = n > 2 ? 6 : 8;
  const tot = n * bh + (n - 1) * gap;
  const y0 = 60 - tot / 2;

  // Detect database association (e.g. guestbook has PostgreSQL)
  const hasDb = Boolean(
    project?.name === 'guestbook' ||
    project?.store ||
    project?.has_db ||
    project?.live?.db
  );

  const statusClass =
    status === 'live' || status === 'ok'
      ? 'st-ok'
      : status === 'deploying' || status === 'starting'
      ? 'st-starting'
      : status === 'attention' || status === 'failed' || status === 'fail'
      ? 'st-fail'
      : 'st-idle';

  return (
    <svg
      className={`mini-map-svg ${className}`}
      viewBox="0 0 320 120"
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
      focusable="false"
    >
      {/* User Origin */}
      <circle className="mm-user" cx="24" cy="60" r="7" />
      <text className="mm-user-t" x="24" y="78" textAnchor="middle">User</text>

      {/* Target Nodes */}
      {targets.map((t, i) => {
        const y = y0 + i * (bh + gap);
        const cy = y + bh / 2;
        const label = PLACE_LABEL[t] || t;
        const boxWidth = hasDb ? 124 : 148;
        const boxX = 86;

        return (
          <g key={t}>
            {/* Connection line from user to target */}
            <path
              className={`mm-line ${statusClass === 'st-ok' ? 'live' : ''}`}
              d={`M32 60 C 60 60, 60 ${cy}, ${boxX} ${cy}`}
            />

            {/* Target pill box */}
            <rect
              className="mm-box"
              x={boxX}
              y={y}
              width={boxWidth}
              height={bh}
              rx={bh / 2}
            />

            {/* Target icon container */}
            <rect
              className="mm-app"
              x={boxX + 5}
              y={y + 3}
              width={bh - 6}
              height={bh - 6}
              rx={(bh - 6) / 2}
            />

            {/* Status dot */}
            <circle
              className={`mm-dot ${statusClass}`}
              cx={boxX + bh + 4}
              cy={cy}
              r="3"
            />

            {/* Label */}
            <text
              className="mm-t"
              x={boxX + bh + 12}
              y={cy + 3.5}
            >
              {label}
            </text>

            {/* Database node if attached */}
            {hasDb && (
              <>
                <path
                  className={`mm-line ${statusClass === 'st-ok' ? 'live' : ''}`}
                  d={`M${boxX + boxWidth} ${cy} H${boxX + boxWidth + 24}`}
                />
                <rect
                  className="mm-db"
                  x={boxX + boxWidth + 24}
                  y={y + 3}
                  width="28"
                  height={bh - 6}
                  rx="5"
                />
                <text
                  className="mm-db-t"
                  x={boxX + boxWidth + 38}
                  y={cy + 3.5}
                  textAnchor="middle"
                >
                  DB
                </text>
              </>
            )}
          </g>
        );
      })}
    </svg>
  );
}
