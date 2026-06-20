# 구독/결제 서버 종합 코드 감사 리포트

- **일자**: 2026-06-11
- **대상**: 전체 코드베이스 (`app/` 약 9,700 LOC + 테스트 + 인프라 설정)
- **범위**: 보안 / 성능 / 구조·유지보수성
- **방법**: 영역별 독립 분석(코드 직접 확인, file:line 근거 기반). 추측성 항목은 배제하고 검증된 사항만 수록.

---

## 종합 평가 (Executive Summary)

| 영역 | 평가 | Critical | High | Medium | Low |
|---|---|---|---|---|---|
| 보안 | **양호** — 3중 인증, 암호화, CSRF/XSS 방어 등 기본기 탄탄 | 0 | 0 | 5 | 6 |
| 성능 | **주의** — 토스 지연 시 전면 장애 가능, 배치 확장성 한계 | 0 | 3 | 5 | 3 |
| 구조 | **우수** — 계층 규율·멱등성·테스트 안전망 단단 | 0 | 0 | 6 | 8 |

**가장 시급한 3가지:**

1. **[성능 H1] 토스 API 호출 중 DB 행 잠금(FOR UPDATE) + 커넥션 점유 (최대 65초)** — 토스가 느려지면 커넥션 풀(기본 15개)이 고갈되어 결제와 무관한 API·어드민까지 전면 중단될 수 있음.
2. **[성능 H2] 갱신 배치 직렬 처리** — 구독 1만 건이면 배치가 수 시간 소요, 전역 락 TTL(240초) 만료로 배치 중첩 실행 가능.
3. **[보안 M-5] `TRUST_PROXY=true` 시 X-Forwarded-For 위조로 IP 화이트리스트·웹훅 IP 검증 우회 가능** — prod 권장 설정이 이 모드이므로 프록시의 XFF 덮어쓰기 보장을 즉시 확인해야 함.

---

# 1. 보안 감사

Critical 없음. 전반적으로 보안 설계 수준이 높으며, 아래 Medium 5건 / Low 6건이 확인됨.

## 1.1 Medium

### M-1. `order_id` 전역 유니크 → 타 서비스 주문번호 선점(스쿼팅)·존재 탐지

- **위치**: `app/models/payment.py:33`, `app/services/payments.py:64-67, 89-93`
- **근거**:
  ```python
  # models/payment.py:33
  order_id: Mapped[str] = mapped_column(String(64), unique=True)  # 전체 고유
  # services/payments.py:64-67
  existing = await db.scalar(select(Payment).where(Payment.order_id == order_id))
  if existing is not None:
      if existing.service_id != service.id:
          raise ConflictError("이미 사용된 주문번호입니다")
  ```
- **영향**: order_id가 서비스별이 아닌 **전역** 유니크라서 (1) 악의적/오작동 서비스 A가 서비스 B의 order_id를 선점해 B의 결제를 차단(테넌트 간 DoS)할 수 있고, (2) 409 응답 차이로 타 서비스 주문번호 존재 여부를 탐지할 수 있음.
- **권고**: order_id에 서비스별 접두사 강제(`{service_code}-{order_id}`) 또는 `(service_id, order_id)` 복합 유니크로 변경. 토스 전달 시에만 전역 고유 문자열로 변환.

### M-2. 어드민 로그인 엔드포인트에 IP/전역 처리율 제한 없음

- **위치**: `app/admin/routes/auth.py:37-68`, `app/services/auth.py:43-44, 101-134`
- **근거**: 외부 API(`app/api/deps.py:105-112`)는 Redis 카운터로 throttle하지만 `/admin/login` POST에는 rate limit이 없음. 방어는 계정당 5회 실패 → 15분 잠금뿐이며, **존재하지 않는 이메일**에 대한 시도는 무제한(매 시도마다 `auth.login_failed` 감사 행 적재 → 감사 로그 팽창 DoS 가능, `auth.py:104-107`).
- **영향**: 이메일을 바꿔가며 패스워드 스프레이 공격 가능, 감사 테이블 무한 증가.
- **권고**: 로그인 엔드포인트에 IP 기준 Redis 카운터(예: 분당 10회) 추가. unknown_email 감사 기록은 샘플링 또는 별도 카운터로 대체.

