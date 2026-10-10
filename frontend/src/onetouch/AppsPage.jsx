import React, { useState } from 'react';
import { ArrowLeftRight, Cloud, Database, ExternalLink, Loader2, Monitor, Network, RotateCcw, Square, Zap } from 'lucide-react';
import ServerMap from './ServerMap';
import Explain from './Explain';
import { PLACES, STATUS, ago, josa } from './copy';

const PLACE_ICON = { local: Monitor, cloudrun: Cloud, node: Network, function: Zap };
const COLUMNS = ['local', 'cloudrun', 'node', 'function'];

function appHealth(health, name, target) {
  const h = health?.[name]?.[target];
  if (!h) return 'slate';
  return h.ok ? 'emerald' : 'rose';
}

const DOT = { emerald: 'bg-emerald-400', rose: 'bg-rose-400', amber: 'bg-amber-400', violet: 'bg-violet-400', slate: 'bg-slate-500' };

/** Every place with the apps that run there: the whole fleet at a glance. */
function Overview({ projects, health, selected, onSelect }) {
  const columns = COLUMNS.filter((c) => c === 'local' || c === 'cloudrun' || projects.some((p) => p.live?.[c]));
  return (
    <section className="rounded-2xl border border-white/5 bg-slate-900/60 p-4">
      <p className="mb-3 text-sm font-semibold text-white">어디에서 무엇이 돌아가고 있나요?</p>
      <div className={`grid gap-3 ${columns.length > 2 ? 'md:grid-cols-3' : 'md:grid-cols-2'}`}>
        {columns.map((c) => {
          const Icon = PLACE_ICON[c];
          const here = projects.filter((p) => p.live?.[c]);
          return (
            <div key={c} className="rounded-xl border border-white/5 bg-black/20 p-3">
              <p className="flex items-center gap-1.5 text-[12px] font-semibold text-slate-200">
                <Icon className="h-4 w-4 text-slate-400" /> <Explain term={PLACES[c].name} text={PLACES[c].explain}>{PLACES[c].name}</Explain>
                <span className="font-normal text-slate-500">· {PLACES[c].tech}</span>
              </p>
              <div className="mt-2 space-y-1.5">
                {here.length === 0 && <p className="text-[11px] text-slate-600">아직 여기서 돌아가는 앱이 없어요</p>}
                {here.map((p) => {
                  const tone = appHealth(health, p.name, c);
                  const hasDb = p.live[c]?.db_container || p.live[c]?.cloudsql_connection || p.resources?.postgres?.mode === 'url';
                  return (
                    <button key={p.name} type="button" onClick={() => onSelect(p.name)}
                      className={`flex w-full items-center gap-2 rounded-lg border px-2.5 py-2 text-left text-[12px] transition ${selected === p.name ? 'border-violet-400/50 bg-violet-500/10' : 'border-white/5 hover:border-white/20'}`}>
                      <span className="relative inline-flex h-2 w-2 shrink-0">
                        {tone === 'rose' && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400 opacity-60" />}
                        <span className={`relative inline-flex h-2 w-2 rounded-full ${DOT[tone]}`} />
                      </span>
                      <span className="min-w-0 flex-1 truncate font-mono text-slate-100">{p.name}</span>
                      {hasDb && <Database className="h-3.5 w-3.5 text-slate-400" aria-label="DB 연결됨" />}
                      {health?.[p.name]?.[c]?.ms != null && tone === 'emerald' && <span className="tabular-nums text-[11px] text-slate-500">{health[p.name][c].ms}ms</span>}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function MoveSwitch({ project, onMove, busy, cloudReady }) {
  const [confirm, setConfirm] = useState(null);
  const live = Object.keys(project.live || {});
  const options = ['local', 'cloudrun'];
  return (
    <div>
      <div className="inline-flex rounded-xl border border-white/10 bg-black/30 p-1" role="radiogroup" aria-label="앱이 돌아갈 곳">
        {options.map((t) => {
          const Icon = PLACE_ICON[t];
          const on = live.includes(t);
          return (
            <button key={t} type="button" role="radio" aria-checked={on} disabled={busy || (on && live.length === 1)}
              onClick={() => !on && setConfirm(t)}
              className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] font-semibold transition ${on ? 'bg-white text-slate-900' : 'text-slate-400 hover:text-white'} disabled:cursor-default`}>
              <Icon className="h-3.5 w-3.5" /> {PLACES[t].name}
            </button>
          );
        })}
      </div>
      {confirm && (
        <div role="dialog" className="mt-3 rounded-2xl border border-violet-500/40 bg-slate-900 p-4 shadow-2xl shadow-black/50">
          <p className="flex items-center gap-1.5 text-sm font-bold text-white"><ArrowLeftRight className="h-4 w-4" /> {josa(PLACES[confirm].name, '으로', '로')} 옮길까요?</p>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-[12px] leading-5 text-slate-300">
            <li>{PLACES[confirm].explain}</li>
            <li>같은 앱을 {PLACES[confirm].name}에 새로 띄우고, 다 뜨면 {live.map((t) => PLACES[t]?.name || t).join(', ')}의 앱은 내려요. 그 사이에도 서비스는 계속돼요.</li>
            <li>저장해 둔 설정값은 그대로 가져가요. DB 안의 데이터는 같이 옮겨지지 않아요 (새 DB로 시작해요).</li>
            {confirm === 'cloudrun' && !cloudReady && <li className="text-amber-200">이 서버에는 클라우드 설정(GCP_PROJECT_ID)이 없어서 옮기기가 실패할 수 있어요.</li>}
          </ul>
          <div className="mt-3 flex gap-2">
            <button type="button" disabled={busy} onClick={async () => { await onMove(confirm); setConfirm(null); }}
              className="flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-[13px] font-bold text-slate-900 hover:bg-slate-200 disabled:bg-slate-700">
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} 옮기기
            </button>
            <button type="button" onClick={() => setConfirm(null)} className="rounded-lg border border-white/10 px-3 py-1.5 text-[13px] text-slate-300 hover:text-white">취소</button>
          </div>
        </div>
      )}
    </div>
  );
}

function Detail({ project, health, run, busy, notice, cloudReady, onResource, onMove, onRedeploy, onStop, onWatch }) {
  const st = STATUS[project.last_status] || STATUS.running;
  const urls = Object.entries(project.urls || {}).filter(([, u]) => u);
  const working = run && run.name === project.name && run.status === 'running';
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-white/5 bg-slate-900/60 p-4">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-lg font-bold text-white">{project.name}</span>
            <span className={`flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium bg-white/5 text-slate-200`}>
              <span className={`h-1.5 w-1.5 rounded-full ${DOT[st.tone] || DOT.slate}`} /> {st.label}
            </span>
          </p>
          <p className="mt-0.5 truncate font-mono text-[11px] text-slate-500">{project.source}{project.ref ? ` @ ${project.ref}` : ''}</p>
          <p className="mt-1 text-[12px] text-slate-400">
            {project.updated_at ? `${ago(project.updated_at)} 배포` : ''}
            {project.decision ? ` · AI 추천: ${project.decision.label} (${(project.decision.ms / 1000).toFixed(2)}초 판단)` : ''}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {urls.map(([t, u]) => (
              <a key={t} href={u} target="_blank" rel="noreferrer" className="flex items-center gap-1 rounded-lg border border-white/10 px-2.5 py-1 text-[12px] text-sky-200 hover:border-sky-400/40">
                {PLACES[t]?.name || t} 열기 <ExternalLink className="h-3 w-3" />
              </a>
            ))}
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <MoveSwitch project={project} onMove={onMove} busy={busy || working} cloudReady={cloudReady} />
          <div className="flex gap-2">
            <button type="button" disabled={busy || working} onClick={onRedeploy} className="flex items-center gap-1 rounded-lg border border-white/10 px-2.5 py-1 text-[12px] text-slate-300 hover:text-white disabled:opacity-50">
              <RotateCcw className="h-3.5 w-3.5" /> 다시 배포
            </button>
            <button type="button" disabled={busy || working || !Object.keys(project.live || {}).length} onClick={onStop} className="flex items-center gap-1 rounded-lg border border-rose-500/30 px-2.5 py-1 text-[12px] text-rose-300 hover:bg-rose-500/10 disabled:opacity-40">
              <Square className="h-3.5 w-3.5" /> 멈추기
            </button>
          </div>
        </div>
      </div>
      {(working || notice) && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-violet-500/30 bg-violet-500/10 px-4 py-2.5 text-[13px] text-violet-100">
          {working && <Loader2 className="h-4 w-4 animate-spin" />} {notice}
          {working && <button type="button" onClick={onWatch} className="ml-auto text-[12px] underline-offset-4 hover:underline">진행 상황 보기 →</button>}
        </div>
      )}
      <ServerMap project={project} health={health?.[project.name]} onResource={onResource} busy={busy || working} />
    </section>
  );
}

export default function AppsPage({ projects, health, selected, onSelect, run, busy, notice, cloudReady, onResource, onMove, onRedeploy, onStop, onWatch }) {
  if (!projects) return <p className="py-10 text-center text-sm text-slate-500"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> 앱 목록을 불러오고 있어요…</p>;
  if (projects.length === 0) {
    return (
      <section className="rounded-2xl border border-dashed border-white/10 p-10 text-center">
        <p className="text-base font-semibold text-white">아직 배포한 앱이 없어요</p>
        <p className="mt-1 text-sm text-slate-400">배포하기에서 GitHub 주소를 넣으면 여기에 서버 지도가 생겨요.</p>
      </section>
    );
  }
  const project = projects.find((p) => p.name === selected) || projects[0];
  return (
    <div className="space-y-5">
      <Overview projects={projects} health={health} selected={project.name} onSelect={onSelect} />
      <Detail project={project} health={health} run={run} busy={busy} notice={notice} cloudReady={cloudReady}
        onResource={(kind, mode, url) => onResource(project, kind, mode, url)}
        onMove={(to) => onMove(project, to)}
        onRedeploy={() => onRedeploy(project)}
        onStop={() => onStop(project)}
        onWatch={onWatch} />
    </div>
  );
}
