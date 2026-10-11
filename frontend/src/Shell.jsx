import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Cloud, KeyRound } from 'lucide-react';
import {
  answerQuestion, cancelDeploy, drill, getDeployment, getHealth, getIncidents, getProjects, getToken, moveProject,
  setResource, setToken, startDeploy, stopProject, submitAnswers, subscribe,
} from './api';
import { applyEvent, emptyRun } from './lib/run';
import DeployPage from './onetouch/DeployPage';
import AppsPage from './onetouch/AppsPage';
import IncidentsPage from './onetouch/IncidentsPage';
import { PLACES } from './onetouch/copy';
import { sentence } from './onetouch/progress';
import './onetouch/onetouch.css';

const TABS = [
  { id: 'deploy', label: '배포하기' },
  { id: 'apps', label: '내 앱' },
  { id: 'incidents', label: '장애 대응' },
];
// These screens are the front door ("#/<tab>", default 배포하기); the 3D dashboard lives at "#/3d" (Root.jsx).
const tabFromHash = () => TABS.find((t) => window.location.hash === `#/${t.id}`)?.id || 'deploy';

/** The one-touch experience, CloudMorph's front door: deploy in one click, see every app on a map, and watch
 * the incident guard. The team's 3D dashboard (App) is one link away at "#/3d". */
