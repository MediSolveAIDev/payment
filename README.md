# 구독/결제 API 서버

사내 서비스 공용 구독/결제 서버. 토스페이먼츠 빌링키 기반 자동결제.

- 스펙: `docs/superpowers/specs/2026-06-05-subscription-payment-server-design.md`
- 스택: FastAPI · PostgreSQL(SQLAlchemy 2 async) · Redis · htmx admin · APScheduler

## 빠른 시작

```bash
docker compose up -d                  # PostgreSQL(5433), Redis(6380)
cp .env.example .env                  # ENCRYPTION_KEY/TOSS_SECRET_KEY 채우기
uv sync
uv run alembic upgrade head
uv run python -m app.cli create-admin --email admin@medisolveai.com --password '<10자 이상>'
uv run uvicorn app.main:app --reload
```

- Admin: http://localhost:8000/admin
- Health: http://localhost:8000/health

## 테스트

```bash
docker compose up -d
uv run pytest                          # 전체 (unit/integration/security/e2e)
uv run pytest --cov=app --cov-report=term-missing
```

## 외부 서비스 연동 가이드

### 1. 인증 헤더 (모든 요청)

| 헤더 | 값 |
|---|---|
| `X-Service-Key` | 발급받은 서비스 키 (`svc_...`) |
| `X-Timestamp` | Unix epoch 초 (서버와 ±5분 이내) |
| `X-Nonce` | 요청마다 새로운 UUID |
| `X-Signature` | 아래 서명 |

서명 생성 (HMAC-SHA256, hex):

```python
import hashlib, hmac, json, time, uuid

def sign(secret: str, method: str, path: str, body: bytes) -> dict:
    ts = str(int(time.time()))
    nonce = str(uuid.uuid4())
    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join([method.upper(), path, ts, nonce, body_hash])
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {"X-Service-Key": SERVICE_KEY, "X-Timestamp": ts,
            "X-Nonce": nonce, "X-Signature": sig}
```

주의: 등록된 서버 IP에서만 호출 가능. 키 유출 시 admin에서 즉시 재발급.

### 2. 구독 생성 플로우

1. 프론트에서 토스 SDK `payment.requestBillingAuth()` 호출
   (`customerKey`는 UUID로 생성)
2. successUrl 리다이렉트로 받은 `authKey` + `customerKey`를 백엔드로 전달
3. 백엔드에서 `POST /api/v1/subscriptions` 호출:

```json
{"external_user_id": "<서비스측 사용자 ID>", "plan_id": "<요금제 UUID>",
 "auth_key": "<authKey>", "customer_key": "<customerKey>"}
```

결제 금액은 서버가 요금제에서 계산한다(요청 본문에 금액 없음).

### 3. 주요 엔드포인트

| 메서드/경로 | 설명 |
|---|---|
| `POST /api/v1/subscriptions` | 구독 생성(빌링키 발급+첫 결제). `trial:true`면 체험으로 시작(요금제가 체험 제공 시) |
| `GET /api/v1/subscriptions/{external_user_id}` | 구독 상태 + **`access_allowed`**(서비스 접근 허용 여부) |
| `POST /api/v1/subscriptions/{external_user_id}/cancel` | 취소(만료일까지 유지, 체험은 즉시 종료) |
| `POST /api/v1/subscriptions/{external_user_id}/resume` | 취소 철회 |
| `POST /api/v1/subscriptions/{external_user_id}/change-card` | 카드 교체 |
| `POST /api/v1/subscriptions/{external_user_id}/pay` | **정지(SUSPENDED) 구독 수동 결제** → 성공 시 ACTIVE 복귀(기준일 리셋) |
| `GET /api/v1/plans` | 요금제 목록 |
| `GET /api/v1/payments/{external_user_id}` | 결제 이력 |

에러 응답: `{"error": {"code": "...", "message": "..."}}`
(401 인증실패 · 403 IP/권한 · 402 결제실패 · 409 중복구독 · 429 한도초과 · 503 결제결과불명)

