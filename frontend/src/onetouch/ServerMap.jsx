import React, { useState } from 'react';
import { Box, Database, Globe, GripVertical, Link2, Loader2, Plug, Unplug } from 'lucide-react';
import Explain from './Explain';
import { PLACES, josa } from './copy';

const PALETTE = [
  { id: 'pg-new', kind: 'postgres', mode: 'new', label: 'PostgreSQL 새로 만들기', hint: '앱 전용 DB를 새로 만들어 붙여요' },
  { id: 'pg-url', kind: 'postgres', mode: 'url', label: '내 PostgreSQL·MySQL', hint: '이미 있는 DB 주소로 연결해요' },
  { id: 'redis-url', kind: 'redis', mode: 'url', label: '내 Redis', hint: '이미 있는 Redis 주소로 연결해요' },
];
const KIND_NAME = { postgres: 'PostgreSQL', mysql: 'MySQL', redis: 'Redis' };

/** What the database next to the app is, in words: made by CloudMorph (sidecar / Cloud SQL), the user's own, or none. */
function databases(project) {
  const out = [];
  const live = project.live || {};
  const res = project.resources || {};
  const stores = project.analysis?.stores || {};
  const kinds = new Set([...Object.keys(stores), ...Object.keys(res)]);
  // what is actually running counts too (apps deployed before the analysis summary was kept)
  if (live.local?.db_container || live.node?.db_container || live.cloudrun?.cloudsql_connection) kinds.add('postgres');
  for (const kind of kinds) {
    const mode = res[kind]?.mode;
    if (mode === 'none') continue;
    if (mode === 'url') out.push({ kind, label: `내 ${KIND_NAME[kind] || kind}`, where: '내가 연결한 주소' });
    else if (kind === 'postgres' && (live.local?.db_container || live.cloudrun?.cloudsql_connection || live.node?.db_container || mode === 'new')) {
      const where = live.cloudrun?.cloudsql_connection ? 'Cloud SQL' : '사이드카';
      out.push({ kind, label: 'PostgreSQL', where, made: true });
    }
  }
  return out;
}

function Node({ icon: Icon, title, sub, tone = 'slate', children, onDragOver, onDrop, highlight, dashed }) {
  const ring = { emerald: 'border-emerald-500/40', rose: 'border-rose-500/50', amber: 'border-amber-500/50', violet: 'border-violet-500/40', slate: 'border-white/10' }[tone];
  return (
    <div onDragOver={onDragOver} onDrop={onDrop}
      className={`relative min-w-0 rounded-xl border ${dashed ? 'border-dashed' : ''} ${highlight ? 'border-violet-400 bg-violet-500/10' : `${ring} bg-black/30`} p-3 transition`}>
      <p className="flex items-center gap-1.5 text-[12px] font-semibold text-white"><Icon className="h-4 w-4 text-slate-400" /> {title}</p>
      {sub && <p className="mt-0.5 truncate text-[11px] text-slate-400">{sub}</p>}
      {children}
    </div>
  );
}

function Wire({ on }) {
  return (
    <div className="relative hidden h-px min-w-[24px] flex-1 self-center bg-white/10 md:block" aria-hidden>
      {on && <span className="absolute -top-[3px] left-0 h-[7px] w-[7px] animate-[flow_1.6s_linear_infinite] rounded-full bg-emerald-400 shadow-[0_0_8px] shadow-emerald-400" />}
    </div>
  );
}

function health(h) {
  if (!h) return { tone: 'slate', text: '상태를 확인하는 중이에요' };
  if (h.ok) return { tone: 'emerald', text: `잘 돌아가요 · 응답 ${h.ms}ms` };
  if (h.container && h.container !== 'running') return { tone: 'rose', text: '앱이 꺼져 있어요' };
  if (h.db && h.db !== 'running') return { tone: 'rose', text: 'DB가 꺼져 있어요' };
  if (h.http) return { tone: 'rose', text: `오류를 돌려줘요 (HTTP ${h.http})` };
  return { tone: 'rose', text: '응답이 없어요' };
}

