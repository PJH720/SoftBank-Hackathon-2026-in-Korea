import React, { useEffect, useState } from 'react';
import { Cloud, KeyRound, LogIn, UserPlus } from 'lucide-react';
import { checkToken, getAuthConfig, logIn, signUp } from '../api';
import { Card, Dot } from './ui';

const input = 'w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-violet-500/60 focus:outline-none';

// Shown instead of the dashboard until the caller has a token that maps to a user.
// Login and sign-up both end with a token in localStorage; onAuthed(token) lets App re-check who it is.
export default function AuthScreen({ onAuthed, healthy }) {
  const [mode, setMode] = useState('login');
  const [form, setForm] = useState({ username: '', password: '', confirm: '', code: '' });
  const [needCode, setNeedCode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [token, setToken] = useState('');
  const [tokenError, setTokenError] = useState('');
  const [tokenBusy, setTokenBusy] = useState(false);

  useEffect(() => { getAuthConfig().then((c) => setNeedCode(!!c.signup_code)); }, []);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const signup = mode === 'signup';

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (signup && form.password !== form.confirm) { setError('비밀번호 확인이 일치하지 않습니다'); return; }
    setBusy(true);
    try {
      const res = await (signup ? signUp(form) : logIn(form));
      onAuthed(res.token);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  // an unknown token would just leave this screen up with no hint, so check it first
  const submitToken = async (e) => {
    e.preventDefault();
    const t = token.trim();
    if (!t) return;
    setTokenError('');
    setTokenBusy(true);
    try {
      if (await checkToken(t)) onAuthed(t);
      else setTokenError('유효하지 않은 API 토큰입니다');
    } catch (err) {
      setTokenError(err.message);
    } finally {
      setTokenBusy(false);
    }
  };

  const tab = (m, label, Icon) => (
    <button type="button" onClick={() => { setMode(m); setError(''); }}
      className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm ${mode === m ? 'bg-violet-600/80 text-white' : 'text-slate-400 hover:text-slate-200'}`}>
      <Icon className="h-4 w-4" /> {label}
    </button>
  );

  return (
    <div className="grid min-h-screen place-items-center bg-[#0a0d14] px-4 text-slate-100">
      <div className="pointer-events-none fixed inset-x-0 top-0 h-72 bg-gradient-to-b from-violet-900/20 via-indigo-900/5 to-transparent" />
      <div className="relative w-full max-w-sm space-y-5">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-violet-500 to-indigo-600 shadow-lg shadow-violet-900/50">
            <Cloud className="h-5 w-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-bold tracking-tight">CloudMorph</h1>
            <p className="flex items-center gap-1.5 text-xs text-slate-400">
              <Dot tone={healthy ? 'emerald' : healthy === false ? 'rose' : 'slate'} /> 백엔드 {healthy ? '연결됨' : healthy === false ? '끊김' : '확인 중'}
            </p>
          </div>
        </div>

        <Card>
          <div className="mb-4 flex gap-1 rounded-xl bg-black/30 p-1">
            {tab('login', '로그인', LogIn)}
            {tab('signup', '회원가입', UserPlus)}
          </div>
          <form className="space-y-3" onSubmit={submit}>
            <input className={input} value={form.username} onChange={set('username')} placeholder="아이디 (영문 소문자 · 숫자 3자 이상)" autoComplete="username" autoFocus required />
            <input className={input} type="password" value={form.password} onChange={set('password')} placeholder="비밀번호 (8자 이상)" autoComplete={signup ? 'new-password' : 'current-password'} required />
            {signup && <input className={input} type="password" value={form.confirm} onChange={set('confirm')} placeholder="비밀번호 확인" autoComplete="new-password" required />}
            {signup && needCode && <input className={input} value={form.code} onChange={set('code')} placeholder="가입 코드 (관리자에게 받으세요)" required />}
            {error && <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">{error}</p>}
            <button type="submit" disabled={busy}
              className="w-full rounded-lg bg-gradient-to-r from-violet-600 to-indigo-600 px-3 py-2 text-sm font-semibold text-white shadow-lg shadow-violet-900/40 disabled:opacity-50">
              {busy ? '확인 중…' : signup ? '가입하고 시작하기' : '로그인'}
            </button>
          </form>
          <p className="mt-4 text-xs leading-5 text-slate-500">
            앱 이름은 계정마다 따로 관리됩니다. 다른 사람이 쓰고 있는 앱 이름은 사용할 수 없고, 내 앱과 배포 기록은 나에게만 보입니다.
          </p>
        </Card>

        <div className="text-center text-xs text-slate-500">
          {showToken ? (
            <form className="space-y-2" onSubmit={submitToken}>
              <div className="flex gap-2">
                <input className={input} type="password" value={token} onChange={(e) => { setToken(e.target.value); setTokenError(''); }} placeholder="API 토큰" autoFocus />
                <button type="submit" disabled={tokenBusy} className="rounded-lg border border-white/10 px-3 text-slate-300 hover:text-white disabled:opacity-50">{tokenBusy ? '확인 중…' : '접속'}</button>
              </div>
              {tokenError && <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-left text-xs text-rose-200">{tokenError}</p>}
            </form>
          ) : (
            <button type="button" onClick={() => setShowToken(true)} className="inline-flex items-center gap-1 hover:text-slate-300">
              <KeyRound className="h-3.5 w-3.5" /> API 토큰으로 접속 (관리자)
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
