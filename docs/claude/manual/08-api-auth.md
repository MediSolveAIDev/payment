# 08. 외부 API 인증 (HMAC · IP · nonce · 레이트리밋)

> `/api/v1/*`로 들어오는 **외부 서비스(서버) 요청을 검증**하는 관문. 04~07의 모든 외부 API가
> 이 관문을 먼저 통과한다. Admin의 세션 인증(문서 02)과는 완전히 다른 **무상태 + 서명 기반**이다.
>
> 선행: [00-overview.md](00-overview.md)의 "두 진입 평면", [04-subscription-create.md](04).

---

## 0. 한눈에 보기

- **무엇을 막나**: 위조 요청, 본문 변조, 재전송(replay), 허용되지 않은 IP, 과도한 호출(DoS).
- **어떻게**: 서비스 키 + HMAC 서명 + IP 화이트리스트 + 타임스탬프 + nonce + 레이트리밋의 **다층 방어**.
- **진입점**: `app/api/deps.py`의 `authenticate_service`(6단계) / `payment_rate_limit`(결제성 추가 제한).
- **서명 규약**: `app/core/security.py`의 `sign_request`.

| 의존성 | 쓰는 곳 | 추가 보호 |
|---|---|---|
| 없음(무인증) | **`GET /api/v1/services`** — 서비스 목록(id·이름·상태, 민감정보 미포함) | — |
| `authenticate_service` | 조회/취소/재개 (`GET /plans`, `GET/POST subscriptions...`, `GET payments`) | 기본 6단계 |
| `payment_rate_limit` | 결제성(구독 생성·카드변경·수동결제·**단건 결제 `POST /api/v1/payments`**·**단건 결제 취소 `POST /api/v1/payments/{order_id}/cancel`**) | 6단계 + 결제 전용 throttle |

> 웹훅(`/webhooks/toss`)은 토스가 우리 서명 규약을 모르므로 **이 인증을 안 쓰고 IP 화이트리스트로만** 막는다(문서 07).

---

## 1. 요청에 필요한 것 (클라이언트 측)

외부 서버는 매 요청에 4개 헤더를 붙인다(README "외부 서비스 연동" 참고):

| 헤더 | 값 |
|---|---|
| `X-Service-Key` | 발급받은 서비스 API 키(`svc_...`, 문서 01) |
| `X-Timestamp` | Unix epoch 초(서버와 ±5분 이내) |
| `X-Nonce` | 요청마다 새 UUID(1회용) |
| `X-Signature` | 아래 서명(HMAC-SHA256 hex) |

서명 대상 문자열(canonical string):
```
METHOD \n PATH \n TIMESTAMP \n NONCE \n SHA256(body)
```
```python
message = "\n".join([method.upper(), path, timestamp, nonce, sha256(body)])
signature = HMAC_SHA256(hmac_secret, message)   # hex
```
`hmac_secret`은 서비스 등록 시 발급된 값(문서 01). **본문(body)의 해시까지 서명에 포함**되므로
본문을 한 글자라도 바꾸면 서명이 깨진다(본문 무결성).

---

## 2. 서명 생성 — `sign_request` (`core/security.py`)

```python
def sign_request(secret, method, path, timestamp, nonce, body: bytes) -> str:
    for name, comp in [("method",method),("path",path),("timestamp",timestamp),("nonce",nonce)]:
        if "\n" in comp or "\r" in comp:
            raise ValueError(...)            # ★ 개행 금지
    body_hash = sha256(body).hexdigest()
    message = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
    return hmac.new(secret.encode(), message.encode(), sha256).hexdigest()
```

**왜 개행을 거부하나(초급자용)**: 구분자가 `\n`이라, 어떤 구성요소에 개행이 섞이면 서로 다른
입력이 **같은 canonical string**을 만들 수 있다(필드 간 바이트 이동 공격). 예를 들어
`path="/a\n2026"`처럼 넣으면 timestamp 자리로 값이 밀려 같은 문자열이 될 수 있어, 미리 막는다.

서버는 같은 함수로 **기대 서명을 재계산**해 요청의 서명과 비교한다(2-1의 5단계).

---

## 3. 인증 파이프라인 — `authenticate_service` (`api/deps.py`)

순서가 보안상 중요하다. **싼 검사·DoS 방어를 앞에, 비싼 검증을 뒤에** 배치한다.

