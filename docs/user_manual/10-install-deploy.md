# 10. 설치·설정·배포

구독·결제 API 서버를 **로컬 개발 환경에서 띄우는 절차**부터 **docker로 운영 배포하는 절차**까지 정리한다. 핵심 원칙은 하나다 — **PostgreSQL(DB)은 개발·배포 둘 다 별도 docker(외부)로 따로 구성**하고, compose에는 포함하지 않는다. 운영 compose(`docker-compose.prod.yml`)는 `nginx`·`app`·`redis` 3개만 띄운다.

> 함께 보기: [서비스 API](11-service-api.md)

---

## 1. 요구사항·구성 요소

### 1.1 요구사항

| 항목 | 버전·비고 |
|------|-----------|
| Python | 3.13 이상 (`pyproject.toml:6` — `requires-python = ">=3.13"`) |
| uv | 패키지·가상환경 관리(로컬 개발에서 앱 직접 실행 시 사용) |
| Docker / Docker Compose | redis·운영 스택 기동, 별도 PostgreSQL 컨테이너 구성 |
| PostgreSQL | 16 권장. asyncpg 드라이버로 접속(별도 docker 또는 외부 관리형 DB) |
| Redis | 7 (세션·nonce·레이트리밋·킬스위치 캐시) |

### 1.2 구성 요소

운영 스택은 클라우드 단일 인스턴스에 세 컨테이너를 띄우고, **DB는 외부**에 둔다.

```
인터넷 ──443/80──▶ nginx(TLS 종단) ──8000──▶ app(payment_system) ──▶ 외부 PostgreSQL
                                                  └──▶ redis(세션·캐시)
```

| 컨테이너 | 이미지·역할 | 외부 노출 |
|----------|-------------|-----------|
| `nginx` | `nginx:1.27-alpine` — HTTPS 입구(TLS 종단)·리버스 프록시 | 80 / 443 (유일하게 공개) |
| `app` | `Dockerfile` 빌드(FastAPI/uvicorn). 상태 없음(stateless) | 없음(내부 8000, nginx만 접근) |
| `redis` | `redis:7-alpine` — 세션·nonce·레이트리밋 캐시 | 없음(내부 6379) |
| **PostgreSQL** | **컨테이너에 없음** — 별도 docker 또는 외부 관리형 DB(예: Azure Database for PostgreSQL)를 `DATABASE_URL`로 연결 | 외부 |

> 참고: 회사 클라우드는 MS Azure 기준이며, 운영 DB는 관리형(Azure Database for PostgreSQL)을 권장한다. 돈 기록(DB)을 앱과 분리해 백업·보안·확장을 단순화한다(`docker/README.md`).

---

## 2. 로컬 개발

호스트에서 앱을 `uv run uvicorn`으로(핫리로드) 직접 실행하고, **Redis만 docker로** 띄운다. **DB는 별도 docker(외부)** 로 따로 구성해 앱이 그 엔드포인트로 연결한다.

### 2.1 절차

<ol class="steps">
<li>저장소를 받고 의존성을 설치한다.</li>
<li><b>외부 PostgreSQL</b>를 별도 docker로 띄운다(아래 예: <code>payment-postgres</code>, 호스트 5432).</li>
<li>개발용 <b>Redis</b>를 <code>docker compose up -d</code>로 띄운다(<code>payment-dev</code> 프로젝트, 호스트 6380).</li>
<li><code>.env</code>(또는 <code>.env.dev</code>)에 접속·비밀값을 채운다.</li>
<li>마이그레이션을 적용한 뒤 앱을 실행한다.</li>
</ol>

### 2.2 명령

의존성 설치:

```bash
uv sync
```

외부 PostgreSQL을 별도 docker로 기동(예시 — DB는 compose에 없으므로 직접 띄운다):

```bash
docker run -d --name payment-postgres \
  -e POSTGRES_USER=payment \
  -e POSTGRES_PASSWORD='Payment!2002' \
  -e POSTGRES_DB=payment \
  -p 5432:5432 postgres:16
```

개발용 Redis 기동(`docker-compose.yml`, 프로젝트명 `payment-dev`, 호스트 `127.0.0.1:6380`만 노출):

```bash
docker compose up -d
```

마이그레이션 적용 후 앱 실행(핫리로드):

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

> 참고: `docker-compose.yml`은 **Redis만** 띄운다. 포트는 루프백(`127.0.0.1:6380:6379`)에만 바인딩해 외부 노출을 막는다(`docker-compose.yml`).

### 2.3 `.env` 핵심 값