function Confirm({ item, project, places, onCancel, onConfirm, busy }) {
  const [url, setUrl] = useState('');
  const reads = project.analysis?.stores?.[item.kind] || (item.kind === 'postgres' && Object.values(project.analysis?.stores || {}).includes('DATABASE_URL'));
  const name = KIND_NAME[item.kind];
  const title = item.mode === 'new' ? `${josa(name, '을', '를')} 새로 만들어 연결할까요?` : item.mode === 'url' ? `내 ${josa(name, '을', '를')} 연결할까요?` : `${name} 연결을 끊을까요?`;
  return (
    <div role="dialog" aria-label={title} className="rounded-2xl border border-violet-500/40 bg-slate-900 p-4 shadow-2xl shadow-black/50">
      <p className="text-sm font-bold text-white">{title}</p>
      <ul className="mt-2 list-disc space-y-1 pl-4 text-[12px] leading-5 text-slate-300">
        {item.mode === 'none' ? (
          <>
            <li>앱을 DB 없이 다시 배포해요.</li>
            <li>DB 안의 데이터는 지우지 않고 그대로 둬요. 다시 연결하면 그대로 이어서 써요.</li>
          </>
        ) : (
          <>
            <li>연결하려면 앱을 지금 있는 곳({places})에 다시 배포해요. 보통 1분 안에 끝나요.</li>
            {item.mode === 'new' && <li>내 컴퓨터에서는 앱 옆에 DB 상자(<Explain term="사이드카" />)를 하나 더 띄우고, 클라우드에서는 <Explain term="Cloud SQL" />에 이 앱 전용 DB를 만들어요.</li>}
            {item.mode === 'url' && <li>주소는 화면과 기록에 남기지 않아요.</li>}
          </>
        )}
        {item.mode !== 'none' && !reads && <li className="text-amber-200">이 앱 코드에서 DB 주소를 읽는 곳을 찾지 못했어요. 연결해도 앱이 쓰지 않을 수 있어요.</li>}
      </ul>
      {item.mode === 'url' && (
        <input type="password" autoComplete="off" value={url} onChange={(e) => setUrl(e.target.value)} autoFocus
          placeholder={item.kind === 'redis' ? 'redis://:비밀번호@호스트:6379/0' : 'postgresql://아이디:비밀번호@호스트:5432/DB이름'}
          className="mt-3 w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2 font-mono text-[12px] text-slate-100 placeholder:text-slate-600 focus:border-violet-400/60 focus:outline-none" />
      )}
      <div className="mt-3 flex gap-2">
        <button type="button" disabled={busy || (item.mode === 'url' && !url.includes('://'))} onClick={() => onConfirm(url)}
          className="flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-[13px] font-bold text-slate-900 hover:bg-slate-200 disabled:bg-slate-700 disabled:text-slate-400">
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} {item.mode === 'none' ? '연결 끊기' : '연결하기'}
        </button>
        <button type="button" onClick={onCancel} className="rounded-lg border border-white/10 px-3 py-1.5 text-[13px] text-slate-300 hover:text-white">취소</button>
      </div>
    </div>
  );
}

