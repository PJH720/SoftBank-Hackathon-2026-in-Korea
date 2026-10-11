import React from 'react';
import { Gauge, Sparkles } from 'lucide-react';
import Explain from './Explain';
import { PLACES, SERVICE_NAME, seconds } from './copy';

const ORDER = ['serverless_container', 'vm', 'static_site', 'serverless_function'];

/** Jev's or clef-flash's 1-second pick, shown as soon as it arrives (before the rest of the analysis). */
export default function DecisionCard({ decision, finalTarget }) {
  if (!decision) return null;
  const place = PLACES[decision.target] || PLACES.cloudrun;
  const changed = finalTarget && finalTarget !== decision.target;
  return (
    <section className="rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-500/10 via-slate-900/70 to-slate-900/70 p-5 shadow-xl shadow-black/20">
      <p className="flex items-center gap-1.5 text-[12px] font-medium text-violet-200">
        <Sparkles className="h-3.5 w-3.5" /> 코드를 읽고 {seconds(decision.ms)} 만에 정했어요
      </p>
      <h3 className="mt-1.5 text-xl font-bold leading-snug text-white">
        이 앱은 <span className="text-violet-300">{SERVICE_NAME[decision.service]}</span>에 올리면 좋아요
      </h3>
      <p className="mt-1 text-sm leading-6 text-slate-300">{decision.explain}</p>

      {decision.why?.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {decision.why.map((w) => (
            <span key={w} className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 font-mono text-[11px] text-slate-300">{w}</span>
          ))}
        </div>
      )}

      <div className="mt-4 space-y-1.5">
        {ORDER.filter((k) => decision.probabilities?.[k] !== undefined).map((k) => {
          const p = Math.round(decision.probabilities[k] * 100);
          const top = k === decision.service;
          return (
            <div key={k} className="flex items-center gap-2 text-[12px]">
              <span className={`w-28 shrink-0 ${top ? 'font-semibold text-white' : 'text-slate-400'}`}>{SERVICE_NAME[k]}</span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/5">
                <span className={`block h-full rounded-full ${top ? 'bg-violet-400' : 'bg-slate-600'}`} style={{ width: `${p}%` }} />
              </span>
              <span className={`w-9 text-right tabular-nums ${top ? 'text-violet-200' : 'text-slate-500'}`}>{p}%</span>
            </div>
          );
        })}
      </div>

      {changed && (
        <p className="mt-4 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] leading-5 text-amber-100">
          다만 데이터가 사라질 수 있는 코드가 있어서, 안전하게 <b>{PLACES[finalTarget]?.name || finalTarget}</b>에 먼저 올리기로 했어요. 바꾸고 싶으면 아래에서 고를 수 있어요.
        </p>
      )}

      <p className="mt-4 flex flex-wrap items-center gap-x-1 text-[11px] text-slate-500">
        <Gauge className="h-3.5 w-3.5" /> 판단: <Explain term={decision.engine === 'Jev' ? 'Jev' : 'clef-flash'}>{decision.engine}</Explain>
        <span>· 코드 읽기(전처리) {decision.prep_ms}ms는 따로예요 · 입력 {decision.tokens}토큰 · 추천 위치 {place.name}</span>
      </p>
    </section>
  );
}
