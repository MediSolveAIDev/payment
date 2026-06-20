# 단건(일반) 결제 API + 서비스별 결제 구분 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 구독 없이 단건 결제하는 외부 API를 추가하고, Payment에 kind/service_id를 두어 Admin(결제리스트·정산·대시보드)에서 구독/일반 결제를 구분·집계한다.

**Architecture:** Payment 테이블에 `kind`(SUBSCRIPTION/ONE_OFF)·`service_id`·`external_user_id`를 추가해 결제 스코프를 "구독 경유"가 아닌 `Payment.service_id` 직접 기준으로 통일. 단건 결제는 구독 생성(문서 04)과 같은 "PENDING 선커밋 → 토스 → 결과 확정" 패턴을 따르되 plan이 없어 금액은 요청값, 빌링키는 미보관(발급→결제→삭제).

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, TossPayments, pytest

**스펙:** `docs/superpowers/specs/2026-06-08-one-off-payment-design.md`
**테스트 실행:** `uv run pytest <경로> -q` (테스트 DB는 conftest의 `create_all` 사용 — 모델 변경 즉시 반영. 마이그레이션은 운영 DB용)

## 파일 구조

- `app/models/enums.py` — `PaymentKind` 추가, `PaymentType.ONE_OFF` 추가
- `app/models/payment.py` — kind/service_id/external_user_id 추가, subscription_id nullable
- `app/models/__init__.py` — `PaymentKind` export
- `alembic/versions/c3d4e5f6a7b8_payment_one_off.py` — 신설(백필 포함)
- `app/services/payments.py` — 신설: `create_one_off_payment`
- `app/api/v1/payments.py` — `POST /payments` 추가
- `app/schemas/api.py` — `OneOffPaymentRequest`, `PaymentResponse.kind`
- `app/admin/audit_labels.py` — `payment.one_off*` 라벨
- `app/services/renewals.py` — 정산 스윕 outerjoin + None 가드
- `app/admin/routes/subscriptions.py` — `payments_list` 종류/서비스 필터 + outerjoin
- `app/admin/templates/payments/list.html` — 종류/서비스 컬럼·필터
- `app/services/settlement.py` + `app/admin/templates/settlement/index.html` — service_id 기준 + kind 분리
- `app/services/dashboard.py` + `app/admin/templates/dashboard.html` — 일반결제 카드 + 매출 스코프 통일
- 테스트: `tests/integration/test_one_off_payment.py`(신설) 외 회귀

---

### Task 1: 모델 · enum · 마이그레이션 + 기존 Payment 생성부 갱신

**Files:**
- Modify: `app/models/enums.py`, `app/models/payment.py`, `app/models/__init__.py`
- Create: `alembic/versions/c3d4e5f6a7b8_payment_one_off.py`
- Modify: `app/services/subscriptions.py`, `app/services/renewals.py`
- Test: `tests/integration/test_models.py`(있으면 확장) 또는 신규 단언

- [ ] **Step 1: enum 추가** — `app/models/enums.py`

`PaymentType`에 한 줄 추가:
```python
class PaymentType(StrEnum):
    FIRST = "FIRST"
    RENEWAL = "RENEWAL"
    RETRY = "RETRY"
    ONE_OFF = "ONE_OFF"
```
파일 끝(또는 PaymentType 아래)에 추가:
```python
class PaymentKind(StrEnum):
    SUBSCRIPTION = "SUBSCRIPTION"
    ONE_OFF = "ONE_OFF"
```

- [ ] **Step 2: models export** — `app/models/__init__.py`의 enums import 블록에 `PaymentKind` 추가하고 `__all__`에도 `"PaymentKind"` 추가.

- [ ] **Step 3: Payment 모델 수정** — `app/models/payment.py`

import에 추가: `from app.models.enums import PaymentKind`(기존 import 줄에 병합).
`subscription_id`를 nullable로, 신규 컬럼 추가:
```python
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="RESTRICT"), nullable=True, index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), index=True)
    external_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(20), default=PaymentKind.SUBSCRIPTION,
        server_default=PaymentKind.SUBSCRIPTION, index=True)
```
(`String`이 이미 import돼 있는지 확인 — payment.py는 `from sqlalchemy import BigInteger, ...` 사용 중. `String` 없으면 추가.)

- [ ] **Step 4: 기존 Payment 생성부 3곳 갱신**

`app/services/subscriptions.py:193`(첫 결제):
```python
        payment = Payment(subscription_id=sub.id, order_id=new_order_id("f"),
                          amount=amount, payment_type=PaymentType.FIRST,
                          status=PaymentStatus.PENDING,
                          idempotency_key=f"first-{sub.id}", requested_at=now,
                          kind=PaymentKind.SUBSCRIPTION, service_id=service.id,
                          external_user_id=external_user_id)
```
`app/services/subscriptions.py:299`(수동결제):
```python
    payment = Payment(subscription_id=sub.id, order_id=order_id, amount=amount,
                      payment_type=PaymentType.RETRY, status=PaymentStatus.PENDING,
                      idempotency_key=f"manual-{order_id}", requested_at=now,
                      kind=PaymentKind.SUBSCRIPTION, service_id=service.id,
                      external_user_id=external_user_id)
```
`app/services/renewals.py:231`(갱신):
```python
                payment = Payment(
                    subscription_id=sub.id, order_id=order_id, amount=amount,
                    payment_type=(PaymentType.RENEWAL if sub.retry_count == 0
                                  else PaymentType.RETRY),
                    status=PaymentStatus.PENDING,
                    idempotency_key=f"renew-{order_id}", requested_at=now,
                    kind=PaymentKind.SUBSCRIPTION, service_id=sub.service_id,
                    external_user_id=sub.external_user_id)
```
두 파일 상단 import에 `PaymentKind` 추가(`from app.models import (... PaymentKind ...)`).

