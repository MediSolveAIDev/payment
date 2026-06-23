# 10. 설치·설정·배포

구독·결제 API 서버를 **로컬 개발**부터 **운영 배포**까지 정리한다. 운영은 **VM 1대**에 다음 셋을 올리는 구조다 — ① **호스트 nginx**(VM에 직접 설치, TLS 종단), ② **docker A — PostgreSQL**(`db_server`, DB 전용 컨테이너), ③ **docker B — app·redis**(`docker-compose.prod.yml`). 즉 DB도 같은 VM의 **별도 docker**로 띄우고, 앱은 그 DB에 접속한다.

> 함께 보기: [서비스 API](11-service-api.md)

---

## 1. 요구사항·구성 요소

### 1.1 요구사항

| 항목                    | 버전·비고                                                            |
| ----------------------- | -------------------------------------------------------------------- |
| VM(Ubuntu)              | 호스트 nginx + Docker를 올릴 단일 인스턴스                           |
| Docker / Docker Compose | docker A(PostgreSQL)·docker B(app·redis) 기동                        |
| 호스트 nginx            | `nginx` + `certbot`(Let's Encrypt). VM에 `apt`로 설치                |
| PostgreSQL              | 16 (db_server 컨테이너). asyncpg 드라이버로 접속                     |
| Redis                   | 7 (app 스택에 포함 — 세션·nonce·레이트리밋·킬스위치 캐시)            |
| Python / uv             | **로컬 개발**에서 앱을 직접 실행할 때만 필요(운영은 컨테이너가 담당) |

### 1.2 구성 요소 — VM 1대에 셋

```
                          ┌──────────────────── VM 1대 ────────────────────┐
 인터넷 ─443/80─▶ 호스트 nginx(TLS) ─127.0.0.1:8000─▶ [docker B] app ──┐    │
                          │                                    redis ◀─┘    │
                          │      [docker B] app ─5432(host.docker.internal)─▶ [docker A] PostgreSQL │
                          └─────────────────────────────────────────────────┘
```

| 구성요소                  | 설치 방식                                                         | 외부 노출                                     | 설정 절 |
| ------------------------- | ----------------------------------------------------------------- | --------------------------------------------- | ------- |
| **호스트 nginx**          | VM에 `apt`로 설치. TLS 종단·리버스 프록시, certbot 발급·자동 갱신 | **80 / 443** (유일하게 인터넷 공개)           | 5.4     |
| **docker A — PostgreSQL** | `db_server/run.sh`로 `postgres:16` 컨테이너(`payment-postgres`)   | 5432 (VM 내부/사설망만 — 인터넷 금지)         | 5.2     |
| **docker B — app·redis**  | `docker-compose.prod.yml`(app + redis)                            | app `127.0.0.1:8000`(루프백), redis 내부 6379 | 5.3     |

> 참고: 회사 클라우드는 Azure. 실제 VM·도메인·NSG는 `docs/cloud/PAY-VM-ONBOARDING.md` — stg `api-stg-pay.medisolveai.com`(`vm-pay-api-stg`) / prod `api-pay.medisolveai.com`(`vm-pay-api-prod`), NSG 인바운드 **22·80·443**만 개방(5432는 비공개).

> 왜 호스트 nginx인가: VM에 이미 nginx가 설치돼 있으면 컨테이너 nginx와 80/443이 충돌한다. 그래서 nginx는 호스트가 맡고, compose는 app·redis만 띄우며 app을 `127.0.0.1:8000`으로만 노출한다.

> 대안: DB를 자체 docker 대신 **관리형(Azure Database for PostgreSQL)**으로 쓸 수도 있다. 그 경우 5.2(docker A)를 건너뛰고 `.env.prod`의 `DATABASE_URL`만 관리형 엔드포인트로 두면 된다(`?ssl=require` 필요할 수 있음).

---

## 2. 로컬 개발

호스트에서 앱을 `uv run uvicorn`(핫리로드)으로 직접 실행하고, **Redis만 docker**로 띄운다. **DB는 별도 docker**로 띄워 그 엔드포인트로 연결한다(운영과 동일 원칙).

### 2.1 명령

```bash
uv sync                                   # 의존성 설치

# DB(별도 docker) — 간단 기동(개발용)
docker run -d --name payment-postgres \
  -e POSTGRES_USER=payment -e POSTGRES_PASSWORD='XXXXXXXX' -e POSTGRES_DB=payment \
  -p 5432:5432 postgres:16

docker compose up -d                      # 개발용 redis(payment-dev, 호스트 127.0.0.1:6380)

uv run alembic upgrade head               # 스키마 적용
uv run uvicorn app.main:app --reload --port 8000   # 앱(핫리로드)
```

> 참고: `docker-compose.yml`(개발 인프라)은 **Redis만** 띄우고 포트를 루프백(`127.0.0.1:6380:6379`)에만 바인딩한다.

### 2.2 `.env.dev` 핵심 값

`cp .env.example .env.dev` 후 값을 채운다. 로드 순서는 `.env` → `.env.<APP_ENV>`(뒤가 우선), `APP_ENV` 미지정 시 `dev`.

```env
ENVIRONMENT=dev
BASE_URL=http://localhost:8000
DATABASE_URL=postgresql+asyncpg://payment:XXXXXXXX@localhost:5432/payment
REDIS_URL=redis://localhost:6380/0
# AES-256-GCM 키:  python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"
ENCRYPTION_KEY=
```

> 팁: `ENCRYPTION_KEY`는 위 한 줄로 새로 생성. 분실·변경 시 기존 암호화 데이터(카드 빌링키 등)를 복호화할 수 없다.

> **토스 시크릿 키**: 2026-06-23부터 전역 `TOSS_SECRET_KEY` 환경변수가 제거됨. 서비스별 토스 시크릿은 앱 기동 후 어드민 콘솔 → 서비스 상세 → **Toss 시크릿 키** 카드에서 각 서비스마다 등록한다(AES 암호화 저장, 평문 미노출). 키 미등록 서비스에서 결제 시도 시 HTTP 422 (`TOSS_KEY_NOT_CONFIGURED`) 반환.

---

## 3. 환경변수

`.env.example` 기준 주요 변수. `.env`(공통)와 환경별(`.env.dev`/`.env.prod`)로 나뉜다.

| 변수                                               | 설명                                                                             | 예시                                                                 |
| -------------------------------------------------- | -------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `ENVIRONMENT` / `APP_ENV`                          | 실행 환경.`prod`면 docs 비공개·세션 쿠키 secure 등 보안 강화                     | `dev` / `prod`                                                       |
| `BASE_URL`                                         | 서버 공개 URL(이메일 링크 등).**운영은 https FQDN**                              | `https://api-stg-pay.medisolveai.com`                                |
| `DATABASE_URL`                                     | PostgreSQL 접속(반드시 `asyncpg`). 운영(같은 VM docker)은 `host.docker.internal` | `postgresql+asyncpg://payment:...@host.docker.internal:5432/payment` |
| `REDIS_URL`                                        | Redis 접속(운영은 compose가 `redis://redis:6379/0`으로 덮어씀)                   | `redis://localhost:6380/0`                                           |
| `ENCRYPTION_KEY`                                   | AES-256-GCM 키(base64 32바이트).**필수**                                         | (직접 생성)                                                          |
| ~~`TOSS_SECRET_KEY`~~                              | **제거됨** — 서비스별 키는 어드민 콘솔에서 등록(AES 암호화 저장)                | —                                                                    |
| `TRUST_PROXY` / `TRUST_PROXY_HOPS`                 | 프록시 뒤면 `true` + XFF hop 수(nginx 1단=1)                                     | `true` / `1`                                                         |
| `WEBHOOK_IP_CHECK_ENABLED`                         | 토스 발신 IP 외 웹훅 거부(운영 `true`)                                           | `true`                                                               |
| `SWAGGER_ID` / `SWAGGER_PW`                        | `/docs` HTTP Basic 계정. 비우면 docs 404                                         | `admin` / (강력값)                                                   |
| `SCHEDULER_ENABLED` / `SCHEDULER_INTERVAL_MINUTES` | 자동 갱신 배치 사용·주기                                                         | `true` / `5`                                                         |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`                 | DB 커넥션 풀(총 최대 = 합)                                                       | `10` / `20`                                                          |

> 중요: `ENCRYPTION_KEY`·DB 비밀번호 등 비밀값은 Git에 커밋하지 않는다. `.env.example`만 추적한다.
> `TOSS_SECRET_KEY`는 2026-06-23부로 제거됨 — `.env.example`에서도 삭제 대상.

> 참고: 운영에서 `REDIS_URL`·`TRUST_PROXY`·`TRUST_PROXY_HOPS`·`APP_ENV`는 compose `environment`가 고정 주입한다(`.env.prod`에 적어도 compose 값이 우선, `docker-compose.prod.yml`).

---

## 4. 테스트

테스트도 DB를 쓴다. `payment-postgres`에 `payment_test` 데이터베이스를 만들어 둔다(운영 DB와 별개). 기본 접속값은 `tests/conftest.py:18`, 다르면 `TEST_DATABASE_URL`로 덮어쓴다.

```bash
docker exec -it payment-postgres createdb -U payment payment_test   # 테스트 DB 1회 생성
uv run pytest                       # 전체
uv run pytest tests/integration/    # 통합
```

---

## 5. 운영 배포 — VM 1대에 셋 올리기

설치 순서: **공통 준비(5.1) → docker A: PostgreSQL(5.2) → docker B: app·redis(5.3) → 호스트 nginx·TLS(5.4) → 동작 확인(5.5)**. (Azure 실제 값은 `docs/cloud/PAY-VM-ONBOARDING.md`)

### 5.1 공통 준비 (Docker 설치 + 코드)

<ol class="steps">
<li><b>Docker 설치</b> — Engine + Compose 플러그인 설치 후 현재 사용자를 docker 그룹에(이후 재로그인).
<pre><code class="language-bash">curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER                  # 재로그인 필요
docker --version && docker compose version</code></pre></li>
<li><b>코드 가져오기</b> — 저장소를 클론한다(배치 경로 예: <code>/opt/pay</code>).
<pre><code class="language-bash">git clone <이-저장소-URL> /opt/pay
cd /opt/pay</code></pre></li>
</ol>

### 5.2 docker A — PostgreSQL (`db_server`)

DB 전용 컨테이너(`payment-postgres`)를 `db_server/run.sh`로 띄운다. 데이터 영속·헬스체크·재부팅 자동복구·멱등 실행을 제공한다.

<ol class="steps">
<li><b>설정 파일 작성</b> — <code>db_server/.env.example</code>를 <code>.env</code>로 복사하고 <b>비밀번호를 반드시 변경</b>한다(예시 기본값이면 스크립트가 거부).
<pre><code class="language-bash">cd /opt/pay/db_server
cp .env.example .env
# .env 편집: POSTGRES_PASSWORD=강한값, (선택) TZ, DATA_DIR 등</code></pre></li>
<li><b>기동</b> — <code>run.sh</code> 실행.
<pre><code class="language-bash">chmod +x run.sh
./run.sh
docker logs -f payment-postgres   # 'ready to accept connections' 확인</code></pre></li>
</ol>

`db_server/.env` 핵심 값:

```env
POSTGRES_CONTAINER=payment-postgres
POSTGRES_IMAGE=postgres:16-alpine
POSTGRES_USER=payment
POSTGRES_PASSWORD=__강한_비밀번호로_변경__
POSTGRES_DB=payment
# 보안: 0.0.0.0이면 방화벽/NSG에서 5432를 '앱서버(같은 VM)만' 허용. 인터넷 노출 금지.
HOST_BIND=0.0.0.0
HOST_PORT=5432
DATA_VOLUME=payment_pgdata    # 또는 DATA_DIR=전용디스크경로
TZ=Asia/Seoul
```

- **데이터 영속**: 기본은 docker 명명 볼륨(`payment_pgdata`). 전용 디스크를 쓰려면 `.env`의 `DATA_DIR` 지정.
- **빈 `payment` DB만 있으면 된다** — 테이블(스키마)은 app이 자동 생성한다(5.3의 마이그레이션).
- 운영 명령: `docker start/stop payment-postgres`, `docker exec -it payment-postgres psql -U payment -d payment`.

> 주의(보안): DB(5432)는 **인터넷에 노출 금지**. Azure NSG 인바운드에 5432가 없으니 외부에서 막히고, VM 내부에서만 app이 접근한다. 별도 DB 서버를 둘 땐 사설망·방화벽으로 앱서버 IP만 허용.

### 5.3 docker B — app·redis (`docker-compose.prod.yml`)

app(FastAPI) + redis 2개 컨테이너. app은 `127.0.0.1:8000`(호스트 루프백)에만 노출되고, **같은 VM의 docker A(PostgreSQL)** 에 `host.docker.internal:5432`로 접속한다(compose에 `extra_hosts: host.docker.internal:host-gateway` 설정됨).

<ol class="steps">
<li><b><code>.env.prod</code> 작성</b> — <code>.env.example</code> 참고. 필수값은 아래.
<pre><code class="language-bash">cd /opt/pay
cp .env.example .env.prod
python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"  # ENCRYPTION_KEY 생성</code></pre></li>
<li><b>기동(+ 자동 마이그레이션)</b> — 엔트리포인트가 <code>alembic upgrade head</code>로 docker A의 DB에 테이블을 만든 뒤 앱을 띄운다.
<pre><code class="language-bash">docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f app
curl -I http://127.0.0.1:8000/        # 200/3xx/405면 앱 도달(정상)</code></pre></li>
<li><b>최초 관리자(SYSTEM_ADMIN) 생성</b> — 앱 컨테이너에서 CLI로.
<pre><code class="language-bash">docker compose -f docker-compose.prod.yml exec app \
  python -m app.cli create-admin --email admin@medisolveai.com --password '강력한비밀번호!'</code></pre></li>
</ol>

`.env.prod` 최소 필수값:

```env
ENVIRONMENT=prod
BASE_URL=https://api-stg-pay.medisolveai.com        # prod: https://api-pay.medisolveai.com
# 같은 VM의 docker A(payment-postgres)에 접속 — 호스트 게이트웨이 경유
DATABASE_URL=postgresql+asyncpg://payment:__DB비밀번호__@host.docker.internal:5432/payment
ENCRYPTION_KEY=                                      # 위 명령으로 생성한 값
# TOSS_SECRET_KEY 는 전역 설정에서 제거됨 — 어드민에서 서비스별 등록 필요
SWAGGER_ID=admin
SWAGGER_PW=강력한값
WEBHOOK_IP_CHECK_ENABLED=true
```

> **토스 시크릿 키 등록 순서(운영 전환 시 필수)**:
> 1. `alembic upgrade head` (toss_secret_key_encrypted 컬럼 추가 마이그레이션 적용)
> 2. 어드민 콘솔 → 각 서비스 상세 → **Toss 시크릿 키** 카드에서 키 등록
> 3. `.env.prod`에서 `TOSS_SECRET_KEY` 항목 제거 (이미 없으면 생략)

> 주의: `DATABASE_URL`을 `localhost`/`127.0.0.1`로 두면 **app 컨테이너 자신**을 가리켜 접속 실패한다. 같은 VM의 DB docker에는 반드시 `host.docker.internal:5432`(또는 VM 사설 IP)를 쓴다.

### 5.4 호스트 nginx + TLS

VM에 nginx·certbot을 설치하고 **인증서를 먼저 발급한 뒤**, 레포의 `docker/nginx/host/payment-host.conf`를 `/etc/nginx/conf.d/payment.conf`로 복사한다. 이 파일은 **80→443 리다이렉트 + 443 TLS 프록시**를 모두 담은 완성본이라 인증서 경로를 참조한다(그래서 발급이 먼저).

**`payment.conf` 전체** — `server_name` 2곳과 `ssl_certificate` 2경로를 환경 FQDN으로 맞춘다(stg `api-stg-pay` / prod `api-pay`).

```nginx
# /etc/nginx/conf.d/payment.conf — payment_system (stg) 리버스 프록시
# 인터넷 80/443 ──▶ 호스트 nginx(TLS 종단) ──▶ docker app(127.0.0.1:8000)

# ── 80: ACME 챌린지 + 나머지는 전부 https로 리다이렉트 ──
server {
    listen 80;
    listen [::]:80;
    server_name api-stg-pay.medisolveai.com;

    location /.well-known/acme-challenge/ { root /var/www/html; }   # LE 갱신 경로
    location / { return 301 https://$host$request_uri; }            # 평문 전부 https로
}

# ── 443: 실제 서비스(HTTPS) ──
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name api-stg-pay.medisolveai.com;

    ssl_certificate     /etc/letsencrypt/live/api-stg-pay.medisolveai.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api-stg-pay.medisolveai.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1h;

    client_max_body_size 16m;                            # 엑셀 업로드/다운로드 등

    # 보안 헤더(앱도 일부 부착하지만 프록시단에서 한 번 더 보강)
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://127.0.0.1:8000;                # docker B의 app(루프백 publish)
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        # 실제 클라이언트 IP를 XFF 맨 오른쪽에 append → app TRUST_PROXY_HOPS=1 과 정합
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection        "";
        proxy_read_timeout 90s;                          # 토스 자동결제 승인(최대 65s) 대비
    }
}
```

핵심: **80 블록의 `return 301`**이 평문을 전부 https로 보낸다(이게 없으면 브라우저 "Not secure"). 443 블록은 인증서로 TLS를 종단하고 `proxy_pass`로 app(루프백 8000)에 전달하며, `X-Forwarded-For`/`X-Forwarded-Proto`로 app이 진짜 클라이언트 IP·https를 인식한다(`TRUST_PROXY_HOPS=1` 정합). HSTS로 이후 브라우저가 https를 강제한다.

> **8001(sample_service)은 nginx로 프록시하지 않는다.** sample_service 컨테이너가 호스트 `0.0.0.0:8001`을 직접 publish 하므로, nginx에 `listen 8001` server 블록을 두면 같은 포트를 두고 **bind 충돌**(`98: Address already in use`)이 나서 nginx가 기동 실패한다. 외부에서 샘플에 접근하려면 nginx를 거치지 않고 **방화벽/보안그룹에서 8001 인바운드만 열면** `http://<도메인>:8001`로 컨테이너에 직결된다(평문 http). 자세한 건 `sample_service/README.md`의 "외부에서 접속하기" 참고.

