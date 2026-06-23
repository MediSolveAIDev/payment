# docker-compose.dev DB 접속 실패 수정 (localhost → host.docker.internal) 워크로그

- 날짜: 2026-06-23
- 작업자: seungjinhan (oasis@medisolveai.com)

## 증상

`docker compose -f docker-compose.dev.yml up -d --build` 후 app 컨테이너가 DB 접속 실패로 기동 불가:
```
OSError: Multiple exceptions: [Errno 111] Connect call failed ('::1', 5432, 0, 0),
[Errno 111] Connect call failed ('127.0.0.1', 5432)
```

## 원인

- `.env.dev`의 `DATABASE_URL=...@localhost:5432/payment` 의 `localhost`는 **app 컨테이너 자신**을 가리킨다(컨테이너 네트워크 네임스페이스). 호스트에서 도는 `payment-postgres`(호스트 `0.0.0.0:5432` publish)에 닿지 못함.
- 호스트 5432는 정상 OPEN(컨테이너 `payment-postgres` 가 publish 중)임을 확인 → DB 자체 문제 아님, **호스트네임 문제**.
- 같은 문제를 REDIS는 이미 해결돼 있었음: `.env.dev`는 `redis://localhost:6380`이나 compose `environment:`가 `redis://redis:6379`로 덮어씀. **DATABASE_URL만 동일 처리가 누락**된 상태였다.

## 수정

`docker-compose.dev.yml` app 서비스 `environment:`에 DB 호스트 고정 오버라이드 추가(REDIS_URL과 동일 패턴). **비밀번호는 추적 파일에 박지 않고** gitignore된 `.env`의 `${DB_PASSWORD}`로 치환:
```yaml
DATABASE_URL: postgresql+asyncpg://payment:${DB_PASSWORD}@host.docker.internal:5432/payment
```
- `.env`(gitignore)에 `DB_PASSWORD=Payment!2002` 추가 → compose가 파싱 시 프로젝트 `.env`에서 주입. 추적되는 compose엔 `${DB_PASSWORD}`만 남아 **dev 비번이 git에 올라가지 않음**(기존 "추적 파일엔 비밀 없음" 원칙 유지).
- `.env`/`.env.dev`의 `@localhost`(호스트에서 uvicorn 직접 실행 시 정상)는 그대로 둠 — 컨테이너 실행 시에만 `host.docker.internal`로 덮어쓴다.
- `extra_hosts: host.docker.internal:host-gateway` 가 이미 있어 컨테이너 → 호스트로 해석됨(Linux 포함).
- 같이 정리: environment 하단 주석의 "DATABASE_URL ... 은 .env.prod 에서 주입" → 실제 결선에 맞게 갱신.

## 검증

```
docker compose -f docker-compose.dev.yml up -d
```
- app 로그: `alembic upgrade head` 정상 실행(`Context impl PostgresqlImpl`) → DB 연결 성공, `Application startup complete`.
- `curl http://127.0.0.1:8000/health` → HTTP 200.
- `docker compose ... ps` → app `Up (healthy)`.

## 비고

- `payment-postgres` 컨테이너는 `db_server/run.sh`(또는 별도 docker)로 띄우는 개발용 DB. dev 비밀번호는 `.env.dev`에 이미 평문이라 dev compose에 동일 값 노출은 허용 범위.
- 운영(`docker-compose.prod.yml` 류)은 외부 관리형 DB를 `DATABASE_URL`로 주입하므로 이 수정과 무관.
