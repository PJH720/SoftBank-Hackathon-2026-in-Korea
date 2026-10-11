# 노드 풀과 자동 확장 (target `node`, `app/nodes.py`, `app/fleet.py`) — 전동훈, 10/9

## 한 줄 요약
어느 클라우드의 VM이든 **SSH + Docker만 되면 노드**다. deployer는 가장 한가한 노드를 골라 컨테이너를 올리고,
라우터(Caddy)가 앱마다 고정 호스트 이름을 주며, 감시 루프가 복제본 CPU를 재다가 뜨거우면 **다른 노드(다른 회사여도 됨)** 에 복제한다.

```
사용자 ──▶ 라우터 VM (Caddy :80)  ──round robin──▶  앱 복제본 @ gcp-node-1
            <app>.<router-ip>.nip.io                    앱 복제본 @ oci-node-1   ← 자동 확장으로 추가됨
                                                        ...
노트북(백엔드) ──SSH──▶ 노드들: probe(부하) / docker load / docker run / docker exec cat cpu.stat
```

## 구성 요소
| 파일 | 역할 |
|---|---|
| `backend/nodes.json` (gitignored, 예시는 `nodes.example.json`) | 노드·라우터 목록: host, user, key, arch, 포트 범위 |
| `app/nodes.py` | SSH 실행기, 노드 부하 측정(`/proc/loadavg`, 메모리, 컨테이너 수 → score), 최소 부하 노드 선택, 이미지 전송(`docker save \| ssh docker load`), 원격 실행·상태·로그·CPU 누적값 |
| `app/deployer.py::deploy_node` | 노드 선택 → 노드 아키텍처로 빌드 → 전송 → 실행 → 헬스체크 → 라우터 등록 → 공개 호스트 이름 반환 |
| `app/fleet.py` | 라우터(Caddy admin API) 설정 생성·반영, 앱/복제본 상태(`.fleet/fleet.json`), 감시 루프(scale-out/in), 수동 scale, 부하 생성기 |
| `app/main.py` | `GET /fleet` (노드 부하·앱·복제본·최근 이벤트), `POST /fleet/{app}/scale/{n}`, `CLOUDMORPH_FLEET_WATCH=1`이면 감시 스레드 자동 시작 |

## 실측 (10/9, GCP e2-small 노드 2대 + e2-micro 라우터)
- 노드 배포: 22초 (이미지 전송 15초가 대부분). 공개 주소 `http://healthy.34.50.17.157.nip.io`, 응답 헤더 `X-Cloudmorph-Upstream`에 처리 노드 표시.
- 자동 확장: keep-alive 32 동시 요청(≈1,100 rps) → 복제본 CPU 69% 감지 → **약 20초 만에 node-2에 복제** → 트래픽 40k:29k로 분산, p95 202ms, 확장 순간 오류 31건(0.04%).
- 풀 소진 시 `scale_out_blocked: no eligible node` 이벤트. 노드를 더 등록하면 그쪽으로 간다.

## 겪은 함정 (설계 판단의 근거)
1. **COS 호스트 방화벽**: Container-Optimized OS는 INPUT 기본 DROP. `--network host`로 띄운 Caddy는 밖에서 안 보임. Docker 포트 매핑(`-p`)은 다른 체인을 타므로 포트 매핑 방식으로 변경.
2. **Caddy `--config`와 `--resume` 동시 사용 금지**: resume이 이기고 config가 무시됨. 이전 볼륨의 설정(관리 API가 컨테이너 localhost에만 바인딩)이 복원되어 reload가 "connection reset".
3. **수동 헬스체크 제거**: 업스트림이 하나뿐일 때 과부하 타임아웃을 "다운"으로 판정해 10초간 전부 거부. 확장 전 단계에서 치명적.
4. **CPU 측정**: `docker stats`는 1초 순간값이라 0~72%를 오감. cgroup `cpu.stat usage_usec`를 두 번 읽어 구간 평균으로 계산. 노드 1분 부하(/core)도 보조 신호.
5. **부하 생성기 자체 결함**: 요청마다 새 TCP 연결 → 맥 임시 포트 고갈 → 주기적 0 rps. 서버 장애처럼 보였음. keep-alive로 수정하니 1,100 rps.
6. **노드 간 이미지 복사**: 노드에는 SSH 키를 두지 않는다. 확장 시 이미지는 노트북이 중계(`ssh A docker save | ssh B docker load`), 노트북에 이미지가 남아 있으면 바로 전송.

