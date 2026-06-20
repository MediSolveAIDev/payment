# 카드 활성/비활성 · 카드별 결제내역 · 결제 카드 표시 — 설계

- 날짜: 2026-06-19
- 상태: 승인됨 (사용자 "진행")

## 1. 목표

1. **서비스 상세 "등록 카드" 리스트**에서 카드를 클릭하면 그 카드로 결제한 내역이 리스트로 보인다.
2. **구독 상세 · 결제 상세**에 어떤 카드로 결제되었는지 표시한다.
3. **등록 카드에 활성/비활성 상태**를 두고, 버튼으로 토글한다. 비활성화되면 **모든 결제가 차단**된다.
4. **결제 메뉴 → 결제 상세**에서도 결제한 카드 정보를 표시한다.

## 2. 결정 사항 (사용자 확인)

- 카드별 결제내역은 **전용 카드 상세 페이지**에 표시한다.
- 비활성화 시 **모든 결제(구독 자동연장·첫구독·재시도·외부 일반결제)를 차단**한다.
- 활성 구독이 있는 카드를 비활성화하면 구독 상태는 **즉시 바꾸지 않고 다음 결제 시 실패 처리**(기존 PAST_DUE/SUSPENDED 흐름 재사용)한다.
- 카드 상세/토글 권한은 **SYSTEM_ADMIN 전용**(서비스 상세와 동일).

## 3. 데이터 모델

### cards.is_active 추가
- `app/models/card.py`: `is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False, default=True)`
- 마이그레이션: 현재 head `b1c2d3e4f5a6`에 체이닝. `op.add_column('cards', sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False))`, downgrade는 `drop_column`.

### Payment↔Card 연결 (스키마 변경 없음)
- Payment는 `card_id`가 없지만 `service_id` + `external_user_id`를 가진다. Card 고유키 `(service_id, external_user_id)`와 동일.
- "이 카드로 결제한 내역" = `Payment.service_id == card.service_id AND Payment.external_user_id == card.external_user_id` (구독·일반결제 모두 포함). 카드 교체는 같은 행을 덮어쓰므로 사용자 관점에서 일관됨.

## 4. 서비스 레이어

### app/services/cards.py
- `set_card_active(db, *, card_id, is_active, actor_user_id) -> Card`
  - 카드 조회(없으면 NotFoundError) → `card.is_active = is_active` → 감사로그 `card.activate`/`card.deactivate`(actor_type="USER") → commit.
  - 멱등(이미 같은 상태면 그대로 두되 감사로그는 남기지 않음).

### 결제 차단 (각 충전 지점에서 `get_card` 직후 가드)
- `renewals.py:_renew_one` (line ~379): 기존 `if card is None or sub.card_id is None:` → `or not card.is_active` 추가. 비활성 시 합성 `TossError("CARD_INACTIVE", "비활성화된 카드입니다")`로 `_handle_charge_failure`에 위임(Q3: 다음 결제 실패 → PAST_DUE/SUSPENDED).
- `subscriptions.py:create_subscription` (line ~191): `card is None` 체크 뒤 `if not card.is_active: raise ConflictError("비활성화된 카드로는 구독을 생성할 수 없습니다")`.
- `subscriptions.py:_perform_manual_charge` (line ~353): `if card is None or sub.card_id is None or not card.is_active:` → `PaymentFailedError(code="CARD_INACTIVE")`.
- `payments.py` one-off (line ~85): `card is None` 체크 뒤 `if not card.is_active: raise ConflictError("비활성화된 카드입니다")`.

## 5. 관리자 화면

### 카드 상세 페이지 (신규)
- 라우트: `GET /admin/cards/{card_id}` (require_admin). 카드 조회(없으면 404).
- 결제내역: `select(Payment).where(service_id==card.service_id, external_user_id==card.external_user_id).order_by(requested_at desc)` 페이징(`page`, 15건).
- 템플릿 `app/admin/templates/cards/detail.html`: 카드 정보(사용자·마스킹번호·발급사·customerKey·등록/변경일·상태 뱃지) + 활성/비활성 토글 폼 + 결제내역 테이블(요청시각/주문번호/종류/금액/상태, 행 클릭 → 결제 상세).

### 카드 토글 라우트
- `POST /admin/cards/{card_id}/toggle` (require_admin, CSRF). 현재 상태 반전 → `set_card_active`.
- 응답: htmx 요청(서비스 상세 리스트에서 호출)이면 `_cards_table.html` partial 재조회 후 반환(`list-svc-cards`), 아니면 카드 상세로 리다이렉트.

### 서비스 상세 _cards_table.html 보강
- **상태** 컬럼(활성/비활성 뱃지) + **토글 버튼**(htmx POST → `list-svc-cards` 갱신, `event.stopPropagation`로 행 이동과 분리).
- 행 클릭 → `/admin/cards/{card.id}`.
- 라우트 `services.py:_cards_tab`는 이미 `is_active` 포함 Card를 로드(모델 컬럼 추가만으로 사용 가능).

### 구독 상세 / 결제 상세 카드 표시
- 구독 상세(`subscriptions/detail.html`): 카드 행에 발급사·상태 뱃지 보강. 재결제 버튼은 카드 없음 **또는 비활성**이면 비활성화.
- 결제 상세(`payments.py:payment_detail`): `external_user_id`가 있으면 `get_card(service_id, external_user_id)`로 `card` 로드해 전달. 템플릿에 "결제 카드" 행 추가 — **`payment.raw_response.card.number`(실제 충전 카드) 우선, 없으면 `card.card_info.number`** 폴백.

## 6. 테스트 (TDD)

- 비활성 카드 → 구독 자동연장(`renewals`) 차단 → 결제 실패/PAST_DUE.
- 비활성 카드 → 외부 일반결제(one-off) 차단(ConflictError).
- 비활성 카드 → 구독 생성 차단.
- 비활성 카드 → 수동 재결제 차단.
- 토글 라우트: 활성↔비활성 + 감사로그 기록 + htmx partial 응답.
- 카드 상세: 결제내역 스코프(타 사용자/타 서비스 결제 미표시), 상태 뱃지·토글 노출.
- 결제 상세: 카드번호 표시. 구독 상세: 비활성 시 재결제 버튼 disabled.

## 7. 문서

- `docs/dev_manual/16-card-vault.md`: 활성 상태·결제 차단·카드 상세 페이지 추가.
- `docs/dev_manual/admin/03-services.md`: 등록 카드 탭에 상태·토글·행 클릭 설명.
- `docs/dev_manual/admin/06-payments.md`: 결제 상세 카드 표시 설명.
- `build_html.py` 재빌드 + 워크로그 `docs/audit/2026-06-19-card-status-payment-linkage-worklog.md`.

## 8. 비범위 (YAGNI)

- Payment에 card_id 컬럼 추가/백필 — 불필요(tuple 매칭으로 충분).
- 서비스 담당자(매니저)의 카드 토글 권한 — 현재 서비스 상세가 admin 전용이므로 admin만.
- 카드 자동 비활성(연속 실패 시) — 이번 범위 아님.