### M-3. 보안 응답 헤더 부재 (CSP / X-Frame-Options / HSTS)

- **위치**: `app/main.py:241-258` (앱 전체에 보안 헤더 미들웨어 없음)
- **영향**: 어드민 화면 클릭재킹, XSS 발생 시 피해 확대(완화 계층 부재).
- **권고**: 응답 헤더 미들웨어 추가. 최소 `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, prod에서 HSTS.

### M-4. 개발 인프라 기본 자격증명 + 무인증 Redis 포트 노출

- **위치**: `docker-compose.yml:4-9, 13-16`, `alembic.ini:89`
- **근거**:
  ```yaml
  POSTGRES_USER: payment / POSTGRES_PASSWORD: payment
  ports: - "5433:5432"        # postgres
  ports: - "6380:6379"        # redis — requirepass 없음
  ```
  `alembic.ini:89`에도 `postgresql+asyncpg://payment:payment@localhost:5433/payment` 하드코딩.
- **영향**: 외부 접근 가능한 호스트에서 그대로 쓰이면 DB(결제·빌링키 암호문)와 Redis(**세션 저장소** — 세션 위조로 어드민 탈취 가능)가 무방비 노출. Docker 포트 publish는 기본 0.0.0.0 바인딩.
- **권고**: `127.0.0.1:5433:5432` 루프백 바인딩, Redis `requirepass` 설정, prod 별도 자격증명(개발 전용임을 compose에 명시).

### M-5. `trust_proxy` 시 X-Forwarded-For 첫 항목 신뢰 — IP 화이트리스트 전면 우회 위험

- **위치**: `app/api/deps.py:70-74`
- **근거**:
  ```python
  if settings.trust_proxy:
      forwarded = request.headers.get("x-forwarded-for")
      if forwarded:
          return forwarded.split(",")[0].strip()   # 가장 왼쪽 = 클라이언트 조작 가능
  ```
  이 값이 서비스 IP 화이트리스트(`deps.py:101-103`), 어드민 접속 IP 제한(`admin/deps.py:80`), **토스 웹훅 IP 검증**(`api/v1/webhooks.py:52-55`) 세 곳 모두에 사용됨. 문서(`docs/dev_manual/01-getting-started.md:133`)는 prod에서 `TRUST_PROXY=true` 권장.
- **영향**: 프록시가 XFF를 덮어쓰지 않고 append만 하면 공격자가 `X-Forwarded-For: <화이트리스트IP>` 헤더로 IP 계층과 웹훅 IP 검증(웹훅은 IP가 유일한 인증 수단)을 모두 우회.
- **권고**: 프록시 hop 수 기반으로 **오른쪽에서 n번째**를 취하거나 신뢰 프록시 CIDR 검증 추가. 최소한 prod 기동 시 경고 로그.

## 1.2 Low

