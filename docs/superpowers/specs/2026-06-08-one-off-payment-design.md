# 단건(일반) 결제 API + 서비스별 결제 데이터(구독/일반 구분) 설계

날짜: 2026-06-08
상태: 승인됨
요청: 구독 외 단건 결제 API 추가 + Admin 서비스별 결제 데이터, 구독/일반 구분

## 결정 사항

- 단건 결제 수단: **매번 authKey로 단회 결제**(빌링키 발급→결제→삭제, 카드 미보관).
- 저장: **Payment 테이블에 통합 + `kind`(SUBSCRIPTION/ONE_OFF) 구분 컬럼**.
- order_id: **외부가 제공**(고유) → 멱등 키. 같은 order_id 재요청은 기존 결제 반환(이중결제 방지).
- 단건 금액은 **요청값**(plan이 없으므로). HMAC 서명(문서 08)으로 본문 무결성 보장 → 변조 차단.
- Payment에 **`service_id`(직접 보유)** 추가 → 결제 스코프/집계를 "구독 경유"가 아니라 service_id 직접 기준으로 통일.
- Admin: 결제리스트 구분/필터 + 정산 구독/일반 분리 + 대시보드 일반결제 카드(3곳 모두).

## 1. 데이터 모델 (`app/models/payment.py` + Alembic)

### enum (`app/models/enums.py`)
- `PaymentType`에 `ONE_OFF = "ONE_OFF"` 추가(단건 행의 payment_type).
- 신규 `class PaymentKind(StrEnum): SUBSCRIPTION; ONE_OFF`. `models/__init__.py` export.

### Payment 컬럼 변경
- `subscription_id`: `ForeignKey(..., ondelete="RESTRICT")` **nullable=True**로 변경(단건은 None).
- 추가 `kind: str` (default `SUBSCRIPTION`, server_default) — 구독/일반 구분의 권위 컬럼, index.
- 추가 `service_id: uuid.UUID` FK→services(ondelete RESTRICT), index. **백필 후 NOT NULL**.
- 추가 `external_user_id: str | None` (nullable) — 단건은 요청값, 구독은 구독에서 백필.

### 마이그레이션 `c3d4e5f6a7b8_payment_one_off` (down_revision='b2c3d4e5f6a7')
upgrade:
1. `subscription_id` nullable=True로 alter.
2. `kind`(String(20), server_default 'SUBSCRIPTION', not null), `service_id`(Uuid, nullable=True 우선),
   `external_user_id`(String(255), nullable=True) 컬럼 추가.
3. 백필: `UPDATE payments p SET service_id=s.service_id, external_user_id=s.external_user_id
   FROM subscriptions s WHERE p.subscription_id=s.id`.
4. `service_id` NOT NULL로 alter + FK 추가 + index. `kind` index.
downgrade: 역순(컬럼 drop, subscription_id NOT NULL 복구).

### 기존 Payment 생성부 갱신(3곳) — `kind`/`service_id`/`external_user_id` 채우기
- `services/subscriptions.py:193`(첫결제), `:299`(수동결제 RETRY): `kind=SUBSCRIPTION,
  service_id=service.id, external_user_id=external_user_id` 추가.
- `services/renewals.py:231`(갱신): `kind=SUBSCRIPTION, service_id=sub.service_id,
  external_user_id=sub.external_user_id` 추가.
- `tests/factories.py`에 Payment 직접 생성 헬퍼가 있으면 동일 보강(없으면 무시).

## 2. 단건 결제 API — `POST /api/v1/payments`

### 스키마 (`app/schemas/api.py`)
```python
class OneOffPaymentRequest(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=255)
    order_id: str = Field(min_length=6, max_length=64)   # 외부 제공, 토스 orderId 규칙
    order_name: str = Field(min_length=1, max_length=100)
    amount: int = Field(gt=0)
    auth_key: str = Field(min_length=1, max_length=300)
    customer_key: str = Field(min_length=2, max_length=300)
```
- `PaymentResponse`에 `kind: str` 필드 추가(from_attributes라 Payment.kind 자동 매핑).
- order_id 형식 검증: `^[A-Za-z0-9\-_=.]{6,64}$`(서비스 계층에서 재검증).

### 라우트 (`app/api/v1/payments.py`)
```python
@router.post("/payments", status_code=201)
async def create_payment(payload: OneOffPaymentRequest,
                         service: Service = Depends(payment_rate_limit),
                         db=..., toss=..., cipher=...):
    payment = await payment_service.create_one_off_payment(
        db, toss, cipher, service=service, external_user_id=payload.external_user_id,
        order_id=payload.order_id, order_name=payload.order_name, amount=payload.amount,
        auth_key=payload.auth_key, customer_key=payload.customer_key)
    return PaymentResponse.model_validate(payment)
```

### 서비스 계층 (`app/services/payments.py` 신설)
`create_one_off_payment(db, toss, cipher, *, service, external_user_id, order_id, order_name, amount, auth_key, customer_key) -> Payment`:
1. 입력 검증: order_id 정규식, external_user_id 길이, amount>0, customer_key 형식(기존 CUSTOMER_KEY_RE 재사용).
2. **멱등**: `Payment where order_id == order_id` 존재 시 → 기존 Payment 반환(서비스 일치 확인;
   다른 서비스의 order_id면 ConflictError "이미 사용된 주문번호").
3. `Payment(kind=ONE_OFF, payment_type=ONE_OFF, service_id=service.id,
   external_user_id=..., order_id=order_id(외부값), amount, status=PENDING,
   idempotency_key=order_id, requested_at=now)` → **commit(PENDING 선커밋, 문서 04 원칙)**.
   - order_id unique 충돌(동시 요청) → rollback 후 기존 행 반환(멱등).
