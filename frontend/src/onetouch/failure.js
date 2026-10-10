// Why a deployment failed, in words a second-year student can act on. The raw text stays in "자세한 기록".
// Sources: deployer preflight problems ("- gcloud CLI not installed …") and healer summaries.

const PREFLIGHT = [
  [/gcloud CLI not installed/, () => '이 컴퓨터에 구글 클라우드 도구(gcloud)가 없어요. 설치한 뒤 다시 해 주세요: brew install --cask gcloud-cli'],
  [/no active gcloud account/, () => '구글 클라우드에 로그인돼 있지 않아요. 터미널에서 gcloud auth login을 해 주세요.'],
  [/GCP project not set/, () => '어느 구글 클라우드 프로젝트에 올릴지 정해 주세요. .env의 GCP_PROJECT_ID에 넣으면 돼요.'],
  [/billing not enabled/, () => '구글 클라우드 프로젝트에 결제 계정이 연결돼 있지 않아요. 콘솔에서 연결해 주세요.'],
  [/API not enabled: (\S+)/, (m) => `필요한 구글 클라우드 기능(${m[1]})이 꺼져 있어요. gcloud services enable ${m[1]}로 켜 주세요.`],
  [/docker daemon not running/, () => 'Docker가 꺼져 있어요. Docker Desktop을 켜 주세요.'],
  [/docker CLI not installed/, () => '이 컴퓨터에 Docker가 없어요. Docker Desktop을 설치해 주세요.'],
  [/cloudflared not installed/, () => '밖에서 접속하는 통로(cloudflared)가 없어요. brew install cloudflared로 설치해 주세요.'],
  [/node pool is empty/, () => '아직 VM 노드가 없어요. backend/nodes.json에 노드를 등록해 주세요.'],
  [/BuildKit/, () => '비밀값을 쓰는 빌드에는 Docker buildx가 필요해요.'],
];

const SUMMARY = [
  [/application code error/, '설정이 아니라 앱 코드 안에서 오류가 났어요. 코드를 고쳐야 해요. 자세한 기록에서 위치를 볼 수 있어요.'],
  [/Gave up after/, '세 번 고쳐 봤지만 해결하지 못했어요. 자세한 기록에서 오류를 확인해 주세요.'],
  [/source fix is up as a PR/, 'AI가 고친 코드를 PR로 올렸어요. 확인하고 머지한 뒤 다시 배포해 주세요.'],
  [/source fix was not deployed/, '코드를 고치지 않기로 해서 이번 배포는 멈췄어요.'],
  [/cancelled by user/, '배포를 중단했어요.'],
  [/kind=timeout/, '시간 안에 끝나지 않았어요. 잠시 뒤 다시 해 주세요.'],
];

/** { title, lines } for a failed target. `stderr` is the deployer's first failure (stage=heal event). */
export function explainFailure(message = '', stderr = '') {
  const text = `${message}\n${stderr}`;
  if (/user_action_required/.test(text)) {
    const problems = stderr.split('\n').filter((l) => l.startsWith('- ')).map((l) => l.slice(2));
    const lines = problems.map((p) => {
      for (const [rx, say] of PREFLIGHT) {
        const m = p.match(rx);
        if (m) return say(m);
      }
      return p;
    });
    return { title: '올리기 전에 준비가 필요해요', lines: lines.length ? [...new Set(lines)] : ['필요한 설정이 빠졌어요. 자세한 기록을 확인해 주세요.'] };
  }
  for (const [rx, say] of SUMMARY) if (rx.test(text)) return { title: say, lines: [] };
  return { title: message || '알 수 없는 이유로 실패했어요', lines: [] };
}