설치·발급·적용 — 위 파일은 인증서 경로를 참조하므로 **certbot 발급을 먼저** 해야 `nginx -t`가 통과한다.

```bash
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx

# 1) 인증서 먼저 발급(DNS A레코드가 VM 공인 IP, NSG 80 열림 전제)
sudo certbot certonly --nginx -d api-stg-pay.medisolveai.com   # prod: -d api-pay.medisolveai.com

# 2) payment.conf 배치(server_name·인증서 경로를 환경에 맞게 수정)
sudo cp /opt/pay/docker/nginx/host/payment-host.conf /etc/nginx/conf.d/payment.conf
sudo rm -f /etc/nginx/sites-enabled/default                   # 기본 사이트(80 default_server) 충돌 방지

# 3) 검사 후 적용
sudo nginx -t && sudo systemctl reload nginx
```

> 대안: 80 블록만 둔 상태에서 `sudo certbot --nginx -d <도메인>`을 실행하면 certbot이 443 블록을 자동 생성한다(위 1·2단계를 한 번에). 결과 파일은 위와 동일하다.

> 참고: 인증서는 `/etc/letsencrypt/`에서 certbot이 관리하고 **systemd 타이머로 자동 갱신**한다. 레포의 `docker/nginx/`(컨테이너 nginx용 conf·certs)는 이 방식에선 쓰지 않는다.

