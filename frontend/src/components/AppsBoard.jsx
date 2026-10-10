import React, { useMemo } from 'react';
import { Boxes, Plus, Radio, Sparkles } from 'lucide-react';
import AppCard from './AppCard';
import { Badge, Bar, Dot } from './ui';
import { STATUS_STYLE, classifyNode, loadRatio } from '../lib/fleetStatus';
import { sortApps, summarizeApps } from '../lib/operations';
import { GuideBubble, Mascot } from './Mascot';
import './apps-board.css';

const PERCENT = 100;

const SUMMARY_CHIPS = [
  { key: 'live', label: '운영 중', tone: 'emerald' },
  { key: 'attention', label: '확인 필요', tone: 'amber' },
  { key: 'deploying', label: '배포 중', tone: 'violet' },
  { key: 'stopped', label: '중지', tone: 'slate' },
];

const SCENARIOS = [
  { id: 'all', label: '모든 상태 (7개 앱)' },
  { id: 'handoff', label: '배포 완료 핸드오프' },
  { id: 'empty', label: '빈 목록' },
  { id: 'no-fleet', label: '노드 풀 미연결' },
  { id: 'error', label: '목록 갱신 실패' },
];

function DemoToolbar({ scenario, setScenario }) {
  return (
    <div className="ops-demo-bar" role="region" aria-label="시각 검증 시나리오">
      <div className="ops-demo-bar-label">
        <Sparkles className="h-3.5 w-3.5 text-purple-300" aria-hidden="true" />
        <span><strong>시각 검증 모드</strong> 시나리오를 선택해 상태를 직접 확인해 보세요:</span>
      </div>
      <div className="ops-demo-scenarios">
        {SCENARIOS.map((s) => (
          <button
            key={s.id}
            type="button"
            className={`ops-demo-btn ${scenario === s.id ? 'active' : ''}`}
            onClick={() => setScenario?.(s.id)}
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function SummaryChips({ summary, hasFleet }) {
  return (
    <ul className="ops-chips" aria-label="앱 상태 요약">
      {SUMMARY_CHIPS.map((c) => (
        <li key={c.key} className="ops-chip" data-tone={c.tone} data-zero={summary[c.key] === 0 || undefined}>
          {c.label} <strong>{summary[c.key]}</strong>
        </li>
      ))}
      {hasFleet && (
        <li className="ops-chip" data-tone={summary.nodesOk === summary.nodesTotal ? 'emerald' : 'amber'} data-zero={summary.nodesTotal === 0 || undefined}>
          노드 <strong>{summary.nodesOk}/{summary.nodesTotal}</strong> 정상
        </li>
      )}
    </ul>
  );
}

function AutoFollowSwitch({ autoFollow, setAutoFollow }) {
  return (
    <label className="ops-switch">
      <input type="checkbox" checked={!!autoFollow} onChange={(e) => setAutoFollow?.(e.target.checked)} />
      <Radio className="h-3.5 w-3.5" aria-hidden="true" />
      <span>GitHub push 자동 추적</span>
    </label>
  );
}

function memoryPercent(node) {
  const total = Number(node.mem_total_mb);
  const avail = Number(node.mem_avail_mb);
  if (!(total > 0) || !Number.isFinite(avail)) return null;
  return Math.round((1 - avail / total) * PERCENT);
}

function NodeMeter({ label, value, tone }) {
  return (
    <div className="ops-node-meter">
      <span>{label}</span>
      {value === null ? <span className="ops-muted">n/a</span> : <Bar value={value} tone={tone} />}
      <span className="font-mono">{value === null ? '' : `${value}%`}</span>
    </div>
  );
}

function NodeRow({ node }) {
  const status = STATUS_STYLE[classifyNode(node)];
  const cpu = node.ok ? Math.round(loadRatio(node) * PERCENT) : null;
  const mem = node.ok ? memoryPercent(node) : null;
  return (
    <li className="ops-node">
      <div className="ops-node-head">
        <Dot tone={status.tone} />
        <span className="ops-node-status">{status.label}</span>
        <span className="ops-node-name font-mono" title={node.node}>{node.node}</span>
        {node.provider && <Badge tone="sky">{node.provider}</Badge>}
      </div>
      <NodeMeter label="코어당 부하" value={cpu} tone="emerald" />
      <NodeMeter label="메모리" value={mem} tone="sky" />
      {!node.ok && node.error && <p className="ops-node-error" title={node.error}>{node.error}</p>}
    </li>
  );
}

function NodesPanel({ fleet, onShowNodes }) {
  const nodes = (Array.isArray(fleet?.nodes) ? fleet.nodes : []).filter((n) => n && typeof n === 'object');
  return (
    <section className="ops-nodes" aria-labelledby="ops-nodes-title">
      <header className="ops-nodes-head">
        <h3 id="ops-nodes-title">노드 상태</h3>
        {fleet && (
          <button type="button" className="dashboard-button" onClick={() => onShowNodes?.()}>
            <Boxes className="h-3.5 w-3.5" aria-hidden="true" /> 3D로 보기
          </button>
        )}
      </header>
      {!fleet && <p className="ops-muted">노드 풀이 연결되지 않았어요</p>}
      {fleet && nodes.length === 0 && <p className="ops-muted">아직 등록된 노드가 없어요</p>}
      {nodes.length > 0 && (
        <ul className="ops-node-list">
          {nodes.map((n, i) => <NodeRow key={`${n.node}|${i}`} node={n} />)}
        </ul>
      )}
    </section>
  );
}

function getGuideBriefing(projects, summary, projectsState) {
  if (projectsState?.status === 'loading') {
    return { mood: 'building', text: '앱 목록을 불러오는 중이에요…' };
  }
  if (projects === null || projectsState?.status === 'error') {
    return { mood: 'teary', text: '백엔드 연결에 문제가 생겼어요. 서버 연결을 확인해 주세요.' };
  }
  if (!Array.isArray(projects) || projects.length === 0) {
    return { mood: 'sleepy', text: '오늘은 조용해요. 첫 앱을 등록하고 바로 배포해 볼까요?' };
  }
  if (summary.deploying > 0) {
    return { mood: 'building', text: '새 버전을 올리는 중이에요… 잠시만 기다려 주세요.' };
  }
  if (summary.attention > 0) {
    return { mood: 'fixing', text: '점검이 필요한 앱이 있어요. 로그를 확인하고 다시 배포해 보세요.' };
  }
  return { mood: 'sparkly', text: '모든 앱이 정상적으로 잘 돌아가고 있어요 ✦' };
}

function EmptyState({ projects, projectsState, onNewDeploy, onEnableDemo }) {
  if (projectsState?.status === 'loading') {
    return <p className="ops-empty" role="status">앱 목록을 불러오는 중이에요…</p>;
  }
  if (projects === null || projectsState?.status === 'error') {
    return (
      <div className="ops-empty ops-empty-error" role="status">
        <Mascot mood="teary" size={60} />
        <p>앱 목록을 불러오지 못했어요. 백엔드 연결을 확인해 주세요.</p>
        {onEnableDemo && (
          <button type="button" className="dashboard-button primary" onClick={onEnableDemo}>
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" /> 시각 검증(데모) 데이터로 둘러보기
          </button>
        )}
      </div>
    );
  }
  return (
    <div className="ops-empty first">
      <div className="first-ic">
        <Mascot mood="sleepy" size={68} />
      </div>
      <p className="font-semibold text-slate-100">아직 배포한 앱이 없어요</p>
      <p className="max-w-md text-xs text-slate-400">
        GitHub 저장소를 고르면 AI가 코드를 읽고 알아서 올려요. 다 되면 이 앱이 어디서 돌아가는지 지도로 보여 드려요.
      </p>
      <div className="flex flex-wrap items-center justify-center gap-2 mt-2">
        <button type="button" className="dashboard-button primary" onClick={() => onNewDeploy?.()}>
          <Plus className="h-3.5 w-3.5" aria-hidden="true" /> 새 배포
        </button>
        {onEnableDemo && (
          <button type="button" className="dashboard-button" onClick={onEnableDemo}>
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" /> 시각 검증(데모) 모드
          </button>
        )}
      </div>
    </div>
  );
}

export default function AppsBoard({
  projects, projectsState, fleet, currentRunId, runRunning, highlight, stopping, redeploying,
  autoFollow, setAutoFollow, onOpenLogs, onRedeploy, onStop, onNewDeploy, onShowNodes,
  demoMode, scenario, setScenario, onEnableDemo,
}) {
  const apps = useMemo(() => sortApps(projects), [projects]);
  const summary = useMemo(() => summarizeApps(projects, fleet), [projects, fleet]);
  const hasApps = apps.length > 0;
  const guide = useMemo(() => getGuideBriefing(projects, summary, projectsState), [projects, summary, projectsState]);

  return (
    <div className="ops-board">
      <header className="ops-board-head">
        <div className="ops-board-title">
          <h2>앱 관리</h2>
          <p>배포한 앱이 지금 어디서 돌아가는지, 무엇을 할 수 있는지 한눈에 봐요</p>
        </div>
        <div className="ops-board-tools">
          {Array.isArray(projects) && <SummaryChips summary={summary} hasFleet={!!fleet} />}
          <AutoFollowSwitch autoFollow={autoFollow} setAutoFollow={setAutoFollow} />
        </div>
      </header>

      {/* Mascot Briefing Guide */}
      <div className="picker-guide">
        <GuideBubble mood={guide.mood} mascotSize={52}>
          <p>{guide.text}</p>
        </GuideBubble>
      </div>

      {demoMode && <DemoToolbar scenario={scenario} setScenario={setScenario} />}

      <div className="ops-board-body">
        <div className="ops-apps">
          {hasApps && projectsState?.status === 'error' && (
            <div className="ops-banner ops-banner-warning" role="alert">
              앱 목록 갱신에 실패했어요. 백엔드 연결을 확인해 주세요.
            </div>
          )}
          {hasApps ? (
            <div className="ops-grid">
              {apps.map((p) => (
                <AppCard
                  key={p.name}
                  project={p}
                  fleet={fleet}
                  isWatched={!!currentRunId && p.last_deployment_id === currentRunId}
                  isHighlighted={!!highlight && (highlight === p.name || highlight?.name === p.name)}
                  isStopping={stopping instanceof Set ? stopping.has(p.name) : stopping === p.name}
                  isRedeploying={redeploying === p.name}
                  redeployBusy={!!redeploying}
                  runRunning={!!runRunning}
                  onOpenLogs={onOpenLogs}
                  onRedeploy={onRedeploy}
                  onStop={onStop}
                />
              ))}

              {/* Add New App Card */}
              <button
                type="button"
                className="acard add ops-card-add"
                onClick={() => onNewDeploy?.()}
                aria-label="새 앱 올리기"
              >
                <span className="add-ic">
                  <Plus size={22} aria-hidden="true" />
                </span>
                <b className="font-semibold text-slate-100 text-sm">새 앱 올리기</b>
                <span className="text-xs text-slate-400">저장소를 고르면 AI가 알아서 올려요.</span>
              </button>
            </div>
          ) : <EmptyState projects={projects ?? null} projectsState={projectsState} onNewDeploy={onNewDeploy} onEnableDemo={onEnableDemo} />}
        </div>
        <NodesPanel fleet={fleet} onShowNodes={onShowNodes} />
      </div>
    </div>
  );
}
