# GitHub push → 자동 재배포 (CD)

## 여러 사용자 기준 설계 (10/10 추가)

서버 한 대를 여러 사람이 같이 쓴다는 전제다. 그래서 세 가지를 사용자 단위로 나눴다.

| 무엇 | 어떻게 |
|---|---|
| **누구인가** | API 토큰이 곧 사용자다 (`backend/app/accounts.py`). `CLOUDMORPH_API_TOKEN` = 관리자, `CLOUDMORPH_USERS="alice:<토큰>,bob:<토큰>"` 또는 `CLOUDMORPH_USERS_FILE`(JSON) = 일반 사용자. `GET /projects/me`로 확인한다 |
| **무엇을 볼 수 있나** | 프로젝트·배포 상태·SSE 로그·fleet 앱·스케일은 **소유자만** (관리자는 전부). 남의 것은 404로 보여서 이름도 드러나지 않는다. 노드 목록은 공용 인프라라 모두에게 보인다 |
| **앱 이름** | **먼저 배포한 사람의 것**이다. Cloud Run 서비스 이름은 GCP 프로젝트 전체에서 하나라서, 같은 이름으로 남의 서비스를 덮어쓰지 못하게 409로 막는다. 관리자도 남의 이름은 못 쓴다. 소유자가 없는 예전 프로젝트는 관리자 소유로 본다 |

### 프로젝트별 push 자동 배포 (권장)
```
대시보드에서 GitHub 저장소 배포 (이름 shop, 타깃 cloudrun+node)
  └─ 프로젝트 목록 → "push 자동 배포 연결"
       POST /projects/shop/cd {github_token?, branch?}
       ├─ 링크 생성: 링크 id(웹훅 URL에 들어감) + 이 프로젝트 전용 HMAC 비밀값
       ├─ github_token 있음 → 서버가 GitHub API로 웹훅 등록 (토큰은 이 한 번만 쓰고 저장하지 않음)
       └─ 없음            → URL + 비밀값을 한 번만 보여 줌 → 사용자가 GitHub 설정에 직접 등록

git push origin main
  └─ GitHub ─▶ POST /webhook/github/<링크 id>  (그 링크의 비밀값으로 서명)
       ─▶ 서명 확인 ─▶ 연결한 저장소가 맞나 ─▶ 연결한 브랜치(없으면 저장소 기본 브랜치)인가
       ─▶ 그 프로젝트의 이름·타깃·소유자 그대로 재배포 → 같은 Cloud Run 서비스에 새 리비전(블루그린)
```
- **UI 배포와 CD가 같은 앱이다.** 예전에는 push가 저장소 이름·전역 타깃으로 별개 서비스를 만들었다.
- **비밀값이 프로젝트마다 다르다.** 하나가 새어도 그 프로젝트 하나만 영향을 받고, 공용 비밀값을 사용자에게 나눠 줄 필요가 없다.
- 배포 소스는 링크한 저장소로 고정한다. 나중에 프로젝트 소스를 바꿔도 push가 엉뚱한 코드를 배포하지 않는다.
- 해제(`POST /projects/{name}/cd/unlink`)하면 링크 id가 사라져 그 즉시 push가 401이 된다. GitHub 토큰을 주면 GitHub 쪽 웹훅도 지운다.
- 비밀값은 `projects.json`(권한 600)에만 있고 `GET /projects`에는 나오지 않는다. 마지막 수신 시각·이벤트는 보인다.
- GitHub 토큰: fine-grained면 대상 저장소의 **Webhooks: Read and write**, classic이면 **admin:repo_hook**.
- 웹훅 URL은 `CLOUDMORPH_PUBLIC_URL`이 있으면 그것, 없으면 요청의 Host·X-Forwarded-Proto로 만든다.