## 노드 추가 (오라클 / AWS)
1. VM 생성(리눅스, Docker 설치, 사용자를 docker 그룹에), 보안 그룹에서 22와 8000-8100 개방.
2. `nodes.json`의 `nodes`에 항목 추가. `arch`는 `uname -m`(ARM이면 `arm64`), `key`는 접속 키 경로.
3. `uv run python -m app.fleet status`로 probe가 되는지 확인. 끝. 코드 변경 없음.

## 데모 순서
```bash
uv run python -m app.fleet init-router                     # 라우터 1회
uv run python -m app.deployer ../sample-apps/healthy node  # → http://healthy.<ip>.nip.io
uv run python -m app.fleet watch --interval 5              # 터미널 1
uv run python -m app.fleet stress healthy --seconds 120 --concurrency 32   # 터미널 2 → 20초쯤 뒤 scale_out
uv run python -m app.fleet status
```

## 한계와 다음 단계
- 상태(DB)가 있는 앱은 복제하면 안 된다 → analyzer의 "상태 있음" 신호를 받아 `max_replicas=1`로 고정 (미구현).
- scale-in은 최신 복제본이 2분 이상 살아 있고 CPU < 10%일 때만. 보수적.
- 라우터가 단일 장애점. 도메인이 생기면 Cloudflare DNS 뒤에 두고, 라우터를 2대로.
- 노드 장애 감지·재배치는 미구현(설계: probe 실패 N회 → 해당 노드 복제본을 다른 노드로).

---

## 데이터베이스 프로비저닝 (10/10 추가) — "SQLite → 관리형 Postgres"

앱이 `DATABASE_URL`을 읽거나 Postgres 드라이버를 쓰면(`detect_database`) deployer가 타깃에 맞는 DB를 만들어 주입한다.
SQLite 경로를 하드코딩한 앱은 대상이 아니다(읽지도 않는 URL을 넣는 것은 눈속임이므로). 그런 앱은 analyzer/healer가 코드를 고칠 영역.

엔진은 드라이버·URL 스킴·ORM provider 설정으로 정한다(`scan_database`, 판단 근거는 배포 로그의 `database` 단계에 남는다).

- Postgres 근거(`psycopg`, `asyncpg`, npm `pg`, `postgresql://`, prisma `provider = "postgresql"` …)가 있으면 Postgres, 없고 MySQL 근거(`pymysql`, `mysql2`, `mysql://` …)가 있으면 MySQL이다.
- ORM만 있는 경우(`sqlalchemy`, `prisma`)는 근거가 아니다. 엔진을 알 수 없기 때문이다.
- `DATABASE_URL`만 읽는 앱은 Postgres를 받는다. 다만 SQLite로 fallback하거나(`sqlite:///`), 의존성 파일(`requirements.txt`, `package.json` …)에 드라이버가 없으면 DB를 만들지 않는다. 드라이버 없이 URL만 넣으면 앱이 시작하다 죽는다.
- lock 파일과 `node_modules`, `.venv`는 보지 않는다.