`cp .env.example .env.dev` 후 값을 채운다. 로드 순서는 `.env` → `.env.<APP_ENV>`(뒤가 우선)이며, `APP_ENV` 미지정 시 `dev`로 동작한다(`.env.example`).

```env
ENVIRONMENT=dev
BASE_URL=http://localhost:8000
# 별도 docker로 띄운 외부 Postgres(asyncpg 드라이버) — 호스트 5432
DATABASE_URL=postgresql+asyncpg://payment:Payment!2002@localhost:5432/payment
# 개발용 redis(docker-compose.yml) — 호스트 6380
REDIS_URL=redis://localhost:6380/0
# AES-256-GCM 키(base64 32바이트)
#   python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"
ENCRYPTION_KEY=
# 토스 시크릿 키: dev=test_sk_*
TOSS_SECRET_KEY=test_sk_xxxx
```

> 팁: `ENCRYPTION_KEY`는 위 한 줄로 새로 생성한다. 분실·변경 시 기존 암호화 데이터(카드 빌링키 등)를 복호화할 수 없다.

---

## 3. 환경변수

`.env.example` 기준 주요 변수. `.env`(공통)에 두는 값과 환경별(`.env.dev`/`.env.prod`)로 덮어쓰는 값이 나뉜다.

| 변수 | 설명 | 예시·기본 |
|------|------|-----------|
| `ENVIRONMENT` / `APP_ENV` | 실행 환경. `prod`면 docs 비공개·세션 쿠키 secure 등 보안 강화 | `dev` / `prod` |
| `BASE_URL` | 서버 공개 URL(이메일 링크 등) | `http://localhost:8000` / `https://도메인` |
| `DATABASE_URL` | **외부 PostgreSQL** 접속(반드시 `asyncpg` 드라이버) | `postgresql+asyncpg://payment:...@localhost:5432/payment` |
| `REDIS_URL` | Redis 접속(운영은 compose가 `redis://redis:6379/0`으로 덮어씀) | `redis://localhost:6380/0` |
| `ENCRYPTION_KEY` | AES-256-GCM 키(base64 32바이트). **필수** | (직접 생성) |
| `TOSS_SECRET_KEY` | 토스 시크릿 키(dev=`test_sk_*`, prod=`live_sk_*`). **필수** | `test_sk_xxxx` |
| `TOSS_API_BASE_URL` | 토스 API 베이스 URL | `https://api.tosspayments.com` |
| `TRUST_PROXY` / `TRUST_PROXY_HOPS` | 리버스 프록시 뒤면 `true` + XFF hop 수(nginx 1단=1, 앞에 LB 더 있으면 2) | `false` / `1` |
| `WEBHOOK_IP_CHECK_ENABLED` | 토스 발신 IP 외 웹훅 거부(운영 `true` 권장) | `true` |
| `SWAGGER_ID` / `SWAGGER_PW` | `/docs` HTTP Basic 계정. 비우면 docs 404 | `admin` / (강력값) |
| `SCHEDULER_ENABLED` / `SCHEDULER_INTERVAL_MINUTES` | 구독 자동 갱신 배치 사용·주기 | `true` / `5` |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | DB 커넥션 풀(총 최대 = 합) | `10` / `20` |

> 중요: `ENCRYPTION_KEY`·`TOSS_SECRET_KEY`·DB 비밀번호 등 비밀값은 Git에 커밋하지 않는다. `.env.example`만 추적하고, 실제 값은 `.env` / `.env.dev` / `.env.prod`에 둔다.

> 참고: 운영에서는 `REDIS_URL`·`TRUST_PROXY`·`APP_ENV`를 compose가 컨테이너 네트워크 기준으로 주입하므로 `.env.prod`에 적지 않아도 된다(적어도 compose 값이 우선, `docker-compose.prod.yml:24`).

---

## 4. 테스트

테스트도 **외부 DB**를 사용한다. 외부 `payment-postgres`에 `payment_test` 데이터베이스를 만들어 두면 된다(운영 DB와 별개). 기본 접속값은 `tests/conftest.py:18`에 정의되어 있고, 다르면 `TEST_DATABASE_URL`로 덮어쓴다.

테스트용 DB 생성(외부 Postgres 컨테이너 안에서):

```bash
docker exec -it payment-postgres createdb -U payment payment_test
```

전체·부분 테스트 실행:

```bash
uv run pytest                       # 전체
uv run pytest tests/integration/    # 통합
uv run pytest tests/e2e/            # E2E(어드민 화면)
```

