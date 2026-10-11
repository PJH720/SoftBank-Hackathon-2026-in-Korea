import React from 'react';
import { Check, Loader2, Square, X } from 'lucide-react';
import { STEPS } from './copy';
import { progress, sentence } from './progress';

const ICON = {
  done: <Check className="h-3.5 w-3.5" />,
  active: <Loader2 className="h-3.5 w-3.5 animate-spin" />,
  healing: <Loader2 className="h-3.5 w-3.5 animate-spin" />,
  waiting: <span className="text-[11px] font-bold">?</span>,
  failed: <X className="h-3.5 w-3.5" />,
  cancelled: <Square className="h-3 w-3" />,
};
const RING = {
  done: 'bg-emerald-500 text-white',
  active: 'bg-violet-500 text-white',
  healing: 'bg-amber-500 text-white',
  waiting: 'bg-amber-500 text-white',
  failed: 'bg-rose-500 text-white',
  cancelled: 'bg-slate-600 text-white',
  idle: 'bg-white/5 text-slate-600 ring-1 ring-white/10',
};

export default function RunProgress({ run, elapsed, onCancel, cancelling }) {
  const s = progress(run);
  return (
    <section className="rounded-2xl border border-white/5 bg-slate-900/60 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-base font-semibold text-white" aria-live="polite">{sentence(run)}</p>
        <div className="flex items-center gap-3 text-[12px] text-slate-400">
          {elapsed && <span className="tabular-nums">{elapsed}</span>}
          {run.status === 'running' && onCancel && (
            <button type="button" onClick={onCancel} disabled={cancelling} className="rounded-lg border border-white/10 px-2.5 py-1 text-slate-300 hover:border-rose-400/50 hover:text-rose-200 disabled:opacity-50">
              {cancelling ? '멈추는 중…' : '배포 멈추기'}
            </button>
          )}
        </div>
      </div>
      <ol className="mt-4 grid grid-cols-5 gap-2">
        {STEPS.map((step) => {
          const state = s[step.id];
          return (
            <li key={step.id} className="flex flex-col items-center gap-1.5 text-center">
              <span className={`grid h-7 w-7 place-items-center rounded-full ${RING[state] || RING.idle}`}>{ICON[state] || <span className="text-[11px]">·</span>}</span>
              <span className={`text-[11px] leading-4 ${state === 'idle' ? 'text-slate-600' : 'text-slate-300'}`}>{step.label}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