export default function Shell() {
  const [tab, setTab] = useState(tabFromHash);
  const [form, setForm] = useState({ source: 'sample-apps/guestbook', name: '', ref: '', place: 'auto' });
  const [run, setRun] = useState(() => emptyRun());
  const [now, setNow] = useState(() => Date.now());
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [projects, setProjects] = useState(null);
  const [snapshot, setSnapshot] = useState(null);
  const [healthy, setHealthy] = useState(null);
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const [token, setTokenState] = useState(getToken());
  const [showToken, setShowToken] = useState(false);
  const closeRef = useRef(null);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const go = (id) => { window.location.hash = `#/${id}`; setTab(id); };

  useEffect(() => {
    if (run.status !== 'running') return undefined;
    const h = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(h);
  }, [run.status]);

  const attach = useCallback((id, meta) => {
    closeRef.current?.();
    setRun(emptyRun(id, meta));
    closeRef.current = subscribe(id, (type, p, raw) => setRun((r) => (r.id === id ? applyEvent(r, type, p, raw) : r)));
  }, []);

  useEffect(() => {
    if (run.id && run.status !== 'running') {
      closeRef.current?.();
      closeRef.current = null;
      getDeployment(run.id).catch(() => {});
      getProjects().then((p) => p && setProjects(p)).catch(() => {});
      setNotice((n) => (n ? sentence(run) : n)); // "다시 배포하고 있어요" -> how it ended
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when a run finishes
  }, [run.id, run.status]);

  // projects and health: quicker while someone is looking at them
  useEffect(() => {
    let alive = true;
    const watching = tab === 'apps' || tab === 'incidents';
    const poll = async () => {
      const [h, p, s] = await Promise.all([getHealth(), getProjects().catch(() => null), getIncidents().catch(() => null)]);
      if (!alive) return;
      setHealthy(h);
      if (p) setProjects(p);
      if (s) setSnapshot(s);
    };
    poll();
    const id = setInterval(poll, watching ? 2000 : 6000);
    return () => { alive = false; clearInterval(id); };
  }, [tab]);

  const flash = (text) => { setNotice(text); setTimeout(() => setNotice((n) => (n === text ? null : n)), 8000); };

  const onStart = async () => {
    setStarting(true);
    const targets = form.place === 'auto' ? [] : [form.place];
    try {
      const id = await startDeploy({ source: form.source.trim(), targets, name: form.name.trim() || undefined, ref: form.ref.trim() || undefined });
      attach(id, { source: form.source, targets, name: form.name.trim() || `cloudmorph-${id}`, trigger: 'api' });
    } catch (e) {
      setRun({ ...emptyRun(), status: 'failed', error: `배포를 시작하지 못했어요 · ${e.message}`, startedAt: Date.now(), finishedAt: Date.now() });
    } finally {
      setStarting(false);
    }
  };

  const follow = (project, id, text) => {
    attach(id, { source: project.source, targets: [], name: project.name, trigger: 'api' });
    flash(text);
  };

  const act = async (fn) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      flash(`하지 못했어요 · ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const onResource = (project, kind, mode, url) => act(async () => {
    const r = await setResource(project.name, { kind, mode, ...(url ? { url } : {}) });
    const what = mode === 'none' ? 'DB 연결을 끊고' : 'DB를 연결하고';
    if (r.deployment_id) follow(project, r.deployment_id, `${what} 앱을 다시 배포하고 있어요.${r.warning ? ` ${r.warning}` : ''}`);
    else flash(r.warning || '저장했어요. 다음 배포부터 적용돼요.');
  });

  const onMove = (project, to) => act(async () => {
    const r = await moveProject(project.name, to);
    follow(project, r.deployment_id, `${PLACES[to]?.name || to}에 띄우고 있어요. 다 뜨면 원래 자리의 앱을 내려요.`);
  });

  const onRedeploy = (project) => act(async () => {
    const live = Object.keys(project.live || {});
    const id = await startDeploy({ source: project.source, targets: live.length ? live : project.targets || [], name: project.name, ref: project.ref || undefined });
    follow(project, id, '같은 곳에 새 버전으로 다시 배포하고 있어요.');
  });

  const onStop = (project) => act(async () => {
    if (!window.confirm(`${project.name} 앱을 멈출까요?\n떠 있는 곳의 앱을 모두 내려요. DB 안의 데이터는 남겨 둬요.`)) return;
    const r = await stopProject(project.name);
    const failed = Object.entries(r.failed || {});
    flash(failed.length ? `일부를 멈추지 못했어요 · ${failed.map(([t, e]) => `${t}: ${e}`).join(' · ')}` : `${project.name} 앱을 멈췄어요.`);
    getProjects().then((p) => p && setProjects(p)).catch(() => {});
  });

  const onCancel = async () => {
    if (!window.confirm('진행 중인 배포를 멈출까요?\n이미 떠 있는 곳은 그대로 두고, 나머지는 시작하지 않아요.')) return;
    setCancelling(true);
    try {
      await cancelDeploy(run.id);
    } catch (e) {
      flash(`멈추지 못했어요 · ${e.message}`);
    } finally {
      setCancelling(false);
    }
  };

  const cloudReady = !!projects?.some((p) => p.live?.cloudrun);
  const saveToken = (v) => { setToken(v); setTokenState(v); };

  return (
    <div className="min-h-screen bg-[#0a0d14] text-slate-100">
      <Nav tab={tab} go={go} healthy={healthy} token={token} showToken={showToken} setShowToken={setShowToken} saveToken={saveToken} />
      <main className="mx-auto max-w-[1200px] px-4 pb-16 pt-6 sm:px-6">
        {notice && tab !== 'apps' && <p className="mb-4 rounded-xl border border-sky-500/30 bg-sky-500/10 px-4 py-2.5 text-[13px] text-sky-100" role="status">{notice}</p>}
        {tab === 'deploy' && (
          <DeployPage form={form} setForm={setForm} run={run} now={now} starting={starting} cancelling={cancelling}
            onStart={onStart} onCancel={onCancel}
            onAnswer={(values, targets) => submitAnswers(run.id, values, targets)}
            onAnswerQuestion={(qid, choice) => answerQuestion(run.id, qid, choice)}
            onOpenApps={(name) => { setSelected(name); go('apps'); }} />
        )}
        {tab === 'apps' && (
          <AppsPage projects={projects} health={snapshot?.health} selected={selected} onSelect={setSelected} run={run}
            busy={busy} notice={notice} cloudReady={cloudReady} onResource={onResource} onMove={onMove}
            onRedeploy={onRedeploy} onStop={onStop} onWatch={() => go('deploy')} />
        )}
        {tab === 'incidents' && <IncidentsPage snapshot={snapshot} projects={projects} onDrill={drill} />}
      </main>
    </div>
  );
}

function Nav({ tab, go, healthy, token, showToken, setShowToken, saveToken }) {
  return (
    <nav className="sticky top-0 z-30 border-b border-white/5 bg-[#0a0d14]/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1200px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 sm:px-6">
        <button type="button" onClick={() => go('deploy')} className="flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600"><Cloud className="h-4 w-4 text-white" /></span>
          <span className="text-[15px] font-bold tracking-tight text-white">CloudMorph</span>
        </button>
        <div className="-mx-1 order-last flex w-full min-w-0 gap-1 overflow-x-auto px-1 sm:order-none sm:w-auto sm:flex-1" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => go(t.id)}
              className={`shrink-0 rounded-lg px-3 py-1.5 text-[13px] font-medium transition ${tab === t.id ? 'bg-white/10 text-white' : 'text-slate-400 hover:text-slate-100'}`}>
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 text-[12px]">
          <a href="#/3d" className="flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-slate-300 hover:text-white" title="3D 대시보드에서 배포 여정과 노드 풀을 볼 수 있어요">
            <Box className="h-3.5 w-3.5" /> 3D 대시보드
          </a>
          <span className="flex items-center gap-1.5 text-slate-400">
            <span className={`h-2 w-2 rounded-full ${healthy ? 'bg-emerald-400' : healthy === false ? 'bg-rose-400' : 'bg-slate-500'}`} />
            {healthy ? '연결됨' : healthy === false ? '서버와 끊겼어요' : '확인 중'}
          </span>
          <button type="button" onClick={() => setShowToken((s) => !s)} className={`flex items-center gap-1 rounded-lg border px-2 py-1 ${token ? 'border-emerald-500/30 text-emerald-300' : 'border-white/10 text-slate-400'} hover:text-white`}>
            <KeyRound className="h-3.5 w-3.5" /> {token ? '토큰 있음' : 'API 토큰'}
          </button>
          {showToken && (
            <input autoFocus type="password" value={token} onChange={(e) => saveToken(e.target.value)} placeholder="공개 주소로 열었을 때만 필요해요"
              className="w-52 rounded-lg border border-white/10 bg-black/40 px-2.5 py-1 text-slate-100 focus:border-violet-500/60 focus:outline-none" />
          )}
        </div>
      </div>
    </nav>
  );
}
