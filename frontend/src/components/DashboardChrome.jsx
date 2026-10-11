import React from 'react';
import { Boxes, Cloud, ExternalLink, LayoutGrid, LogOut, Maximize, Minimize, MousePointerClick, Play, Rocket, Settings2, Sparkles, Square } from 'lucide-react';
import { TARGET_META, stepStatus } from '../lib/run';
import { activeStepLabel, liveEndpoints, pendingInput } from '../lib/dashboard';
import { fmtDur } from '../lib/style';
import { Dot } from './ui';
import LogConsole from './LogConsole';

const STEPS = { analyze: '분석', deploy: '배포', heal: '자가치유', live: '공개 URL' };
const PHASES = { pending: '대기', deploying: '배포 중', healing: '치유 중', done: '완료', failed: '실패', cancelled: '중단' };
const SCENE_TABS = [['journey', '배포 여정', Rocket], ['apps', '앱 관리', LayoutGrid], ['fleet', '노드 풀', Boxes]];
const ROVING_KEYS = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];

function rovingIndex(key, index, count) {
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  const step = key === 'ArrowRight' ? 1 : -1;
  return (index + step + count) % count;
}

export function DashboardHeader({ scene, onScene, healthy, onPanel, fullscreen, onFullscreen, demoMode, onToggleDemo, user, onLogout }) {
  return (
    <header className="dashboard-header">
      <div className="dashboard-brand"><Cloud size={25} /><h1>CloudMorph</h1></div>
      <div className="scene-tabs" role="tablist" aria-label="화면 전환">
        {SCENE_TABS.map(([id, label, Icon], index) => (
          <button key={id} id={`scene-tab-${id}`} role="tab" aria-selected={scene === id} aria-controls="scene-view" tabIndex={scene === id ? 0 : -1}
            onClick={() => onScene(id)} onKeyDown={(e) => {
              if (!ROVING_KEYS.includes(e.key)) return;
              e.preventDefault();
              const [next] = SCENE_TABS[rovingIndex(e.key, index, SCENE_TABS.length)];
              onScene(next); document.getElementById(`scene-tab-${next}`)?.focus();
            }}><Icon size={15} />{label}</button>
        ))}
      </div>
      <div className="header-actions">
        <a className="dashboard-button" href="#/deploy" title="원터치 배포 · 서버 지도 · 장애 대응">
          <MousePointerClick size={14} aria-hidden="true" />
          <span>원터치</span>
        </a>
        <button
          type="button"
          className={`dashboard-button ${demoMode ? 'primary' : ''}`}
          onClick={onToggleDemo}
          title={demoMode ? '시각 검증(데모) 모드 끄기' : '시각 검증(데모) 모드 켜기'}
        >
          <Sparkles size={14} aria-hidden="true" />
          <span>{demoMode ? '검증 모드 ON' : '시각 검증 모드'}</span>
        </button>
        <span className="connection-status" title={`백엔드 ${healthy ? '연결됨' : healthy === false ? '끊김' : '확인 중'}`}>
          <Dot tone={healthy ? 'emerald' : healthy === false ? 'rose' : 'slate'} /><span>백엔드 {healthy ? '연결됨' : healthy === false ? '끊김' : '확인 중'}</span>
        </span>
        {user && <span className="connection-status" title="로그인한 사용자">{user}</span>}
        {onLogout && <button type="button" className="dashboard-button" onClick={onLogout}><LogOut size={14} />로그아웃</button>}
        <button className="dashboard-button primary" onClick={() => onPanel('deploy')}><Play size={14} />새 배포</button>
        <button className="dashboard-button" aria-label="배포 상세" onClick={() => onPanel('details')}><Settings2 size={15} /><span>배포 상세</span></button>
        <button className="dashboard-button icon-button" aria-label={fullscreen ? '전체화면 종료' : '전체화면'} onClick={onFullscreen} disabled={!document.fullscreenEnabled}>
          {fullscreen ? <Minimize size={16} /> : <Maximize size={16} />}
        </button>
      </div>
    </header>
  );
}

export function RunHUD({ run, now, notice, onPanel, onCancel, cancelling, celebrate, onShowApp }) {
  const steps = stepStatus(run);
  const pending = pendingInput(run);
  const elapsed = run.status === 'idle' ? null : Math.max(0, (run.finishedAt || now) - run.startedAt);
  return (
    <section className="run-hud" aria-label="배포 상태">
      <div className="run-headline">
        <div className="run-title"><span className={`status-light status-${run.status}`} /><strong>{celebrate ? 'AI 자가치유 성공' : activeStepLabel(run)}</strong>
          <span className="run-identity">{run.name || run.id || 'One Action, Infinite Clouds'}</span>
        </div>
        <div className="run-actions">
          {elapsed != null && <span className="elapsed">{fmtDur(elapsed)}</span>}
          {pending && <button className="dashboard-button attention" onClick={() => onPanel('details')}>입력 필요</button>}
          {onShowApp && <button className="dashboard-button" onClick={onShowApp}><LayoutGrid size={13} />앱 관리에서 보기</button>}
          {run.status === 'running' && run.id && <button className="dashboard-button danger" disabled={cancelling} onClick={onCancel}><Square size={12} />{cancelling ? '중단 중…' : '중단'}</button>}
        </div>
      </div>
      <div className="run-progress">
        <ol className="compact-steps">{Object.entries(STEPS).map(([id, label], index) => <li key={id} data-state={steps[id]}><span>{index + 1}</span>{label}</li>)}</ol>
        <div className="target-summary">{run.targets.map((target) => <span key={target} data-phase={run.targetState[target]?.phase}>{TARGET_META[target]?.label || target} · {PHASES[run.targetState[target]?.phase] || '대기'}</span>)}</div>
      </div>
      {(pending || run.error || notice) && <div className={`hud-message ${run.error ? 'error' : ''}`} role={run.error ? 'alert' : 'status'}>
        {pending ? pending.label : run.error || notice}
      </div>}
    </section>
  );
}

export function ActivityDock({ run, project }) {
  const urls = liveEndpoints(run, project);
  return (
    <section className="activity-dock" aria-label="파이프라인 로그">
      <LogConsole logs={run.logs} running={run.status === 'running'} height="var(--dock-log-height)" />
      {urls.length > 0 && <div className="live-links">{urls.map(({ target, label, url }) => <a key={target} href={url} target="_blank" rel="noopener noreferrer"><ExternalLink size={12} />{label} 열기</a>)}</div>}
    </section>
  );
}