Cloud SQL의 DB·계정 이름은 앱 이름과 소스(`main._source_identity`)의 해시로 만든다(`db_identifier`: `<앱>_<해시 8자>`, 최대 32자).
같은 이름으로 다른 저장소를 배포해도 서로의 데이터를 받지 않는다(env_store와 같은 규칙). 서비스에는 `cloudmorph-owner=<해시>` 라벨을 남긴다.
이 규칙 이전에 만든 서비스(라벨 없음)나 같은 소유자의 서비스가 예전 이름(`<앱>`)의 DB로 서비스 중이면 그 DB를 계속 쓴다. 업그레이드해도 데이터가 끊기지 않는다.
재배포 때는 서비스 중인 리비전의 비밀번호를 그대로 쓴다(PR #25). 검증 전에 비밀번호를 바꾸면 기존 리비전과 롤백 대상이 DB에 접속하지 못한다.

| 타깃 | DB | 방법 | 데이터 보존 |
|---|---|---|---|
| local | 사이드카: Postgres `postgres:16-alpine` → `<app>-db`, MySQL `mysql:8.4` → `<app>-mysql`, Redis `redis:7-alpine` → `<app>-redis`, MongoDB `mongo:7` → `<app>-mongo`, S3 SeaweedFS `chrislusf/seaweedfs` → `<app>-s3` | 앱 전용 docker network, 볼륨 `<app>-dbdata` / `<app>-mysql-data` / `<app>-redisdata` / `<app>-mongodata` / `<app>-s3data` | 재배포(healer 재시도) 후에도 유지. `cleanup`에서만 삭제 |
| node | 같은 방식을 노드에서 SSH로 (`nodes.sidecar_script` 하나를 local·node가 같이 씀) | 노드마다 사이드카 | 앱이 `stateful`로 표시되어 **자동 확장 제외** |
| cloudrun | Cloud SQL 인스턴스 `cloudmorph-pg`(공유), Postgres만 | `gcloud sql databases/users create` → `--add-cloudsql-instances` + `DATABASE_URL=postgresql://u:p@/db?host=/cloudsql/<conn>` (비밀번호는 Secret Manager `cloudmorph-db-<db>`에도 보관) | 관리형 |

- 사이드카 비밀번호는 사이드카마다 무작위 값이고, 컨테이너 자신의 env에만 있다. 재배포는 `docker inspect`로 그 값을 읽어 쓴다(예전 `app` 비밀번호 사이드카도 그대로 동작). 같은 `cloudmorph` 네트워크의 다른 앱은 비밀번호 없이 접속하지 못한다.
- 컨테이너가 지워지고 볼륨만 남은 Postgres는 새 컨테이너가 컨테이너 안 소켓(trust)으로 새 비밀번호를 설정한다. MySQL은 그렇게 할 수 없어서 `user_action_required`로 멈춘다(볼륨을 지우거나 컨테이너를 되살린다).
- **역할별로 채운다**(#68에서 가져옴): analyzer가 찾은 저장소의 접속 설정(`EnvRequirement.resource/role/template`) 중 사용자가 모두 비워 둔 것을 deployer가 만들고, 앱이 읽는 이름에 역할대로 넣는다. 예를 들어 `DB_HOST`·`DB_PASSWORD` 같은 분리 변수, `jdbc:mysql://…`·`postgresql+asyncpg://…` 같은 형식이 있다. Cloud SQL은 소켓 디렉터리를 host로 넣는다(JDBC는 소켓을 받지 못해 비워 둔다). S3는 `AWS_ENDPOINT_URL_S3`도 넣어, 엔드포인트 설정이 없는 SDK도 사이드카로 간다. 분석 없이 부를 때(CLI)만 코드 스캔으로 `DATABASE_URL`·`REDIS_URL`을 추측한다.
- MySQL은 예전 드라이버(node `mysql`, cryptography 없는 pymysql)를 위해 `mysql_native_password`로 띄운다. MongoDB는 root 계정 `app`(`authSource=admin`)을 쓴다. S3는 access key `app`, secret key는 사이드카 비밀번호이고, 버킷 `app`을 만들며, 키 없는 요청은 거부한다.
- 같은 이름의 컨테이너가 다른 이미지로 떠 있으면(예: 예전 `<app>-db`가 MySQL) 손대지 않고 `user_action_required`로 멈춘다.
- cloudrun에서 MySQL·MongoDB는 `user_action_required`로 멈추고(서버의 값을 주거나 local/node로 배포), Redis·S3·`other`는 경고만 남긴다. `other`(Kafka 등)는 어디서도 만들지 않는다.
- 대시보드에서 떼어 낸 종류(`mode=none`, 또는 사용자 주소를 잃은 `url`)는 `skip_stores`로 넘어가서 만들지 않는다.
- Cloud SQL 비밀번호는 Secret Manager에도 보관한다(CloudMorph만 읽는다). 다른 머신에서 배포하거나 리비전 env에서 읽을 수 없어도 비밀번호를 바꾸지 않는다(`DEPLOYER_DB_SECRET=0`이면 보관하지 않음).
- 앱에는 기본적으로 env 변수로 넣는다. `DEPLOYER_DB_SECRET_MOUNT=1`이면 secret을 `--set-secrets`로 마운트하고 런타임 서비스 계정에 그 secret의 `roles/secretmanager.secretAccessor`를 준다. 앱마다 서비스 계정이 따로 있을 때만 켠다: 모든 앱이 `DEPLOYER_APP_SERVICE_ACCOUNT` 하나를 같이 쓰면 앱끼리 서로의 DB secret을 읽을 수 있다. 서비스 계정은 `DEPLOYER_APP_SERVICE_ACCOUNT`를 따르고(마이그레이션 job도 같음), 없으면 서비스의 계정, 그다음 기본 compute 계정이다.
- 대시보드에서 Redis·MySQL을 떼어 내면(`mode=none`, 또는 저장한 주소를 잃은 `url`) deployer가 사이드카를 스스로 붙이지 않는다(`cache=False` / `database=None`).
- 인스턴스가 없으면 기본은 `user_action_required`로 멈춘다. `CLOUDSQL_AUTOCREATE=1`이면 `POSTGRES_16`, `ENTERPRISE`, `db-f1-micro`(`CLOUDSQL_TIER`)로 만든다(약 10분, 있는 동안 과금).
- 마이그레이션: DB가 붙은 앱에 `alembic.ini`, Django `manage.py`, `prisma/migrations`가 있으면 새 이미지로 한 번 실행한 뒤 앱을 띄운다(`DEPLOYER_MIGRATE=0`이면 끔). local/node는 `docker run --rm --entrypoint <도구>`, cloudrun은 Cloud Run job `<서비스>-migrate`(`--execute-now --wait`, 서비스와 같은 DB·서비스 계정). 실패하면 새 버전을 띄우지 않고(cloudrun은 트래픽도 그대로) 출력이 `runtime_error`로 남는다.
- 삭제: `POST /projects/{name}/stop?drop_database=true`(또는 `teardown(..., drop_database=True)`, `cleanup`)는 앱을 내린 뒤 DB를 덤프하고 지운다. 사이드카는 `backend/.cloudmorph/backups/<컨테이너>-<시각>.sql`(0600, node는 이 머신으로 가져옴)에 덤프한 뒤 컨테이너와 볼륨을 지운다. Cloud SQL은 `CLOUDSQL_BACKUP_BUCKET`으로 `gcloud sql export`한 뒤 DB·계정·secret을 지운다. 버킷이 없거나 덤프가 실패하면 아무것도 지우지 않는다. MongoDB는 `mongodump --archive`(`.archive`), S3는 데이터 폴더 tar(`.tar`)로 백업한다. Redis는 캐시로 보고 덤프 없이 지운다.
- env 값은 명령줄에 쓰지 않는다: local은 `docker run -e NAME`(값은 docker 프로세스 env), cloudrun/function은 0600 임시 파일 `--env-vars-file`. env가 없으면 `--clear-env-vars`로 이전 리비전의 값을 지운다.

실측(sample-apps/guestbook): local 9.7초, cloudrun 44초(앱별 DB·계정 생성 포함), node 20초. 세 곳 모두 글 쓰기 → 읽기 확인, local은 재배포 후 데이터 유지 확인.
Cloud SQL 생성 시 `--edition=ENTERPRISE`를 줘야 `db-f1-micro`를 쓸 수 있고, Cloud Run 서비스 계정에 `roles/cloudsql.client`가 필요하다.