4. 토스: `bk = issue_billing_key(auth_key, customer_key)` (실패→PaymentFailedError).
5. `resolve_charge(amount, order_id, order_name, idempotency_key=order_id)`:
   - DONE: payment=DONE(+toss_payment_key/approved_at/raw) → commit →
     **빌링키 삭제(best-effort)**(카드 미보관). 반환.
   - TossError: payment=FAILED(+code/message) → commit → 빌링키 삭제 → PaymentFailedError(4xx).
   - TossTimeoutError: PENDING 유지 + 감사 → commit → PaymentFailedError(PENDING_GRACE, 503).
     (빌링키가 메모리에 있으면 삭제 시도, 실패 시 고아 키 — 로그. 단건 1회 키라 영향 미미.)
6. 감사 기록: `actor_type="SERVICE", actor_service_id=service.id, action="payment.one_off"`(+
   created/failed/unresolved 변형). `audit_labels`에 라벨 추가.

> 구독 결제(04)와 동일한 3원칙: 결제 전 PENDING 커밋, 타임아웃=결과 불명(PENDING 유지), 결정적
> order_id로 멱등. 차이: plan 없음 → 금액은 요청값, 빌링키 미보관(성공/실패 후 삭제).

## 3. 정합성 — PENDING 정산 스윕 보강 (`services/renewals.py`)

- `_reconcile_pending_payments`: `select(Payment, Subscription).join(Subscription...)` →
  **outerjoin**으로 변경(단건은 subscription_id NULL이라 inner join이면 누락됨).
  스킵 조건 `payment.payment_type != FIRST and sub.status in _DUE_STATUSES`는 sub None이면 False →
  단건은 정상적으로 정산 진행.
- `_reconcile_one_payment`: `sub = db.get(Subscription, payment.subscription_id)`가 None일 수 있으므로
  `payment.subscription_id`가 None이면 sub 조회 스킵 + sub None 가드. 단건은 구독 만료/고아 분기
  없이 **DONE/FAILED 확정만**.

## 4. Admin (3곳, `Payment.service_id` 기준 통일)

### 4-1. 결제리스트 (`app/admin/routes/subscriptions.py` `payments_list` + `payments/list.html`)
- 쿼리: `select(Payment, Subscription).outerjoin(Subscription)` + Service는 `Payment.service_id`로 join.
  스코프도 `Payment.service_id.in_(scope)`로 변경(구독 경유 제거).
- `filter_keys`에 `kind`, `service_id` 추가. 검색 q: 기존(order_id/external_user_id) 유지하되
  external_user_id는 `Payment.external_user_id`(단건 포함) 기준.
- 템플릿: "종류" 컬럼(구독/일반 badge), "서비스" 컬럼, 종류 select(전체/구독/일반) + 서비스 select 추가.
- 사용자 표시: `Payment.external_user_id`(구독·단건 공통).

### 4-2. 정산 (`services/settlement.py` + `settlement/index.html`)
- `settlement_summary` 쿼리: `Payment JOIN Service ON Payment.service_id`(구독 join 제거),
  스코프도 service_id 직접. → 단건 포함.
- `SettlementRow`에 `sub_amount`/`one_off_amount`(또는 kind별 분리) 추가 — 서비스별 행에 구독/일반 금액 분리.
- 전체 합계도 구독/일반 분리 카드 또는 행. 서비스별 모드 건별 목록에 "종류" 컬럼.

### 4-3. 대시보드 (`services/dashboard.py` + `dashboard.html`)
- `_revenue_between` 등 결제 스코프를 `Payment.service_id` 기준으로 통일(단건 포함) — 기존 매출 카드는
  전체(구독+일반) DONE 합 유지.
- 카드 추가: **"이번달 일반결제"**(kind=ONE_OFF, 이번달 approved_at DONE 합·건수). 클릭 →
  `/admin/payments?kind=ONE_OFF&from=..&to=..`.
- (선택) 12개월 매출 추이는 전체 유지. 일반결제는 카드로 충분(YAGNI — 차트 분리는 안 함).

## 5. 에러/엣지

- 같은 order_id 다른 서비스 → ConflictError(주문번호 도용 방지).
- 같은 order_id 같은 서비스 재요청 → 기존 Payment 반환(멱등). DONE이면 그대로, PENDING이면 그대로(스윕이 확정).
- amount ≤ 0 → 스키마 422.
- 빌링키 발급 실패 → PaymentFailedError(결제 미생성? — PENDING 선커밋 이후이므로 FAILED로 마감).
  → 정정: 빌링키 발급은 PENDING 커밋 **이후** 호출하므로, 발급 실패 시 payment=FAILED로 마감 후 4xx.
- 타임아웃 고아 빌링키 → 로그(수용).

## 6. 테스트

- `tests/integration/test_one_off_payment.py`(신설): 성공(빌링키 삭제 호출), 카드거절 FAILED,
  타임아웃 PENDING/503, 멱등 재요청(동일 order_id), 타 서비스 order_id 충돌, 정산 스윕 단건 확정.
- 기존 `test_renewals.py`: 정산 스윕 outerjoin/단건 가드 회귀.
- e2e: 결제리스트 종류/서비스 필터, 정산 구독/일반 분리, 대시보드 일반결제 카드.
- 마이그레이션 백필: 기존 구독 결제가 service_id/external_user_id/kind=SUBSCRIPTION으로 채워지는지
  (통합 테스트는 create_all 기준이라 모델 default로 검증; 백필 SQL은 코드리뷰로 확인).

## 7. 변경하지 않는 것

- 구독 결제 흐름(04~06)의 금액 계산·상태 전이. 외부 API 인증(08). 감사 기록 방식(10).
- 빌링키를 Payment에 저장하지 않음(단건은 미보관, 구독은 기존대로 Subscription에 보관).