```python
# 0) 킬스위치(요청 013): 서버 비활성화 상태면 API 키 읽기 전에 즉시 503 차단
await ensure_server_enabled(db)

api_key, timestamp, nonce, signature = 헤더 4개
if not all(...): raise AuthenticationError      # 헤더 누락

# 1) API 키 — 해시 대조
service = Service where api_key_hash == sha256(api_key)
if service is None or service.status != ACTIVE: raise AuthenticationError

# 2) IP 화이트리스트
ip = get_client_ip(request, settings)
if ip not in service.allowed_ips: raise PermissionDeniedError("허용되지 않은 IP입니다")

# 3) 레이트리밋 — 서명 검증 '전'에 카운트(무효 요청도 throttle → DoS 완화)
window = int(time()//60); key = f"rl:{service.id}:{window}"
count = redis.incr(key); if count==1: redis.expire(key,90)
if count > rate_limit_per_minute: raise RateLimitedError

# 4) 타임스탬프 윈도우 (재전송 방어 1차)
if abs(now - int(timestamp)) > hmac_timestamp_tolerance_seconds(±300s): raise AuthenticationError

# 5) HMAC 서명 검증 (본문 무결성 포함)
body = await request.body()
secret = cipher.decrypt(service.hmac_secret_encrypted)
expected = sign_request(secret, method, path, timestamp, nonce, body)
if not constant_time_equals(expected, signature): raise AuthenticationError

# 6) nonce 1회용 (재전송 방어 2차) — 서명 통과 후에만 소비
if not redis.set(f"nonce:{service.id}:{nonce}", "1", nx=True, ex=600): raise AuthenticationError

return service       # 통과 → 인증된 Service 객체
```

단계별 의미:

- **0) 킬스위치 게이트** (`ensure_server_enabled`, 요청 013): `GlobalSettings.server_disabled=True`이면
  **API 키를 읽기 전에** `ServerDisabledError(503, code="SERVER_DISABLED", message=<사유>)`를 발생시킨다.
  `disabled_reason`이 없으면 "서비스 점검 중입니다" 기본 메시지가 반환된다. **어드민 라우트는 영향 없음**
  (어드민은 이 의존성을 사용하지 않음). 킬스위치 설정은 문서 13 참조.

- **1) API 키 = 해시 대조**: 들어온 키를 sha256해서 `api_key_hash`와 비교(문서 01). DB엔 평문 키가
  없으므로, 키 자체로 누가 호출했는지 식별한다. 서비스가 `INACTIVE`면 거부(문서 01의 상태 차단).
- **2) IP 화이트리스트**: 그 서비스에 등록된 `allowed_ips`(문서 01)에 발신 IP가 있어야 함.
  `get_client_ip`는 `trust_proxy=True`일 때만 `X-Forwarded-For`를 신뢰한다(아래 5절 주의).
- **3) 레이트리밋을 서명 검증보다 먼저**: 서명이 틀린 무효 요청도 일단 카운트해서 throttle한다.
  안 그러면 공격자가 잘못된 요청을 무한정 보내 비싼 서명 검증을 계속 돌리게 만들 수 있다(DoS).
  분당 카운터를 Redis `incr`로 세고, 한도(`rate_limit_per_minute`, 기본 120) 초과 시 429.
- **4) 타임스탬프 ±5분**: 오래된 요청(가로챈 요청의 재전송)을 1차로 거른다.
- **5) HMAC 서명 검증**: 본문을 읽어 기대 서명을 재계산하고 **상수시간 비교**(`constant_time_equals`,
  타이밍 공격 방지)로 대조. 본문이 변조됐거나 secret을 모르면 통과 불가.
- **6) nonce 1회용**: `redis.set(..., nx=True, ex=600)` — 같은 nonce가 이미 있으면 실패 → **재전송 차단**(2차).
  **서명 검증을 통과한 뒤에만** nonce를 저장한다. 안 그러면 서명 위조 요청이 Redis에 nonce 키를
  무한 적재(메모리 DoS)할 수 있다. 유효 요청의 재전송은 어차피 유효 서명을 동반하므로 방어력은 동일.

**재전송 방어가 2겹인 이유**: 타임스탬프(±5분)만으론 5분 내 같은 요청 재전송을 못 막는다.
nonce 1회용이 그 창을 닫는다. 타임스탬프는 nonce 저장량을 5분 윈도로 제한하는 역할도 한다.

---

## 4. 결제성 추가 제한 — `payment_rate_limit`

```python
async def payment_rate_limit(..., service=Depends(authenticate_service), ...):
    window = int(time()//60); key = f"rlp:{service.id}:{window}"
    count = redis.incr(key); if count==1: redis.expire(key,90)
    if count > rate_limit_payment_per_minute: raise RateLimitedError("결제 요청 한도를 초과했습니다")
    return service
```
- 먼저 `authenticate_service`(6단계)를 통과해야 하고, 그 위에 **결제 전용 분당 한도**
  (`rate_limit_payment_per_minute`, 기본 20)를 추가로 적용한다.
