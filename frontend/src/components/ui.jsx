import React, { useEffect, useRef, useState } from 'react';
import { Check, Copy, Square } from 'lucide-react';
import { TONE } from '../lib/style';


export function Card({ title, icon: Icon, right, children, className = '', bodyClass = '' }) {
  return (
    <section className={`rounded-2xl border border-white/5 bg-slate-900/60 shadow-xl shadow-black/20 backdrop-blur ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-white/5 px-5 py-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
            {Icon && <Icon className="h-4 w-4 text-slate-400" />} {title}
          </h2>
          <div className="flex items-center gap-2">{right}</div>
        </header>
      )}
      <div className={`p-5 ${bodyClass}`}>{children}</div>
    </section>
  );
}

export function Badge({ tone = 'slate', children, className = '' }) {
  return <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${TONE[tone].soft} ${className}`}>{children}</span>;
}

export function Dot({ tone = 'slate', pulse = false }) {
  return (
    <span className="relative inline-flex h-2 w-2">
      {pulse && <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${TONE[tone].dot}`} />}
      <span className={`relative inline-flex h-2 w-2 rounded-full ${TONE[tone].dot}`} />
    </span>
  );
}

export function Bar({ value, tone = 'violet' }) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  const t = v >= 80 ? 'rose' : v >= 50 ? 'amber' : tone;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/5">
      <div className={`h-full rounded-full transition-all duration-700 ${TONE[t].bar}`} style={{ width: `${v}%` }} />
    </div>
  );
}

export function CopyButton({ text, className = '' }) {
  const [status, setStatus] = useState('idle');
  const resetTimer = useRef(null);
  const mounted = useRef(true);
  const pending = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; clearTimeout(resetTimer.current); };
  }, []);
  const copy = async (e) => {
    e.preventDefault(); e.stopPropagation();
    if (pending.current) return;
    clearTimeout(resetTimer.current);
    pending.current = true;
    setStatus('pending');
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text);
      if (!mounted.current) return;
      setStatus('done');
      resetTimer.current = setTimeout(() => setStatus('idle'), 1800);
    } catch {
      if (mounted.current) setStatus('failed');
    } finally {
      pending.current = false;
    }
  };
  const message = status === 'done' ? '복사했어요.' : status === 'failed' ? '복사하지 못했어요. 내용을 직접 복사하거나 다시 시도해 주세요.' : '';
  return (
    <div className="inline-flex shrink-0 items-center">
      <button
        type="button"
        title={message || '복사'}
        aria-label={status === 'failed' ? '복사 다시 시도' : status === 'done' ? '복사 완료' : '복사'}
        aria-busy={status === 'pending'}
        disabled={status === 'pending'}
        onClick={copy}
        className={`inline-flex items-center gap-1 rounded-md p-1 text-slate-500 hover:bg-white/5 hover:text-slate-200 ${className}`}
      >
        {status === 'done' ? <Check aria-hidden="true" className="h-3.5 w-3.5 text-emerald-400" /> : <Copy aria-hidden="true" className="h-3.5 w-3.5" />}
        {status === 'failed' && <span className="whitespace-nowrap text-[11px] text-rose-300">복사 실패</span>}
      </button>
      <span role="status" className="sr-only">{message}</span>
    </div>
  );
}

const STOP_CONFIRM_MS = 6000;
const STOP_IDLE_CLASS = 'flex items-center gap-1 rounded-lg border border-rose-500/30 px-2 py-1 text-[11px] text-rose-300 hover:border-rose-400/60 hover:bg-rose-500/10 hover:text-rose-200 disabled:opacity-60';

// Two-step stop: the first click only asks, so a stray click never takes an app down.
export function StopButton({ onClick, busy, appName, disabled = false }) {
  const [confirming, setConfirming] = useState(false);
  const groupRef = useRef(null);
  const stopRef = useRef(null);
  const confirmRef = useRef(null);
  const restoreFocus = useRef(false);

  useEffect(() => {
    if (confirming) {
      confirmRef.current?.focus();
      const timer = setTimeout(() => {
        restoreFocus.current = !!groupRef.current?.contains(document.activeElement);
        setConfirming(false);
      }, STOP_CONFIRM_MS);
      return () => clearTimeout(timer);
    }
    if (restoreFocus.current) {
      restoreFocus.current = false;
      stopRef.current?.focus();
    }
    return undefined;
  }, [confirming]);

  const cancel = () => { restoreFocus.current = true; setConfirming(false); };
  // Back to idle before calling out, so a double click cannot stop twice.
  const confirm = () => {
    if (busy || disabled) return;
    setConfirming(false); onClick?.();
  };
  // Reset only when focus lands on a known element outside. A null relatedTarget also comes from the
  // swapped-out button unmounting and from Safari clicks (Safari never focuses buttons on click).
  const onBlur = (e) => {
    if (e.relatedTarget && !groupRef.current?.contains(e.relatedTarget)) {
      restoreFocus.current = false;
      setConfirming(false);
    }
  };
  const onKeyDown = (e) => {
    if (e.key !== 'Escape' || !confirming) return;
    e.preventDefault(); e.stopPropagation();
    cancel();
  };

  if (busy) {
    return (
      <button type="button" disabled className={STOP_IDLE_CLASS}>
        <Square className="h-3.5 w-3.5" /> 중지 중…
      </button>
    );
  }
  return (
    <span ref={groupRef} role="group" aria-label={appName ? `${appName} 중지` : '앱 중지'} onBlur={onBlur} onKeyDown={onKeyDown} className="inline-flex min-w-0 flex-wrap items-center gap-1.5">
      {confirming ? (
        <>
          <span role="status" className="text-[11px] text-rose-200" style={{ overflowWrap: 'anywhere' }}>{appName ? `${appName}의 모든 서비스를 중지할까요?` : '모든 서비스를 중지할까요?'} DB 데이터는 보존돼요.</span>
          <button ref={confirmRef} type="button" onClick={confirm} disabled={disabled}
            className="rounded-lg border border-rose-500/60 bg-rose-500/20 px-2 py-1 text-[11px] font-medium text-rose-100 hover:bg-rose-500/30">
            중지 확인
          </button>
          <button type="button" onClick={cancel}
            className="rounded-lg border border-white/10 px-2 py-1 text-[11px] text-slate-300 hover:border-white/20 hover:text-white">
            취소
          </button>
        </>
      ) : (
        <button ref={stopRef} type="button" onClick={() => setConfirming(true)} disabled={disabled} className={STOP_IDLE_CLASS}>
          <Square className="h-3.5 w-3.5" /> 중지
        </button>
      )}
    </span>
  );
}
