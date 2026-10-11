import React, { useState } from 'react';
import { Activity, Bomb, Brain, Loader2, ShieldCheck, Siren } from 'lucide-react';
import Explain from './Explain';
import { DRILLS, INCIDENT_STATUS, PLACES, ago } from './copy';

const DOT = { emerald: 'bg-emerald-400', rose: 'bg-rose-400', amber: 'bg-amber-400', violet: 'bg-violet-400', slate: 'bg-slate-500' };
const KIND_TONE = { alert: 'text-amber-200', decision: 'text-violet-200', action: 'text-sky-200', resolved: 'text-emerald-200', error: 'text-rose-200', info: 'text-slate-300' };
const CAUSE_KO = {
  app_crashed: '앱이 멈췄어요', bad_release: '새 버전 문제', database_down: 'DB가 꺼졌어요',
  overloaded: '요청이 너무 많아요', network: '연결 통로가 끊겼어요', platform_outage: '플랫폼 장애',
};
const ACTION_KO = { restart: '다시 켜기', start_database: 'DB 켜기', rollback: '이전 버전으로', failover: '다른 곳으로 옮기기', scale_out: '하나 더 띄우기', wait: '지켜보기' };

function Guard({ guard }) {
  if (!guard) return null;
  return (
    <section className="flex flex-wrap items-center gap-4 rounded-2xl border border-white/5 bg-slate-900/60 p-5">
      <span className={`grid h-11 w-11 place-items-center rounded-2xl ${guard.running ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-700/40 text-slate-400'}`}>
        <ShieldCheck className="h-6 w-6" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-base font-bold text-white">
          {guard.running ? `${guard.interval}초마다 앱을 지켜보고 있어요` : '지금은 앱을 지켜보고 있지 않아요'}
        </p>
        <p className="mt-0.5 text-[12px] leading-5 text-slate-400">
          문제가 생기면 <Explain term="Jev">Jev가</Explain> 원인과 할 일을 1초 안팎으로 고르고, 바로 대처해요. 판단: <b className="text-slate-200">{guard.engine}</b>
          {!guard.jev && ' · Jev 키(TYPESAFE_API_KEY)를 넣으면 Jev가 직접 판단해요.'}
        </p>
      </div>
    </section>
  );
}

function Board({ projects, health, onDrill, drilling }) {
  const rows = (projects || []).flatMap((p) => Object.keys(p.live || {}).map((t) => ({ p, t, h: health?.[p.name]?.[t] })));
  return (
    <section className="rounded-2xl border border-white/5 bg-slate-900/60 p-5">
      <p className="flex items-center gap-2 text-sm font-semibold text-white"><Activity className="h-4 w-4 text-slate-400" /> 지금 상태</p>
      {rows.length === 0 && <p className="mt-2 text-[12px] text-slate-500">떠 있는 앱이 없어요. 앱을 배포하면 여기서 지켜봐요.</p>}
      <div className="mt-3 divide-y divide-white/5">
        {rows.map(({ p, t, h }) => {
          const tone = !h ? 'slate' : h.ok ? 'emerald' : 'rose';
          const hasDb = !!p.live[t]?.db_container;
          return (
            <div key={`${p.name}-${t}`} className="flex flex-wrap items-center gap-3 py-3">
              <span className={`h-2.5 w-2.5 rounded-full ${DOT[tone]}`} />
              <span className="min-w-0 flex-1 basis-48">
                <span className="block truncate font-mono text-[13px] text-slate-100">{p.name}</span>
                <span className="block text-[11px] text-slate-500">
                  {PLACES[t]?.name || t} · {!h ? '확인 중' : h.ok ? `잘 돌아가요 · ${h.ms}ms` : '문제가 있어요'}
                </span>
              </span>
              <span className="flex flex-wrap gap-1.5">
                {DRILLS.filter((d) => (d.needs === 'local' ? t === 'local' : d.needs === 'db' ? t === 'local' && hasDb : true)).map((d) => (
                  <button key={d.kind} type="button" title={d.explain} disabled={!!drilling} onClick={() => onDrill(p.name, d.kind, t)}
                    className="flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] text-slate-300 hover:border-amber-400/50 hover:text-amber-100 disabled:opacity-40">
                    {drilling === `${p.name}:${d.kind}` ? <Loader2 className="h-3 w-3 animate-spin" /> : <Bomb className="h-3 w-3" />} {d.label}
                  </button>
                ))}
              </span>
            </div>
          );
        })}
      </div>
      {rows.length > 0 && <p className="mt-2 text-[11px] leading-4 text-slate-500">장애 훈련: 일부러 고장 내고 알아서 회복하는지 봐요. 실제 데이터는 지우지 않아요.</p>}
    </section>
  );
}

