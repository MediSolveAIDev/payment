# 운영 배포 (Docker) — nginx · payment_system(app) · redis

클라우드 단일 인스턴스에 세 컨테이너를 띄운다. **PostgreSQL은 외부 관리형(Azure Database for PostgreSQL 등)**을
`DATABASE_URL`로 연결한다(컨테이너에 포함하지 않음). 회사 클라우드는 **MS Azure** 기준.

```
인터넷 ──443/80──▶ nginx(TLS 종단) ──8000──▶ app(payment_system) ──▶ 외부 관리형 PostgreSQL(Azure DB for PostgreSQL)
                                                  └──▶ redis(세션·nonce·레이트리밋·킬스위치 캐시)
```

구성 파일:
- `Dockerfile` — 앱 이미지(uv로 잠금 의존성 설치 → uvicorn)
- `docker-compose.prod.yml` — app/redis/nginx 오케스트레이션
- `docker/entrypoint.sh` — 시작 시 `alembic upgrade head` 후 앱 실행
- `docker/nginx/conf.d/payment.conf` — TLS 리버스 프록시(80→443 리다이렉트)

---

## 1) 사전 준비

### (a) 운영 환경변수 — `.env.prod`
`.env.example`를 참고해 `.env.prod`를 채운다(Git 미추적). 최소 필수값:

```dotenv
ENVIRONMENT=prod
BASE_URL=https://your-domain.example.com
# 외부 관리형 Postgres — 반드시 asyncpg 드라이버
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@your-rds-host:5432/payment
# AES-256-GCM 키(base64 32바이트):
#   python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"
ENCRYPTION_KEY=...
TOSS_SECRET_KEY=live_sk_xxxx
SWAGGER_ID=admin
SWAGGER_PW=강력한값
WEBHOOK_IP_CHECK_ENABLED=true
```

> `REDIS_URL` · `TRUST_PROXY` · `APP_ENV` 는 compose가 컨테이너 네트워크 기준으로 주입하므로
> `.env.prod`에 적지 않아도 된다(적어도 compose 값이 우선).

### (b) TLS 인증서 — `docker/nginx/certs/`
`fullchain.pem` 과 `privkey.pem` 두 파일을 둔다.

- **운영(권장): Let's Encrypt** — 80포트가 열린 상태에서 certbot으로 발급 후
  결과 파일을 `docker/nginx/certs/`에 복사(또는 심볼릭). webroot 갱신은
  `docker/nginx/certbot-www/`(`/.well-known/acme-challenge/`)로 처리된다.
  ```bash
  sudo certbot certonly --webroot -w docker/nginx/certbot-www -d your-domain.example.com
  sudo cp /etc/letsencrypt/live/your-domain.example.com/fullchain.pem docker/nginx/certs/
  sudo cp /etc/letsencrypt/live/your-domain.example.com/privkey.pem  docker/nginx/certs/
  ```
- **테스트(자체 서명)** — 도메인 없이 우선 띄워볼 때:
  ```bash
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout docker/nginx/certs/privkey.pem \
    -out   docker/nginx/certs/fullchain.pem \
    -subj "/CN=localhost"
  ```

---

## 2) 실행

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f app      # 마이그레이션·기동 로그
docker compose -f docker-compose.prod.yml ps
```

- 앱 시작 시 엔트리포인트가 `alembic upgrade head`로 외부 DB 스키마를 최신화한다.
- 첫 시스템 관리자 계정 생성 등 초기화가 필요하면 `scripts/` 참고
  (예: `docker compose -f docker-compose.prod.yml exec app python -m scripts....`).

## 3) 종료 / 업데이트

```bash
docker compose -f docker-compose.prod.yml down            # 중지(redis 볼륨은 유지)
git pull && docker compose -f docker-compose.prod.yml up -d --build   # 재배포
```

---

## 메모

- **DB는 컨테이너에 없음**: 백업·HA는 관리형 DB(Azure Database for PostgreSQL)에 위임. Azure 방화벽/NSG에서 앱서버→DB만 허용.
- **포트 노출**: 외부에 열리는 건 nginx의 80/443뿐. app(8000)·redis(6379)는 내부 네트워크 전용.
- **클라이언트 IP**: nginx가 `X-Forwarded-For`를 세팅하고 앱은 `TRUST_PROXY=true`,
  `TRUST_PROXY_HOPS=1`로 읽는다. 앞단에 **LB가 하나 더** 있으면 `TRUST_PROXY_HOPS=2`로 올린다
  (서비스/어드민 IP 화이트리스트 정합에 중요).
- **여러 대로 확장** 시 마이그레이션 중복을 피하려면 한 대만 기본값으로 두고 나머지는
  `RUN_MIGRATIONS=0` 환경변수로 띄운다.
- **컴포즈 프로젝트명 분리**: 운영 스택은 `payment_system`(nginx·redis·app 3개), 개발 인프라(`docker-compose.yml`)는 `payment-dev`(redis만). 같은 프로젝트명으로 섞이지 않게 각 파일에 `name:`을 고정했다.
- **DB는 개발·배포 둘 다 별도 docker로 따로 구성**한다(컴포즈에 postgres 없음). 앱·테스트 모두 외부 DB 엔드포인트로 연결한다(예: 호스트의 `payment-postgres` → `host.docker.internal:5432`).
- 인증서 자동 갱신: `certbot renew` 후 `docker compose -f docker-compose.prod.yml exec nginx nginx -s reload`.

## 트러블슈팅

- `OSError: [Errno 111] Connect call failed ('127.0.0.1', 5433)` 류 DB 접속 실패:
  `DATABASE_URL`이 `localhost`/`127.0.0.1`로 되어 있으면 **컨테이너 자신**을 가리켜 실패한다.
  - 실제 배포: 외부 관리형 DB 엔드포인트로 교체(`...@<db-host>:5432/...`, 필요 시 `?ssl=require`).
  - 로컬에서 호스트의 별도 DB docker(예: `payment-postgres`, 5432)에 붙일 때:
    `...@host.docker.internal:5432/...` 사용(app 서비스에 `extra_hosts: host-gateway` 설정됨).
    DB는 컴포즈가 아니라 별도 docker로 띄운다.
