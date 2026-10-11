import { useEffect, useState } from 'react';

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

// Tracks the OS reduced-motion setting live, so scenes calm down without a reload.
export default function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.(REDUCED_MOTION_QUERY).matches);
  useEffect(() => {
    const mq = window.matchMedia?.(REDUCED_MOTION_QUERY);
    if (!mq) return undefined;
    const onChange = (e) => setReduced(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return reduced;
}