| # | 제목 | 위치 | 요지 |
|---|---|---|---|
| L-1 | 무인증 서비스 목록 공개 | `app/api/v1/services.py:16-29` | `GET /api/v1/services`가 인증 없이 전체 서비스 UUID·이름·상태 반환. 의도된 설계이나 운영에서는 인증 뒤로 이동 권장 |
| L-2 | 토스 웹훅 서명 검증 없음 (IP 단일 계층) | `app/api/v1/webhooks.py:52-55`, `app/services/webhooks.py:118-152` | `PAYMENT_STATUS_CHANGED`는 토스 재조회로 불신 처리(잘 됨)되나, `BILLING_DELETED`는 페이로드를 사용해 메일 발송 — M-5와 결합 시 위조 메일 가능 |
| L-3 | 단건 결제 `amount` 상한 없음 | `app/schemas/api.py:165-166` | `gt=0`만 검증. 비정상 고액 요청이 토스까지 전달됨 — 상한 설정 권장 |
| L-4 | 로그인 폼 CSRF 토큰 없음 (Login CSRF) | `app/admin/routes/auth.py:37-68` | 인증 후 POST는 전부 검증되나 로그인 자체는 미적용. SameSite=Lax로 일부 완화 — 사전 세션 기반 토큰 권장 |
| L-5 | 세션 절대 만료 없음 (sliding TTL만) | `app/services/auth.py:146-158` | 활동 중 세션 무한 연장 — 탈취 세션 영구 유효 가능. 절대 수명(예: 12시간) 권장 |
| L-6 | 로그인 비밀번호 HTML 프리필 (dev 편의) | `app/core/config.py:92-94`, `templates/login.html:15` | `environment != "prod"` 가드는 있으나 `APP_ENV` 기본값이 "dev"라 설정 누락 시 prod 노출 — 배포 체크리스트에 환경변수 검증 추가 |

## 1.3 잘 된 점 (유지할 것)

- **비밀번호**: Argon2id 해시, 최소 10자, 계정 잠금(5회/15분), 더미 해시로 계정 열거 타이밍 방어 (`core/security.py`, `services/auth.py:52,103`)
- **외부 API 3중 인증**: API키(SHA-256 해시 저장) + IP 화이트리스트 + HMAC-SHA256 본문 서명. 타임스탬프 윈도우 + nonce 1회용(서명 검증 **후** 소비 — Redis 적재 DoS까지 고려), `hmac.compare_digest` 상수시간 비교, canonical string 개행 주입 거부
- **비밀값 보관**: 빌링키·HMAC secret AES-256-GCM 암호화, 빌링키 해시 조회, 단건 결제 빌링키 즉시 삭제, 구독 종료 시 빌링키 말소, 소스 내 하드코딩 시크릿 없음
- **테넌트 격리(IDOR)**: 모든 외부 API 조회가 `service.id` 스코프 제한, 어드민도 `service_scope`/`_can_manage` 일관 적용 + 비담당 리소스는 403 대신 **404**(존재 비노출)
- **SQL 주입**: raw SQL/f-string 쿼리 전무, 정렬 컬럼 allowlist 매핑
- **XSS/CSRF**: Jinja2 autoescape, `|safe` 사용 0건, 어드민 POST 전체에 세션 바인딩 CSRF 토큰 + 상수시간 비교
- **세션**: 로그인마다 신규 무작위 세션 ID(고정 공격 불가), HttpOnly + SameSite=Lax + prod Secure, 비밀번호 변경 시 전체 세션 파기
- **엑셀 수식 주입 방어** (`admin/export.py:13-20`), **웹훅 멱등 처리 + 페이로드 불신(토스 재조회)**, catch-all 핸들러의 내부정보 비노출, Swagger 기본 비활성화 + Basic 보호

---

# 2. 성능 감사

블로킹 I/O(동기 HTTP, `time.sleep`)는 없음 — 토스는 async httpx, SMTP는 `asyncio.to_thread`로 처리됨. 아래는 확장 시 문제가 되는 항목.

## 2.1 High

### H1. 외부(토스) API 호출 동안 DB 행 잠금(FOR UPDATE) + 커넥션 점유 — 최대 65초

- **위치**: `app/services/renewals.py:289→327` (갱신 재시도 경로), `app/services/reconciliation.py:98→107` (정산 스윕 — 항상 해당), `app/services/subscriptions.py:160~180` (구독 생성 — 읽기 트랜잭션이 열린 채 빌링키 발급)
- **근거**:
  ```python
  # reconciliation.py:98,107 — 항상 FOR UPDATE 잠금 상태에서 토스 재조회
  payment = await db.get(Payment, payment_id, with_for_update=True)
  ...
  found = await toss.get_payment_by_order_id(payment.order_id)
  ```
  `HttpTossClient`의 read timeout은 65초(`app/toss/client.py:56`). 갱신의 신규 Payment 경로는 commit으로 락이 풀리지만, **PENDING이 이미 존재하는 재시도 경로**와 정산 스윕은 FOR UPDATE 행 잠금과 풀 커넥션을 토스 응답까지 쥔 채 대기.
