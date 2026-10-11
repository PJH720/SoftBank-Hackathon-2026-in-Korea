import React, { useMemo, useState } from 'react';
import { AlertTriangle, Loader2, MessageCircleQuestion, Play } from 'lucide-react';
import Explain from './Explain';
import { PLACES, SERVICE_NAME, josa } from './copy';

// The same name can be needed at build and at run time; one answer covers both.
function byName(items) {
  const merged = new Map();
  for (const it of items) {
    const prev = merged.get(it.name);
    merged.set(it.name, prev
      ? { ...prev, scopes: [...new Set([...prev.scopes, it.scope])], secret: prev.secret || it.secret, generate: prev.generate && it.generate, auto: prev.auto && it.auto, resource: prev.resource || it.resource }
      : { ...it, scopes: [it.scope] });
  }
  return [...merged.values()];
}

function about(it) {
  if (it.resource) return `${it.resource === 'postgres' ? 'PostgreSQL' : it.resource} 접속 주소예요.`;
  if (it.scopes.includes('build') && !it.scopes.includes('runtime')) return '앱을 만들 때(빌드) 필요한 값이에요.';
  if (it.secret) return '비밀값이에요. 화면과 기록에는 남기지 않아요.';
  return '앱이 실행될 때 읽는 설정값이에요.';
}

function blank(it) {
  if (it.auto) return '비워 두면 DB를 새로 만들어 연결해요';
  if (it.generate) return '비워 두면 무작위 값을 만들어 넣어요';
  return '비워 두면 이 값 없이 배포해요';
}