다른 DB로 실행할 때:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://payment:Payment!2002@localhost:5432/payment_test uv run pytest
```

> 주의: 테스트 DB는 운영 DB와 별개(`payment_test`)다. 기본값은 외부 `payment-postgres`(host 5432, user `payment`)를 가리킨다(`tests/conftest.py:14-18`).

---

## 5. 운영 배포(docker)

운영 스택은 `docker-compose.prod.yml`(프로젝트명 `payment_system`)로 **nginx·app·redis 3개**를 띄운다. DB는 외부다.

### 5.0 새 리눅스 서버에 처음 설치하기 (전체 절차)

아무것도 없는 **새 리눅스 서버(예: Ubuntu 22.04)**에 처음부터 올리는 순서다. 아래 1~9를 차례로 따라가면 된다. (1~3은 서버 준비, 4~6은 설정·실행, 7~9는 마무리)

<ol class="steps">
<li><b>Docker 설치</b> — Docker Engine + Compose 플러그인을 설치하고 현재 사용자를 docker 그룹에 넣는다(이후 로그아웃·재로그인).
<pre><code class="language-bash">curl -fsSL https://get.docker.com | sudo sh        # Docker Engine + compose 플러그인
sudo usermod -aG docker $USER                       # sudo 없이 docker 사용(재로그인 필요)
docker --version &amp;&amp; docker compose version          # 설치 확인</code></pre></li>

<li><b>코드 가져오기</b> — 저장소를 서버에 클론(또는 복사)한다.
<pre><code class="language-bash">git clone &lt;이-저장소-URL&gt; payment_system
cd payment_system</code></pre>
Git을 쓰지 않으면 <code>scp -r ./payment_system user@서버:/opt/</code> 처럼 통째로 복사해도 된다.</li>

<li><b>외부 PostgreSQL 준비</b> — DB는 컴포즈에 없으므로 <b>따로</b> 마련한다. 관리형 DB(Azure Database for PostgreSQL 등)를 쓰거나, 같은 서버/별도 서버에 postgres 컨테이너를 띄운다. 빈 <code>payment</code> 데이터베이스와 접속 계정만 있으면 되고, <b>스키마(테이블)는 앱이 자동 생성</b>한다(아래 6단계의 마이그레이션). DB 방화벽에서 <b>앱 서버 → DB(5432)</b>만 허용한다.
<blockquote>참고: 리눅스에서 같은 호스트의 DB에 붙을 때는 <code>DATABASE_URL</code>의 호스트를 <code>host.docker.internal</code>로 둔다. compose에 <code>extra_hosts: "host.docker.internal:host-gateway"</code>가 이미 있어 컨테이너가 호스트에 닿는다.</blockquote></li>

<li><b>환경설정 <code>.env.prod</code> 작성</b> — <code>.env.example</code>를 참고해 채운다. 필수: <code>DATABASE_URL</code>(외부 DB), <code>ENCRYPTION_KEY</code>(새로 생성), <code>TOSS_SECRET_KEY</code>(라이브), <code>BASE_URL</code>(도메인), <code>SWAGGER_ID/PW</code>. 상세는 아래 <b>5.1</b> 참고.
<pre><code class="language-bash"># AES-256-GCM 키 생성(운영용으로 새로):
python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"</code></pre></li>

<li><b>TLS 인증서 배치</b> — <code>docker/nginx/certs/</code>에 <code>fullchain.pem</code>·<code>privkey.pem</code>을 둔다(Let's Encrypt 권장, 도메인 없으면 자체 서명). 명령은 아래 <b>5.1</b> 참고.</li>

<li><b>실행 — 빌드·기동(+ 자동 마이그레이션)</b>:
<pre><code class="language-bash">docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f app   # 마이그레이션·기동 로그 확인</code></pre>
컨테이너가 켜지면 엔트리포인트가 <code>alembic upgrade head</code>로 외부 DB에 테이블을 자동 생성한 뒤 앱을 띄운다.</li>

<li><b>최초 관리자(SYSTEM_ADMIN) 생성</b> — 로그인하려면 첫 관리자 계정을 만들어야 한다. 앱 컨테이너 안에서 CLI로 생성한다.
<pre><code class="language-bash">docker compose -f docker-compose.prod.yml exec app \
  python -m app.cli create-admin --email admin@yourco.com --password '강력한비밀번호!'</code></pre></li>

<li><b>방화벽 개방</b> — 외부에서 들어오는 포트는 <b>80·443</b>만 연다(앱 8000·redis는 비공개). 예(ufw):
<pre><code class="language-bash">sudo ufw allow 80,443/tcp &amp;&amp; sudo ufw enable</code></pre></li>

<li><b>동작 확인</b> — <code>https://도메인/</code> 접속 → 로그인 화면. <code>docker compose -f docker-compose.prod.yml ps</code>로 3개 컨테이너가 healthy인지, <code>/docs</code>(Swagger, SWAGGER_ID/PW)와 관리자 로그인이 되는지 확인한다.</li>
</ol>

> 주의: `.env.prod`와 TLS 키 같은 **비밀값은 Git에 커밋하지 말 것**. `ENCRYPTION_KEY`는 분실·변경 시 기존 암호화 데이터(빌링키 등)를 복호화할 수 없으니 안전하게 보관한다.

> 팁: 코드 업데이트 후 재배포는 `git pull` → `docker compose -f docker-compose.prod.yml up -d --build`. 새 마이그레이션이 있으면 기동 시 자동 적용된다.

### 5.1 사전 준비

<ol class="steps">
<li><code>.env.example</code>를 참고해 <b><code>.env.prod</code></b>를 채운다(Git 미추적).</li>
<li>TLS 인증서를 <code>docker/nginx/certs/</code>에 배치한다.</li>
<li><code>DATABASE_URL</code>이 <b>외부 관리형 DB</b> 엔드포인트를 가리키는지 확인한다.</li>
</ol>

`.env.prod` 최소 필수값(`docker/README.md`):

```env
ENVIRONMENT=prod
BASE_URL=https://your-domain.example.com
# 외부 관리형 Postgres — 반드시 asyncpg 드라이버(필요 시 ?ssl=require)
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@your-db-host:5432/payment
ENCRYPTION_KEY=
TOSS_SECRET_KEY=live_sk_xxxx
SWAGGER_ID=admin
SWAGGER_PW=강력한값
WEBHOOK_IP_CHECK_ENABLED=true
```

TLS 인증서 — `docker/nginx/certs/`에 `fullchain.pem`·`privkey.pem` 두 파일을 둔다.

Let's Encrypt(운영 권장):

```bash
sudo certbot certonly --webroot -w docker/nginx/certbot-www -d your-domain.example.com
sudo cp /etc/letsencrypt/live/your-domain.example.com/fullchain.pem docker/nginx/certs/
sudo cp /etc/letsencrypt/live/your-domain.example.com/privkey.pem  docker/nginx/certs/
```

자체 서명(도메인 없이 우선 띄워볼 때):

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout docker/nginx/certs/privkey.pem \
  -out   docker/nginx/certs/fullchain.pem \
  -subj "/CN=localhost"
```