function IncidentCard({ incident: i }) {
  const st = INCIDENT_STATUS[i.status] || INCIDENT_STATUS.open;
  const v = i.verdicts[i.verdicts.length - 1];
  const took = i.resolved ? i.resolved - i.opened : null;
  return (
    <article className="rounded-2xl border border-white/5 bg-slate-900/60 p-4">
      <header className="flex flex-wrap items-center gap-2">
        <Siren className={`h-4 w-4 ${i.status === 'resolved' ? 'text-emerald-300' : i.status === 'escalated' ? 'text-rose-300' : 'text-amber-300'}`} />
        <span className="font-mono text-[13px] font-semibold text-white">{i.app}</span>
        <span className="text-[12px] text-slate-500">· {PLACES[i.target]?.name || i.target}</span>
        {i.drill && <span className="rounded-md bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-200">훈련</span>}
        <span className="ml-auto flex items-center gap-1.5 text-[11px] text-slate-400">
          <span className={`h-1.5 w-1.5 rounded-full ${DOT[st.tone]}`} /> {st.label} · {ago(i.opened)}
          {took != null && ` · ${took.toFixed(1)}초 만에 회복`}
        </span>
      </header>
      <ol className="mt-3 space-y-1.5 border-l border-white/10 pl-3">
        {i.timeline.map((t, n) => (
          <li key={n} className={`text-[12px] leading-5 ${KIND_TONE[t.kind] || KIND_TONE.info}`}>
            <span className="mr-2 font-mono text-[11px] tabular-nums text-slate-500">+{(t.at - i.opened).toFixed(1)}s</span>
            {t.text}
          </li>
        ))}
      </ol>
      {v && v.cause_p !== undefined && (
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl bg-black/30 px-3 py-2 text-[11px] text-slate-400">
          <span className="flex items-center gap-1"><Brain className="h-3.5 w-3.5 text-violet-300" /> {v.engine} · {v.ms}ms{v.tokens ? ` · ${v.tokens}토큰` : ''}</span>
          <span>원인: <b className="text-slate-200">{CAUSE_KO[v.cause]}</b> {Math.round(v.cause_p * 100)}%</span>
          <span>할 일: <b className="text-slate-200">{ACTION_KO[v.action]}</b> {Math.round(v.action_p * 100)}%</span>
          {v.severity_label && <span>심각도: {v.severity_label}</span>}
        </div>
      )}
    </article>
  );
}

export default function IncidentsPage({ snapshot, projects, onDrill }) {
  const [drilling, setDrilling] = useState(null);
  const [error, setError] = useState(null);
  const drill = async (name, kind) => {
    setDrilling(`${name}:${kind}`);
    setError(null);
    try {
      await onDrill(name, kind);
    } catch (e) {
      setError(e.message);
    } finally {
      setTimeout(() => setDrilling(null), 1500);
    }
  };
  if (!snapshot) return <p className="py-10 text-center text-sm text-slate-500"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> 상태를 불러오고 있어요…</p>;
  return (
    <div className="space-y-5">
      <Guard guard={snapshot.guard} />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="space-y-3">
          <Board projects={projects} health={snapshot.health} onDrill={drill} drilling={drilling} />
          {error && <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-200">훈련을 시작하지 못했어요 · {error}</p>}
        </div>
        <div className="space-y-3">
          <p className="text-sm font-semibold text-white">지금까지 있었던 일</p>
          {snapshot.incidents.length === 0 && (
            <p className="rounded-2xl border border-dashed border-white/10 p-6 text-center text-[13px] text-slate-500">아직 문제가 없었어요. 장애 훈련 버튼으로 직접 해 볼 수 있어요.</p>
          )}
          {snapshot.incidents.map((i) => <IncidentCard key={i.id} incident={i} />)}
        </div>
      </div>
    </div>
  );
}
