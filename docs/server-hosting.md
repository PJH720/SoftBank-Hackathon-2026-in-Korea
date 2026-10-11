# CloudMorph 서버 호스팅 (10/10)

서비스 자체를 GCE VM 한 대에 올렸다. 노트북마다 gcloud·Docker·키를 맞출 필요가 없고, 노트북을 꺼도 대시보드와 GitHub 웹훅이 계속 동작한다.

| 항목 | 값 |
|---|---|
| 주소 | https://cloudmorph.34-64-253-41.sslip.io (sslip.io 무료 DNS + Let's Encrypt 자동 인증서, 추가 비용 없음) |
| VM | `cloudmorph-control` · e2-standard-4 · Ubuntu 24.04 · 고정 IP 34.64.253.41 · 하루 약 4천 원 |
| 서비스 계정 | `cloudmorph-control@…` — run.admin, artifactregistry.writer, cloudsql.admin, iam.serviceAccountUser, logging.viewer, cloudfunctions.developer, cloudbuild.builds.editor, storage.admin, viewer, serviceUsageConsumer |
| 추가로 켠 API | Cloud Functions, **Cloud Billing**, **Cloud Resource Manager** (서비스 계정으로 쓰면 필요 — 실측으로 확인) |
| 방화벽 | `cloudmorph-control-web` 80·443 |

## 구성
- `deploy/server/bootstrap.sh` — 빈 VM에 Docker·uv·cloudflared, gcloud 설정, Docker→Artifact Registry 인증, 노드 풀용 SSH 키, 저장소 클론
- `deploy/server/cloudmorph-backend.service` — 백엔드 systemd 서비스. `.env`(API 키) + `~/cloudmorph-server.env`(토큰, 자동 확장 감시, 프로젝트)
- `deploy/server/Caddyfile` — HTTPS, 대시보드 정적 파일, `/deploy` `/projects` `/fleet` `/webhook` `/health`를 백엔드로 (SSE는 버퍼링 없이)
- `scripts/server-deploy.sh [ref]` — 노트북에서 서버 갱신: 코드 checkout → 의존성 → 대시보드 빌드·업로드 → 서비스 재시작 → 헬스체크

```bash
CLOUDMORPH_SERVER=cloudmorph@34.64.253.41 CLOUDMORPH_DOMAIN=cloudmorph.34-64-253-41.sslip.io scripts/server-deploy.sh main
```

## 서버에서 달라지는 점
- **local 타깃 = 서버 자신의 Docker** (대시보드 표기 "CloudMorph 서버"). 사용자 PC 자원을 쓰는 진짜 온프레미스는 `agent` 타깃이다 → [onprem-agent.md](onprem-agent.md). 내 노트북에 배포하는 장면이 필요하면 노트북에서 `scripts/demo-up.sh`로 같은 코드를 띄운다(노트북은 공인 IP가 없어 서버가 들어갈 수 없음).
- 소스는 **서버의 임시 폴더**로 가져온다. GitHub URL을 권장. 서버 경로(`sample-apps/...`)는 서버의 저장소 기준.
- 서버가 x86이라 Cloud Run용 amd64 빌드가 교차 빌드 없이 빠르다.
- 토큰은 서버에 항상 설정되어 있다(대시보드에서 입력). `CLOUDMORPH_API_TOKEN`은 관리자, 팀원·사용자별 토큰은 `~/cloudmorph-server.env`에 `CLOUDMORPH_USERS=alice:<토큰>,bob:<토큰>`으로 넣고 백엔드를 재시작한다. 사용자는 자기 프로젝트만 보고 바꾼다.
- 대시보드에서 아이디·비밀번호로 회원가입과 로그인을 할 수 있다(`backend/app/users.py`). 로그인하면 사용자별 토큰이 발급되어 위의 토큰과 똑같이 동작한다. 공개 서버에서는 `~/cloudmorph-server.env`에 `CLOUDMORPH_SIGNUP_CODE=<가입 코드>`를 넣어서, 코드를 아는 사람만 가입하게 한다. 이 값이 없으면 누구나 가입해서 배포할 수 있다. 계정은 `backend/.cloudmorph/accounts.json`(권한 600)에 저장되며, 비밀번호는 scrypt 해시로, 세션 토큰은 sha256 해시로만 남는다. 로그인 실패는 접속 IP별로 센다. 한 IP에서 같은 아이디로 5번, 아이디와 무관하게 20번 틀리면 그 IP만 5분 동안 막히므로, 다른 곳에서 일부러 틀려서 계정 주인을 잠글 수는 없다. 백엔드는 Caddy·cloudflared 뒤(루프백)에 있으므로 `X-Forwarded-For`의 마지막 값을 접속 IP로 쓴다.
- push 자동 배포는 프로젝트마다 연결한다(대시보드 프로젝트 목록 → "push 자동 배포 연결"). 프로젝트별 웹훅 URL·비밀값을 쓴다. 자세한 것은 [cd-github-webhook.md](cd-github-webhook.md).
- 자동 확장 감시 루프가 백엔드 안에서 상시 돈다(`CLOUDMORPH_FLEET_WATCH=1`).

## 실측 (서버 대시보드, Playwright + Chrome)
| 시나리오 | 결과 |
|---|---|
| 방명록 → local(서버) + cloudrun + node | 83초, 세 URL 모두 Postgres 연결 |
| function-hello → function | 70초, `served_by: cloud run functions (gen2)` |
| GitHub push → 서버 웹훅 | ping 200, push 후 Cloud Run에 새 버전(v8) |
| node 타깃 → 가장 한가한 노드 | **AWS EC2**가 선택됨, GCP 라우터 → AWS 노드로 트래픽 |

## 노드 풀
| 노드 | 회사 | 비고 |
|---|---|---|
| gcp-node-1, gcp-node-2 | GCP 서울 | e2-small |
| aws-node-1 | AWS 서울 | t3.small, `deploy/nodes/aws-node.sh`로 생성, 사용자 `ubuntu` |
| (오라클) | OCI | CLI 설정 후 추가 예정 |
