# 멀티 클라우드 로드 밸런싱과 격리 시연 (10/11)

한 앱의 복제본을 GCP·AWS·OCI 노드에 하나씩 띄우고, 라우터 하나가 세 회사의 인스턴스에 트래픽을 나눠 보내는 장면을 보여 준다. 같은 자리에서 앱별 DB 격리와 사용자 격리도 확인한다.

```
사용자 ──▶ 라우터 (GCP, Caddy)  <app>.<router-ip>.nip.io
              │ round robin, 연결이 안 되는 복제본은 15초 동안 제외하고 다른 복제본으로 재시도
              ├──▶ 복제본 @ gcp-node-1   (GCP 서울)
              ├──▶ 복제본 @ aws-node-1   (AWS 서울)
              └──▶ 복제본 @ oci-node-1   (OCI)
```

## 바뀐 점 (feat/multicloud-lb)

| 항목 | 이전 | 지금 |
|---|---|---|
| 복제본 위치 | 가장 한가한 노드 | **아직 없는 회사의 노드를 우선** (`select_node(spread_from=...)`). 한 회사가 장애여도 다른 회사가 응답 |
| 확장 복제본의 환경변수 | 전달하지 않음 (API 키, DATABASE_URL 누락) | 첫 복제본의 `docker inspect`에서 읽어 그대로 전달. CloudMorph는 사본을 따로 저장하지 않음 |
| 확장 복제본 준비 확인 | 30번 실패해도 라우터에 연결 | 응답하지 않으면 컨테이너를 지우고 **라우터에 넣지 않음** (`scale_out_failed`) |
| 상태 있는 앱(DB) 복제 | 자동 확장만 막고 수동 scale은 통과 | `scale_out` 안에서 막아 어느 경로로도 복제하지 않음 |
| 장애 복제본 | 매 요청이 죽은 복제본을 먼저 시도 | 복제본이 2개 이상이면 연결 실패 3번에 15초 동안 제외 (passive health check), 연결 제한 시간 2초 |
| Docker 네트워크 | 모든 앱과 DB가 `cloudmorph` 네트워크 하나를 공유 | **앱마다 `cm-<app>` 네트워크**. 앱과 그 사이드카만 연결되고, Docker는 네트워크 사이를 라우팅하지 않음 |
| 노드 VM 메타데이터 | 제어 서버만 차단 | `deploy/nodes/harden-node.sh`로 노드 컨테이너도 차단 (GCP·AWS·OCI 공통 169.254.169.254) |
| 서버 경로 소스 | 일반 사용자도 서버 디렉터리 지정 가능 | 관리자만 가능. 일반 사용자는 https Git URL과 `sample-apps/` 프리셋만 |
| `/fleet` 감시 이벤트 | 모든 사용자의 앱 이름이 보임 | 자기 앱의 이벤트만 |

## 준비

1. 노드 등록: 제어 서버의 `backend/nodes.json`에 노드마다 `provider`를 정확히 쓴다 (`gcp` / `aws` / `oci`). 분산 기준이 이 값이다.
2. 노드 보안: 노드마다 한 번 실행한다.
   ```bash
   deploy/nodes/harden-node.sh cloudmorph@<gcp-node-ip>
   deploy/nodes/harden-node.sh ubuntu@<aws-node-ip>
   deploy/nodes/harden-node.sh ubuntu@<oci-node-ip>
   ```
3. OCI 노드: Always Free `VM.Standard.E2.1.Micro`(x86, 1GB) 또는 A1(ARM이면 `"arch": "arm64"`). 보안 목록(Security List)에서 22번(제어 서버 IP만)과 8000-8100을 연다. Ubuntu 이미지는 호스트 iptables가 기본으로 막혀 있으므로, 접속이 안 되면 `sudo iptables -I INPUT -p tcp --dport 8000:8100 -j ACCEPT`를 추가한다. Docker를 설치하고 사용자를 docker 그룹에 넣는다.

## 시연 순서

```bash
cd backend
uv run python -m app.fleet status                          # 세 회사 노드가 모두 ok=True인지
uv run python -m app.deployer ../sample-apps/healthy node  # → http://healthy.<router-ip>.nip.io
uv run python -m app.fleet spread healthy                  # 회사마다 복제본 1개: gcp, aws, oci
uv run python -m app.fleet stress healthy --seconds 60 --concurrency 32
#   by cloud: {'gcp': 21034, 'aws': 20987, 'oci': 20911}   ← 세 회사로 고르게
```

장애 전환: stress가 도는 중에 한 회사의 복제본을 멈춘다.

```bash
ssh ubuntu@<aws-node-ip> docker stop <healthy-r2-...>
#   by cloud: {'gcp': ..., 'oci': ...}  aws는 15초 동안 빠지고, 오류는 전환 순간 몇 건뿐
ssh ubuntu@<aws-node-ip> docker start <healthy-r2-...>   # 15초 뒤 다시 aws로도 분산
```

브라우저에서는 응답 헤더 `X-Cloudmorph-Upstream`으로 어느 노드가 응답했는지 보인다. `/fleet` API의 복제본마다 `provider`가 있다.

## 격리 확인

DB 격리: DB가 있는 앱 두 개(`a`, `b`)를 같은 노드에 배포한 뒤, a의 컨테이너 안에서 b의 DB를 찾는다.

```bash
ssh <node> 'docker exec <a 컨테이너> getent hosts b-db || echo "b-db: 보이지 않음"'
ssh <node> 'docker network inspect cm-a --format "{{range .Containers}}{{.Name}} {{end}}"'   # a와 a-db만
```

비밀번호도 사이드카마다 무작위이므로(#61), 네트워크와 자격 증명 두 겹으로 막힌다.

사용자 격리: 계정 두 개로 로그인해서 서로의 앱이 `/projects`, `/fleet`에 보이지 않는지, 남의 앱 이름으로 scale·stop을 호출하면 404인지 확인한다. 소유권 테스트는 `tests/test_main_security.py`, `tests/test_multiuser_cd.py`에 있다.

## 한계 (질문 대비)

- 라우터는 GCP VM 한 대다. 라우터가 죽으면 전체가 멈춘다. 다음 단계는 DNS 뒤에 라우터 2대.
- DB가 있는 앱은 복제하지 않는다. 사이드카 DB는 첫 노드에만 있기 때문이다. 여러 회사에 복제하려면 관리형 DB(Cloud SQL 등)를 공유해야 한다.
- 노드끼리는 공인 IP로 통신한다(사설망 연결 없음). 앱 포트(8000-8100)는 인터넷에 열려 있으므로, 라우터를 거치지 않는 직접 접근도 가능하다.
- 기존 앱은 다시 배포해야 앱별 네트워크로 옮겨 간다. 예전 사이드카는 새 네트워크에도 연결되지만 공유 네트워크에서도 빠지지 않는다.