### 5.2 실행

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

마이그레이션은 **자동**이다. 컨테이너 시작 시 엔트리포인트(`docker/entrypoint.sh`)가 `alembic upgrade head`로 외부 DB 스키마를 최신(head)까지 적용한 뒤 uvicorn을 실행한다.

### 5.3 로그·상태·종료

```bash
docker compose -f docker-compose.prod.yml logs -f app   # 마이그레이션·기동 로그
docker compose -f docker-compose.prod.yml ps            # 컨테이너 상태
docker compose -f docker-compose.prod.yml down          # 중지(redis 볼륨은 유지)
```

재배포(업데이트):

```bash
git pull && docker compose -f docker-compose.prod.yml up -d --build
```

> 참고: 외부에 열리는 포트는 nginx의 80/443뿐이다. `app`(8000)·`redis`(6379)는 publish하지 않아 docker 내부 네트워크에서만 컨테이너 이름으로 서로를 호출한다(`docker-compose.prod.yml`).

---

## 6. 운영 주의사항

- **컴포즈 프로젝트명 분리**: 운영 스택은 `name: payment_system`(nginx·redis·app 3개), 개발 인프라는 `name: payment-dev`(redis만)로 격리되어 있다. 같은 프로젝트명으로 섞으면 운영 컨테이너에 개발용 컨테이너가 끼어드는 사고가 난다(`docker-compose.prod.yml:18`, `docker-compose.yml`).
- **DB는 개발·배포 둘 다 별도 docker(외부)**: compose에 postgres가 없다. 앱·테스트 모두 외부 DB 엔드포인트로 연결한다. `DATABASE_URL`을 `localhost`/`127.0.0.1`로 두면 컨테이너 자신을 가리켜 접속에 실패한다 — 실제 배포는 외부 DB 호스트로, 로컬에서 호스트의 DB docker에 붙일 때는 `host.docker.internal:5432`를 사용한다(`docker/README.md`).
- **비밀값 관리**: `ENCRYPTION_KEY`(운영 전용 새 값)·`TOSS_SECRET_KEY`(`live_sk_*`)·`SWAGGER_PW` 등은 `.env.prod`에 두고 Git에 커밋하지 않는다.
- **마이그레이션 head 적용**: 엔트리포인트가 매 기동 시 `alembic upgrade head`를 실행한다. 앱을 여러 대로 확장하면 마이그레이션 중복을 피하기 위해 한 대만 기본값으로 두고 나머지는 `RUN_MIGRATIONS=0`으로 띄운다(`docker/entrypoint.sh`).
- **클라이언트 IP**: nginx가 `X-Forwarded-For`를 세팅하고 앱은 `TRUST_PROXY=true`·`TRUST_PROXY_HOPS=1`로 읽는다. 앞단에 LB가 하나 더 있으면 `TRUST_PROXY_HOPS=2`로 올린다(웹훅·어드민 IP 화이트리스트 정합에 중요).
- **인증서 갱신**: `certbot renew` 후 `docker compose -f docker-compose.prod.yml exec nginx nginx -s reload`.
- **세션 보안(추가 키 없음)**: 어드민 세션은 별도 쿠키 서명키 없이 **Redis에 저장된 무작위 세션 토큰**(`secrets.token_urlsafe(32)`)으로 동작한다(`app/services/auth.py`). 따라서 `SECRET_KEY` 류의 환경변수는 없으며, redis를 내부 네트워크 전용으로 두는 것(포트 미공개)이 곧 세션 보호다.