/** One app as a picture: people → address → app (per place) → database. Drop a database on the app to connect it. */
export default function ServerMap({ project, health: h = {}, onResource, busy }) {
  const [dragging, setDragging] = useState(false);
  const [over, setOver] = useState(false);
  const [pending, setPending] = useState(null);
  const targets = Object.keys(project.live || {});
  const places = targets.map((t) => PLACES[t]?.name || t).join(', ') || '마지막으로 배포한 곳';
  const dbs = databases(project);
  const anyUp = targets.some((t) => h[t]?.ok);

  const allowDrop = (e) => { if (dragging) { e.preventDefault(); setOver(true); } };
  const drop = (e) => {
    e.preventDefault();
    setOver(false);
    setDragging(false);
    const item = PALETTE.find((p) => p.id === e.dataTransfer.getData('text/plain'));
    if (item) setPending(item);
  };
  const confirm = async (url) => {
    const item = pending;
    await onResource(item.kind, item.mode, url || undefined);
    setPending(null);
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_240px]">
      <div className="rounded-2xl border border-white/5 bg-slate-900/60 p-4">
        <p className="mb-3 text-[12px] text-slate-400">사람들이 앱에 들어오는 길이에요. 초록 점이 움직이면 잘 연결된 거예요.</p>
        <div className="space-y-3">
          {(targets.length ? targets : [null]).map((t) => {
            const st = health(t ? h[t] : null);
            const handles = (t && project.live[t]) || {};
            const url = t && (project.urls?.[t] || handles.public_url || handles.local_url);
            const local = t === 'local';
            return (
              <div key={t || 'none'} className="flex flex-col gap-2 md:flex-row md:items-stretch">
                <Node icon={Globe} title="사람들" sub="인터넷에서 들어와요" />
                <Wire on={anyUp && st.tone === 'emerald'} />
                <Node icon={Link2} title="주소" sub={url || '아직 주소가 없어요'}>
                  {local && !handles.public_url && <p className="mt-1 text-[11px] text-slate-500">이 컴퓨터에서만 열려요 · <Explain term="터널" /> 켜면 밖에서도 열려요</p>}
                  {url && <a href={url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-[11px] text-sky-300 hover:underline">열어 보기</a>}
                </Node>
                <Wire on={st.tone === 'emerald'} />
                <Node icon={Box} title={t ? `앱 · ${PLACES[t]?.name || t}` : '앱'} sub={t ? PLACES[t]?.tech : '아직 떠 있지 않아요'} tone={st.tone}
                  onDragOver={allowDrop} onDrop={drop} highlight={over}>
                  <p className={`mt-1 flex items-center gap-1.5 text-[11px] ${st.tone === 'emerald' ? 'text-emerald-300' : st.tone === 'rose' ? 'text-rose-300' : 'text-slate-400'}`}>
                    <span className={`h-1.5 w-1.5 rounded-full ${st.tone === 'emerald' ? 'bg-emerald-400' : st.tone === 'rose' ? 'bg-rose-400' : 'bg-slate-500'}`} /> {st.text}
                  </p>
                  {project.analysis && <p className="mt-0.5 text-[11px] text-slate-500">{project.analysis.language}{project.analysis.framework && project.analysis.framework !== 'None' ? ` · ${project.analysis.framework}` : ''} · <Explain term="컨테이너" /></p>}
                </Node>
                <Wire on={dbs.length > 0 && st.tone === 'emerald'} />
                {dbs.length ? (
                  <div className="flex min-w-0 flex-col gap-2">
                    {dbs.map((db) => (
                      <Node key={db.kind} icon={Database} title={db.label} sub={db.where}
                        tone={local && h.local?.db && h.local.db !== 'running' ? 'rose' : 'emerald'}>
                        <button type="button" disabled={busy} onClick={() => setPending({ kind: db.kind, mode: 'none' })}
                          className="mt-1.5 flex items-center gap-1 text-[11px] text-slate-400 hover:text-rose-200 disabled:opacity-50">
                          <Unplug className="h-3 w-3" /> 연결 끊기
                        </button>
                      </Node>
                    ))}
                  </div>
                ) : (
                  <Node icon={Database} title="DB 없음" sub="오른쪽에서 끌어다 놓으세요" dashed onDragOver={allowDrop} onDrop={drop} highlight={over} />
                )}
              </div>
            );
          })}
        </div>
        {pending && (
          <div className="mt-4">
            <Confirm item={pending} project={project} places={places} busy={busy} onCancel={() => setPending(null)} onConfirm={confirm} />
          </div>
        )}
      </div>

      <aside className="rounded-2xl border border-white/5 bg-slate-900/60 p-4">
        <p className="text-sm font-semibold text-white">붙일 수 있는 것</p>
        <p className="mt-0.5 text-[11px] leading-4 text-slate-500">앱 상자로 끌어다 놓거나 눌러 주세요. <Explain term="데이터베이스" /></p>
        <div className="mt-3 space-y-2">
          {PALETTE.map((item) => (
            <button key={item.id} type="button" draggable disabled={busy}
              onDragStart={(e) => { e.dataTransfer.setData('text/plain', item.id); e.dataTransfer.effectAllowed = 'copy'; setDragging(true); }}
              onDragEnd={() => { setDragging(false); setOver(false); }}
              onClick={() => setPending(item)}
              className="flex w-full cursor-grab items-start gap-2 rounded-xl border border-white/10 bg-black/30 p-3 text-left hover:border-violet-400/50 active:cursor-grabbing disabled:opacity-50">
              <GripVertical className="mt-0.5 h-4 w-4 shrink-0 text-slate-600" />
              <span>
                <span className="flex items-center gap-1.5 text-[12px] font-semibold text-white"><Plug className="h-3.5 w-3.5 text-violet-300" /> {item.label}</span>
                <span className="mt-0.5 block text-[11px] text-slate-400">{item.hint}</span>
              </span>
            </button>
          ))}
        </div>
      </aside>
    </div>
  );
}
