# Phase 2 수정 작업 내역 (보안 보강)

- **일자**: 2026-06-11
- **근거**: [2026-06-11-code-audit.md](2026-06-11-code-audit.md) — Phase 2 (보안 보강)
- **선행**: [Phase 1 워크로그](2026-06-11-phase1-worklog.md) (운영 안정성)
- **검증**: 전체 테스트 `495 passed` (Phase 1 종료 시 490 → 신규 5 + 기존 2건 신정책 반영 갱신, 실패 0)
- **대상 항목**: 보안 M-1·M-2·M-3·M-4, L-1·L-3·L-5 (L-2·L-4·L-6은 보류 — 하단 참조)

---

## 작업 1. order_id 테넌트 스코프 분리 (보안 M-1) ✅ — DB 마이그레이션 포함

**문제**: `payments.order_id`가 전역 유니크라서 ① 서비스 A가 B의 주문번호를 선점(스쿼팅)해 B의 결제를 차단할 수 있고(테넌트 간 DoS), ② 409 응답 차이로 타 서비스 주문번호 존재를 탐지할 수 있었음. 추가 발견: 토스 멱등키도 클라이언트 order_id를 그대로 사용해, 서비스 간 멱등키 충돌 시 **타 서비스의 결제 응답이 재생될 수 있는** 문제도 함께 존재했음.

**설계**: 시스템 전체가 토스 계정 하나를 공유하므로 토스 측 orderId는 여전히 전역 고유여야 함. 따라서:
- 클라이언트 `order_id` → **(service_id, order_id) 복합 유니크** (서비스 내에서만 고유)
- 신규 컬럼 **`toss_order_id`** (전역 유니크) → 토스에 전달하는 식별자
  - 구독 결제(FIRST/RENEWAL/RETRY/manual): 서버가 order_id를 생성하므로 이미 전역 고유 → `toss_order_id = order_id` (모델의 `before_insert` 이벤트가 자동 처리 — 기존 생성 지점·테스트 픽스처 무수정)
  - 단건 결제(클라이언트가 order_id 지정): 서버가 `t{uuid4().hex}` 생성, **토스 멱등키도 같은 값** 사용

**변경 파일**:

| 파일 | 변경 내용 |
|---|---|
| `app/models/payment.py` | `order_id` unique 해제 + `(service_id, order_id)` 복합 유니크, `toss_order_id` 추가, `before_insert` 기본값 이벤트 |
| `alembic/versions/a7b8c9d0e1f2_payment_order_scope.py` | **신규 마이그레이션** — toss_order_id 추가(기존 행은 order_id로 백필) + 유니크 제약 재구성. dev DB 적용 완료 |
| `app/services/payments.py` | 단건 결제: (service_id, order_id) 스코프 멱등 조회(교차 테넌트 ConflictError 제거), toss_order_id 생성, 토스 호출 order_id/멱등키를 toss_order_id로. 취소: 스코프 조회 |
| `app/services/reconciliation.py` | 토스 재조회를 `payment.toss_order_id`로 |
| `app/services/webhooks.py` | 웹훅 orderId(토스 측 식별자) → `Payment.toss_order_id`로 매칭 |
| `app/schemas/api.py` | order_id 설명을 "서비스 내 고유"로 정정 |
| `tests/integration/test_one_off_payment.py` | 교차 테넌트 충돌 테스트 → **격리 검증 테스트**로 교체, 정산 테스트의 fake 주입 키를 toss_order_id로 |

**외부 API 영향**: 호환성 유지 — 기존 연동 서비스의 요청/응답 형식 무변경. 동작 변화는 "타 서비스와 같은 order_id를 써도 409가 나지 않음"뿐(완화 방향).

## 작업 2. 어드민 로그인 IP rate limit (보안 M-2) ✅

**문제**: `/admin/login`에 처리율 제한이 없어 존재하지 않는 이메일로 무제한 패스워드 스프레이 가능 + 시도마다 감사 행이 쌓여 감사 테이블 팽창 DoS 가능.

**변경**: `app/admin/routes/auth.py` — IP당 **분당 10회**(`LOGIN_RATE_LIMIT_PER_MINUTE`) Redis 카운터. 초과 시 인증 로직(DB 조회·감사 기록) **진입 전** 차단 → 감사 팽창도 함께 해결. 외부 API rate limit과 동일한 윈도우 패턴(90초 TTL) 사용.

## 작업 3. 보안 응답 헤더 미들웨어 (보안 M-3) ✅

**변경**: `app/main.py` — 모든 응답에:
- `X-Frame-Options: DENY` (클릭재킹), `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`
- `Strict-Transport-Security`(HSTS)는 **prod에서만** (개발 HTTP 환경 배려)

**미적용(의도)**: CSP — 어드민 템플릿이 인라인 스크립트/스타일(htmx 패턴)을 사용해 무차별 적용 시 화면이 깨짐. 템플릿 정리와 함께 별도 작업 필요(코드 주석에 명시).