---

## 7. 백업·복구·롤백

돈 기록(DB)은 외부 관리형 PostgreSQL에 있으므로 백업·복구는 1차적으로 **관리형 DB 기능**(Azure Database for PostgreSQL의 자동 백업·특정 시점 복구 PITR)에 위임한다. 그 위에, **배포·마이그레이션 직전 수동 스냅샷**을 안전망으로 권장한다.

### 7.1 DB 백업 (pg_dump)

```bash
# DB에 접근 가능한 호스트(또는 앱 컨테이너)에서 — 배포/마이그레이션 직전 권장
pg_dump "postgresql://USER:PASSWORD@DB_HOST:5432/payment" -Fc -f payment_$(date +%Y%m%d_%H%M).dump
```

- `-Fc`(커스텀 포맷)은 `pg_restore`로 부분/선택 복구가 가능하다.
- 관리형 DB의 자동 백업·PITR과 **함께** 쓰는 것을 권장한다(자동 백업은 일상 보호, 수동 덤프는 배포 직전 롤백 안전망).

### 7.2 DB 복구 (pg_restore)

```bash
# 주의: 운영 DB에 덮어쓰기 전, 반드시 현재 상태를 먼저 백업한다.
pg_restore --clean --if-exists -d "postgresql://USER:PASSWORD@DB_HOST:5432/payment" payment_YYYYMMDD_HHMM.dump
```

### 7.3 마이그레이션 롤백 (alembic downgrade)

엔트리포인트는 기동 시 `alembic upgrade head`만 수행한다(`docker/entrypoint.sh`). 직전 배포의 스키마 변경을 되돌리려면 앱 컨테이너에서 downgrade를 **직접** 실행한다.

```bash
docker compose -f docker-compose.prod.yml exec app alembic current      # 현재 리비전 확인
docker compose -f docker-compose.prod.yml exec app alembic downgrade -1  # 한 단계 되돌리기
# 특정 리비전으로: docker compose -f docker-compose.prod.yml exec app alembic downgrade <revision>
```

- **데이터 손실 주의**: 컬럼/테이블을 제거하는 downgrade는 그 데이터를 함께 지운다. 반드시 7.1 백업을 먼저 떠 두고, 코드 롤백(이전 이미지 재배포)으로 해결 가능한지 먼저 검토한다.
- **스키마-이미지 정합**: 새 이미지 배포는 자동으로 `upgrade head`까지 올린다. 이전 이미지로 되돌릴 때는, 그 이미지가 기대하는 리비전까지 **먼저 downgrade**해야 앱과 스키마가 맞는다.

### 7.4 Redis 데이터

redis는 세션·nonce·레이트리밋·킬스위치 **캐시**다. 명명 볼륨(`redis-data`)에 스냅샷이 남지만, 유실되어도 **재로그인** 수준의 영향이며 결제·구독 데이터(DB)와는 무관하다. 별도 백업 대상이 아니다.

> 함께 보기: [서비스 API](11-service-api.md)