- 구독 생성·카드변경·수동결제·**단건 결제(`POST /api/v1/payments`)** 등 **돈이 움직이는 엔드포인트**에만 붙인다(일반 조회보다 훨씬 빡빡).

---

## 5. 클라이언트 IP 판별 — `get_client_ip` (주의)

```python
if settings.trust_proxy:
    xff = request.headers.get("x-forwarded-for")
    if xff: return xff.split(",")[0].strip()
return request.client.host
```
- `trust_proxy=True`는 **반드시 X-Forwarded-For를 덮어쓰는 신뢰된 리버스 프록시 뒤에서만** 켤 것.
  프록시가 XFF를 append만 하면 공격자가 맨 앞에 화이트리스트 IP를 끼워 넣어 IP 검사를 우회할 수 있다.
- 기본값 `False`(직접 연결의 소켓 IP 사용)가 안전한 기본.

---

## 6. 인증 통과 후 — 엔드포인트들

`authenticate_service`/`payment_rate_limit`은 **검증된 `Service` 객체를 반환**하고, 라우트는 그
`service.id`로 데이터를 스코프한다(자기 서비스 데이터만 접근).

### 무인증 엔드포인트 (예외)

- **`GET /api/v1/services`** — 등록된 서비스의 `id·name·status` 목록(이름 오름차순). **인증 헤더 불필요.**
  - 용도: 테스트 도구·샘플 서비스가 키를 입력하기 **전 단계**에서 서버 서비스를 식별·선택하기 위해 호출한다.
  - 응답 형식: `{"services": [{"id": "...", "name": "...", "status": "ACTIVE"}]}`
  - 민감정보 미포함: `api_key`, `hmac_secret`, 해시, 구독 등은 절대 반환하지 않는다.
  - 구현: `app/api/v1/services.py` — 인증 의존성 없음, `get_db`만 사용.

> 다른 모든 `/api/v1/*` 엔드포인트는 HMAC 3중 인증(`authenticate_service` 6단계)이 적용된다.
> 이 엔드포인트만 의도적으로 인증을 제외한 것이며, 공개해도 무방한 정보(id·이름·상태)만 노출한다.

---

조회 전용(인증만):
- `GET /api/v1/plans` — 그 서비스의 **ACTIVE 요금제** 목록(`only_active=True`). 외부 서비스가
  사용자에게 보여줄 요금제 노출용.
- `GET /api/v1/payments/{external_user_id}` — 그 서비스·그 사용자의 결제 이력 최근 50건.
- `GET /api/v1/subscriptions/{external_user_id}` — 최신 구독(`access_allowed` 확인, 문서 04).

결제성(추가 throttle): 구독 생성·카드변경·수동결제(문서 04·06) +
**단건 결제 `POST /api/v1/payments`**(문서 11) +
**단건 결제 취소 `POST /api/v1/payments/{order_id}/cancel`**(문서 11).
단건 결제 본문(금액 포함)은 HMAC 서명 대상에 body hash가 포함되므로, 전송 중 금액이 변조되면 서명 검증(5단계)에서 즉시 차단된다.
단건 결제 취소는 토스 환불 API 호출이 수반되므로 `payment_rate_limit` 대상이다.
구독 취소/재개는 결제가 없어 `authenticate_service`만 쓴다.

> 모든 엔드포인트가 `service.id`로 격리되므로, A 서비스 키로 B 서비스의 구독을 조회/조작할 수 없다.

---

## 7. 에러 응답 형식 (`api/errors.py`)

도메인 예외(`DomainError` 계열, `core/errors.py`)가 일관된 JSON으로 변환된다:

```json
{ "error": { "code": "UNAUTHORIZED", "message": "인증에 실패했습니다" } }
```

| 예외 | code | HTTP |
|---|---|---|
| `AuthenticationError` | UNAUTHORIZED | 401 |
| `PermissionDeniedError` | FORBIDDEN | 403 |
| `InputValidationError` | VALIDATION_ERROR | 422 |
| `RateLimitedError` | RATE_LIMITED | 429 |
| `PaymentFailedError` | PAYMENT_FAILED | 402 |
| `NotFoundError` | NOT_FOUND | 404 |
| `ConflictError` | CONFLICT | 409 |
| `ServerDisabledError` | SERVER_DISABLED | 503 |

- **인증 실패는 대부분 동일한 `AuthenticationError`("인증에 실패했습니다")** 로 응답한다(키/타임스탬프/
  서명/nonce 어디서 막혔는지 노출하지 않음 — 정보 누출 최소화). IP만 예외적으로 403.
- 스키마 검증 실패(Pydantic) → 422 `VALIDATION_ERROR`.
- 처리되지 않은 예외 → 500 `INTERNAL_ERROR`(내부 정보 비노출, 로그만 남김).