> 방화벽: Azure는 NSG 인바운드 22·80·443이 이미 열려 있다(온보딩). VM 자체 ufw를 쓸 때만 `sudo ufw allow 80,443/tcp`. **5432는 열지 않는다.**

### 5.5 동작 확인

```bash
docker ps                                                  # payment-postgres + app + redis 모두 Up/healthy
curl -I http://127.0.0.1:8000/                             # app 도달
curl -sI http://api-stg-pay.medisolveai.com/  | head -n1   # 301(https 리다이렉트)
curl -sI https://api-stg-pay.medisolveai.com/ | head -n1   # 200/3xx/405(앱 도달)
```

브라우저에서 `https://<FQDN>/admin/login` → 자물쇠(보안) + 로그인 화면. 만든 관리자 계정으로 로그인한다.

### 5.6 운영(로그·재배포·종료)

```bash
# app·redis
docker compose -f docker-compose.prod.yml logs -f app
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml down            # 중지(redis 볼륨 유지)
git pull && docker compose -f docker-compose.prod.yml up -d --build   # 재배포(새 마이그레이션 자동 적용)

# DB(docker A)
docker start payment-postgres / docker stop payment-postgres
```

### 5.7 문제해결 (배포 시 자주 겪는 것)

| 증상                                                                | 원인·해결                                                                                                                                                                          |
| ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| app 로그 `Connect call failed ('127.0.0.1', 5432)` / DB 접속 실패   | `DATABASE_URL`이 `localhost`로 됨 → **`host.docker.internal:5432`**로 변경. docker A(`payment-postgres`)가 떠 있는지 `docker ps`로 확인.                                           |
| nginx `[emerg] cannot load certificate ... fullchain.pem`           | 인증서 미발급.`sudo certbot --nginx -d <FQDN>`로 발급.                                                                                                                             |
| 브라우저**"Not secure"**(인증서는 valid인데)                        | http로 접속 + 리다이렉트 미적용. 대개**`reload` 누락**. `sudo systemctl reload nginx` 후 `curl -sI http://<FQDN>/`가 **301**인지 확인, 브라우저는 https로 **새 탭·강력 새로고침**. |
| `nginx -t` **conflicting server name** / http 200(리다이렉트 안 됨) | 기본 사이트 충돌.`sudo rm -f /etc/nginx/sites-enabled/default` 후 reload.                                                                                                          |
| **502 Bad Gateway**                                                 | app 컨테이너 미기동.`docker compose -f docker-compose.prod.yml ps`, `curl -I http://127.0.0.1:8000/` 확인.                                                                         |
| 로그인이 안 됨(쿠키 안 먹음)                                        | prod 세션 쿠키는 `Secure`라 **https로만** 동작. http면 위 리다이렉트부터 해결.                                                                                                     |
| 클라이언트 IP가 `127.0.0.1`로 보임                                  | nginx에 `X-Forwarded-*` 헤더 누락. `payment-host.conf`의 proxy 헤더 라인 확인(app은 `TRUST_PROXY_HOPS=1`).                                                                         |

