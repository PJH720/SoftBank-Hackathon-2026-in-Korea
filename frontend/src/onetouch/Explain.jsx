import React, { useEffect, useRef, useState } from 'react';
import { GLOSSARY } from './copy';

/** A term with a small "?" that opens a one-line plain explanation (click, so it works on touch too). */
export default function Explain({ term, text, children, className = '' }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const body = text || GLOSSARY[term];

  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [open]);

  if (!body) return children ?? term;
  return (
    <span ref={ref} className={`relative inline-flex items-center ${className}`}>
      {children ?? <span>{term}</span>}
      <button
        type="button"
        aria-label={`${term} 설명 보기`}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="ml-1 grid h-4 w-4 shrink-0 place-items-center rounded-full bg-white/10 text-[10px] font-bold text-slate-300 hover:bg-white/20 hover:text-white"
      >
        ?
      </button>
      {open && (
        <span role="tooltip" className="absolute left-0 top-6 z-40 w-64 rounded-xl border border-white/10 bg-slate-900 p-3 text-left text-[12px] font-normal leading-5 text-slate-200 shadow-2xl shadow-black/60">
          <span className="mb-1 block font-semibold text-white">{term}이(가) 뭐예요?</span>
          {body}
        </span>
      )}
    </span>
  );
}
