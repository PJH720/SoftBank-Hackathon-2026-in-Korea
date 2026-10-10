import React, { useEffect, useId, useMemo, useRef } from 'react';
import { Cloud, ExternalLink, Eye, GitBranch, Network, RotateCw, ScrollText, Server, Zap } from 'lucide-react';
import { Badge, Bar, CopyButton, StopButton } from './ui';
import { fmtAgo } from '../lib/style';
import { canStop } from '../lib/run';
import { TARGET_STATE, appStatus, appTargets, canRedeploy, deployMeta, fleetAppFor, primaryUrl, replicaRows } from '../lib/operations';

const TARGET_ICON = { local: Server, cloudrun: Cloud, node: Network, function: Zap };
const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

function prefersReducedMotion() {
  return typeof window !== 'undefined' && !!window.matchMedia?.(REDUCED_MOTION_QUERY).matches;
}

function ReplicaList({ fleetApp }) {
  const rows = replicaRows(fleetApp);
  return (
    <div className="ops-replicas">
      <p className="ops-replicas-title">복제본 {rows.length}개</p>
      {rows.length > 0 && (
        <ul className="ops-replica-list">
          {rows.map((r, i) => (
            <li key={`${r.node}|${r.upstream}|${i}`} className="ops-replica">
              <span className="ops-replica-node font-mono" title={r.node ?? ''}>{r.node ?? '알 수 없는 노드'}</span>
              <Bar value={r.cpu ?? 0} tone={r.tone} />
              <span className="ops-replica-cpu font-mono">{r.cpu === null ? '–' : `${r.cpu}%`}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TargetRow({ row, fleetApp }) {
  const Icon = TARGET_ICON[row.target] || Server;
  const state = TARGET_STATE[row.state] || TARGET_STATE.idle;
  return (
    <li className="ops-target" data-state={row.state}>
      <div className="ops-target-head">
        <Icon className="ops-target-icon" aria-hidden="true" />
        <span className="ops-target-label">{row.label}</span>
        <Badge tone={state.tone}>{state.label}</Badge>
      </div>
      {row.url && (
        <div className="ops-target-url">
          <span className="font-mono" title={row.url}>{row.url}</span>
          <CopyButton text={row.url} />
          <a href={row.url} target="_blank" rel="noopener noreferrer" aria-label={`${row.label} 열기`} className="ops-icon-link">
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
          </a>
        </div>
      )}
      {row.state === 'serving_previous' && <p className="ops-target-note">이번 배포는 실패 · 이전 버전 유지 중</p>}
      {row.target === 'node' && fleetApp && <ReplicaList fleetApp={fleetApp} />}
    </li>
  );
}

function CardActions({ project, url, redeploy, isRedeploying, isStopping, onOpenLogs, onRedeploy, onStop }) {
  return (
    <footer className="ops-card-actions">
      {url && (
        <a href={url} target="_blank" rel="noopener noreferrer" className="dashboard-button primary ops-link-button">
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" /> 열기
        </a>
      )}
      <button type="button" className="dashboard-button" onClick={() => onOpenLogs?.(project)}>
        <ScrollText className="h-3.5 w-3.5" aria-hidden="true" /> 로그 보기
      </button>
      <button type="button" className="dashboard-button" disabled={!redeploy.ok} title={redeploy.reason ?? undefined} onClick={() => onRedeploy?.(project)}>
        <RotateCw className="h-3.5 w-3.5" aria-hidden="true" /> {isRedeploying ? '다시 배포 중…' : '다시 배포'}
      </button>
      {canStop(project) && <StopButton onClick={() => onStop?.(project.name)} busy={isStopping} appName={project.name} />}
    </footer>
  );
}

export default function AppCard({ project, fleet, isWatched, isHighlighted, isStopping, isRedeploying, redeployBusy, runRunning, onOpenLogs, onRedeploy, onStop }) {
  const ref = useRef(null);
  const headingId = useId();
  const targets = useMemo(() => appTargets(project), [project]);
  const status = useMemo(() => appStatus(project, targets), [project, targets]);
  const fleetApp = useMemo(() => fleetAppFor(project, fleet), [project, fleet]);
  const meta = deployMeta(project);
  const redeploy = canRedeploy(project, { runRunning, redeploying: redeployBusy });
  const url = primaryUrl(targets);

  // Keyed by name, so the 4 s refetch keeps this instance and only a highlight change scrolls again.
  useEffect(() => {
    if (!isHighlighted) return;
    ref.current?.scrollIntoView?.({ block: 'nearest', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  }, [isHighlighted]);

  return (
    <article ref={ref} className={`ops-card${isHighlighted ? ' ops-card-highlight' : ''}`} data-status={status.key} aria-labelledby={headingId}>
      <header className="ops-card-head">
        <span className="ops-light" data-status={status.key} aria-hidden="true" />
        <h3 id={headingId} className="ops-card-name font-mono" title={project.name}>{project.name}</h3>
        <Badge tone={status.tone}>{status.label}</Badge>
        {isWatched && <span className="ops-watch-chip"><Eye className="h-3 w-3" aria-hidden="true" /> 보는 중</span>}
      </header>
      <p className="ops-sentence">{status.sentence}</p>
      <p className="ops-meta">
        <span className="ops-meta-trigger">
          {project.trigger === 'github-push' && <GitBranch className="h-3 w-3" aria-hidden="true" />}
          {meta.triggerLabel}
        </span>
        {meta.updatedAt !== null && <span>{fmtAgo(meta.updatedAt)}</span>}
        <span>배포 {meta.count}회</span>
      </p>
      {project.source && <p className="ops-source font-mono" title={project.source}>{project.source}</p>}

      <section className="ops-targets">
        <h4 className="ops-subheading">서비스 위치</h4>
        {targets.length ? (
          <ul className="ops-target-list">
            {targets.map((row) => <TargetRow key={row.target} row={row} fleetApp={fleetApp} />)}
          </ul>
        ) : <p className="ops-muted">아직 서비스 위치 정보가 없어요</p>}
      </section>

      <CardActions
        project={project}
        url={url}
        redeploy={redeploy}
        isRedeploying={isRedeploying}
        isStopping={isStopping}
        onOpenLogs={onOpenLogs}
        onRedeploy={onRedeploy}
        onStop={onStop}
      />
    </article>
  );
}