---

## 6. 운영 주의사항

- **컴포즈 프로젝트명 분리**: 운영 app 스택은 `name: payment_system`(app·redis 2개; nginx는 호스트), 개발 인프라는 `name: payment-dev`(redis만). DB(docker A)는 compose가 아니라 `db_server/run.sh`로 독립 기동한다.
- **DB는 별도 docker(docker A)**: app compose에 postgres가 없다. app은 `host.docker.internal:5432`로 docker A에 접속한다. 같은 VM이지만 컨테이너가 분리돼 있어 DB를 따로 재시작·백업·이전하기 쉽다.
- **비밀값 관리**: `ENCRYPTION_KEY`(운영 전용 새 값)·`SWAGGER_PW`·`POSTGRES_PASSWORD`는 각 `.env`에 두고 Git 커밋 금지. `TOSS_SECRET_KEY`는 제거됨 — 서비스별 토스 키는 어드민 콘솔에서 등록(DB에 AES 암호화 저장).
- **마이그레이션**: 엔트리포인트가 매 기동 시 `alembic upgrade head`. 앱을 여러 대로 늘리면 한 대만 기본값, 나머지는 `RUN_MIGRATIONS=0`(`docker/entrypoint.sh`).
- **클라이언트 IP**: 호스트 nginx가 `X-Forwarded-For`/`X-Forwarded-Proto`를 세팅, 앱은 `TRUST_PROXY=true`·`TRUST_PROXY_HOPS=1`로 읽는다(compose 고정). 앞단에 LB가 더 있으면 `2`.
- **인증서 갱신**: 호스트 certbot이 **systemd 타이머로 자동 갱신**. 점검 `sudo certbot renew --dry-run`, 반영 `sudo systemctl reload nginx`.
- **세션 보안(추가 키 없음)**: 어드민 세션은 별도 쿠키 서명키 없이 **Redis의 무작위 토큰**(`secrets.token_urlsafe(32)`, `app/services/auth.py`)으로 동작. `SECRET_KEY` 류 환경변수는 없으며, redis 비공개가 곧 세션 보호다.