- **스케일 영향**: 기본 풀 15커넥션(M3)이므로, 토스가 느려지는 순간 동시 결제/정산 몇 건만으로 풀이 고갈되어 **결제와 무관한 API·어드민 전체가 멈춤**.
- **권고**: FOR UPDATE 조회 → 필요한 값 추출 → `commit()`으로 커넥션 반납 → 외부 호출 → 새 트랜잭션에서 재검증 후 결과 기록. 동시성 방어는 이미 있는 Redis 락 + 결정적 order_id/멱등키가 담당.

### H2. 갱신 배치가 구독을 1건씩 직렬 처리 — 1만 건이면 배치가 사실상 끝나지 않음

- **위치**: `app/services/renewals.py:139~167` (4개 for 루프), `app/scheduler/runner.py:25, 58~60`
- **근거**: `_renew_one` 1건 = Redis 락 + 세션 생성 + SELECT 4~5회 + 토스 charge(정상 수백 ms~수 초, 최악 65초) + commit + 실패 시 동기 이메일 발송(`renewals.py:414,430`, SMTP timeout 15초). 동시성 제어(`asyncio.gather`/세마포어) 전혀 없음.
- **스케일 영향**: 건당 1초만 잡아도 10,000건 ≈ 2.8시간. 전역 락 TTL 240초(`runner.py:25`)가 배치 도중 만료되어 다중 인스턴스에서 **배치 중첩 실행**(구독별 락+멱등키가 2차 방어지만 모든 인스턴스가 같은 ID 목록을 재순회하며 락 경합). 5분 간격이라 due 적체가 해소되지 않고 갱신이 수 시간~수 일 지연.
- **권고**: (1) `asyncio.Semaphore(N)` + `gather`로 병렬도 10~50 부여, (2) ID 목록 청크 단위 + 배치당 처리 상한, (3) 전역 락 TTL heartbeat 갱신 또는 `SKIP LOCKED` 큐 전환, (4) 이메일 발송은 `asyncio.create_task` 또는 큐로 분리.

### H3. 대시보드가 구독 테이블 전체를 메모리 적재 + O(42×N) 파이썬 루프

- **위치**: `app/services/dashboard.py:194~202, 248~264, 267~287`
- **근거**: `select(Subscription.status, created_at, current_period_end)`를 LIMIT 없이 전체 적재 후, 12개월 시리즈(12회)와 30일 트렌드(30회)가 각각 전 행 재순회(`_open_count_at`) — 대시보드 1회 로드에 **42 × 전체 구독 수** 만큼 파이썬 반복이 이벤트 루프에서 실행.
- **스케일 영향**: 구독 10만 건이면 행 10만 fetch + 420만 회 루프가 대시보드 열 때마다 발생. 이벤트 루프를 CPU로 점유해 같은 워커의 다른 요청까지 지연. EXPIRED 누적과 함께 악화.
- **권고**: `date_trunc` + `generate_series` 기반 DB 집계(GROUP BY) 전환, 또는 최소한 Redis 짧은 TTL(5분) 캐시.

## 2.2 Medium

### M1. 핵심 조회 컬럼 인덱스 누락

