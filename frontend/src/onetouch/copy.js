// Everything people read in the one-touch screens, in one place. Toss-style: short 해요체 sentences, one idea
// each, everyday words first; a term that cannot be avoided gets a one-line "이게 뭐예요?" explanation.

export const PLACES = {
  local: {
    name: '내 컴퓨터',
    tech: 'Docker',
    short: '지금 이 컴퓨터에서 돌려요',
    explain: '이 컴퓨터 안에서 앱을 돌려요. 연습하거나, 데이터를 디스크에 두는 앱에 잘 맞아요. 컴퓨터를 끄면 앱도 같이 꺼져요.',
  },
  cloudrun: {
    name: '클라우드',
    tech: 'Google Cloud Run',
    short: '요청이 올 때만 켜지는 서버예요',
    explain: '구글이 대신 서버를 돌려 줘요. 아무도 안 쓰면 꺼져 있어서 비용이 거의 안 들고, 사람이 몰리면 알아서 여러 대로 늘어나요.',
  },
  node: {
    name: 'VM 노드',
    tech: '노드 풀',
    short: '항상 켜진 가상 컴퓨터예요',
    explain: '클라우드에 빌려 둔 가상 컴퓨터에서 24시간 돌려요. 바빠지면 다른 컴퓨터에 하나 더 띄워요.',
  },
  function: {
    name: '서버리스 함수',
    tech: 'Cloud Run functions',
    short: '함수 하나만 올려요',
    explain: '앱 전체가 아니라 함수 하나만 올려 두고, 요청이 올 때마다 그 함수만 실행해요. 가장 가볍고 저렴해요.',
  },
};

export const SERVICE_TO_PLACE = { serverless_container: 'cloudrun', vm: 'local', static_site: 'cloudrun', serverless_function: 'function' };
export const SERVICE_NAME = {
  serverless_container: 'Cloud Run',
  vm: '항상 켜진 서버',
  static_site: '정적 사이트',
  serverless_function: '서버리스 함수',
};

export const GLOSSARY = {
  컨테이너: '앱과 앱이 필요한 것을 한 상자에 담은 거예요. 어디서 열어도 똑같이 돌아가요.',
  빌드: '코드를 실제로 돌릴 수 있는 상자(이미지)로 만드는 과정이에요.',
  환경변수: '코드 밖에서 넣어 주는 설정값이에요. 비밀번호 같은 값을 코드에 적지 않으려고 써요.',
  데이터베이스: '데이터를 오래 보관하는 창고예요. 앱을 다시 켜도 데이터가 남아요.',
  사이드카: '앱 옆에 붙어 함께 돌아가는 보조 상자예요. 여기서는 앱 전용 데이터베이스예요.',
  'Cloud SQL': '구글이 관리해 주는 데이터베이스예요. 백업과 업데이트를 대신 해 줘요.',
  터널: '내 컴퓨터에서 돌아가는 앱을 인터넷에서도 볼 수 있게 이어 주는 통로예요.',
  '블루그린 배포': '새 버전을 옆에 먼저 띄워 보고, 문제가 없을 때만 바꿔 끼워요. 그래서 배포 중에도 서비스가 멈추지 않아요.',
  자가치유: '배포가 실패하면 AI가 오류를 읽고 설정을 고쳐 다시 시도해요. 코드를 고쳐야 할 땐 꼭 먼저 여쭤봐요.',
  'clef-flash': '이 컴퓨터에서 돌아가는 판단 AI예요. 글을 쓰지 않고 선택지마다 확률만 매겨서 1초 안에 답해요.',
  Jev: '장애 상황을 보고 원인과 할 일을 골라 주는 판단 AI예요. 키가 없으면 clef-flash가 같은 방식으로 대신 판단해요.',
  PR: '코드를 이렇게 바꾸자고 저장소에 올리는 제안서예요. 사람이 확인하고 합쳐야(머지) 실제 코드가 바뀌어요.',
};

export const STEPS = [
  { id: 'read', label: '코드 읽기' },
  { id: 'pick', label: '올릴 곳 정하기' },
  { id: 'build', label: '앱 만들기' },
  { id: 'ship', label: '올리고 확인하기' },
  { id: 'done', label: '완료' },
];

export const STATUS = {
  running: { tone: 'violet', label: '배포 중' },
  completed: { tone: 'emerald', label: '잘 돌아가요' },
  partial_failure: { tone: 'amber', label: '일부만 떴어요' },
  failed: { tone: 'rose', label: '배포 실패' },
  stopped: { tone: 'slate', label: '멈춤' },
  cancelled: { tone: 'slate', label: '중단됨' },
};

export const DRILLS = [
  { kind: 'crash', label: '앱 강제로 끄기', explain: '앱 상자를 갑자기 꺼요. 서버가 죽은 상황이에요.', needs: 'local' },
  { kind: 'db_down', label: 'DB 끄기', explain: '앱의 데이터베이스를 꺼요. 앱은 켜져 있지만 데이터를 못 읽어요.', needs: 'db' },
  { kind: 'outage', label: '클라우드 장애 가정', explain: '아무것도 건드리지 않고, 그 자리에 90초 동안 연결이 안 된다고 가정해요.', needs: null },
];

export const INCIDENT_STATUS = {
  open: { tone: 'amber', label: '확인 중' },
  acting: { tone: 'violet', label: '대처 중' },
  resolved: { tone: 'emerald', label: '해결됐어요' },
  escalated: { tone: 'rose', label: '직접 봐 주세요' },
};

export const ago = (ts) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 10) return '방금';
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  return `${Math.floor(s / 3600)}시간 전`;
};

export const seconds = (ms) => (ms < 1000 ? `${(ms / 1000).toFixed(2)}초` : `${(ms / 1000).toFixed(1)}초`);

/** Korean particle that fits the last sound of `word`: josa('Cloud Run', '을', '를') -> 'Cloud Run을'.
 * Hangul by its final consonant; Latin by how it is read in Korean: words ending in n·m·l·k·p·t·b·g end in a
 * consonant ("Docker"·"Jev" do not), acronyms by letter names ("DB" 디비, "SQL" 에스큐엘). Text in parentheses
 * is skipped: "clef-flash (로컬)" reads as "clef-flash". */
export function josa(word, withFinal, withoutFinal) {
  const base = String(word).replace(/\s*\([^)]*\)\s*$/, '').trim();
  const last = base.slice(-1);
  const code = last.charCodeAt(0);
  let final
  if (code >= 0xac00 && code <= 0xd7a3) {
    const jong = (code - 0xac00) % 28
    final = jong !== 0 && !(withFinal === '으로' && jong === 8) // ㄹ takes 로: 서울로, 파일로
  }
  else if (/[A-Z]{2}$/.test(base) || /^[A-Z]$/.test(base)) final = /[LMNR]$/.test(base) // acronyms read by letter: 디비, 에스큐엘
  else final = /[lmnkptbg]$/i.test(last)
  return `${word}${final ? withFinal : withoutFinal}`;
}