---

## 7. 백업·복구·롤백

DB는 docker A(`payment-postgres`)에 있다. 백업은 같은 VM에서 컨테이너의 `pg_dump`를 그대로 쓴다. (전용 백업 도구는 `db_backup_sw/` 참고)

### 7.1 DB 백업 (pg_dump)

```bash
# 같은 VM에서 — 배포/마이그레이션 직전 권장
docker exec payment-postgres pg_dump -U payment -Fc payment > payment_$(date +%Y%m%d-%H%M).dump
```

- `-Fc`(커스텀 포맷)은 `pg_restore`로 부분/선택 복구가 가능하다.

### 7.2 DB 복구 (pg_restore)

```bash
# 주의: 운영 DB에 덮어쓰기 전, 반드시 현재 상태를 먼저 백업한다.
cat payment_YYYYMMDD-HHMM.dump | docker exec -i payment-postgres \
  pg_restore -U payment --clean --if-exists -d payment
```

### 7.3 마이그레이션 롤백 (alembic downgrade)

엔트리포인트는 기동 시 `alembic upgrade head`만 한다. 직전 배포의 스키마 변경을 되돌리려면 app 컨테이너에서 직접 downgrade한다.

```bash
docker compose -f docker-compose.prod.yml exec app alembic current      # 현재 리비전
docker compose -f docker-compose.prod.yml exec app alembic downgrade -1  # 한 단계 롤백
```

- **데이터 손실 주의**: 컬럼/테이블을 지우는 downgrade는 데이터도 지운다. 반드시 7.1 백업을 먼저 뜨고, 코드 롤백(이전 이미지 재배포)으로 해결 가능한지 먼저 검토한다.
- **스키마-이미지 정합**: 새 이미지는 자동으로 `upgrade head`까지 올린다. 이전 이미지로 되돌릴 땐 그 이미지가 기대하는 리비전까지 **먼저 downgrade**해야 정합이 맞는다.

### 7.4 Redis 데이터

redis는 세션·캐시다. 명명 볼륨(`redis-data`)에 스냅샷이 남지만 유실돼도 **재로그인** 수준의 영향이며 결제·구독 데이터(DB)와 무관하다. 별도 백업 대상이 아니다.

> 함께 보기: [서비스 API](11-service-api.md)