- [ ] **Step 5: 마이그레이션 작성** — `alembic/versions/c3d4e5f6a7b8_payment_one_off.py`

```python
"""payment one_off: kind/service_id/external_user_id + subscription_id nullable

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('payments', sa.Column('kind', sa.String(length=20),
                  nullable=False, server_default='SUBSCRIPTION'))
    op.add_column('payments', sa.Column('service_id', sa.Uuid(), nullable=True))
    op.add_column('payments', sa.Column('external_user_id', sa.String(length=255), nullable=True))
    op.alter_column('payments', 'subscription_id', existing_type=sa.Uuid(), nullable=True)
    # 백필: 구독에서 service_id/external_user_id 채움
    op.execute("""
        UPDATE payments p SET service_id = s.service_id,
                              external_user_id = s.external_user_id
        FROM subscriptions s WHERE p.subscription_id = s.id
    """)
    op.alter_column('payments', 'service_id', existing_type=sa.Uuid(), nullable=False)
    op.create_foreign_key('fk_payments_service_id_services', 'payments', 'services',
                          ['service_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_payments_service_id', 'payments', ['service_id'])
    op.create_index('ix_payments_kind', 'payments', ['kind'])


def downgrade() -> None:
    op.drop_index('ix_payments_kind', table_name='payments')
    op.drop_index('ix_payments_service_id', table_name='payments')
    op.drop_constraint('fk_payments_service_id_services', 'payments', type_='foreignkey')
    op.alter_column('payments', 'subscription_id', existing_type=sa.Uuid(), nullable=False)
    op.drop_column('payments', 'external_user_id')
    op.drop_column('payments', 'service_id')
    op.drop_column('payments', 'kind')
```

- [ ] **Step 6: 기존 테스트의 Payment 생성부 보강** — 테스트 DB는 create_all이라 `service_id`가
  NOT NULL이 되면, 인라인으로 `Payment(...)`를 만드는 기존 테스트가 깨진다. grep으로 전수 확인:

Run: `grep -rn "Payment(" tests --include="*.py"`

알려진 헬퍼(있다면 `service_id=svc.id` 추가):
- `tests/integration/test_dashboard.py`의 `_paid(...)` → `Payment(..., service_id=sub.service_id, external_user_id=sub.external_user_id, kind="SUBSCRIPTION")` 추가.
- `tests/integration/test_settlement.py`의 `_done(...)` 및 인라인 → 동일.
- `tests/e2e/test_settlement_page.py`의 `_seed(...)` → 동일.
- `tests/e2e/test_admin_operations.py`의 결제 범위 필터 테스트(`pr-old`/`pr-new`) → `service_id=svc.id` 추가.

규칙: 그 테스트에서 이미 만든 `svc`/`sub`가 있으므로 `service_id=svc.id`(또는 `sub.service_id`),
`external_user_id`는 해당 sub의 값으로. `kind`는 생략 시 default(SUBSCRIPTION).

- [ ] **Step 7: 통과 확인** — Run: `uv run pytest tests/integration tests/unit -q`
Expected: 전체 PASS(모델 변경 + 생성부 보강 반영). 이어서 `uv run pytest -q`로 e2e 회귀.
`uv run alembic heads` → 단일 head `c3d4e5f6a7b8`.

