import React, { useState } from 'react';
import { Laptop, Link2, Unlink } from 'lucide-react';
import { Badge, Card, CopyButton, Dot } from './ui';
import { linkCd, unlinkCd } from '../api';
import { fmtAgo } from '../lib/style';

// Per-user pieces of the dashboard (multi-user CD and the on-premises agent), moved out of the old OpsPanels.

const isGithub = (src) => /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+/i.test(src || '');
const INPUT = 'rounded-lg border border-white/10 bg-black/40 px-2.5 py-1.5 text-[12px] text-slate-100 focus:border-violet-500/60 focus:outline-none';
const BTN = 'flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-slate-300 hover:border-white/20 hover:text-white disabled:opacity-50';

/** Push-to-deploy for one project. The GitHub token is sent once to register the webhook and never kept. */
export function CdControl({ project }) {
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
    <Card title="내 PC 에이전트 (온프레미스)" icon={Laptop} className="mt-5 min-w-0">
      {agents.length > 0 && (
        <div className="mb-4 flex min-w-0 flex-wrap gap-2">
          {agents.map((a) => (
            <div key={a.name} className="flex min-w-0 flex-wrap items-center gap-2 rounded-xl border border-white/5 bg-black/20 px-3 py-2 text-[12px]">
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
        <p className="text-slate-500">배포가 내 컴퓨터의 Docker에서 실행됩니다.</p>
        <p>{agents.some((a) => a.online) ? '에이전트가 연결되어 있습니다. 배포 타깃에서 "내 PC"를 고르세요.' : '이 컴퓨터에서 아래 명령을 실행하면 "내 PC" 타깃이 켜집니다. Python 3와 Docker만 있으면 됩니다.'}</p>
        {token ? (
          <div className="flex items-start gap-2 rounded-lg bg-black/40 p-2.5 font-mono text-[11px] text-slate-300">
            <span className="min-w-0 flex-1 break-all">{cmd('••••••')}</span>
            <CopyButton text={cmd(token.trim())} />
          </div>
        ) : (
          <p className="text-amber-300">먼저 로그인하거나 설정 탭에서 API 토큰을 넣으세요. 에이전트는 이 토큰으로 내 배포만 받습니다.</p>
        )}
        <p className="text-slate-500">
          공개 URL은 cloudflared가 설치되어 있을 때 만들어집니다. 내 PC에서만 열리게 하려면 끝에 <span className="font-mono">--private</span>를 붙이세요.
          에이전트는 서버로 나가는 연결만 쓰므로 공인 IP나 포트 개방이 필요 없고, 내 토큰의 배포만 받습니다.
        </p>
      </div>
    </Card>
  );
}