| 테이블.컬럼 | 사용처 | 현재 |
|---|---|---|
| `payments.status, requested_at` | 정산 스윕(`reconciliation.py:54~55`, **5분마다 실행**), 결제목록 기본 정렬, 대시보드 집계 | 없음 → 풀스캔 |
| `payments.approved_at` | 대시보드 매출(`dashboard.py:117`), 정산(`settlement.py:72~74`) | 없음 |
| `audit_logs.created_at` | 감사 목록 정렬(`audit.py:142`), 대시보드 | 없음 (`action`만 인덱스) |
| `audit_logs.target_id` | `dashboard.py:109`, `services.py:318~325` | 없음 + 문자열 cast 비교 |
| `subscriptions.service_id` | 어드민 목록/대시보드 스코프 필터 전반 | 단독 인덱스 없음 (부분 유니크는 EXPIRED 미커버) |
| `subscriptions.current_period_end` | 배치 due 조회(`renewals.py:121~138`), 만료임박 레일 | 없음 |

- **권고**: `payments(status, requested_at)`, `payments(service_id, approved_at)`, `audit_logs(created_at)`, `audit_logs(target_type, target_id)`, `subscriptions(service_id)`, `subscriptions(status, current_period_end)` 추가 — 마이그레이션 1개로 배치·어드민·대시보드 전반 개선.

### M2. 엑셀 내보내기 — 필터 결과 전체를 무제한 메모리 적재

- **위치**: `app/admin/routes/payments.py:101~107`, `subscriptions.py:95~99`, `audit.py:160~162`, `services.py:120,138~143`, `app/admin/export.py:29~40`
- **영향**: 결제 수십만 건 시 요청 1건이 수백 MB 점유, 동시 다운로드 2~3건이면 워커 OOM 가능.
- **권고**: 행 한도(예: 10만 건) 또는 `stream_results`/`yield_per` + `StreamingResponse`(CSV).

### M3. DB 커넥션 풀 미설정 (기본 5+10=15)

- **위치**: `app/core/db.py:17` — `pool_size`/`max_overflow`/`pool_timeout`/`pool_recycle` 전부 기본값. H1과 결합 시 토스 지연 시 동시 처리량이 15로 캡핑. 워크로드에 맞게 명시 설정 필요.

### M4. 외부 API 요청마다 GlobalSettings DB 조회 — Redis 캐싱 미활용

- **위치**: `app/api/deps.py:86` → `app/services/app_settings.py:24`
- **근거**: 매 요청 `db.get(GlobalSettings, 1)` + Service 행 조회 + HMAC 시크릿 AES 복호화 수행. Redis는 락/레이트리밋/nonce에만 사용되고 캐시 용도는 전무.
- **권고**: 킬스위치 플래그는 Redis 5~10초 TTL 캐시(변경 시 무효화)로 충분.

### M5. 대시보드 1회 로드에 직렬 쿼리 ~25회 + 서비스별 상관 서브쿼리

- **위치**: `app/services/dashboard.py:413~454`, `:290~349`
- **근거**: 모든 집계 쿼리가 `await`로 직렬 실행. `_service_revenue`/`_service_subs`는 서비스 행마다 4~6개 상관 스칼라 서브쿼리. H3과 함께 대시보드가 가장 무거운 페이지. 캐싱 또는 단일 GROUP BY 통합 권고.

## 2.3 Low

- **L1**: 감사 로그 검색이 JSONB 전체 cast ILIKE(`audit.py:104`) — 항상 풀스캔. 빈도 높아지면 pg_trgm GIN 검토.
- **L2**: 목록 count가 조인 포함 서브쿼리 전체를 카운트(`pagination.py:118`) — 필터 미사용 시 조인 없는 count로 절감 가능.
- **L3**: 갱신 배치 due ID 목록 무제한 적재(`renewals.py:121~138`) — ID만 가져와 메모리는 가볍지만 상한 없음, H2와 결합 시 배치 무한 연장.

## 2.4 잘 된 점