### 4. 구독 상태 머신 (요청 002)

외부 서비스는 응답의 **`access_allowed`**(또는 status)로 서비스 접근을 판단한다.

| 상태 | 접근 | 의미 / 전이 |
|---|:---:|---|
| `TRIAL` | O | 체험 — 만료 시 자동 결제 → 성공 `ACTIVE` / 실패 `PAST_DUE`. 사용자 취소 시 즉시 종료 |
| `ACTIVE` | O | 정상 — 기준일에 정기 결제. 실패 `PAST_DUE`, 해지예약 `CANCELED` |
| `PAST_DUE` | O(유예) | 결제 실패 — `RETRY_INTERVAL_HOURS`(12h) 간격 `RETRY_LIMIT`(4)회 재시도. 성공 `ACTIVE`, 소진 `SUSPENDED` |
| `SUSPENDED` | **X** | 강제 정지 — 자동결제 중지, 수동 결제(`/pay`) 대기. `SUSPENDED_GRACE_DAYS`(30) 초과 시 `EXPIRED`. 결제 성공 시 `ACTIVE`+기준일 리셋 |
| `CANCELED` | O | 해지 예약 — 만료일까지 유지, 도달 시 `EXPIRED` |
| `EXPIRED` | **X** | 완전 종료(종단). 스케줄러 영구 제외 |

## 운영 메모

- 자동연장: 5분 주기 배치(APScheduler). 실패 시 `RETRY_INTERVAL_HOURS`(기본 12h)
  간격 `RETRY_LIMIT`(기본 4)회 재시도 후 **SUSPENDED**(접근 차단), 빌링키는 수동
  결제를 위해 보존. `SUSPENDED_GRACE_DAYS`(기본 30) 초과 시 EXPIRED. 모두 .env 관리.
- 토스 웹훅 URL: `POST /api/v1/webhooks/toss` (토스 인바운드 IP만 허용)
- 빌링키/HMAC secret은 AES-256-GCM 암호화 저장 — `ENCRYPTION_KEY` 분실 시 복호화 불가
- 이메일: 기본 콘솔 출력(`ConsoleEmailSender`). SMTP 연동 시
  `app/notifications/email.py`에 구현체 추가 후 `create_app` 주입 교체
- 결과 불명 결제(타임아웃): 구독 생성·갱신에서 토스 응답을 받지 못하면 실패로
  확정하지 않고 결제를 PENDING으로 보존한다. 갱신 배치의 정산 스윕이 토스
  재조회로 추후 확정(DONE/FAILED)하고, 취소된 구독에 결제가 확정되면 담당자에게
  수동 검토 메일을 보낸다.
- 결제 정합성 대시보드/조회에서 PENDING이 오래 남아 있으면 토스 재조회 지연
  또는 정산 스윕 미동작을 의심하고 `payment.reconciled_*` / `*_unresolved`
  감사 로그를 확인한다.

### 운영 도입 시 보완 권장(현재 known limitation)

- 운영 SMTP 도입 시: 서비스 등록 안내 메일이 DB 커밋 직전에 발송된다
  (`registry.register_service`). 커밋 실패 시 죽은 설정 링크가 나갈 수 있으므로
  발송을 커밋 이후로 옮기거나 outbox 패턴을 적용할 것.
- `webhook_events.status == FAILED`(영구 처리 실패) 이벤트를 주기적으로 점검·
  재처리하는 reaper가 아직 없다. 운영에서는 FAILED 웹훅 알림/재처리 잡을 추가할 것.
- `trust_proxy=True`는 인바운드 `X-Forwarded-For`를 덮어쓰는 신뢰 프록시 뒤에서만
  켤 것(append형이면 IP 허용목록 우회 가능). `allowed_ips`는 정확한 IP만 매칭
  (CIDR 미지원).