---

## 8. 설정값 (`core/config.py`)

| 설정 | 기본 | 의미 |
|---|---|---|
| `hmac_timestamp_tolerance_seconds` | 300 | 타임스탬프 허용 오차(±5분) |
| `rate_limit_per_minute` | 120 | 서비스당 분당 일반 호출 한도 |
| `rate_limit_payment_per_minute` | 20 | 서비스당 분당 결제성 호출 한도 |
| `trust_proxy` | False | XFF 신뢰 여부(프록시 뒤에서만 True) |
| `webhook_ip_check_enabled` / `toss_webhook_allowed_ips` | True / 토스 IP | 웹훅 IP 화이트리스트(문서 07) |

키/secret 자체는 서비스별로 DB에 저장(해시+암호문, 문서 01). nonce·레이트리밋 카운터는 Redis.

---

## 9. 다층 방어 요약

```
요청 → [킬스위치 게이트] → [헤더 존재] → [API키 해시] → [IP 화이트리스트] → [레이트리밋]
     → [타임스탬프 ±5분] → [HMAC 서명(본문무결성)] → [nonce 1회용] → 인증된 Service
              (결제성이면 +[결제 레이트리밋])
```
각 층이 다른 위협을 막는다: 킬스위치=운영 차단, 키=식별, IP=발신 제한, 레이트리밋=DoS, 타임스탬프+nonce=재전송,
서명=위조·변조. 하나가 뚫려도 나머지가 막는 **심층 방어(defense in depth)**.

### Admin 접속 IP 제한 (요청 013)

외부 API의 서비스별 `allowed_ips`(문서 01)와 별도로, **어드민 콘솔 접속 IP**를 전역으로 제한할 수
있다. `require_user`(`app/admin/deps.py`)에서 `GlobalSettings.admin_allowed_ips`를 확인한다:

```python
gs = await get_global_settings(db)
if gs.admin_allowed_ips and get_client_ip(request, settings) not in gs.admin_allowed_ips:
    raise PermissionDeniedError("허용되지 않은 IP입니다")
```

- `admin_allowed_ips=[]`(기본): 제한 없음.
- IP가 1개 이상 등록되면 목록에 없는 IP는 모든 어드민 요청에서 403으로 차단된다.
- **lockout 방지**: IP 목록 변경 시 현재 접속 IP가 목록에 없으면 저장 거부(문서 13).
- `get_client_ip` 함수는 외부 API 게이트와 동일한 함수를 공유한다(`trust_proxy` 설정 동일 적용).

---

## 10. 관련 테스트

- `tests/security/` — HMAC 서명 검증, 본문 변조 탐지, 타임스탬프 만료, nonce 재사용 차단,
  IP 화이트리스트, 레이트리밋(일반/결제), 비활성 서비스 거부, 개행 주입 거부.
- `tests/integration/test_api_auth.py` — 인증 통과/실패 경로, 에러 코드/상태.
- `tests/integration/test_api_endpoints.py` — 인증 후 조회/스코프 격리.

---

## 11. 유지보수 체크리스트

1. **단계 순서를 바꾸지 말 것**: 킬스위치 게이트는 가장 앞(모든 외부 요청 차단), 레이트리밋은
   서명 검증보다 앞(무효 요청 throttle), nonce 소비는 서명 검증보다 뒤(메모리 DoS 방지). 순서가 보안 속성이다.
2. **새 결제성 엔드포인트**(구독 생성·카드변경·수동결제·단건 결제·**단건 결제 취소** 등 토스 API 호출이 수반되는 모든 경로)는 `Depends(payment_rate_limit)`, 일반 조회는 `Depends(authenticate_service)`. **인증을 생략하려면 노출 정보가 민감하지 않은지 반드시 확인**(`GET /api/v1/services`가 유일한 무인증 예외).
3. **서명 규약(canonical string) 변경은 클라이언트와 동시에**. 바꾸면 모든 외부 서버의 서명이 깨진다.
   변경 시 README의 클라이언트 예제도 함께 갱신.
4. **인증 실패 메시지를 세분화하지 말 것**(정보 누출). 어디서 막혔는지는 로그로만.
5. **`trust_proxy`는 신뢰된 프록시 환경에서만 True**. 잘못 켜면 IP 화이트리스트가 무력화된다.
6. **모든 데이터 접근은 `service.id`로 스코프**. 새 엔드포인트에서 이 격리를 빠뜨리면 교차 서비스
   데이터 노출이 된다.
7. 비교는 항상 `constant_time_equals`(서명·토큰). 일반 `==` 사용 금지(타이밍 공격).