- 블로킹 I/O 없음(async httpx + 명시 타임아웃, SMTP `asyncio.to_thread`, AsyncIOScheduler)
- **결제 3원칙 설계 견고**: PENDING 선커밋 → 외부 호출 → 확정 패턴(주요 경로), 결정적 order_id + 토스 멱등키로 크래시·중복 안전
- 스케줄러 핵심 due 조회 인덱스 선반영(`ix_subscriptions_due`)
- 배치 건별 신규 세션 — 1건 실패가 배치를 죽이지 않음
- 분산 락 구현 올바름(SET NX + 토큰 + Lua 비교 삭제)
- 어드민 목록 페이지네이션 일관 적용, 각종 조회 limit 상한 습관 양호
- 정산은 단일 GROUP BY 집계(모범적), 감사 로그 이름 해석 배치 조회로 화면 N+1 없음
- `pool_pre_ping=True`, `expire_on_commit=False` 등 async 세션 기본기 충실

---

# 3. 구조·유지보수성 감사

치명적 구조 결함 없음. **단일 개발자 유지보수에 매우 잘 맞는 구조**이며, 아래는 점진적 개선 항목.

## 3.1 아키텍처 강점 (유지할 것)

1. **계층 방향성 깨끗** — services/api/core/models 어디에서도 `app.admin`을 import하지 않고, services는 `app.api`를 import하지 않음. 순환 import 없음.
2. **트랜잭션 소유권 일관** — commit은 서비스 레이어 소유. "2단계 commit(결제 전 슬롯 선점 → 결제 후 확정)" 패턴이 주석으로 문서화(`subscriptions.py:148-150`).
3. **에러 처리 단일 경로** — `core/errors.py`(DomainError 계층) + `api/errors.py`(핸들러 3개)로 중앙화.
4. **돈은 전부 int KRW** — float/Decimal 혼용 0건. 내림 나눗셈 정책·수수료 공식이 `billing_math.py`에 집중.
5. **Toss 주입 시임 깨끗** — `create_app(toss_client=...)` 생성자 주입, `FakeTossClient`가 멱등키 재생·타임아웃 후 승인 시나리오까지 재현 — 결제 시스템 테스트 fake로 모범 수준.
6. **스키마가 ORM 비누출** — 모든 응답이 명시 필드 + `from_model` 변환, 민감 필드 비노출 원칙 docstring 명시.
7. **멱등성/이중결제 방어** — 결정적 order_id, 타임아웃 시 PENDING 유지 정책이 코드·주석·테스트로 삼중 문서화.
8. **모든 변경에 감사 로그 + 한국어 docstring** — 인수인계 자산.

## 3.2 Medium

### S1. 구독 상태 전이가 14곳에 분산 — 중앙 전이 함수 부재

- **근거**: `sub.status = SubscriptionStatus.X` 직접 대입이 `subscriptions.py` 6곳(258, 295, 368, 448, 451, 519), `renewals.py` 4곳(89, 196, 406, 424), `reconciliation.py:147`에 분산.
- **문제**: 상태 머신 규칙("EXPIRED는 종단" 등)이 호출부 if문과 주석에만 존재. 잘못된 전이를 어디서도 막지 못하고, `next_billing_at`·`retry_count` 동기화도 전이마다 수기 반복.
- **제안**: 허용 전이 테이블 + `transition(sub, new_status, *, now)` 헬퍼 1개를 추가하고 기존 대입을 한 곳씩 교체. 기존 통합 테스트가 회귀망 역할.

### S2. 구독 필터 쿼리 3중 중복

- **근거**: 동일한 `external_user_id ilike + status` 필터 빌드가 `admin/routes/subscriptions.py:42-81`, `services.py:111-120`, `services.py:284-289`에 복붙. `services.py:20`이 `subscriptions.py`에서 상수를 import해 라우트 간 수평 결합.
- **문제**: 검색 조건 추가 시 3곳 수정 필요 — 누락 시 목록·엑셀·탭 결과가 미묘하게 어긋나는 발견 어려운 버그.
- **제안**: 기존 공유 모듈 `app/admin/filters.py`로 `subscription_query(...)` + `SUB_SORT` 이동.

### S3. count 쿼리 보일러플레이트 11회 반복

