import React, { useState } from 'react';
import { Activity, Boxes, Eye, GitBranch, Laptop, Layers, Link2, Radio, Server, Unlink } from 'lucide-react';
import { Badge, Bar, Card, CopyButton, Dot } from './ui';
import { linkCd, unlinkCd } from '../api';
import { fmtAgo, fmtTime } from '../lib/style';

const EVENT_LABEL = { deploy: '배포', scale_out: '확장', scale_in: '축소', scale_out_blocked: '확장 보류' };

/** Newest first; consecutive events of the same type collapse into one line with a count. */
function groupEvents(events) {
  const out = [];
  for (const e of [...events].reverse()) {
    const last = out[out.length - 1];
    if (last && last.type === e.type && e.type !== 'deploy') last.count += 1;
    else out.push({ ...e, count: 1 });
  }
  return out;
}

const isGithub = (src) => /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+/i.test(src || '');
const INPUT = 'rounded-lg border border-white/10 bg-black/40 px-2.5 py-1.5 text-[12px] text-slate-100 focus:border-violet-500/60 focus:outline-none';
const BTN = 'flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-slate-300 hover:border-white/20 hover:text-white disabled:opacity-50';

/** Push-to-deploy for one project. The GitHub token is sent once to register the webhook and never kept. */
function CdControl({ project }) {
  const [open, setOpen] = useState(false);
  const [ghToken, setGhToken] = useState('');
  const [branch, setBranch] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [manual, setManual] = useState(null); // { url, secret } shown once
  const cd = project.cd;

  const run = async (fn) => {
    setBusy(true); setError(null);
    try { await fn(); setOpen(false); setGhToken(''); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const link = () => run(async () => {
    const r = await linkCd(project.name, { githubToken: ghToken.trim(), branch: branch.trim() });
    if (r.secret) setManual({ url: r.url, secret: r.secret });
  });
  const unlink = () => run(async () => { await unlinkCd(project.name, { githubToken: ghToken.trim() }); setManual(null); });

  if (!cd && !isGithub(project.source)) return null;
  return (
    <div className="w-full">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        {cd ? (
          <>
            <Badge tone="sky"><Link2 className="h-3 w-3" />자동 배포 · {cd.repo}@{cd.branch || '기본 브랜치'}</Badge>
            <span className="text-slate-500">
              {cd.mode === 'auto' ? 'GitHub에 자동 등록됨' : '수동 등록'} · {cd.last_delivery ? `마지막 수신 ${fmtAgo(cd.last_delivery.at)} (${cd.last_delivery.event})` : '아직 수신 없음'}
            </span>
            <button type="button" className={BTN} onClick={() => setOpen((o) => !o)}><Unlink className="h-3.5 w-3.5" /> 연결 해제</button>
          </>
        ) : (
          <button type="button" className={BTN} onClick={() => setOpen((o) => !o)}><Link2 className="h-3.5 w-3.5" /> push 자동 배포 연결</button>
        )}
      </div>
      {open && (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-xl border border-white/5 bg-black/20 p-3 text-[11px] text-slate-400">
          <input type="password" value={ghToken} onChange={(e) => setGhToken(e.target.value)} className={`${INPUT} w-64`}
            placeholder={cd ? 'GitHub 토큰 (있으면 GitHub 웹훅도 삭제)' : 'GitHub 토큰 (admin:repo_hook, 비우면 수동 등록)'} />
          {!cd && <input value={branch} onChange={(e) => setBranch(e.target.value)} className={`${INPUT} w-36`} placeholder="브랜치 (기본: main)" />}
          <button type="button" disabled={busy} onClick={cd ? unlink : link}
            className="rounded-lg bg-violet-600 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-violet-500 disabled:opacity-50">
            {busy ? '처리 중…' : cd ? '해제' : '연결'}
          </button>
          <span className="w-full text-slate-500">GitHub 토큰은 웹훅 등록에 한 번만 쓰고 서버에 저장하지 않습니다.</span>
        </div>
      )}
      {error && <p className="mt-2 text-[11px] text-rose-300">{error}</p>}
      {manual && (
        <div className="mt-2 space-y-1.5 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3 text-[11px] text-amber-100">
          <p>GitHub 저장소 → Settings → Webhooks → Add webhook에 아래 값을 넣으세요 (Content type: application/json, push 이벤트). 비밀값은 지금 한 번만 보입니다.</p>
          <p className="flex items-center gap-1 font-mono">URL: {manual.url} <CopyButton text={manual.url} /></p>
          <p className="flex items-center gap-1 font-mono">Secret: {manual.secret} <CopyButton text={manual.secret} /></p>
          <button type="button" className={BTN} onClick={() => setManual(null)}>저장했습니다</button>
        </div>
      )}
    </div>
  );
}

/** On-premises agents of the signed-in user, and how to start one on this computer. */
export function AgentPanel({ agents, token }) {
  const server = window.location.origin;
  const cmd = (t) =>
    `curl -fsSL -H "X-API-Token: ${t}" ${server}/agent/script -o cloudmorph_agent.py && python3 cloudmorph_agent.py --server ${server} --token ${t}`;
  return (
    <Card title="내 PC 에이전트 (온프레미스)" icon={Laptop} right={<span className="text-[11px] text-slate-500">배포가 내 컴퓨터의 Docker에서 실행됩니다</span>}>
      {agents.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {agents.map((a) => (
            <div key={a.name} className="flex items-center gap-2 rounded-xl border border-white/5 bg-black/20 px-3 py-2 text-[12px]">
              <Dot tone={a.online ? (a.busy ? 'violet' : 'emerald') : 'slate'} pulse={a.busy} />
              <span className="font-mono font-semibold text-slate-100">{a.name}</span>
              <span className="text-slate-500">
                {a.online ? (a.busy ? '배포 중' : '대기 중') : `오프라인 · ${fmtAgo(a.last_seen)}`} · {a.info?.os} {a.info?.arch} · Docker {a.info?.docker || '?'}
                {a.info?.public ? ' · 공개 URL' : ' · 내 PC 전용 URL'}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="space-y-2 text-[12px] text-slate-400">
        <p>{agents.some((a) => a.online) ? '에이전트가 연결되어 있습니다. 배포 타깃에서 "내 PC"를 고르세요.' : '이 컴퓨터에서 아래 명령을 실행하면 "내 PC" 타깃이 켜집니다. Python 3와 Docker만 있으면 됩니다.'}</p>
        {token ? (
          <div className="flex items-start gap-2 rounded-lg bg-black/40 p-2.5 font-mono text-[11px] text-slate-300">
            <span className="min-w-0 flex-1 break-all">{cmd('••••••')}</span>
            <CopyButton text={cmd(token.trim())} />
          </div>
        ) : (
          <p className="text-amber-300">먼저 오른쪽 위에 내 API 토큰을 넣으세요.</p>
        )}
        <p className="text-slate-500">
          공개 URL은 cloudflared가 설치되어 있을 때 만들어집니다. 내 PC에서만 열리게 하려면 끝에 <span className="font-mono">--private</span>를 붙이세요.
          에이전트는 서버로 나가는 연결만 쓰므로 공인 IP나 포트 개방이 필요 없고, 내 토큰의 배포만 받습니다.
        </p>
      </div>
    </Card>
  );
}

const STATUS = {
  running: { tone: 'violet', label: '배포 중', pulse: true },
  completed: { tone: 'emerald', label: '정상' },
  partial_failure: { tone: 'amber', label: '일부 실패' },
  failed: { tone: 'rose', label: '실패' },
};

export function ProjectsPanel({ projects, currentId, onWatch, autoFollow, setAutoFollow }) {
  return (
    <Card
      title="배포 중인 프로젝트"
      icon={Layers}
      right={
        <label className="flex cursor-pointer items-center gap-2 text-[11px] text-slate-400">
          <Radio className={`h-3.5 w-3.5 ${autoFollow ? 'text-sky-300' : ''}`} /> GitHub push 자동 추적
          <input type="checkbox" checked={autoFollow} onChange={(e) => setAutoFollow(e.target.checked)} className="accent-sky-400" />
        </label>
      }
      bodyClass="p-0"
    >
      {!projects || projects.length === 0 ? (
        <p className="p-5 text-xs text-slate-500">아직 내 프로젝트가 없습니다. 위에서 GitHub 저장소를 배포한 뒤, 여기서 push 자동 배포를 연결하세요.</p>
      ) : (
        <div className="divide-y divide-white/5">
          {projects.map((p) => {
            const st = STATUS[p.last_status] || STATUS.running;
            const urls = Object.entries(p.urls || {}).filter(([, u]) => u);
            const watching = p.last_deployment_id === currentId;
            return (
              <div key={p.name} className={`flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3 ${watching ? 'bg-violet-500/5' : ''}`}>
                <div className="flex min-w-[220px] flex-1 items-center gap-3">
                  <Dot tone={st.tone} pulse={st.pulse} />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-mono text-[13px] font-semibold text-slate-100">{p.name}</span>
                      <Badge tone={st.tone}>{st.label}</Badge>
                      {p.trigger === 'github-push' ? <Badge tone="sky"><GitBranch className="h-3 w-3" />{p.ref || 'push'}</Badge> : <Badge>수동</Badge>}
                    </div>
                    <div className="truncate font-mono text-[11px] text-slate-500">{p.source}</div>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {urls.map(([t, u]) => (
                    <a key={t} href={u} target="_blank" rel="noreferrer" className="rounded-md bg-white/5 px-2 py-1 font-mono text-[11px] text-slate-300 ring-1 ring-inset ring-white/5 hover:bg-white/10 hover:text-white">{t} ↗</a>
                  ))}
                </div>
                <div className="flex items-center gap-3 text-[11px] text-slate-500">
                  <span>{(p.history || []).length}회 배포</span>
                  <span>{p.updated_at ? fmtAgo(p.updated_at) : ''}</span>
                  <button type="button" onClick={() => onWatch(p)} disabled={watching}
                    className="flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-slate-300 hover:border-white/20 hover:text-white disabled:border-violet-500/40 disabled:text-violet-300">
                    <Eye className="h-3.5 w-3.5" /> {watching ? '보는 중' : '로그 보기'}
                  </button>
                </div>
                <CdControl project={p} />
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

export function FleetPanel({ fleet }) {
  if (!fleet) return null;
  return (
    <Card
      title="노드 풀 · 멀티 클라우드"
      icon={Boxes}
      right={<span className="text-[11px] text-slate-500">라우터 {fleet.router ? `${fleet.router.host}:${fleet.router.public_port}` : '없음'} · 15초 갱신</span>}
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500"><Server className="h-3.5 w-3.5" /> 노드</h3>
          {fleet.nodes.map((n) => {
            const load = n.ok ? Math.round((n.load1 / Math.max(n.cpus, 1)) * 100) : 0;
            const mem = n.ok && n.mem_total_mb ? Math.round((1 - n.mem_avail_mb / n.mem_total_mb) * 100) : 0;
            return (
              <div key={n.node} className="rounded-xl border border-white/5 bg-black/20 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Dot tone={n.ok ? 'emerald' : 'rose'} />
                    <span className="font-mono text-[13px] font-semibold text-slate-200">{n.node}</span>
                    <Badge tone="sky">{n.provider}</Badge>
                    <Badge>{n.arch}</Badge>
                  </div>
                  <span className="font-mono text-[11px] text-slate-500">{n.host}</span>
                </div>
                {n.ok ? (
                  <div className="grid grid-cols-[52px_1fr_44px] items-center gap-x-2 gap-y-1.5 text-[11px] text-slate-400">
                    <span>CPU 부하</span><Bar value={load} /><span className="text-right font-mono tabular-nums">{load}%</span>
                    <span>메모리</span><Bar value={mem} tone="sky" /><span className="text-right font-mono tabular-nums">{mem}%</span>
                    <span>컨테이너</span><span className="text-slate-300">{n.containers}개</span><span className="text-right font-mono text-slate-500">{n.score}</span>
                  </div>
                ) : <p className="text-[11px] text-rose-300">접속 불가</p>}
              </div>
            );
          })}
        </div>
        <div className="space-y-2">
          <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500"><Activity className="h-3.5 w-3.5" /> 앱 · 복제본</h3>
          {fleet.apps.length === 0 ? <p className="text-xs text-slate-500">노드 풀에 배포된 앱이 없습니다.</p> : fleet.apps.map((a) => (
            <div key={a.name} className="rounded-xl border border-white/5 bg-black/20 p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <a href={a.url} target="_blank" rel="noreferrer" className="truncate font-mono text-[13px] font-semibold text-sky-300 hover:underline">{a.name}</a>
                <Badge tone="violet">{a.replicas.length} replica</Badge>
              </div>
              <div className="space-y-1.5">
                {a.replicas.map((r) => (
                  <div key={r.upstream} className="grid grid-cols-[110px_1fr_44px] items-center gap-2 text-[11px] text-slate-400">
                    <span className="truncate font-mono">{r.node}</span><Bar value={r.cpu ?? 0} /><span className="text-right font-mono tabular-nums">{r.cpu != null ? `${Math.round(r.cpu)}%` : '–'}</span>
                  </div>
                ))}
              </div>
              {groupEvents(a.events).slice(0, 3).map((e, i) => (
                <div key={i} className={`mt-1.5 truncate text-[11px] ${e.type === 'scale_out' ? 'text-amber-300' : e.type === 'scale_in' ? 'text-sky-300' : e.type === 'scale_out_blocked' ? 'text-rose-300/80' : 'text-slate-500'}`}>
                  {fmtTime(new Date(e.ts * 1000))} · {EVENT_LABEL[e.type] || e.type}{e.count > 1 ? ` ×${e.count}` : ''} {e.node || ''} {e.type === 'scale_out_blocked' ? '· 남은 노드 없음' : e.reason ? `· ${e.reason}` : ''}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}
