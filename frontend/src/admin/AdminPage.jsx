import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Boxes, ExternalLink, KeyRound, LogOut, RefreshCw, Search, ShieldCheck, Trash2, Users } from 'lucide-react';
import { deleteUser, getAdminUsers, getProjects, logOutUser, resetUserPassword, stopProject } from '../api';
import { TARGET_META } from '../lib/run';
import { STATUS, ago } from '../onetouch/copy';
import { Badge, Card, StopButton } from '../components/ui';

const POLL_MS = 10000;
const KIND = {
  admin: { tone: 'amber', label: '관리자' },
  account: { tone: 'violet', label: '회원' },
  token: { tone: 'sky', label: 'API 토큰' },
  owner: { tone: 'slate', label: '계정 없음' },
};
const input = 'rounded-lg border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-slate-100 placeholder:text-slate-500 focus:border-violet-500/60 focus:outline-none';
const action = 'inline-flex items-center gap-1 rounded-md border border-white/10 px-2 py-1 text-[11px] text-slate-300 hover:border-white/25 hover:text-white disabled:opacity-40';
const th = 'px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-slate-500';
const td = 'px-3 py-2.5 align-middle';
const when = (ts) => (ts ? ago(ts) : '—');

function Stat({ label, value, tone = 'slate' }) {
  return (
    <div className="rounded-xl border border-white/5 bg-black/20 px-4 py-3">
      <p className="text-[11px] text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${tone === 'emerald' ? 'text-emerald-300' : 'text-slate-100'}`}>{value ?? '—'}</p>
    </div>
  );
}

/** Password reset typed inline, so the new password never shows on screen or in a browser dialog. */
function PasswordForm({ name, onDone, onCancel }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await resetUserPassword(name, password);
      onDone(`${name} 비밀번호를 바꿨습니다. 기존 로그인은 모두 끝났습니다.`);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="flex flex-wrap items-center gap-2">
      <input className={`${input} w-56`} type="password" autoComplete="new-password" autoFocus value={password}
        onChange={(e) => setPassword(e.target.value)} placeholder={`${name}의 새 비밀번호 (8자 이상)`} />
      <button type="submit" disabled={busy || password.length < 8} className={action}>{busy ? '바꾸는 중…' : '바꾸기'}</button>
      <button type="button" onClick={onCancel} className={action}>취소</button>
      {error && <span className="text-[11px] text-rose-300">{error}</span>}
    </form>
  );
}