- **근거**: `select(func.count()).select_from(base.order_by(None).subquery())` 패턴이 admin 라우트 11곳에 반복.
- **제안**: `paginate(db, base_q, pp, sort_map)`로 count를 내부 생성(기존 시그니처 당분간 유지하며 점진 이행).

### S4. 라우트에 스며든 비즈니스 규칙 — "대표 담당자 해제 불가"

- **근거**: `admin/routes/services.py:462-475`가 도메인 규칙을 라우트에서 직접 검사(주석으로 자인).
- **문제**: 다른 진입점(향후 API, CLI)에서 규칙이 자동 적용되지 않음.
- **제안**: 검사를 `accounts.unassign_service`로 내리고 `ConflictError` 발생 — 기존 `DomainError → ?error=` 패턴으로 UX 손실 없음.

### S5. enum이 있는데 문자열 리터럴로 상태 비교 (혼용)

- **근거**: `payments.py:163` `payment.status == "DONE"`, `subscriptions.py:163`, `payments.py:104` 등 — 같은 파일이 다른 곳에서는 enum 사용.
- **제안**: 기계적 전수 치환(저위험, 10분).

### S6. `admin/routes/services.py` 580줄 — 책임 5개 동거

- **근거**: 목록/검색 + 엑셀 4종 + 키 발급·회전 + 담당자 관리 3종 + 상세 탭 3종이 한 파일. 500줄 초과는 이 파일뿐이지만 증가 추세.
- **제안**: 동일 prefix 유지하며 라우터만 분리 — `services_export.py`, `services_managers.py`. URL·템플릿 무변경, e2e 그대로 통과하는 무위험 이동.

## 3.3 Low

| # | 제목 | 위치 | 제안 |
|---|---|---|---|
| S7 | `locks.py` private 이름(`_acquire_lock` 등)을 타 모듈이 import | `renewals.py:35-39`, `reconciliation.py` | 밑줄 제거 rename(파일 3개 기계적) |
| S8 | 라우트 직접 commit 예외 1건 (keys-modal) | `services.py:256-260` | `registry.reveal_keys(...)`로 복호화+감사+commit 이동 |
| S9 | 취소 정책 폼 파싱 중복 2회 | `services.py:208-214, 533-541` | `_parse_cancel_policy(form)` 헬퍼 추출 |
| S10 | services 레이어에 표시용 데이터 생성(StatCard tint/포맷) | `dashboard.py:33-39, 85` | 단일 소비자라 실해 적음 — 파일 더 커지면 집계/조립 섹션 분리 |
| S11 | `registry.py` 이름이 역할(서비스 등록·키 관리) 오도 | `services/registry.py:1` | `service_registry.py` 또는 `tenants.py` rename(시급하지 않음) |
| S12 | conftest.py에 140줄 HTML 리포트 생성기 동거 | `tests/conftest.py:119-262` | `tests/_report_plugin.py`로 분리 |
| S13 | admin이 `app.api.deps`에 의존 (공통 인프라 의존성이 api 레이어에 위치) | `admin/deps.py:22` 외 9개 파일 | `get_db` 등을 `app/core/deps.py`로 이동, api/deps는 재export로 호환 유지 |
| S14 | 인라인 매직 넘버 잔존 (rate-limit TTL 90, nonce TTL 600) | `api/deps.py:110, 135` | 모듈 상단 명명 상수로 승격 |

## 3.4 테스트 구조 평가

| 영역 | 상태 |
|---|---|
| unit | `test_billing_math.py`, `test_toss_client.py` 2개뿐 — 순수 함수만 단위 테스트 |
| integration | 갱신 상태머신(539줄), 구독 생성/관리, 결제취소, 웹훅, 정산, 인증 등 폭넓음 |
| e2e | admin 화면 전반, htmx partial, 엑셀 내보내기까지 커버 |
| security | HMAC 인증 전용 테스트 존재 |

