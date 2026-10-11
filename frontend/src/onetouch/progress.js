// Where a deployment is, in plain words (shared by the deploy page and the apps page).

/** Five plain steps: read · pick · build · ship · done. */
export function progress(run) {
  const phases = Object.values(run.targetState || {}).map((s) => s.phase);
  const started = phases.some((p) => p !== 'pending');
  const finished = !['running', 'idle'].includes(run.status);
  const read = run.decision || run.analysis || started ? 'done' : run.status === 'running' ? 'active' : finished ? 'failed' : 'idle';
  const pick = started ? 'done' : run.envRequest ? 'waiting' : read === 'done' ? 'active' : 'idle';
  const building = phases.some((p) => p === 'deploying' || p === 'healing');
  const ok = run.status === 'completed' || run.status === 'partial_failure';
  const build = !started ? (finished && run.status === 'failed' ? 'failed' : 'idle') : building ? 'active' : ok || phases.includes('done') ? 'done' : 'failed';
  const ship = !started ? 'idle' : building ? (phases.includes('healing') ? 'healing' : 'active') : ok ? 'done' : finished ? 'failed' : 'active';
  const done = ok ? 'done' : finished ? (run.status === 'cancelled' ? 'cancelled' : 'failed') : 'idle';
  return { read, pick, build, ship, done };
}

export function sentence(run) {
  const s = progress(run);
  if (run.status === 'idle') return '';
  if (run.status === 'cancelled') return '배포를 중단했어요.';
  if (s.done === 'done') return run.status === 'partial_failure' ? '일부 위치에만 올라갔어요. 아래에서 확인해 주세요.' : '배포를 마쳤어요. 바로 열어 볼 수 있어요.';
  if (s.done === 'failed') return '배포하지 못했어요. 아래 이유를 확인해 주세요.';
  if (run.question && !run.question.choice && run.question.ask) return 'AI가 코드를 고치려고 해요. 먼저 여쭤볼게요.';
  if (s.ship === 'healing') return '오류가 나서 AI가 고치고 있어요. 잠시만 기다려 주세요.';
  if (s.build === 'active') return '앱을 만들어 올리고 있어요. 보통 1분 안에 끝나요.';
  if (s.pick === 'waiting') return '배포 전에 몇 가지만 여쭤볼게요.';
  if (s.read === 'done') return '어디에 올릴지 정했어요. 나머지를 확인하고 있어요.';
  return '코드를 읽고 있어요.';
}