function UsersTable({ users, owner, onOwner, onChanged, flash }) {
  const [resetting, setResetting] = useState(null);
  const [busy, setBusy] = useState(null);
  const run = async (name, fn, done) => {
    setBusy(name);
    try {
      flash(done(await fn(name)));
      await onChanged();
    } catch (err) {
      flash(`${name} · ${err.message}`);
    } finally {
      setBusy(null);
    }
  };
  const logout = (name) => window.confirm(`${name}을(를) 모든 기기에서 로그아웃시킬까요?`)
    && run(name, logOutUser, (r) => `${name} 로그인 ${r.ended}개를 끝냈습니다.`);
  const remove = (u) => window.confirm(
    `${u.name} 계정을 삭제할까요?\n${u.apps ? `이 사용자의 앱 ${u.apps}개는 그대로 남고, 아래 앱 목록에서 중지할 수 있습니다.` : '이 사용자가 가진 앱은 없습니다.'}`,
  ) && run(u.name, deleteUser, () => `${u.name} 계정을 삭제했습니다.`);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead className="border-b border-white/5"><tr>
          <th className={th}>아이디</th><th className={th}>종류</th><th className={th}>앱 (실행 중)</th>
          <th className={th}>로그인 세션</th><th className={th}>가입</th><th className={th}>최근 로그인</th><th className={th}>관리</th>
        </tr></thead>
        <tbody className="divide-y divide-white/5">
          {users.map((u) => (
            <tr key={u.name} className={owner === u.name ? 'bg-violet-500/5' : ''}>
              <td className={`${td} font-medium text-slate-100`}>{u.name}</td>
              <td className={td}><Badge tone={KIND[u.kind]?.tone}>{KIND[u.kind]?.label || u.kind}</Badge></td>
              <td className={td}>
                <button type="button" disabled={!u.apps} onClick={() => onOwner(owner === u.name ? '' : u.name)}
                  className="tabular-nums text-slate-300 hover:text-white disabled:text-slate-600" title="이 사용자의 앱만 보기">
                  {u.apps} <span className="text-slate-500">({u.live})</span>
                </button>
              </td>
              <td className={`${td} tabular-nums text-slate-300`}>{u.kind === 'account' || u.kind === 'admin' ? u.sessions : '—'}</td>
              <td className={`${td} text-slate-400`}>{when(u.created_at)}</td>
              <td className={`${td} text-slate-400`}>{when(u.last_login)}</td>
              <td className={td}>
                {u.kind !== 'account' ? (
                  <span className="text-[11px] text-slate-600">{u.kind === 'admin' ? '서버 환경변수로 관리' : u.kind === 'token' ? '서버 설정으로 관리' : '—'}</span>
                ) : resetting === u.name ? (
                  <PasswordForm name={u.name} onCancel={() => setResetting(null)}
                    onDone={(msg) => { setResetting(null); flash(msg); onChanged(); }} />
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    <button type="button" className={action} disabled={busy === u.name} onClick={() => setResetting(u.name)}><KeyRound className="h-3 w-3" />비밀번호</button>
                    <button type="button" className={action} disabled={busy === u.name || !u.sessions} onClick={() => logout(u.name)}><LogOut className="h-3 w-3" />로그아웃</button>
                    <button type="button" className={`${action} hover:border-rose-500/50 hover:text-rose-200`} disabled={busy === u.name} onClick={() => remove(u)}><Trash2 className="h-3 w-3" />삭제</button>
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AppsTable({ projects, stopping, onStop }) {
  if (!projects.length) return <p className="py-6 text-center text-sm text-slate-500">조건에 맞는 앱이 없습니다.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead className="border-b border-white/5"><tr>
          <th className={th}>앱</th><th className={th}>소유자</th><th className={th}>상태</th>
          <th className={th}>실행 위치</th><th className={th}>최근 변경</th><th className={th}>관리</th>
        </tr></thead>
        <tbody className="divide-y divide-white/5">
          {projects.map((p) => {
            const live = Object.keys(p.live || {});
            const status = STATUS[p.last_status] || { tone: 'slate', label: p.last_status || '—' };
            return (
              <tr key={p.name}>
                <td className={td}>
                  <p className="font-medium text-slate-100">{p.name}</p>
                  <p className="max-w-[260px] truncate text-[11px] text-slate-500" title={p.source}>{p.source}</p>
                </td>
                <td className={`${td} text-slate-300`}>{p.owner}</td>
                <td className={td}><Badge tone={status.tone}>{status.label}</Badge></td>
                <td className={td}>
                  <div className="flex flex-wrap gap-1.5">
                    {live.length === 0 && <span className="text-[11px] text-slate-600">없음</span>}
                    {live.map((t) => {
                      const url = p.urls?.[t];
                      const label = TARGET_META[t]?.label || t;
                      return url
                        ? <a key={t} href={url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[11px] text-sky-300 hover:text-sky-200">{label}<ExternalLink className="h-3 w-3" /></a>
                        : <span key={t} className="text-[11px] text-slate-400">{label}</span>;
                    })}
                  </div>
                </td>
                <td className={`${td} text-slate-400`}>{when(p.updated_at)}</td>
                <td className={td}>
                  {live.length > 0
                    ? <StopButton appName={p.name} busy={stopping.has(p.name)} disabled={p.last_status === 'running'} onClick={() => onStop(p.name)} />
                    : <span className="text-[11px] text-slate-600">—</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** #/admin: every account and every app on this server. The backend lets only admin call /admin. */
export default function AdminPage({ me }) {
  const [users, setUsers] = useState(null);
  const [projects, setProjects] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [owner, setOwner] = useState('');
  const [query, setQuery] = useState('');
  const [stopping, setStopping] = useState(() => new Set());

  const load = useCallback(async () => {
    try {
      const [u, p] = await Promise.all([getAdminUsers(), getProjects()]);
      return () => { setUsers(u); if (p) setProjects(p); setError(''); };
    } catch (err) {
      return () => setError(err.message === '403' ? '관리자만 볼 수 있는 화면입니다.' : `불러오지 못했습니다 (${err.message})`);
    }
  }, []);
  const refresh = useCallback(async () => (await load())(), [load]);

  useEffect(() => {
    if (!me.admin) return undefined;
    let alive = true;
    const poll = async () => { const apply = await load(); if (alive) apply(); };
    poll();
    const h = setInterval(poll, POLL_MS);
    return () => { alive = false; clearInterval(h); };
  }, [me.admin, load]);

  const flash = useCallback((text) => setNotice(text), []);
  const onStop = async (name) => {
    setStopping((s) => new Set(s).add(name));
    try {
      const r = await stopProject(name);
      const failed = Object.entries(r.failed);
      flash(failed.length ? `${name} 일부 중지 실패 · ${failed.map(([t, e]) => `${t}: ${e}`).join(' · ')}` : `${name}을(를) 중지했습니다.`);
    } catch (err) {
      flash(`${name} 중지 실패 · ${err.message}`);
    } finally {
      setStopping((s) => { const next = new Set(s); next.delete(name); return next; });
      refresh();
    }
  };

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (projects || []).filter((p) => (!owner || p.owner === owner)
      && (!q || p.name.toLowerCase().includes(q) || (p.source || '').toLowerCase().includes(q)));
  }, [projects, owner, query]);
  const owners = useMemo(() => [...new Set((projects || []).map((p) => p.owner))].sort(), [projects]);

  if (!me.admin) {
    return (
      <div className="grid min-h-screen place-items-center bg-[#0b0d17] px-4 text-center text-slate-300">
        <div className="space-y-3">
          <ShieldCheck className="mx-auto h-8 w-8 text-slate-500" />
          <p>관리자만 볼 수 있는 화면입니다. ({me.user}(으)로 로그인되어 있습니다)</p>
          <a href="#/" className="text-sm text-violet-300 hover:text-violet-200">대시보드로 돌아가기</a>
        </div>
      </div>
    );
  }

  const live = (projects || []).filter((p) => Object.keys(p.live || {}).length).length;
  const people = (users || []).filter((u) => u.kind === 'account').length;
  const online = (users || []).filter((u) => u.sessions > 0).length;

  return (
    <div className="min-h-screen bg-[#0b0d17] px-4 py-6 text-slate-100 sm:px-8">
      <div className="mx-auto max-w-6xl space-y-5">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <a href="#/" className={action} aria-label="대시보드로 돌아가기"><ArrowLeft className="h-3.5 w-3.5" />대시보드</a>
            <h1 className="flex items-center gap-2 text-lg font-bold"><ShieldCheck className="h-5 w-5 text-[#FFE27A]" />관리자 콘솔</h1>
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <span>{me.user}</span>
            <button type="button" onClick={refresh} className={action}><RefreshCw className="h-3 w-3" />새로고침</button>
          </div>
        </header>

        {error && <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{error}</p>}
        {notice && (
          <p role="status" className="flex items-center justify-between gap-3 rounded-lg border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-sm text-violet-100">
            {notice}<button type="button" onClick={() => setNotice('')} className="text-xs text-violet-300 hover:text-white">닫기</button>
          </p>
        )}

        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="가입한 회원" value={users && people} />
          <Stat label="지금 로그인 중" value={users && online} />
          <Stat label="전체 앱" value={projects?.length} />
          <Stat label="실행 중인 앱" value={projects && live} tone="emerald" />
        </div>

        <Card title="사용자" icon={Users} bodyClass="p-0">
          {users ? <UsersTable users={users} owner={owner} onOwner={setOwner} onChanged={refresh} flash={flash} />
            : <p className="p-5 text-sm text-slate-500">불러오는 중…</p>}
        </Card>

        <Card title="전체 앱" icon={Boxes} bodyClass="p-0" right={(
          <div className="flex flex-wrap items-center gap-2">
            <label className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-500" />
              <input className={`${input} w-44 pl-8`} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="앱 이름·소스 검색" aria-label="앱 검색" />
            </label>
            <select className={input} value={owner} onChange={(e) => setOwner(e.target.value)} aria-label="소유자로 거르기">
              <option value="">모든 소유자</option>
              {owners.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </div>
        )}>
          {projects ? <AppsTable projects={shown} stopping={stopping} onStop={onStop} />
            : <p className="p-5 text-sm text-slate-500">불러오는 중…</p>}
        </Card>
      </div>
    </div>
  );
}