### 공용 엔드포인트 `POST /webhook/github` (예전 방식, 운영자용)
`CLOUDMORPH_WEBHOOK_SECRET`로 서명한다. 이제는 push된 저장소로 배포한 **링크 없는 프로젝트 전부**를 각자의 이름·타깃·소유자로 재배포한다
(링크가 있는 프로젝트는 자기 엔드포인트로만 받아 두 번 배포되지 않는다). 그런 프로젝트가 없을 때만 예전처럼 저장소 이름·`CLOUDMORPH_CD_TARGETS`로
관리자 소유 앱을 만들고, 그 이름이 다른 사용자 것이면 배포하지 않는다.

## (예전 방식 설명)

### 흐름
```
개발자 git push ─▶ GitHub ─webhook(push, HMAC 서명)─▶ 터널 ─▶ 대시보드 프록시 ─▶ 백엔드 /webhook/github
   ─▶ 서명 검증 ─▶ 기본 브랜치인지 확인 ─▶ _launch(저장소 URL, 타깃, name=저장소이름, ref=브랜치)
   ─▶ 분석 → 배포(local + cloudrun) → 실패 시 자가치유 ─▶ 같은 이름의 서비스가 새 버전으로 갱신
```
핵심은 **안정적인 앱 이름**이다. `POST /deploy`는 지금까지 배포마다 새 서비스를 만들었다(`cloudmorph-<id>-<target>`).
CD는 저장소 이름을 앱 이름으로 쓰므로 푸시할 때마다 **같은 Cloud Run 서비스에 새 리비전**이 올라가고(블루그린),
Cloud SQL의 앱별 데이터베이스도 그대로 재사용되어 데이터가 남는다. `POST /deploy`에도 `name`을 주면 같은 동작을 한다.

### 설정
| 환경변수 | 뜻 | 기본 |
|---|---|---|
| `CLOUDMORPH_WEBHOOK_SECRET` | GitHub 웹훅 비밀값. 없으면 엔드포인트가 503으로 꺼짐 | (없음) |
| `CLOUDMORPH_CD_TARGETS` | 푸시 시 배포할 타깃 | `local,cloudrun` |
| `CLOUDMORPH_CD_BRANCHES` | 배포할 브랜치 목록 | 저장소의 기본 브랜치만 |

GitHub 쪽: 저장소 Settings → Webhooks → Payload URL `https://<공개 URL>/webhook/github`, Content type `application/json`,
Secret = 위 값, 이벤트 `push`. CLI로는:
```bash
gh api -X POST repos/<owner>/<repo>/hooks --input hook.json   # {"name":"web","active":true,"events":["push"],"config":{"url":...,"content_type":"json","secret":...}}
```
quick tunnel 주소는 매번 바뀌므로 **발표 직전에 등록**하거나 도메인을 붙여 고정 주소를 쓴다.

## 보안
- `X-Hub-Signature-256`을 HMAC-SHA256으로 검증(상수 시간 비교). 불일치 401, 비밀값 미설정 503
- `ping`과 push 외 이벤트는 200으로 무시. 태그 푸시·브랜치 삭제 무시. 기본 브랜치 외 푸시는 무시(설정으로 허용 가능)
- 저장소 URL은 main.py의 기존 HTTPS·호스트 허용 목록 검증을 그대로 거친다

## 실측 (10/10, Jeonsubb/cloudmorph-demo-guestbook)
| 푸시 | 감지 | 완료 | 결과 |
|---|---|---|---|
| v2 | 6초 | 61초 | local 터널 URL + Cloud Run URL. 분석 로그에 "postgres 필요, 인스턴스 1대 고정" |
| v3 | 수 초 | 79초 | **같은 Cloud Run 서비스**가 리비전 00002로 갱신, 이전에 쓴 방명록 글 유지 |

## 관측
- `GET /projects`: 앱 이름별 마지막 배포(트리거, 브랜치, 상태, URL, 이력). 대시보드가 표시할 수 있다
- 배포 이벤트 첫 줄에 `github push by <who>: <repo>@<branch> <sha> — <message>`가 들어간다

## 한계
- 동시 푸시가 겹치면 두 배포가 병렬로 돈다(같은 이름). 큐잉·취소는 미구현
- 상태는 메모리에만 있어 서버 재시작 시 `/projects`가 비워진다(`.fleet`, `.deployer` 상태 파일은 남음)
