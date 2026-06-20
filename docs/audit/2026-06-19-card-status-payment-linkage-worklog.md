# 카드 활성/비활성 · 카드별 결제내역 · 결제 카드 표시 워크로그

- 날짜: 2026-06-19
- 작업자: seungjinhan
- 설계: `docs/superpowers/specs/2026-06-19-card-status-and-payment-linkage-design.md`

## 요청

1. 서비스 상세 "등록 카드"에서 카드를 누르면 그 카드로 결제한 내역이 리스트로 보일 것.
2. 구독 상세·결제 상세에 어떤 카드로 결제되었는지 표시.
3. 등록 카드에 활성/비활성 상태 + 토글 버튼. 비활성화하면 결제 중지.
4. 결제 메뉴 → 결제 상세에서도 결제한 카드 정보 표시.

## 핵심 설계

- Payment에는 `card_id`가 없지만 `(service_id, external_user_id)`가 Card 고유키와 동일 → "이 카드로 결제한 내역"은 동일 키 매칭으로 조회(스키마 변경 없음, 구독+일반 모두 포함).
- 비활성 차단은 **모든 결제 경로**(사용자 확인). 활성 구독은 즉시 정지하지 않고 **다음 결제 시 실패**(사용자 확인).

## 변경 내용

### 모델 / 마이그레이션
- `app/models/card.py`: `is_active: bool`(NOT NULL, 기본 true) 추가.
- `alembic/versions/c2d3e4f5a6b7_card_is_active.py`: `cards.is_active` 추가(server_default true), head `b1c2d3e4f5a6` 체이닝. up/down 검증 완료.

### 서비스 레이어 (`app/services/`)
- `cards.py`: `set_card_active(db, *, card_id, is_active, actor_user_id)` — 상태 변경 + 감사로그(`card.activate`/`card.deactivate`), 멱등.
- 결제 차단 가드(각 `get_card` 직후):
  - `renewals.py:_renew_one` — `not card.is_active` → 합성 `TossError("CARD_INACTIVE")` → 기존 실패 처리(PAST_DUE/정지).
  - `subscriptions.py:create_subscription` — `ConflictError`.
  - `subscriptions.py:_perform_manual_charge` — `PaymentFailedError(code="CARD_INACTIVE")`.
  - `payments.py:create_one_off_payment` — `ConflictError`.

### 관리자 라우트/템플릿
- 신규 `app/admin/routes/cards.py` + 라우터 등록(`app/admin/__init__.py`):
  - `GET /admin/cards/{card_id}` → `cards/detail.html`(카드 정보 + 토글 + 이 카드 결제내역 페이징).
  - `POST /admin/cards/{card_id}/toggle` → htmx면 `_cards_table.html` partial, 아니면 카드 상세 리다이렉트.
- `services/_cards_table.html`: 상태 뱃지 + 활성/비활성 토글 버튼(htmx) + 행 클릭 → 카드 상세.
- `subscriptions/detail.html`: 카드 행에 발급사·활성/비활성 뱃지·카드 상세 링크. 재결제 버튼은 카드 없음 또는 비활성 시 disabled.
- `payments/detail.html` + `payments.py:payment_detail`: "결제 카드" 행 추가(`raw_response.card.number` 우선, 보관함 카드 폴백) + `get_card` 로드.

### 테스트
- `tests/integration/test_card_active.py`(6) — 토글/멱등/감사 + 비활성 차단(구독생성·일반결제·수동재결제·자동연장→PAST_DUE).
- `tests/e2e/test_card_admin.py`(5) — 카드 상세 결제내역 스코프, 토글 POST+감사, htmx partial, 결제 상세 카드 표시, 구독 상세 재결제 disabled.

### 문서
- `docs/dev_manual/16-card-vault.md`(is_active·차단·카드 상세·감사), `admin/03-services.md`(등록 카드 탭 상태·토글·행 클릭), `admin/06-payments.md`(결제 카드 행) 갱신 + HTML 재빌드.

## 검증

- `uv run pytest` → **577 passed** (신규 11건 포함).
- 마이그레이션 fresh DB upgrade head + downgrade -1 정상.

## 후속(배포)

- 운영/개발 DB는 앱 기동 시(또는 `alembic upgrade head`) `c2d3e4f5a6b7`가 적용되어야 카드 토글이 동작한다. 테스트는 `create_all`이라 영향 없음.
