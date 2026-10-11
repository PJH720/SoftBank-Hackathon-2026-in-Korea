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
- `scripts/server-deploy.sh [ref]` — 서버 갱신: ref를 origin의 커밋 하나로 고정 → 서버 checkout → 의존성 → 같은 커밋의 깨끗한 checkout에서 대시보드 빌드·업로드 → 서비스 재시작 → 헬스체크. 로컬 수정이나 다른 브랜치가 서버에 섞이지 않는다.
- `.github/workflows/server-cd.yml` — **CD**: main에 push → CI 통과 → 그 커밋으로 `server-deploy.sh` 실행. 수동 실행은 Actions → Server CD → Run workflow.

```bash
CLOUDMORPH_SERVER=cloudmorph@34.64.253.41 CLOUDMORPH_DOMAIN=cloudmorph.34-64-253-41.sslip.io scripts/server-deploy.sh main
```

### CD 설정 (한 번)
GitHub 환경 `production`(배포 브랜치 main만)에 둔다. 다른 브랜치의 워크플로는 이 키를 읽을 수 없다.

| 종류 | 이름 | 값 |
|---|---|---|
| secret | `SERVER_SSH_KEY` | CD 전용 ed25519 개인키. 공개키는 VM 메타데이터 `ssh-keys`에 `cloudmorph:` 사용자로 추가 (기존 키 유지) |
| secret | `SERVER_KNOWN_HOSTS` | `ssh-keygen -F 34.64.253.41` 출력 (서버 호스트 키 고정) |
| variable | `CLOUDMORPH_SERVER` | `cloudmorph@34.64.253.41` |
| variable | `CLOUDMORPH_DOMAIN` | `cloudmorph.34-64-253-41.sslip.io` |

배포할 때마다 백엔드가 재시작되므로 진행 중인 배포 작업은 끊긴다. 배포는 한 번에 하나씩, 뒤에 온 push는 기다린다. CI가 늦게 끝난 옛 커밋은 그사이 main이 앞서 나갔으면 건너뛴다(서버가 뒤로 돌아가지 않게).

## 사용자 앱 격리 (누구나 가입해서 배포하는 서버)
사용자 코드는 GCP 메타데이터 서버(`169.254.169.254`)에서 **자기가 도는 기계의 서비스 계정 토큰**을 꺼낼 수 있다. 그래서 사용자 앱에는 권한 없는 계정만 닿게 한다.

| 타깃 | 사용자 앱이 닿는 계정 | 장치 |
|---|---|---|
| cloudrun, function | `cloudmorph-apps@…` — `cloudsql.client`만 | `DEPLOYER_APP_SERVICE_ACCOUNT=cloudmorph-apps` (`cloudmorph-backend.service`) |
| local (서버 Docker) | 없음 | `cloudmorph-egress-guard.service`: `DOCKER-USER` 체인에서 컨테이너 → 메타데이터 서버 차단. 백엔드 자신은 영향 없음 |
| node | 노드 VM의 기본 계정, 범위 `devstorage.read_only` | (노드에도 같은 차단을 넣으면 더 좋다) |

이 설정이 없으면 cloudrun 앱은 프로젝트 기본 Compute 계정(**편집자**)으로, local 앱은 `cloudmorph-control`(run·sql·secret·storage 관리자)로 돈다.

```bash
# 한 번: 앱 전용 계정
gcloud iam service-accounts create cloudmorph-apps --display-name "CloudMorph user apps"
gcloud projects add-iam-policy-binding <PROJECT> --member serviceAccount:cloudmorph-apps@<PROJECT>.iam.gserviceaccount.com --role roles/cloudsql.client --condition=None
```

## 서버에서 달라지는 점
- **local 타깃 = 서버 자신의 Docker** (대시보드 표기 "CloudMorph 서버"). 사용자 PC 자원을 쓰는 진짜 온프레미스는 `agent` 타깃이다 → [onprem-agent.md](onprem-agent.md). 내 노트북에 배포하는 장면이 필요하면 노트북에서 `scripts/demo-up.sh`로 같은 코드를 띄운다(노트북은 공인 IP가 없어 서버가 들어갈 수 없음).
- 소스는 **서버의 임시 폴더**로 가져온다. GitHub URL을 권장. 서버 경로(`sample-apps/...`)는 서버의 저장소 기준.
- 서버가 x86이라 Cloud Run용 amd64 빌드가 교차 빌드 없이 빠르다.
- 토큰은 서버에 항상 설정되어 있다(대시보드에서 입력). `CLOUDMORPH_API_TOKEN`은 관리자, 팀원·사용자별 토큰은 `~/cloudmorph-server.env`에 `CLOUDMORPH_USERS=alice:<토큰>,bob:<토큰>`으로 넣고 백엔드를 재시작한다. 사용자는 자기 프로젝트만 보고 바꾼다.
- 대시보드에서 아이디·비밀번호로 회원가입과 로그인을 할 수 있다(`backend/app/users.py`). 로그인하면 사용자별 토큰이 발급되어 위의 토큰과 똑같이 동작한다. 공개 서버에서는 `~/cloudmorph-server.env`에 `CLOUDMORPH_SIGNUP_CODE=<가입 코드>`를 넣어서, 코드를 아는 사람만 가입하게 한다. 이 값이 없으면 누구나 가입해서 배포할 수 있다. 계정은 `backend/.cloudmorph/accounts.json`(권한 600)에 저장되며, 비밀번호는 scrypt 해시로, 세션 토큰은 sha256 해시로만 남는다. 로그인 실패는 접속 IP별로 센다. 한 IP에서 같은 아이디로 5번, 아이디와 무관하게 20번 틀리면 그 IP만 5분 동안 막히므로, 다른 곳에서 일부러 틀려서 계정 주인을 잠글 수는 없다. 백엔드는 Caddy·cloudflared 뒤(루프백)에 있으므로 `X-Forwarded-For`의 마지막 값을 접속 IP로 쓴다.
- 관리자 계정: `~/cloudmorph-server.env`에 `CLOUDMORPH_ADMIN_PASSWORD=<8자 이상>`을 넣고 백엔드를 재시작하면, 로그인 화면에서 아이디 `admin`과 이 비밀번호로 관리자 로그인을 할 수 있다(API 토큰 입력 없이). 비밀번호는 백엔드가 시작할 때 계정 파일에 반영되므로, 값을 바꾸면 기존 관리자 로그인이 모두 끝나고, 값을 지우면 관리자 계정도 사라진다. 관리자는 대시보드 헤더의 `관리자` 버튼(`#/admin`)에서 모든 사용자와 모든 앱을 본다. 회원의 비밀번호 재설정, 모든 기기에서 로그아웃, 계정 삭제와 아무 앱이나 중지할 수 있다. 계정을 삭제해도 그 사용자의 앱은 남고(관리자가 중지할 수 있음), 그 아이디는 앱 소유자로 계속 예약된다. `/admin/*` API는 관리자 토큰 외에는 403으로 거절한다.
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
