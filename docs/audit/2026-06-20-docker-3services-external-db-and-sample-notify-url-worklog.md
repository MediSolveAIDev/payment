# 운영 docker 3개 구성 + DB 외부화 + sample 알림 URL 수정 워크로그

- 날짜: 2026-06-20
- 작업자: seungjinhan

## 요청

1. payment_system docker는 **nginx·redis·app 3개만**, DB는 외부 다른 docker 호출.
2. sample service는 별도 docker라 알림 등록(`http://localhost:8001/notify`)이 안 됨.
3. 개발용·배포용 **둘 다 DB는 따로 구성**한다.

## 원인/배경

- 테스트용으로 dev compose(`docker-compose.yml`)를 `docker compose up -d postgres redis`로 올리면서, prod와 **같은 프로젝트명(`payment_system`)**에 개발 postgres가 섞여 들어갔다.
- sample은 결제 서버와 다른 compose/네트워크 → app 컨테이너에서 `localhost:8001`(자기 자신)·`sample:8000`(다른 네트워크)으로는 닿지 않고, `host.docker.internal:8001`만 닿는다. 게다가 Django `ALLOWED_HOSTS`에 `host.docker.internal`이 없어 **400 DisallowedHost**로 거부됐다.

## 변경 내용

### docker 구성
- `docker-compose.prod.yml`: `name: payment_system` 명시(운영 = nginx·redis·app 3개, DB 외부).
- `docker-compose.yml`(개발): `name: payment-dev` 명시 + **postgres 서비스 제거**(redis만). 개발 DB도 외부 docker 사용.
- 떠돌던 `payment_system-postgres-1` 컨테이너 제거. 개발 인프라는 `payment-dev` 프로젝트로 격리.
- `tests/conftest.py`: `TEST_DATABASE_URL` 기본값을 외부 DB(`payment:...@localhost:5432/payment_test`)로 변경. 외부 `payment-postgres`에 `payment_test` DB 생성.
- `app/core/config.py`: `database_url` 기본값을 사라진 dev DB(5433) → 외부 DB(`localhost:5432`)로 변경. `.env`(로컬, gitignored)에 `DATABASE_URL`(5432)·`REDIS_URL`(6380) 추가 — dev 앱(호스트 실행)이 외부 DB·dev redis에 연결되도록.
- `docker/README.md`: 프로젝트명 분리·DB 외부화 안내.

### sample 알림 URL
- `config/settings.py`: `ALLOWED_HOSTS`에 `host.docker.internal`·`sample` 추가(+ 환경변수 확장).
- `shop/views.py`·`notifications.html`: 등록용 수신 URL을 **`http://host.docker.internal:8001/notify`**로 안내(`localhost:8001`/`sample:8000`은 안 됨을 명시).

## 검증

- `docker ps` — `payment_system` 프로젝트 = app·redis·nginx **3개만**. `payment-dev` = redis. 외부 DB = `payment-postgres`(5432).
- app 컨테이너 → `http://host.docker.internal:8001/notify` POST → **HTTP 200**(이전 400 해소).
- 결제 서버 `uv run pytest`(외부 DB) → **606 passed**. sample `manage.py test` → **81 passed**.

### 문서
- `17-service-notifications.md`·sample README·`docker/README.md` 갱신 + HTML 재빌드.