## 작업 4. docker-compose 루프백 바인딩 (보안 M-4) ✅

**변경**: `docker-compose.yml` — Postgres/Redis 포트를 `127.0.0.1:`에만 바인딩(Docker 기본 0.0.0.0 노출 차단). 파일 상단에 "개발 전용 — 운영은 별도 자격증명·관리형 인스턴스" 경고 주석 추가.

**미적용(의도)**: Redis `requirepass` — 개발 compose에 추가하면 모든 로컬 `.env`/테스트 설정의 REDIS_URL 변경이 필요해 개발 흐름이 깨짐. 루프백 바인딩으로 주된 노출(외부 접근)은 차단되며, 운영은 이 파일을 쓰지 않음을 주석·매뉴얼에 명시.

## 작업 5. Low 항목

| # | 항목 | 처리 |
|---|---|---|
| L-1 | 무인증 서비스 목록 | ✅ `public_service_list_enabled` 설정 추가(기본 True). False면 404 — 인터넷 직노출 운영에서 끄도록 매뉴얼 안내 |
| L-3 | 단건 결제 금액 상한 | ✅ 스키마 `le=100,000,000`(1억원) + 서비스 레이어 `ONE_OFF_MAX_AMOUNT` 이중 방어 — 토스 호출 전 거부 |
| L-5 | 세션 절대 만료 | ✅ 세션에 `created_at` 기록, `get_session`이 `session_absolute_ttl_seconds`(기본 12시간) 초과 세션 파기. 구버전 세션(created_at 없음)도 안전 측 파기(1회 재로그인 유도) |
| L-2 | 토스 웹훅 서명 부재 | ⏸ **보류** — 토스 빌링 웹훅은 서명을 제공하지 않음. 기존 방어(IP 검증 + 페이로드 불신·토스 재조회)가 유효하고, Phase 1의 XFF 수정으로 IP 검증 신뢰도가 회복됨. BILLING_DELETED는 영향이 메일 발송뿐 |
| L-4 | 로그인 폼 CSRF | ⏸ **보류** — 사전 세션 토큰 도입 시 로그인 UX·테스트 헬퍼 전반 수정 필요. 위험도(공격자 계정으로 강제 로그인) 대비 사내 어드민 특성상 우선순위 낮음. M-2 rate limit이 자동화 공격을 추가로 제한 |
| L-6 | dev 비밀번호 프리필 | ⏸ **체크리스트로 대응** — 코드 가드(`environment != "prod"`)는 이미 존재. 운영 배포 시 `APP_ENV=prod` 설정 확인을 배포 절차에 포함(01 매뉴얼) |

## 신규/갱신 테스트

- **신규** `tests/e2e/test_security_phase2.py` (5건): 로그인 rate limit 차단, 보안 헤더 부착(+dev에서 HSTS 미부착), 서비스 목록 토글 404, 금액 상한 거부(토스 미호출 검증), 세션 절대 만료(유휴 연장 무관 파기)
- **갱신** `tests/integration/test_one_off_payment.py` (2건): 교차 테넌트 order_id **격리** 검증(구 충돌 검증 대체), 정산 스윕의 toss_order_id 조회 반영

```
전체: 495 passed (Phase 1 종료 시 490 → +5 신규, 2건 신정책 갱신)
```

## 운영 배포 체크리스트 추가분 (Phase 1 항목 포함)

1. `APP_ENV=prod` 설정 확인 (L-6 — dev 로그인 프리필 차단 + HSTS 활성화 조건)
2. `TRUST_PROXY=true`면 `TRUST_PROXY_HOPS`를 실제 프록시 단 수로 설정 (Phase 1 M-5)
3. 인터넷 직노출이면 `PUBLIC_SERVICE_LIST_ENABLED=false` (L-1)
4. DB 마이그레이션 `uv run alembic upgrade head` (M-1 — payments 제약 변경)
5. 운영 인프라는 docker-compose.yml 미사용 — 별도 자격증명·방화벽 (M-4)

## 변경 파일 전체 목록

| 파일 | 작업 |
|---|---|
| `app/models/payment.py` | 1 |
| `alembic/versions/a7b8c9d0e1f2_payment_order_scope.py` | 1 (신규) |
| `app/services/payments.py` | 1, L-3 |
| `app/services/reconciliation.py` | 1 |
| `app/services/webhooks.py` | 1 |
| `app/schemas/api.py` | 1, L-3 |
| `app/admin/routes/auth.py` | 2 |
| `app/main.py` | 3 |
| `docker-compose.yml` | 4 |
| `app/core/config.py` | L-1, L-5 |
| `app/api/v1/services.py` | L-1 |
| `app/services/auth.py` | L-5 |
| `tests/e2e/test_security_phase2.py` | 신규 테스트 |
| `tests/integration/test_one_off_payment.py` | 테스트 갱신 |
| `docs/dev_manual/01·02·03·07·15` + `manual.html` | 매뉴얼 반영 + 재빌드 |