- [ ] **Step 8: 커밋**
```bash
git add app/models tests alembic/versions/c3d4e5f6a7b8_payment_one_off.py app/services/subscriptions.py app/services/renewals.py
git commit -m "feat(payment): kind/service_id/external_user_id 추가 + subscription_id nullable"
```
(트레일러: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`)

---

### Task 2: 단건 결제 서비스 + API

**Files:**
- Create: `app/services/payments.py`
- Modify: `app/api/v1/payments.py`, `app/schemas/api.py`, `app/admin/audit_labels.py`
- Test: `tests/integration/test_one_off_payment.py`(신설)

- [ ] **Step 1: 실패 테스트 작성** — `tests/integration/test_one_off_payment.py`:

```python
"""단건(일반) 결제 통합 테스트."""
import pytest
from sqlalchemy import select

from app.core.errors import ConflictError, PaymentFailedError
from app.models import Payment, PaymentKind, PaymentStatus
from app.services import payments as payment_service
from app.toss.fake import FakeTossClient
from tests.factories import create_service


@pytest.fixture
def fake():
    return FakeTossClient()


async def _pay(db, fake, cipher, svc, *, order_id="oo-1", amount=5000, user="u-1"):
    return await payment_service.create_one_off_payment(
        db, fake, cipher, service=svc, external_user_id=user, order_id=order_id,
        order_name="단건상품", amount=amount, auth_key="auth-1", customer_key="ck-oo-1")


async def test_one_off_success_deletes_billing_key(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    p = await _pay(db, fake, cipher, svc)
    assert p.status == PaymentStatus.DONE
    assert p.kind == PaymentKind.ONE_OFF
    assert p.service_id == svc.id and p.subscription_id is None
    assert p.amount == 5000 and p.external_user_id == "u-1"
    assert fake.deleted_billing_keys, "단건 성공 후 빌링키 삭제 호출되어야 함(카드 미보관)"


async def test_one_off_idempotent_same_order_id(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    p1 = await _pay(db, fake, cipher, svc, order_id="oo-dup")
    charges_before = len(fake.charges)
    p2 = await _pay(db, fake, cipher, svc, order_id="oo-dup")
    assert p2.id == p1.id                      # 같은 결제 반환
    assert len(fake.charges) == charges_before  # 재결제 없음


async def test_one_off_other_service_order_id_conflicts(db, cipher, fake):
    svc_a, _, _ = await create_service(db, cipher)
    svc_b, _, _ = await create_service(db, cipher)
    await _pay(db, fake, cipher, svc_a, order_id="oo-x")
    with pytest.raises(ConflictError):
        await _pay(db, fake, cipher, svc_b, order_id="oo-x")


async def test_one_off_card_declined_failed(db, cipher, fake):
    from app.toss.errors import TossError
    svc, _, _ = await create_service(db, cipher)
    fake.charge_error = TossError("REJECT_CARD", "카드 거절")
    with pytest.raises(PaymentFailedError):
        await _pay(db, fake, cipher, svc, order_id="oo-fail")
    row = await db.scalar(select(Payment).where(Payment.order_id == "oo-fail"))
    assert row.status == PaymentStatus.FAILED


async def test_one_off_timeout_pending(db, cipher, fake):
    from app.toss.errors import TossTimeoutError
    svc, _, _ = await create_service(db, cipher)
    fake.charge_error = TossTimeoutError()
    with pytest.raises(PaymentFailedError):
        await _pay(db, fake, cipher, svc, order_id="oo-to")
    row = await db.scalar(select(Payment).where(Payment.order_id == "oo-to"))
    assert row.status == PaymentStatus.PENDING
```

주의: `FakeTossClient`의 실제 인터페이스(`charge_error`/`deleted_billing_keys` 속성, 거절/타임아웃
주입 방식)를 `app/toss/fake.py`에서 확인하고 테스트를 그에 맞춰 조정할 것. 속성명이 다르면 fake가
제공하는 방식으로 맞추고, 없으면 fake에 최소한의 훅(예: `deleted_billing_keys` 목록)을 추가한다.

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/integration/test_one_off_payment.py -x -q`
Expected: FAIL — `app.services.payments` 없음.

- [ ] **Step 3: 서비스 계층 구현** — `app/services/payments.py`:

```python
import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.crypto import AesGcmCipher
from app.core.errors import ConflictError, InputValidationError, PaymentFailedError
from app.models import Payment, PaymentKind, PaymentStatus, PaymentType, Service
from app.services.audit import record_audit
from app.services.subscriptions import (CUSTOMER_KEY_RE, PENDING_GRACE_MESSAGE,
                                        resolve_charge, safe_delete_billing_key)
from app.toss.client import TossClient
from app.toss.errors import TossError, TossTimeoutError

ORDER_ID_RE = re.compile(r"^[A-Za-z0-9\-_=.]{6,64}$")


async def create_one_off_payment(db: AsyncSession, toss: TossClient, cipher: AesGcmCipher,
                                 *, service: Service, external_user_id: str, order_id: str,
                                 order_name: str, amount: int, auth_key: str,
                                 customer_key: str) -> Payment:
    # 1) 입력 검증
    if not ORDER_ID_RE.fullmatch(order_id or ""):
        raise InputValidationError("order_id 형식이 올바르지 않습니다")
    if not CUSTOMER_KEY_RE.fullmatch(customer_key or ""):
        raise InputValidationError("customer_key 형식이 올바르지 않습니다")
    if not external_user_id or len(external_user_id) > 255:
        raise InputValidationError("external_user_id가 올바르지 않습니다")
    if amount <= 0:
        raise InputValidationError("금액은 1원 이상이어야 합니다")

    # 2) 멱등 — 같은 order_id가 이미 있으면 반환(다른 서비스면 충돌)
    existing = await db.scalar(select(Payment).where(Payment.order_id == order_id))
    if existing is not None:
        if existing.service_id != service.id:
            raise ConflictError("이미 사용된 주문번호입니다")
        return existing

    # 3) PENDING 선커밋(문서 04 원칙)
    now = utcnow()
    payment = Payment(subscription_id=None, service_id=service.id,
                      external_user_id=external_user_id, order_id=order_id, amount=amount,
                      payment_type=PaymentType.ONE_OFF, kind=PaymentKind.ONE_OFF,
                      status=PaymentStatus.PENDING, idempotency_key=order_id,
                      requested_at=now)
    db.add(payment)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        again = await db.scalar(select(Payment).where(Payment.order_id == order_id))
        if again is not None:
            if again.service_id != service.id:
                raise ConflictError("이미 사용된 주문번호입니다") from None
            return again
        raise
    await record_audit(db, actor_type="SERVICE", actor_service_id=service.id,
                       action="payment.one_off", target_type="payment",
                       target_id=str(payment.id),
                       detail={"external_user_id": external_user_id, "amount": amount})
    await db.commit()

    # 4) 빌링키 발급(미보관)
    try:
        bk = await toss.issue_billing_key(auth_key, customer_key)
    except TossError as exc:
        payment.status = PaymentStatus.FAILED
        payment.failure_code = exc.code
        payment.failure_message = exc.message
        await record_audit(db, actor_type="SERVICE", actor_service_id=service.id,
                           action="payment.one_off_failed", target_type="payment",
                           target_id=str(payment.id), detail={"code": exc.code})
        await db.commit()
        raise PaymentFailedError(f"빌링키 발급 실패: {exc.message}", code=exc.code) from exc

    # 5) 결제
    try:
        result = await resolve_charge(toss, billing_key=bk.billing_key,
                                      customer_key=customer_key, amount=amount,
                                      order_id=order_id, order_name=order_name,
                                      idempotency_key=order_id)
    except TossTimeoutError as exc:
        await record_audit(db, actor_type="SERVICE", actor_service_id=service.id,
                           action="payment.one_off_unresolved", target_type="payment",
                           target_id=str(payment.id), detail={"order_id": order_id})
        await db.commit()
        # 타임아웃: 빌링키가 메모리에 있으면 best-effort 삭제(고아 방지)
        await safe_delete_billing_key(toss, bk.billing_key)
        raise PaymentFailedError(PENDING_GRACE_MESSAGE, code="PAYMENT_UNRESOLVED",
                                 http_status=503) from exc
    except TossError as exc:
        payment.status = PaymentStatus.FAILED
        payment.failure_code = exc.code
        payment.failure_message = exc.message
        await record_audit(db, actor_type="SERVICE", actor_service_id=service.id,
                           action="payment.one_off_failed", target_type="payment",
                           target_id=str(payment.id), detail={"code": exc.code})
        await db.commit()
        await safe_delete_billing_key(toss, bk.billing_key)
        raise PaymentFailedError(f"결제 실패: {exc.message}", code=exc.code) from exc

    payment.status = PaymentStatus.DONE
    payment.toss_payment_key = result.payment_key
    payment.approved_at = utcnow()
    payment.raw_response = result.raw
    await db.commit()
    # 성공 — 카드 미보관: 빌링키 삭제
    await safe_delete_billing_key(toss, bk.billing_key)
    return payment
```

> 참고: 타임아웃 시 빌링키 삭제를 시도하지만, 결제가 실제로 진행 중일 수 있어 best-effort다.
> 결제 결과 자체는 PENDING으로 두고 정산 스윕(Task 3)이 order_id 재조회로 확정한다.

- [ ] **Step 4: 스키마** — `app/schemas/api.py`

`OneOffPaymentRequest` 추가:
```python
class OneOffPaymentRequest(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=255)
    order_id: str = Field(min_length=6, max_length=64)
    order_name: str = Field(min_length=1, max_length=100)
    amount: int = Field(gt=0)
    auth_key: str = Field(min_length=1, max_length=300)
    customer_key: str = Field(min_length=2, max_length=300)
```
`PaymentResponse`에 `kind: str` 필드 추가(클래스 본문, `model_config = from_attributes=True`라 자동 매핑):
```python
class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    order_id: str
    amount: int
    status: str
    payment_type: str
    kind: str
    failure_code: str | None
    failure_message: str | None
    requested_at: datetime
    approved_at: datetime | None
```

- [ ] **Step 5: API 라우트** — `app/api/v1/payments.py` 상단 import 보강 후 추가:
```python
from app.api.deps import authenticate_service, get_cipher, get_db, get_toss, payment_rate_limit
from app.core.crypto import AesGcmCipher
from app.schemas.api import OneOffPaymentRequest, PaymentResponse
from app.services import payments as payment_service
from app.toss.client import TossClient


@router.post("/payments", status_code=201)
async def create_payment(payload: OneOffPaymentRequest,
                         service: Service = Depends(payment_rate_limit),
                         db: AsyncSession = Depends(get_db),
                         toss: TossClient = Depends(get_toss),
                         cipher: AesGcmCipher = Depends(get_cipher)):
    payment = await payment_service.create_one_off_payment(
        db, toss, cipher, service=service, external_user_id=payload.external_user_id,
        order_id=payload.order_id, order_name=payload.order_name, amount=payload.amount,
        auth_key=payload.auth_key, customer_key=payload.customer_key)
    return PaymentResponse.model_validate(payment)
```
(기존 `GET /payments/{external_user_id}`는 그대로. `Depends`/`AsyncSession` import 확인.)

- [ ] **Step 6: 감사 라벨** — `app/admin/audit_labels.py`의 `ACTION_LABELS`에 추가:
```python
    "payment.one_off": "단건 결제",
    "payment.one_off_failed": "단건 결제 실패",
    "payment.one_off_unresolved": "단건 결제 결과 불명",
```

- [ ] **Step 7: 통과 확인** — Run: `uv run pytest tests/integration/test_one_off_payment.py -q`
이어서 e2e로 API 1건 확인(있으면): `uv run pytest tests/e2e/test_full_flow.py -q`.

- [ ] **Step 8: 커밋**
```bash
git add app/services/payments.py app/api/v1/payments.py app/schemas/api.py app/admin/audit_labels.py tests/integration/test_one_off_payment.py
git commit -m "feat(payment): 단건 결제 API POST /api/v1/payments"
```

---

### Task 3: 정산 스윕 단건 지원 (`renewals.py`)

**Files:**
- Modify: `app/services/renewals.py`
- Test: `tests/integration/test_one_off_payment.py`(추가)

- [ ] **Step 1: 실패 테스트 추가** — `test_one_off_payment.py` 끝에:
```python
async def test_reconcile_confirms_one_off(db, cipher, fake):
    from datetime import timedelta
    from app.core.clock import utcnow
    from app.services.renewals import process_due
    from app.toss.errors import TossTimeoutError
    svc, _, _ = await create_service(db, cipher)
    fake.charge_error = TossTimeoutError()                     # 타임아웃 → PENDING
    with pytest.raises(PaymentFailedError):
        await _pay(db, fake, cipher, svc, order_id="oo-rec")
    # 토스에는 실제로 DONE으로 존재한다고 설정(fake가 지원하는 방식으로)
    fake.charge_error = None
    fake.set_order_status("oo-rec", "DONE")                    # fake 인터페이스에 맞춰 조정
    # 유예(10분) 지난 것처럼 requested_at을 과거로
    row = await db.scalar(select(Payment).where(Payment.order_id == "oo-rec"))
    row.requested_at = utcnow() - timedelta(minutes=15); await db.commit()
    await process_due(_session_factory(db), _redis(), fake, cipher, _email(), now=utcnow())
    db.expire_all()
    row = await db.scalar(select(Payment).where(Payment.order_id == "oo-rec"))
    assert row.status == PaymentStatus.DONE
```
주의: `process_due`는 session_factory/redis/email_sender가 필요하다. 기존 `test_renewals.py`가
이들을 어떻게 준비하는지(픽스처) 참고해 동일하게 구성할 것. fake의 order 상태 주입 메서드명도
`app/toss/fake.py`에서 확인해 맞춘다. (이 테스트가 fake 한계로 까다로우면, 핵심인 outerjoin/None
가드만 검증하는 더 단순한 통합 테스트로 대체 가능 — 단 단건 PENDING이 스윕 대상에 포함됨을 보일 것.)

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/integration/test_one_off_payment.py -k reconcile -x -q`

- [ ] **Step 3: 스윕 수정** — `app/services/renewals.py`

`_reconcile_pending_payments`의 stuck 조회를 outerjoin으로:
```python
        stuck = (await db.execute(
            select(Payment, Subscription)
            .outerjoin(Subscription, Payment.subscription_id == Subscription.id)
            .where(Payment.status == PaymentStatus.PENDING,
                   Payment.requested_at <= now - PENDING_RECONCILE_GRACE))).all()
```
(단건은 subscription_id NULL → outerjoin이라야 포함. 기존 스킵 조건
`if stuck_payment.payment_type != FIRST and stuck_sub.status in _DUE_STATUSES`는 `stuck_sub`가
None이면 AttributeError 위험 → `stuck_sub is not None and stuck_sub.status in _DUE_STATUSES`로 가드.)

`_reconcile_one_payment`에서 sub 조회/사용을 None 안전하게:
```python
            sub = (await db.get(Subscription, payment.subscription_id)
                   if payment.subscription_id else None)
            if (payment.payment_type != PaymentType.FIRST and sub is not None
                    and sub.status in _DUE_STATUSES):
                return
            ...
            # NOT_FOUND 분기의 "FIRST + sub.status == ACTIVE"는 sub None이면 자연히 미해당
            if (payment.payment_type == PaymentType.FIRST and sub is not None
                    and sub.status == SubscriptionStatus.ACTIVE):
                ...
            # orphaned 판정도 sub is not None 가드(이미 그렇게 돼 있으면 유지)
```
단건(sub None)은 DONE/FAILED 확정만 하고 구독 관련 분기는 모두 건너뛴다.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/integration/test_one_off_payment.py tests/integration/test_renewals.py -q`

- [ ] **Step 5: 커밋**
```bash
git add app/services/renewals.py tests/integration/test_one_off_payment.py
git commit -m "feat(reconcile): PENDING 정산 스윕이 단건 결제도 확정"
```

---

### Task 4: Admin 결제리스트 — 종류/서비스 컬럼·필터

**Files:**
- Modify: `app/admin/routes/subscriptions.py`(`payments_list`)
- Modify: `app/admin/templates/payments/list.html`
- Test: `tests/e2e/test_admin_operations.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/e2e/test_admin_operations.py` 끝에:
```python
async def test_payments_kind_and_service_filter(client, db, redis_client, cipher):
    from app.models import Payment, PaymentKind, PaymentStatus
    svc_a, _, _ = await create_service(db, cipher, name="결제구분A")
    svc_b, _, _ = await create_service(db, cipher, name="결제구분B")
    plan = await create_plan(db, svc_a)
    sub = await create_subscription(db, cipher, svc_a, plan, external_user_id="sub-user")
    db.add(Payment(subscription_id=sub.id, service_id=svc_a.id, external_user_id="sub-user",
                   order_id="k-sub", amount=1000, payment_type="RENEWAL",
                   kind=PaymentKind.SUBSCRIPTION, status=PaymentStatus.DONE,
                   idempotency_key="k-sub", requested_at=utcnow()))
    db.add(Payment(subscription_id=None, service_id=svc_b.id, external_user_id="oo-user",
                   order_id="k-oo", amount=2000, payment_type="ONE_OFF",
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="k-oo", requested_at=utcnow()))
    await db.commit()
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)

    def tbody(h): return h[h.find("<tbody>"):]
    # 종류 필터
    body = tbody((await client.get("/admin/payments?kind=ONE_OFF")).text)
    assert "k-oo" in body and "k-sub" not in body
    # 서비스 필터
    body = tbody((await client.get(f"/admin/payments?service_id={svc_a.id}")).text)
    assert "k-sub" in body and "k-oo" not in body
    # 컨트롤 렌더 + 단건 사용자 표시(구독 없음)
    html = (await client.get("/admin/payments")).text
    assert 'name="kind"' in html and 'name="service_id"' in html
    assert "oo-user" in html
```
(`utcnow` import 필요 — 파일 상단/테스트 내 `from app.core.clock import utcnow`.)

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_admin_operations.py::test_payments_kind_and_service_filter -x -q`

- [ ] **Step 3: 라우트 수정** — `app/admin/routes/subscriptions.py` `payments_list`

쿼리를 outerjoin + Payment.service_id 기준으로:
```python
    pp = PageParams.from_request(request, sortable=set(_PAY_SORT),
                                 default_sort="requested_at",
                                 filter_keys=("status", "from", "to", "kind", "service_id"))
    base = (select(Payment, Subscription, Service)
            .outerjoin(Subscription, Payment.subscription_id == Subscription.id)
            .join(Service, Payment.service_id == Service.id))
    scope = _scope(ctx)
    if scope is not None:
        base = base.where(Payment.service_id.in_(scope))
    if pp.q:
        base = base.where(
            Payment.order_id.ilike(f"%{pp.q}%")
            | Payment.external_user_id.ilike(f"%{pp.q}%"))
    if pp.filters.get("status"):
        base = base.where(Payment.status == pp.filters["status"])
    if pp.filters.get("kind"):
        base = base.where(Payment.kind == pp.filters["kind"])
    service_filter = pp.filters.get("service_id", "")
    if service_filter:
        try:
            base = base.where(Payment.service_id == uuid.UUID(service_filter))
        except ValueError:
            service_filter = ""; pp.filters.pop("service_id", None)
    start, end = date_range(pp)
    if start: base = base.where(Payment.requested_at >= start)
    if end:   base = base.where(Payment.requested_at < end)
    count_q = select(func.count()).select_from(base.order_by(None).subquery())
    items_q = base.order_by(pp.order_by(_PAY_SORT))
    page = await paginate(db, items_q, count_q, pp)
    # 서비스 옵션(스코프 내)
    svc_q = select(Service.id, Service.name).order_by(Service.name)
    if scope is not None:
        svc_q = svc_q.where(Service.id.in_(scope))
    service_options = [("", "전체 서비스")] + [(str(sid), name)
                       for sid, name in (await db.execute(svc_q)).all()]
    return render(request, "payments/list.html", ctx=ctx, page=page, pp=pp,
                  status_filter=pp.filters.get("status", ""),
                  from_filter=pp.filters.get("from", ""), to_filter=pp.filters.get("to", ""),
                  kind_filter=pp.filters.get("kind", ""),
                  service_filter=service_filter, service_options=service_options)
```
(`Service` import 확인 — 이미 있음. `page.items`는 `(Payment, Subscription|None, Service)` 3튜플.)

- [ ] **Step 4: 템플릿 수정** — `app/admin/templates/payments/list.html`

toolbar에 kind/service select 추가, 행에 종류/서비스 컬럼, 사용자/주문은 Payment 기준:
```html
{{ L.toolbar('/admin/payments', pp, '주문번호·사용자 검색',
   [('status', [('','전체 상태'),('DONE','DONE'),('FAILED','FAILED'),('PENDING','PENDING'),('CANCELED','CANCELED')], status_filter),
    ('kind', [('','전체 종류'),('SUBSCRIPTION','구독'),('ONE_OFF','일반')], kind_filter),
    ('service_id', service_options, service_filter)],
   date_inputs=[('from', from_filter), ('to', to_filter)]) }}
<div class="card">
<table>
  <thead><tr>
    {{ L.sort_th(pp, '/admin/payments', 'order_id', '주문번호') }}
    <th>서비스</th><th>종류</th><th>사용자</th><th>유형</th>
    {{ L.sort_th(pp, '/admin/payments', 'amount', '금액') }}
    {{ L.sort_th(pp, '/admin/payments', 'status', '상태') }}
    <th>실패 코드</th>
    {{ L.sort_th(pp, '/admin/payments', 'requested_at', '요청 시각') }}
  </tr></thead>
  <tbody>
  {% for p, sub, svc in page.items %}
    <tr>
      <td style="font-family:ui-monospace,monospace;font-size:12px">{{ p.order_id }}</td>
      <td class="muted">{{ svc.name }}</td>
      <td><span class="badge">{{ '구독' if p.kind == 'SUBSCRIPTION' else '일반' }}</span></td>
      <td>{{ p.external_user_id or '-' }}</td>
      <td>{{ p.payment_type }}</td>
      <td style="font-weight:600">{{ "{:,}".format(p.amount) }}원</td>
      <td><span class="badge badge-{{ p.status }}">{{ p.status }}</span></td>
      <td class="muted">{{ p.failure_code or '-' }}</td>
      <td class="muted">{{ p.requested_at|kst("%Y-%m-%d %H:%M") }}</td>
    </tr>
  {% else %}
    <tr><td colspan="9" class="muted">결제 이력이 없습니다</td></tr>
  {% endfor %}
  </tbody>
</table>
{{ L.pager(page, '/admin/payments', pp) }}
</div>
```

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/e2e/test_admin_operations.py -q` → 전체 PASS
(기존 결제 범위 필터 테스트가 `(p, sub)` 2튜플을 가정하지 않는지 — 템플릿만 쓰므로 영향 없음. 라우트
반환이 3튜플로 바뀌었으니 `payments/list.html`만 그 형태를 읽으면 됨. 회귀 확인.)

- [ ] **Step 6: 커밋**
```bash
git add app/admin/routes/subscriptions.py app/admin/templates/payments/list.html tests/e2e/test_admin_operations.py
git commit -m "feat(admin): 결제리스트 종류(구독/일반)·서비스 필터 + Payment.service_id 기준"
```

---

### Task 5: 정산 — service_id 기준 + 구독/일반 분리

**Files:**
- Modify: `app/services/settlement.py`, `app/admin/templates/settlement/index.html`
- Modify: `app/admin/routes/settlement.py`(건별 목록 join)
- Test: `tests/integration/test_settlement.py`, `tests/e2e/test_settlement_page.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/integration/test_settlement.py` 끝에:
```python
async def test_settlement_splits_subscription_and_one_off(db, cipher):
    from app.models import Payment, PaymentKind, PaymentStatus
    from datetime import datetime, timezone
    UTC = timezone.utc
    svc, _, _ = await create_service(db, cipher, name="정산분리")
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u")
    when = datetime(2026, 5, 10, tzinfo=UTC)
    db.add(Payment(subscription_id=sub.id, service_id=svc.id, external_user_id="u",
                   order_id="s-sub", amount=10000, payment_type="RENEWAL",
                   kind=PaymentKind.SUBSCRIPTION, status=PaymentStatus.DONE,
                   idempotency_key="s-sub", requested_at=when, approved_at=when))
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="u2",
                   order_id="s-oo", amount=3000, payment_type="ONE_OFF",
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="s-oo", requested_at=when, approved_at=when))
    await db.commit()
    count, amount, rows = await settlement_summary(
        db, None, datetime(2026,5,1,tzinfo=UTC), datetime(2026,6,1,tzinfo=UTC))
    assert amount == 13000
    row = next(r for r in rows if r.service_name == "정산분리")
    assert row.sub_amount == 10000 and row.one_off_amount == 3000
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/integration/test_settlement.py -k splits -x -q`

- [ ] **Step 3: settlement_summary 수정** — `app/services/settlement.py`

`SettlementRow`에 분리 필드 추가 + 쿼리를 Payment.service_id join + kind별 조건합:
```python
from sqlalchemy import case, func, select
from app.models import Payment, PaymentKind, PaymentStatus, Service, Subscription  # Subscription 제거 가능

@dataclass
class SettlementRow:
    service_id: uuid.UUID
    service_name: str
    count: int
    amount: int
    sub_amount: int
    one_off_amount: int


async def settlement_summary(db, scope, start, end):
    sub_sum = func.coalesce(func.sum(case(
        (Payment.kind == PaymentKind.SUBSCRIPTION, Payment.amount), else_=0)), 0)
    oo_sum = func.coalesce(func.sum(case(
        (Payment.kind == PaymentKind.ONE_OFF, Payment.amount), else_=0)), 0)
    amount_sum = func.coalesce(func.sum(Payment.amount), 0)
    q = (select(Service.id, Service.name, func.count(Payment.id),
                amount_sum, sub_sum, oo_sum)
         .select_from(Payment)
         .join(Service, Payment.service_id == Service.id)     # 구독 경유 제거
         .where(Payment.status == PaymentStatus.DONE)
         .group_by(Service.id, Service.name)
         .order_by(amount_sum.desc(), Service.name))
    if start: q = q.where(Payment.approved_at >= start)
    if end:   q = q.where(Payment.approved_at < end)
    if scope is not None: q = q.where(Payment.service_id.in_(scope))
    rows = [SettlementRow(sid, name, int(c), int(a), int(sa), int(oo))
            for sid, name, c, a, sa, oo in (await db.execute(q)).all()]
    return (sum(r.count for r in rows), sum(r.amount for r in rows), rows)
```
(`Subscription` import는 안 쓰면 제거.)

- [ ] **Step 4: 템플릿 수정** — `app/admin/templates/settlement/index.html`

전체 모드 서비스별 테이블에 구독/일반 컬럼 추가:
```html
  <thead><tr><th>서비스</th><th>건수</th><th>구독</th><th>일반</th><th>금액</th><th></th></tr></thead>
  ...
    <td class="muted">{{ "{:,}".format(r.sub_amount) }}원</td>
    <td class="muted">{{ "{:,}".format(r.one_off_amount) }}원</td>
    <td style="font-weight:600">{{ "{:,}".format(r.amount) }}원</td>
```
서비스별 모드(건별)에서 `settlement_view`의 건별 쿼리도 종류 컬럼 표시(`p.kind`). 라우트의 건별
쿼리는 이미 `Payment` 기준 → 그대로 두되 `Subscription`이 None일 수 있으면 사용자 표시를
`p.external_user_id`로. (건별 쿼리가 `Payment JOIN Subscription`이면 outerjoin으로 바꾸고 사용자/종류를
Payment에서 읽도록 정리.)

- [ ] **Step 5: e2e 추가** — `tests/e2e/test_settlement_page.py`에 전체 모드 구독/일반 컬럼 노출 검증 1건.

- [ ] **Step 6: 통과 확인** — Run: `uv run pytest tests/integration/test_settlement.py tests/e2e/test_settlement_page.py -q`

- [ ] **Step 7: 커밋**
```bash
git add app/services/settlement.py app/admin/routes/settlement.py app/admin/templates/settlement/index.html tests/integration/test_settlement.py tests/e2e/test_settlement_page.py
git commit -m "feat(settlement): service_id 기준 + 구독/일반 금액 분리"
```

---

### Task 6: 대시보드 — 일반결제 카드 + 매출 스코프 통일

**Files:**
- Modify: `app/services/dashboard.py`, `app/admin/templates/dashboard.html`
- Test: `tests/integration/test_dashboard.py`, `tests/e2e/test_dashboard_page.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/integration/test_dashboard.py` 끝에:
```python
async def test_dashboard_one_off_card(db, cipher):
    from app.models import Payment, PaymentKind, PaymentStatus
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u", status="ACTIVE")
    await _paid(db, sub, 10000, order="d-sub")                  # 구독 결제(헬퍼)
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="oo",
                   order_id="d-oo", amount=4000, payment_type="ONE_OFF",
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="d-oo", requested_at=utcnow(), approved_at=utcnow()))
    await db.commit()
    data = await build_dashboard(db, None)
    card = _card(data, "이번달 일반결제")
    assert card.value == "4,000원"
```
(`utcnow` import 확인.)

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/integration/test_dashboard.py -k one_off -x -q`

- [ ] **Step 3: dashboard.py 수정** — `app/services/dashboard.py`

(a) 결제 매출/건수 헬퍼의 스코프를 `Payment.service_id` 기준으로 통일(단건 포함):
```python
async def _revenue_between(db, scope, start, end, *, kind=None) -> int:
    q = select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.status == PaymentStatus.DONE,
        Payment.approved_at >= start, Payment.approved_at < end)
    if kind is not None:
        q = q.where(Payment.kind == kind)
    return int(await db.scalar(_scoped(q, scope, Payment.service_id)) or 0)
```
(`_scoped`가 컬럼을 받으므로 `Payment.service_id` 전달. 기존 `Subscription` join 제거.
`_payment_count_between`도 동일하게 `Payment.service_id` 기준으로.)
import에 `PaymentKind` 추가.

(b) `_month_cards`에 일반결제 카드 추가(반환 리스트에 한 항목):
```python
    one_off_rev = await _revenue_between(db, scope, month_start, end, kind=PaymentKind.ONE_OFF)
    ...
    StatCard("이번달 일반결제", _won(one_off_rev), "", True, 4,
             f"/admin/payments?kind=ONE_OFF&{range_qs}"),
```
(카드가 9개가 되며 tint는 1~4 순환 임의 — 기존 스타일 따름.)

- [ ] **Step 4: 템플릿** — 카드 루프는 `d.cards`를 도는 구조라 **자동 렌더**됨. 별도 수정 불필요
  (카드 개수만 늘어남). 레이아웃이 깨지면 `dashboard.html`의 stats grid 확인.

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/integration/test_dashboard.py tests/e2e/test_dashboard_page.py -q`
e2e `test_dashboard_cards_with_links`가 카드 라벨 목록을 검사하면 "이번달 일반결제" 추가에 맞춰 갱신.

- [ ] **Step 6: 커밋**
```bash
git add app/services/dashboard.py app/admin/templates/dashboard.html tests/integration/test_dashboard.py tests/e2e/test_dashboard_page.py
git commit -m "feat(dashboard): 이번달 일반결제 카드 + 결제 매출 스코프 통일(Payment.service_id)"
```

---

### Task 7: 전체 검증

- [ ] **Step 1: 전체 테스트** — Run: `uv run pytest -q` → 전체 PASS.
- [ ] **Step 2: 마이그레이션 단일 head** — Run: `uv run alembic heads` → `c3d4e5f6a7b8`.
- [ ] **Step 3: 잔여 확인**
  - `grep -rn "join(Subscription, Payment.subscription_id" app/services app/admin` — 정산/대시보드/리스트에서
    Payment를 구독 경유로 스코프하는 곳이 남았는지(있으면 `Payment.service_id` 기준으로 통일 검토).
  - `grep -rn "Payment(" app tests --include="*.py" | grep -v service_id` — service_id 누락 생성부 없는지.
- [ ] **Step 4: 커밋(잔여 정리 시)**
```bash
git add -A app tests
git commit -m "test: 단건 결제 잔여 정리"
```

## 변경하지 않는 것 (스펙 동일)

- 구독 결제 흐름(04~06)의 금액 계산·상태 전이. 외부 API 인증(08). 감사 기록 방식(10).
- 빌링키는 Payment에 저장하지 않음(단건 미보관, 구독은 기존대로 Subscription 보관).