/** "배포 전에 몇 가지만 여쭤볼게요": where to deploy (when CloudMorph picks) and the values the code needs. */
export default function AskCard({ request, onSubmit }) {
  const items = useMemo(() => byName(request.items || []), [request.items]);
  const choose = request.chooseTarget;
  const [target, setTarget] = useState(choose?.recommended || null);
  const [values, setValues] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const decided = choose?.decision;
  // place -> why the analyzer took it off; a reason without `consent` is a hard limit, so the place can't be picked
  const removed = choose?.removed || {};
  const blocked = (t) => (removed[t] || []).some((r) => !r.consent);
  const consenting = Boolean(removed[target]?.length) || (choose?.held_back && decided && target === decided.target);

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSubmit(values, choose ? [target] : null);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  };

  return (
    <section className="rounded-2xl border border-amber-500/40 bg-slate-900/80 p-5 shadow-xl shadow-black/30">
      <h3 className="flex items-center gap-2 text-base font-bold text-white">
        <MessageCircleQuestion className="h-5 w-5 text-amber-300" /> 배포 전에 몇 가지만 여쭤볼게요
      </h3>
      <p className="mt-1 text-[13px] leading-5 text-slate-400">
        꼭 필요한 것만 물어봐요.{request.timeoutSec ? ` ${Math.round(request.timeoutSec / 60)}분 안에 답이 없으면 배포를 멈춰요.` : ''}
      </p>

      {choose && (
        <div className="mt-4">
          <p className="mb-2 text-sm font-semibold text-slate-100">어디에 올릴까요?</p>
          {choose.held_back && (
            <div className="mb-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-[12px] leading-5 text-amber-100">
              <p className="flex items-center gap-1.5 font-semibold">
                <AlertTriangle className="h-4 w-4" />
                {decided && decided.target !== choose.recommended ? `AI는 ${josa(SERVICE_NAME[decided.service], '을', '를')} 추천했어요` : '데이터가 사라질 수 있는 곳이 있어요'}
              </p>
              <p className="mt-1">그래서 안전하게 <b>{josa(PLACES[choose.recommended]?.name || choose.recommended, '을', '를')}</b> 먼저 골라 뒀어요. 왜 위험한지와 그렇게 본 코드는 아래 카드에 적어 뒀어요.</p>
              <p className="mt-1.5">데이터가 사라져도 괜찮다면 다른 곳을 골라도 돼요. 고르시면 이 위험에 동의한 걸로 볼게요.</p>
            </div>
          )}
          <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="올릴 곳">
            {choose.options.map((t) => {
              const place = PLACES[t] || { name: t, tech: t, short: '' };
              const on = target === t;
              const off = blocked(t);
              const pick = () => { if (!off) setTarget(t); };
              return (
                // a div, not a button: the "?" explanation inside is a button of its own
                <div key={t} role="radio" aria-checked={on} aria-disabled={off} tabIndex={off ? -1 : 0} onClick={pick}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } }}
                  className={`rounded-xl border p-3 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60 ${off ? 'cursor-not-allowed border-white/5 opacity-60' : on ? 'cursor-pointer border-violet-400/60 bg-violet-500/10' : 'cursor-pointer border-white/10 hover:border-white/25'}`}>
                  <span className="flex items-center gap-1.5 text-[13px] font-semibold text-white">
                    <Explain term={place.name} text={place.explain}>{place.name}</Explain>
                    <span className="font-normal text-slate-500">· {place.tech}</span>
                    {t === choose.recommended && <span className="ml-auto rounded-md bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-300">안전한 선택</span>}
                    {decided && t === decided.target && t !== choose.recommended && <span className="ml-auto rounded-md bg-violet-500/15 px-1.5 py-0.5 text-[10px] text-violet-200">AI 추천</span>}
                  </span>
                  <span className="mt-0.5 block text-[12px] text-slate-400">{place.short}</span>
                  {removed[t] && (
                    <ul className="mt-2 space-y-1.5 border-t border-white/5 pt-2 text-[12px] leading-5">
                      <li className={`inline-block rounded-md px-1.5 py-0.5 text-[10px] ${off ? 'bg-slate-500/15 text-slate-300' : 'bg-amber-500/15 text-amber-200'}`}>
                        {off ? '갈 수 없어요' : '데이터가 사라질 수 있어요'}
                      </li>
                      {removed[t].map((r, i) => (
                        <li key={i}>
                          <span className={r.consent ? 'text-amber-100' : 'text-slate-300'}>{r.reason}</span>
                          {r.ai && <span className="block text-slate-400">AI 판단: {r.ai}</span>}
                          {r.evidence?.length > 0 && (
                            <details className="mt-0.5" onClick={(e) => e.stopPropagation()}>
                              <summary className="cursor-pointer text-[11px] text-slate-500 hover:text-slate-300">그렇게 본 코드 {r.evidence.length}줄</summary>
                              {r.evidence.map((line) => <span key={line} className="block truncate font-mono text-[11px] text-slate-500" title={line}>{line}</span>)}
                            </details>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {items.length > 0 && (
        <div className="mt-5 space-y-3">
          <p className="text-sm font-semibold text-slate-100">
            앱이 쓰는 값이에요 <Explain term="환경변수" />
          </p>
          {items.map((it) => (
            <label key={it.name} className="block rounded-xl border border-white/5 bg-black/20 p-3">
              <span className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[13px] font-semibold text-slate-100">{it.name}</span>
                {it.secret && <span className="rounded-md bg-rose-500/10 px-1.5 py-0.5 text-[10px] text-rose-300">비밀값</span>}
                {it.auto && <span className="rounded-md bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-300">비워 두면 자동</span>}
              </span>
              <span className="mt-0.5 block text-[12px] text-slate-400">{about(it)}</span>
              {it.evidence?.length > 0 && <span className="block truncate font-mono text-[11px] text-slate-600">코드 위치: {it.evidence.join(' · ')}</span>}
              <input
                type={it.secret ? 'password' : 'text'}
                autoComplete="off"
                value={values[it.name] || ''}
                onChange={(e) => setValues((v) => ({ ...v, [it.name]: e.target.value }))}
                placeholder={blank(it)}
                className="mt-2 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-[13px] text-slate-100 placeholder:text-slate-600 focus:border-amber-400/60 focus:outline-none"
              />
            </label>
          ))}
        </div>
      )}

      {error && <p className="mt-3 text-xs text-rose-300">보내지 못했어요 · {error}</p>}
      <button type="button" disabled={busy || (choose && !target)} onClick={send}
        className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-white px-4 py-3 text-sm font-bold text-slate-900 transition hover:bg-slate-200 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400">
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4 fill-current" />}
        {consenting ? '위험을 알고 이대로 배포하기' : '이대로 배포하기'}
      </button>
    </section>
  );
}