- 서비스가 `AsyncSession`을 직접 받아 DB 없는 단위 테스트는 불가 — 대신 실 Postgres/Redis + FakeToss 통합 테스트가 1차 방어선. **결제 도메인 특성상 합리적 선택, 바꿀 필요 없음.**
- 상대적 갭: `pagination.py`의 PageParams 파싱(순수 로직인데 단위 테스트 없음), 대시보드 시계열 경계 케이스, 라벨 매핑 모듈(저위험).

---

# 4. 통합 권장 착수 순서

## Phase 1 — 운영 안정성 (장애 방지, 시급)

| 순서 | 항목 | 출처 | 작업량 |
|---|---|---|---|
| 1 | 토스 호출 전 트랜잭션 분리 (FOR UPDATE 락 해제 후 외부 호출) | 성능 H1 | 중 |
| 2 | DB 커넥션 풀 명시 설정 | 성능 M3 | 소 |
| 3 | 갱신 배치 병렬화(Semaphore) + 청크 상한 + 전역 락 heartbeat | 성능 H2 | 중 |
| 4 | prod 프록시의 XFF 덮어쓰기 보장 확인 + 코드 방어(오른쪽 n번째/CIDR) | 보안 M-5 | 소~중 |

## Phase 2 — 보안 보강 (운영 전 체크리스트)

| 순서 | 항목 | 출처 | 작업량 |
|---|---|---|---|
| 5 | order_id 서비스별 스코프 분리 | 보안 M-1 | 중 |
| 6 | 어드민 로그인 IP rate limit | 보안 M-2 | 소 |
| 7 | 보안 응답 헤더 미들웨어 | 보안 M-3 | 소 |
| 8 | docker-compose 루프백 바인딩 + Redis requirepass | 보안 M-4 | 소 |
| 9 | Low 항목 일괄(세션 절대 만료, amount 상한, 서비스 목록 인증 등) | 보안 L-1~6 | 소 |

## Phase 3 — 확장성·성능

| 순서 | 항목 | 출처 | 작업량 |
|---|---|---|---|
| 10 | 인덱스 6종 추가 (마이그레이션 1개) | 성능 M1 | 소 |
| 11 | 대시보드 DB 집계(GROUP BY) 전환 + Redis 캐시 | 성능 H3/M5 | 중 |
| 12 | 엑셀 export 행 한도/스트리밍, 킬스위치 Redis 캐시 | 성능 M2/M4 | 소 |

## Phase 4 — 유지보수성 (코드를 건드릴 때 점진 처리)

| 순서 | 항목 | 출처 | 작업량 |
|---|---|---|---|
| 13 | 문자열 리터럴 → enum 전수 치환 | 구조 S5 | 소(10분) |
| 14 | locks.py 밑줄 rename | 구조 S7 | 소 |
| 15 | 구독 쿼리 중복 → filters.py 통합 | 구조 S2 | 소~중 |
| 16 | paginate 시그니처 개선(11곳 점진 이행) | 구조 S3 | 중 |
| 17 | 대표 담당자 규칙 서비스 레이어로 이동 | 구조 S4 | 소 |
| 18 | **구독 상태 전이 헬퍼 중앙화** (가장 가치 큼, 가장 신중히) | 구조 S1 | 중 |
| 19 | services.py 라우터 분리, 나머지 Low | 구조 S6 외 | 해당 파일 작업 시 |

---

## 결론

- **보안**: Critical 없음. 3중 인증·암호화·테넌트 격리 등 기본기가 탄탄하며, prod 배포 전 M-5(XFF)와 M-1(order_id 스코프)만 반드시 정리하면 됨.
- **성능**: 현재 규모에서는 문제없으나, **토스 지연 시 전면 장애 가능성(H1+M3)** 과 **배치 확장성 한계(H2)** 는 트래픽 증가 전에 선제 해결 필요.
- **구조**: 1인 유지보수 기준 상태가 좋음. 핵심 투자처는 **상태 전이 중앙화(S1)** 와 **admin 쿼리 중복 제거(S2, S3)**.
