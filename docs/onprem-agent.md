# 온프레미스 에이전트 (타깃 `agent`)

"온프레미스" 배포가 **사용자 자신의 컴퓨터 자원**을 쓰게 하는 장치다. 예전 `local` 타깃은 CloudMorph 서버 VM의 Docker에 배포해서,
모든 사용자의 "온프레미스" 배포를 팀 VM 한 대가 떠안았다. `local`은 이름만 "CloudMorph 서버"로 바꿔 그대로 둔다.

```
사용자 PC: python3 cloudmorph_agent.py --server https://<cloudmorph> --token <내 API 토큰>
   │  밖으로 나가는 HTTPS만 사용 (공인 IP·포트 개방 불필요): POST /agent/poll (long-poll, 약 25초)
   ▼
서버: 타깃 agent → AgentHub.dispatch()가 그 사용자의 에이전트 큐에 작업을 넣고, 결과가 올 때까지 파이프라인 스레드를 세운다
   ▲
에이전트: GET 소스 tar → 내 PC Docker로 build/run (127.0.0.1에만 바인딩) → 헬스체크 → (선택) cloudflared 터널 → POST 결과
   └ 실패하면 서버 healer가 Dockerfile을 고쳐 같은 PC로 다시 보낸다 (stderr를 deployer와 같은 형식으로 보내서 같은 규칙이 적용된다)
```

## 설계
| 항목 | 내용 |
|---|---|
| 설치 | `agent/cloudmorph_agent.py` 한 파일, 표준 라이브러리만 사용. Python 3.9+와 Docker만 있으면 된다. 대시보드 "내 PC 에이전트" 카드의 명령을 복사해 실행 |
| 여러 사용자 | 에이전트는 사용자 토큰으로 인증하고 **그 사용자의 작업만** 받는다. 다른 사용자는 남의 에이전트·작업·소스를 볼 수 없다(404) |
| 여러 대 | 한 사용자가 PC 여러 대를 붙일 수 있다. 같은 앱의 재배포는 **마지막으로 그 앱을 돌린 PC**로 간다(그 PC가 온라인일 때) |
| 공개 여부 | cloudflared가 있으면 `*.trycloudflare.com` 공개 URL, `--private`이거나 cloudflared가 없으면 `http://127.0.0.1:<포트>`(그 PC에서만 열림) |
| 무중단 교체 | 새 버전이 헬스체크를 통과한 뒤에 같은 앱의 이전 컨테이너·터널을 내린다 |
| 실패 처리 | 온라인 에이전트가 없으면 즉시 `user_action_required`로 실패(healer 중단). 60초 안에 아무도 안 가져가거나 작업 중 45초 넘게 소식이 없으면 실패. 전체 상한 `CLOUDMORPH_AGENT_JOB_TIMEOUT`(기본 1800초) |
| 보낸 소스 | `.git`, `node_modules`, `.venv`, 심볼릭 링크는 tar에서 뺀다. 에이전트는 경로 탈출·링크가 든 tar를 풀지 않는다 |
| 상태 | 작업과 에이전트 목록은 서버 메모리에만 있다. 서버가 재시작되면 에이전트가 다음 poll에서 다시 등록된다 |

## API (`/agent/*`, API 토큰 필요, Caddy가 백엔드로 넘김)
- `GET /agent/script` 에이전트 파일 · `GET /agent/status` 내 에이전트 목록
- `POST /agent/poll {name, info}` · `GET /agent/jobs/{id}/source` · `POST /agent/jobs/{id}/log {lines}` (하트비트 겸용) · `POST /agent/jobs/{id}/result`

## 한계
- DB 자동 연결(Postgres 사이드카)은 아직 에이전트에 없다. 서버 `local`·`node`·`cloudrun`만 지원
- 에이전트를 끄면 터널은 닫히고 컨테이너는 남는다. 정리: `docker rm -f $(docker ps -aq --filter label=cloudmorph.app)`
- 사용자의 Dockerfile이 사용자 PC에서 실행된다. 에이전트를 켠다는 것이 그에 대한 동의다
