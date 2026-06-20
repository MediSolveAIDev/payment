# 카드 등록 기반 결제 흐름 (Card Vault) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 결제수단(카드)을 1급 엔티티(`cards` 테이블)로 도입하고, 구독·단건결제가 사전 등록된 카드를 참조해 처리하도록 기존 `auth_key`-매회-전달 방식을 완전히 대체한다.

**Architecture:** 신규 `cards` 테이블이 빌링키(AES-GCM 암호문)·customer_key·카드 마스킹정보를 `(service_id, external_user_id)`당 1건 보관한다. 빌링키를 `subscriptions`에서 떼어 카드로 옮기고, 구독은 `card_id` FK로 카드를 참조한다. 카드 등록 API(`POST/GET/DELETE /api/v1/cards`)에서만 토스 빌링키를 발급하며, 구독·단건결제·자동갱신은 카드에서 빌링키를 읽어 결제한다.

**Tech Stack:** FastAPI · SQLAlchemy(async) · Alembic · PostgreSQL · Redis · pytest(async) · 토스페이먼츠 빌링 API · htmx(admin).

**스펙:** `docs/superpowers/specs/2026-06-19-card-registration-payment-flow-design.md`

---

## 사전 참고(엔지니어가 먼저 읽을 파일)

- 모델 패턴: `app/models/payment.py`, `app/models/subscription.py`, `app/models/base.py`(TimestampMixin), `app/models/__init__.py`
- 서비스 패턴: `app/services/registry.py`(검증·감사·flush 경쟁 처리), `app/services/subscriptions.py`(create_subscription), `app/services/payments.py`(create_one_off_payment), `app/services/payment_utils.py`(resolve_charge, delete billing key)
- 암호/해시: `app/core/crypto.py`(AesGcmCipher.encrypt/decrypt), `app/core/security.py`(sha256_hex), `app/core/deps.py`(get_cipher)
- API 패턴: `app/api/v1/subscriptions.py`, `app/api/v1/payments.py`, `app/api/deps.py`(authenticate_service, payment_rate_limit, get_toss, get_db, get_cipher), `app/api/openapi.py`(응답 상수)
- 스키마: `app/schemas/api.py`
- 토스: `app/toss/client.py`(issue_billing_key/charge/delete_billing_key), `app/toss/fake.py`(테스트용)
- 감사: `app/services/audit*`의 `record_audit` (registry.py 사용례 참고)
- 테스트 인프라: `tests/conftest.py`(스키마는 `Base.metadata.create_all`로 생성 — 모델 변경이 자동 반영), `tests/factories.py`(create_service/create_plan), `tests/helpers.py`(api_request/signed_headers/client_from_ip)

**개발 인프라 기동(테스트 전 필수):** `docker compose up -d` (Postgres 5433 · Redis 6380).

---

## Task 1: `cards` 모델 추가

**Files:**
- Create: `app/models/card.py`
- Modify: `app/models/__init__.py` (Card export)
- Test: `tests/integration/test_models.py` (Card 생성·유니크)

- [ ] **Step 1: 실패 테스트 작성** — `tests/integration/test_models.py`에 추가

```python
async def test_card_unique_per_service_user(db):
    """(service_id, external_user_id)당 카드 1건 — 중복은 IntegrityError."""
    import uuid
    from sqlalchemy.exc import IntegrityError
    from app.models import Card, Service
    svc = Service(name=f"svc-{uuid.uuid4().hex[:6]}", allowed_ips=[],
                  manager_email="m@x.com", api_key_hash="h"+uuid.uuid4().hex,
                  api_key_encrypted="e", hmac_secret_encrypted="e")
    db.add(svc); await db.flush()
    db.add(Card(service_id=svc.id, external_user_id="u1", customer_key="c1",
                billing_key_encrypted="enc", billing_key_hash="h1", card_info={"n": "1"}))
    await db.flush()
    db.add(Card(service_id=svc.id, external_user_id="u1", customer_key="c2",
                billing_key_encrypted="enc2", billing_key_hash="h2"))
    with pytest.raises(IntegrityError):
        await db.flush()
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_models.py::test_card_unique_per_service_user -v` · Expected: FAIL (`ImportError: cannot import name 'Card'`)

- [ ] **Step 3: 모델 구현** — `app/models/card.py` 생성

```python
"""카드(Card) 모델 — 결제수단 보관함(vault).

(service_id, external_user_id)당 1건. 토스 빌링키를 암호화 보관하고,
구독·단건결제가 이 카드를 참조해 결제한다. 카드 등록 API에서만 생성/교체된다.
"""
import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Card(TimestampMixin, Base):
    __tablename__ = "cards"
    __table_args__ = (
        # 사용자(서비스+external_user_id)당 카드 1장 — 재등록은 교체(서비스 레이어에서 upsert)
        UniqueConstraint("service_id", "external_user_id", name="uq_cards_service_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), index=True)  # 카드가 속한 서비스
    external_user_id: Mapped[str] = mapped_column(String(255))        # 외부 서비스 사용자 ID
    customer_key: Mapped[str] = mapped_column(String(300))            # 토스 customerKey(등록 시 SDK 사용값)
    billing_key_encrypted: Mapped[str] = mapped_column(String(1024)) # 자동결제 빌링키(AES-GCM 암호문)
    billing_key_hash: Mapped[str] = mapped_column(String(64), index=True)  # 빌링키 SHA-256(중복/조회용)
    card_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)    # 카드 마스킹 정보(표시용)
```

- [ ] **Step 4: export 추가** — `app/models/__init__.py`에 `Card`를 다른 모델과 동일한 형식으로 import·`__all__` 등록(파일의 기존 패턴을 그대로 따른다).

- [ ] **Step 5: 통과 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_models.py::test_card_unique_per_service_user -v` · Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add app/models/card.py app/models/__init__.py tests/integration/test_models.py
git commit -m "feat(model): cards 테이블 추가(결제수단 보관함)"
```

---

## Task 2: `subscriptions` 모델을 카드 참조로 변경

**Files:**
- Modify: `app/models/subscription.py` (빌링키 컬럼 4개 제거, `card_id` 추가)
- Test: 기존 모델 테스트로 충분(create_all 반영). 본 Task는 모델 정의 변경만.

- [ ] **Step 1: 컬럼 교체** — `app/models/subscription.py`에서 다음 4개 컬럼 **제거**: `customer_key`, `billing_key_encrypted`, `billing_key_hash`, `card_info`. 그 자리에 **추가**:

```python
    card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cards.id", ondelete="RESTRICT"), index=True)  # 결제에 사용할 등록 카드
```

(주의: 클래스 상단 docstring의 "card_info는 …" 문장도 카드 테이블로 이동했음을 반영해 수정.)

- [ ] **Step 2: import 확인** — `ForeignKey`가 이미 import되어 있는지 확인(payment.py처럼). 없으면 추가.

- [ ] **Step 3: 정적 점검** — Run: `.venv/bin/python -c "from app.models import Subscription, Card; print('ok', 'card_id' in Subscription.__table__.columns, 'billing_key_encrypted' not in Subscription.__table__.columns)"` · Expected: `ok True True`

- [ ] **Step 4: 커밋**

```bash
git add app/models/subscription.py
git commit -m "feat(model): subscriptions 빌링키 컬럼 제거 + card_id FK 추가"
```

---

## Task 3: Alembic 마이그레이션 (cards 생성 + subscriptions 변경)

**Files:**
- Create: `alembic/versions/<rev>_card_vault.py` (rev id 예: `a1b2c3d4e5f6`, down_revision = 현재 head `f2a3b4c5d6e7`)

> head 확인: `.venv/bin/python -m alembic heads` → 결과를 down_revision으로 사용.

- [ ] **Step 1: 마이그레이션 작성** — `alembic/versions/a1b2c3d4e5f6_card_vault.py`

```python
"""카드 보관함: cards 생성 + subscriptions 빌링키 컬럼→card_id 이동

운영 전 도입이라 기존 구독 데이터 보존 불필요. dev/test DB에 남은 subscriptions 행은
리셋 전제(card_id NOT NULL 추가가 기존 행과 충돌). 신규 환경은 깨끗하게 적용된다.

Revision ID: a1b2c3d4e5f6
Revises: f2a3b4c5d6e7
Create Date: 2026-06-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'cards',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('service_id', UUID(as_uuid=True),
                  sa.ForeignKey('services.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('external_user_id', sa.String(255), nullable=False),
        sa.Column('customer_key', sa.String(300), nullable=False),
        sa.Column('billing_key_encrypted', sa.String(1024), nullable=False),
        sa.Column('billing_key_hash', sa.String(64), nullable=False),
        sa.Column('card_info', JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('service_id', 'external_user_id', name='uq_cards_service_user'),
    )
    op.create_index('ix_cards_service_id', 'cards', ['service_id'])
    op.create_index('ix_cards_billing_key_hash', 'cards', ['billing_key_hash'])

    # 운영 전 — 기존 구독 데이터 없음 전제. 안전하게 비운 뒤 컬럼 교체.
    op.execute('DELETE FROM subscriptions')
    op.drop_column('subscriptions', 'card_info')
    op.drop_column('subscriptions', 'billing_key_hash')
    op.drop_column('subscriptions', 'billing_key_encrypted')
    op.drop_column('subscriptions', 'customer_key')
    op.add_column('subscriptions', sa.Column('card_id', UUID(as_uuid=True), nullable=False))
    op.create_foreign_key('fk_subscriptions_card', 'subscriptions', 'cards',
                          ['card_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_subscriptions_card_id', 'subscriptions', ['card_id'])


def downgrade() -> None:
    op.drop_index('ix_subscriptions_card_id', 'subscriptions')
    op.drop_constraint('fk_subscriptions_card', 'subscriptions', type_='foreignkey')
    op.drop_column('subscriptions', 'card_id')
    op.add_column('subscriptions', sa.Column('customer_key', sa.String(300), nullable=True))
    op.add_column('subscriptions', sa.Column('billing_key_encrypted', sa.String(1024), nullable=True))
    op.add_column('subscriptions', sa.Column('billing_key_hash', sa.String(64), nullable=True))
    op.add_column('subscriptions', sa.Column('card_info', JSONB, nullable=True))
    op.drop_table('cards')
```

> `TimestampMixin`의 created_at/updated_at가 `server_default`를 쓰는지 `app/models/base.py`에서 확인. server_default가 없다면 위처럼 nullable=False 컬럼에 default가 필요하므로, 기존 다른 테이블 마이그레이션의 created_at 정의 방식을 그대로 모방한다.

- [ ] **Step 2: 적용·롤백 검증** — Run:
```
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic downgrade -1
.venv/bin/python -m alembic upgrade head
```
Expected: 오류 없이 왕복. `docker compose exec -T postgres psql -U payment -d payment -c "\d cards"` 로 테이블 확인.

- [ ] **Step 3: 커밋**

```bash
git add alembic/versions/a1b2c3d4e5f6_card_vault.py
git commit -m "feat(db): card_vault 마이그레이션(cards 생성, subscriptions card_id)"
```

---

## Task 4: 카드 서비스 — 등록/교체 (`register_or_replace_card`)

**Files:**
- Create: `app/services/cards.py`
- Test: `tests/integration/test_cards.py`

토스 fake는 `tests/conftest.py`/`tests/factories.py`에서 주입되는 방식(기존 구독 테스트가 쓰는 fake)을 그대로 사용한다. fake의 `issue_billing_key`/`delete_billing_key` 동작은 `app/toss/fake.py` 참고.

- [ ] **Step 1: 실패 테스트** — `tests/integration/test_cards.py`

```python
import pytest
from app.services.cards import register_or_replace_card, get_card, delete_card
from app.core.errors import ConflictError
from tests.factories import create_service


async def test_register_card_stores_encrypted_billing_key(db, cipher, fake_toss):
    svc, _, _ = await create_service(db, cipher)
    card = await register_or_replace_card(
        db, fake_toss, cipher, service=svc, external_user_id="u1",
        customer_key="cust-1", auth_key="authkey-1")
    assert card.external_user_id == "u1"
    assert card.billing_key_encrypted and card.billing_key_encrypted != "authkey-1"
    assert card.billing_key_hash
    # 같은 사용자 재등록 → 같은 행 교체(새 행 아님)
    card2 = await register_or_replace_card(
        db, fake_toss, cipher, service=svc, external_user_id="u1",
        customer_key="cust-1", auth_key="authkey-2")
    assert card2.id == card.id
```

> `fake_toss` fixture가 없다면 기존 구독 테스트가 토스를 어떻게 주입하는지 확인하여 동일 fixture명을 사용(예: conftest의 `toss`/`fake_toss`). 픽스처명은 기존 컨벤션을 따른다.

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_cards.py::test_register_card_stores_encrypted_billing_key -v` · Expected: FAIL (ImportError)

- [ ] **Step 3: 구현** — `app/services/cards.py`

```python
"""카드(결제수단) 서비스 — 등록/교체·조회·삭제.

빌링키는 토스에서 발급해 AES-GCM으로 암호화 저장한다. (service, external_user_id)당
1건이며 재등록은 같은 행을 교체한다(옛 토스 빌링키는 best-effort 삭제).
"""
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import AesGcmCipher
from app.core.errors import ConflictError, InputValidationError, NotFoundError
from app.core.security import sha256_hex
from app.models import Card, Service, Subscription
from app.models.enums import OPEN_SUBSCRIPTION_STATUSES, SubscriptionStatus
from app.services.audit import record_audit          # 실제 모듈 경로는 registry.py import 확인
from app.services.payment_utils import delete_billing_key_safely  # 실제 함수명은 payment_utils 확인
from app.toss.client import TossClient

CUSTOMER_KEY_RE = re.compile(r"[A-Za-z0-9\-_=.@]{2,300}")  # subscriptions._validate_inputs와 동일 규칙

# 자동결제가 앞으로 일어날 수 있어 카드 삭제를 막아야 하는 상태(스펙 §6.1)
CARD_DELETE_BLOCKING_STATUSES = frozenset({
    SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE,
    SubscriptionStatus.SUSPENDED, SubscriptionStatus.EXTENDED,
})


async def get_card(db: AsyncSession, *, service_id, external_user_id: str) -> Card | None:
    return await db.scalar(select(Card).where(
        Card.service_id == service_id, Card.external_user_id == external_user_id))


async def register_or_replace_card(db: AsyncSession, toss: TossClient,
                                   cipher: AesGcmCipher, *, service: Service,
                                   external_user_id: str, customer_key: str,
                                   auth_key: str) -> Card:
    """카드 등록/교체. 토스 빌링키 발급 → 카드 upsert.

    - 기존 카드가 있으면 같은 행 교체(옛 토스 빌링키 best-effort 삭제).
    - 빌링키 발급 실패 시 기존 카드는 보존(망가뜨리지 않음).
    """
    if not CUSTOMER_KEY_RE.fullmatch(customer_key or ""):
        raise InputValidationError("customer_key 형식이 올바르지 않습니다")
    if not external_user_id or len(external_user_id) > 255:
        raise InputValidationError("external_user_id가 올바르지 않습니다")

    bk = await toss.issue_billing_key(auth_key, customer_key)  # 실패 시 TossError → 라우터에서 처리

    existing = await get_card(db, service_id=service.id, external_user_id=external_user_id)
    old_billing_key = None
    if existing is not None:
        old_billing_key = cipher.decrypt(existing.billing_key_encrypted)
        existing.customer_key = customer_key
        existing.billing_key_encrypted = cipher.encrypt(bk.billing_key)
        existing.billing_key_hash = sha256_hex(bk.billing_key)
        existing.card_info = bk.card
        card = existing
        action = "card.replace"
    else:
        card = Card(service_id=service.id, external_user_id=external_user_id,
                    customer_key=customer_key,
                    billing_key_encrypted=cipher.encrypt(bk.billing_key),
                    billing_key_hash=sha256_hex(bk.billing_key), card_info=bk.card)
        db.add(card)
        action = "card.register"

    await record_audit(db, actor_type="SERVICE", actor_service_id=service.id,
                       action=action, target_type="card", target_id=str(card.id),
                       detail={"external_user_id": external_user_id})
    await db.commit()

    # 교체 성공 후 옛 빌링키 정리(best-effort — 실패해도 교체는 유효)
    if old_billing_key:
        await delete_billing_key_safely(toss, old_billing_key)
    return card
```

> 구현 시 확인사항: (a) `record_audit` 실제 import 경로/인자(registry.py 사용례 그대로), (b) `delete_billing_key_safely` 실제 함수명(payment_utils.py에서 best-effort 삭제 함수 확인 — 없으면 `toss.delete_billing_key`를 try/except로 감싼다), (c) `OPEN_SUBSCRIPTION_STATUSES` import는 Task 6에서 사용.

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_cards.py::test_register_card_stores_encrypted_billing_key -v` · Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/services/cards.py tests/integration/test_cards.py
git commit -m "feat(service): 카드 등록/교체(register_or_replace_card)"
```

---

## Task 5: 카드 서비스 — 삭제 (`delete_card`) + 차단 규칙

**Files:**
- Modify: `app/services/cards.py`
- Test: `tests/integration/test_cards.py`

- [ ] **Step 1: 실패 테스트** — 추가

```python
async def test_delete_card_blocked_when_active_subscription(db, cipher, fake_toss):
    from app.services.cards import delete_card
    from app.core.errors import ConflictError
    svc, _, _ = await create_service(db, cipher)
    card = await register_or_replace_card(db, fake_toss, cipher, service=svc,
        external_user_id="u1", customer_key="cust-1", auth_key="a1")
    # ACTIVE 구독이 카드를 참조 → 삭제 거부
    from app.models import Subscription, Plan
    from tests.factories import create_plan
    plan = await create_plan(db, svc, name="p", price=1000)
    db.add(Subscription(service_id=svc.id, plan_id=plan.id, external_user_id="u1",
                        card_id=card.id, status="ACTIVE",
                        current_period_start=None, current_period_end=None))
    await db.commit()
    with pytest.raises(ConflictError):
        await delete_card(db, fake_toss, service_id=svc.id, external_user_id="u1")


async def test_delete_card_allowed_when_canceled(db, cipher, fake_toss):
    from app.services.cards import delete_card, get_card
    svc, _, _ = await create_service(db, cipher)
    card = await register_or_replace_card(db, fake_toss, cipher, service=svc,
        external_user_id="u2", customer_key="cust-1", auth_key="a1")
    from app.models import Subscription
    from tests.factories import create_plan
    plan = await create_plan(db, svc, name="p2", price=1000)
    db.add(Subscription(service_id=svc.id, plan_id=plan.id, external_user_id="u2",
                        card_id=card.id, status="CANCELED",
                        current_period_start=None, current_period_end=None))
    await db.commit()
    await delete_card(db, fake_toss, service_id=svc.id, external_user_id="u2")
    assert await get_card(db, service_id=svc.id, external_user_id="u2") is None
```

> Subscription 생성에 필요한 NOT NULL 필드(current_period_start/end 등)는 `tests/factories.py`에 `create_subscription`이 있으면 그것을 쓰고, 없으면 위처럼 직접 채운다. 실제 NOT NULL 목록은 모델 확인 후 맞춘다.

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_cards.py -k delete_card -v` · Expected: FAIL (delete_card 없음)

- [ ] **Step 3: 구현** — `app/services/cards.py`에 추가

```python
async def delete_card(db: AsyncSession, toss: TossClient, *, service_id,
                      external_user_id: str) -> None:
    """카드 삭제. 자동결제 예정 상태의 구독이 참조 중이면 거부(스펙 §6.1).
    토스 빌링키는 best-effort 삭제 후 카드행 삭제."""
    card = await get_card(db, service_id=service_id, external_user_id=external_user_id)
    if card is None:
        raise NotFoundError("등록된 카드가 없습니다")
    blocking = await db.scalar(select(Subscription).where(
        Subscription.card_id == card.id,
        Subscription.status.in_(CARD_DELETE_BLOCKING_STATUSES)))
    if blocking is not None:
        raise ConflictError("활성 구독이 사용 중인 카드는 삭제할 수 없습니다")
    billing_key = cipher_decrypt_billing_key(card)  # 아래 주석 참고
    await db.delete(card)
    await record_audit(db, actor_type="SERVICE", actor_service_id=service_id,
                       action="card.delete", target_type="card", target_id=str(card.id),
                       detail={"external_user_id": external_user_id})
    await db.commit()
    await delete_billing_key_safely(toss, billing_key)
```

> 주의: `delete_card`는 cipher가 필요하다(빌링키 복호화 후 토스 삭제). 시그니처에 `cipher: AesGcmCipher`를 추가하고 `billing_key = cipher.decrypt(card.billing_key_encrypted)`로 구한 뒤 삭제하라(위 `cipher_decrypt_billing_key` 자리). 테스트 호출부도 cipher 인자를 넘기도록 맞춘다.

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_cards.py -k delete_card -v` · Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/services/cards.py tests/integration/test_cards.py
git commit -m "feat(service): 카드 삭제 + 활성구독 차단 규칙"
```

---

## Task 6: 카드 외부 API (`POST/GET/DELETE /api/v1/cards`) + 스키마

**Files:**
- Create: `app/api/v1/cards.py`
- Modify: `app/schemas/api.py` (CardRegisterRequest, CardResponse 추가)
- Modify: `app/main.py` 또는 라우터 등록 위치 (cards 라우터 include — 기존 v1 라우터 등록부 확인)
- Test: `tests/integration/test_cards_api.py`

- [ ] **Step 1: 스키마 추가** — `app/schemas/api.py` (기존 모델들과 동일 스타일)

```python
class CardRegisterRequest(BaseModel):
    """카드 등록/교체 요청. auth_key는 토스 SDK에서 발급받은 1회용 인증값."""
    external_user_id: str = Field(min_length=1, max_length=255, examples=["user-123"])
    customer_key: str = Field(min_length=2, max_length=300, examples=["cust-123"])
    auth_key: str = Field(min_length=1, max_length=300, examples=["toss_auth_key_xxx"])


class CardResponse(BaseModel):
    """등록 카드 응답(마스킹). 빌링키는 절대 포함하지 않는다."""
    external_user_id: str
    card: dict | None = Field(default=None, description="카드 마스킹 정보")

    @classmethod
    def from_model(cls, card) -> "CardResponse":
        return cls(external_user_id=card.external_user_id, card=card.card_info)
```

- [ ] **Step 2: 실패 테스트** — `tests/integration/test_cards_api.py`

```python
from tests.factories import create_service
from tests.helpers import api_request


async def test_register_then_get_card(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    body = {"external_user_id": "u1", "customer_key": "cust-1", "auth_key": "ak-1"}
    resp = await api_request(client, "POST", "/api/v1/cards", api_key, secret, json=body)
    assert resp.status_code == 201
    assert "billingKey" not in resp.text and "billing_key" not in resp.text
    got = await api_request(client, "GET", "/api/v1/cards/u1", api_key, secret)
    assert got.status_code == 200
    assert got.json()["external_user_id"] == "u1"
```

> `api_request`의 정확한 시그니처(json 본문 전달 방식)는 `tests/helpers.py` 확인 후 맞춘다.

- [ ] **Step 3: 실패 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_cards_api.py -v` · Expected: FAIL (404/route 없음)

- [ ] **Step 4: 라우터 구현** — `app/api/v1/cards.py`

```python
"""카드 라우터 — 등록/교체·조회·삭제. 등록은 빌링키 발급이 있어 payment_rate_limit 적용."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (authenticate_service, get_cipher, get_db, get_toss,
                          payment_rate_limit)
from app.core.crypto import AesGcmCipher
from app.core.errors import NotFoundError
from app.models import Service
from app.schemas.api import CardRegisterRequest, CardResponse
from app.services import cards as card_service
from app.toss.client import TossClient

router = APIRouter()


@router.post("/cards", status_code=201, response_model=CardResponse, summary="카드 등록/교체")
async def register_card(payload: CardRegisterRequest,
                        service: Service = Depends(payment_rate_limit),
                        db: AsyncSession = Depends(get_db),
                        toss: TossClient = Depends(get_toss),
                        cipher: AesGcmCipher = Depends(get_cipher)):
    card = await card_service.register_or_replace_card(
        db, toss, cipher, service=service, external_user_id=payload.external_user_id,
        customer_key=payload.customer_key, auth_key=payload.auth_key)
    return CardResponse.from_model(card)


@router.get("/cards/{external_user_id}", response_model=CardResponse, summary="카드 조회")
async def get_card(external_user_id: str,
                   service: Service = Depends(authenticate_service),
                   db: AsyncSession = Depends(get_db)):
    card = await card_service.get_card(db, service_id=service.id,
                                       external_user_id=external_user_id)
    if card is None:
        raise NotFoundError("등록된 카드가 없습니다")
    return CardResponse.from_model(card)


@router.delete("/cards/{external_user_id}", status_code=204, summary="카드 삭제")
async def delete_card(external_user_id: str,
                      service: Service = Depends(authenticate_service),
                      db: AsyncSession = Depends(get_db),
                      toss: TossClient = Depends(get_toss),
                      cipher: AesGcmCipher = Depends(get_cipher)):
    await card_service.delete_card(db, toss, cipher, service_id=service.id,
                                   external_user_id=external_user_id)
```

- [ ] **Step 5: 라우터 등록** — v1 라우터 등록부(`app/api/v1/__init__.py` 또는 `app/main.py`에서 subscriptions/payments 라우터를 include하는 곳)에 cards 라우터를 동일 방식으로 추가.

- [ ] **Step 6: 통과 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_cards_api.py -v` · Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add app/api/v1/cards.py app/schemas/api.py tests/integration/test_cards_api.py app/api/v1/__init__.py
git commit -m "feat(api): /api/v1/cards 등록·조회·삭제"
```

---

## Task 7: 구독 생성 — `auth_key` 제거, 등록 카드 사용

**Files:**
- Modify: `app/services/subscriptions.py` (`create_subscription` 시그니처/본문)
- Modify: `app/schemas/api.py` (`SubscriptionCreateRequest`에서 auth_key/customer_key 제거)
- Modify: `app/api/v1/subscriptions.py` (create 라우트 호출부)
- Test: `tests/integration/test_subscription_create.py` (카드 선등록 방식으로 수정)

- [ ] **Step 1: 테스트 수정** — `test_subscription_create.py`의 구독 생성 케이스를 "카드 먼저 등록 → auth_key 없이 구독" 흐름으로 바꾸고, 카드 미등록 시 거부 케이스 추가:

```python
async def test_create_subscription_requires_registered_card(db, cipher, fake_toss):
    from app.services.subscriptions import create_subscription
    from app.core.errors import NotFoundError  # 또는 약속된 예외
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, name="basic", price=9900)
    with pytest.raises(NotFoundError):
        await create_subscription(db, fake_toss, cipher, service=svc, plan_id=plan.id,
                                  external_user_id="u1")


async def test_create_subscription_with_registered_card(db, cipher, fake_toss):
    from app.services.cards import register_or_replace_card
    from app.services.subscriptions import create_subscription
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, name="basic", price=9900)
    await register_or_replace_card(db, fake_toss, cipher, service=svc,
        external_user_id="u1", customer_key="c1", auth_key="ak")
    sub = await create_subscription(db, fake_toss, cipher, service=svc, plan_id=plan.id,
                                    external_user_id="u1")
    assert sub.card_id is not None and sub.status in ("ACTIVE", "TRIAL")
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_subscription_create.py -k registered_card -v` · Expected: FAIL

- [ ] **Step 3: 서비스 수정** — `app/services/subscriptions.py`의 `create_subscription`:
  - 시그니처에서 `customer_key`, `auth_key` 제거.
  - `_validate_inputs(customer_key, external_user_id)` 호출 제거(또는 external_user_id만 검증).
  - 빌링키 발급 블록(`bk = await toss.issue_billing_key(...)`) **삭제**.
  - 대신 등록 카드 조회: 없으면 거부.

```python
    from app.services.cards import get_card
    card = await get_card(db, service_id=service.id, external_user_id=external_user_id)
    if card is None:
        raise NotFoundError("등록된 카드가 없습니다. 먼저 카드를 등록하세요")
```
  - `Subscription(...)` 생성에서 `customer_key=…, billing_key_encrypted=…, billing_key_hash=…, card_info=…`를 제거하고 `card_id=card.id`로 대체.
  - 첫 결제(`resolve_charge`) 호출 시 빌링키/customer_key를 카드에서 읽도록 변경:
    `billing_key=cipher.decrypt(card.billing_key_encrypted), customer_key=card.customer_key`.
  - 첫 결제 실패 시 기존엔 빌링키를 삭제했는데, **이제 카드는 보존**(영속) — 빌링키 삭제 로직 제거. 구독 행만 정리(기존 로직 유지).

- [ ] **Step 4: 스키마 수정** — `SubscriptionCreateRequest`에서 `auth_key`, `customer_key` 필드 제거(나머지 `plan_id`, `external_user_id`, `trial` 유지).

- [ ] **Step 5: 라우트 수정** — `app/api/v1/subscriptions.py` create 라우트에서 `customer_key=payload.customer_key, auth_key=payload.auth_key` 인자 제거.

- [ ] **Step 6: 통과 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_subscription_create.py -v` · Expected: PASS (수정한 케이스 포함)

- [ ] **Step 7: 커밋**

```bash
git add app/services/subscriptions.py app/schemas/api.py app/api/v1/subscriptions.py tests/integration/test_subscription_create.py
git commit -m "feat: 구독 생성을 등록 카드 참조 방식으로 변경(auth_key 제거)"
```

---

## Task 8: 자동 갱신·수동결제 — 카드에서 빌링키 읽기

**Files:**
- Modify: `app/services/renewals.py` (갱신 결제에서 `sub.billing_key_encrypted` → 카드 조회)
- Modify: `app/services/subscriptions.py` (수동결제 `pay` 경로의 빌링키 출처)
- Test: `tests/integration/test_renewals.py`, `tests/integration/test_subscription_manage.py` (카드 선등록 반영)

- [ ] **Step 1: 사용처 식별** — Run: `grep -rn "billing_key_encrypted\|sub.customer_key\|\.card_info" app/services` · 구독에서 빌링키/customer_key/card_info를 읽던 모든 지점을 카드 조회로 바꿔야 한다.

- [ ] **Step 2: 테스트 수정** — `test_renewals.py`·`test_subscription_manage.py`에서 구독을 만들 때 **카드를 먼저 등록**하도록 픽스처/헬퍼를 수정(또는 `tests/factories.py`에 `create_card` + `create_subscription(card=...)` 헬퍼 추가). 헬퍼 추가 시:

```python
# tests/factories.py
async def create_card(db, cipher, service, external_user_id="u1", billing_key="bk-test"):
    from app.models import Card
    from app.core.security import sha256_hex
    card = Card(service_id=service.id, external_user_id=external_user_id,
                customer_key="cust-"+external_user_id,
                billing_key_encrypted=cipher.encrypt(billing_key),
                billing_key_hash=sha256_hex(billing_key), card_info={"number": "****"})
    db.add(card); await db.flush()
    return card
```

- [ ] **Step 3: 실패 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_renewals.py -v` · Expected: FAIL (구독에 billing_key_encrypted 없음 → AttributeError)

- [ ] **Step 4: 구현** — 갱신/수동결제 경로에서 카드 조회로 교체:

```python
    from app.services.cards import get_card
    card = await get_card(db, service_id=sub.service_id, external_user_id=sub.external_user_id)
    if card is None:
        # 카드가 삭제된 채 갱신 시점 도달 — 결제 실패 경로로 처리(재시도/정지)
        ...  # 기존 결제실패 처리와 동일하게 다룬다
    billing_key = cipher.decrypt(card.billing_key_encrypted)
    customer_key = card.customer_key
```
  갱신 경로가 `cipher`를 받지 않으면, 스케줄러 진입점에서 `cipher`를 주입하도록 시그니처를 확장한다(`app/scheduler/runner.py`에서 cipher 생성/주입 방식 확인).

- [ ] **Step 5: 통과 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_renewals.py tests/integration/test_subscription_manage.py -v` · Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add app/services/renewals.py app/services/subscriptions.py app/scheduler/runner.py tests/
git commit -m "feat: 자동갱신·수동결제가 카드에서 빌링키를 읽도록 변경"
```

---

## Task 9: 단건결제 — `auth_key` 제거, 등록 카드 사용(카드 보존)

**Files:**
- Modify: `app/services/payments.py` (`create_one_off_payment`)
- Modify: `app/schemas/api.py` (`OneOffPaymentRequest`에서 auth_key/customer_key 제거)
- Modify: `app/api/v1/payments.py` (one-off 라우트 호출부)
- Test: `tests/integration/test_one_off_payment.py`

- [ ] **Step 1: 테스트 수정** — 카드 선등록 → auth_key 없이 단건결제, 카드 미등록 거부, 결제 후 카드 보존 케이스:

```python
async def test_one_off_requires_card_and_keeps_it(db, cipher, fake_toss):
    from app.services.cards import register_or_replace_card, get_card
    from app.services.payments import create_one_off_payment
    svc, _, _ = await create_service(db, cipher)
    await register_or_replace_card(db, fake_toss, cipher, service=svc,
        external_user_id="u1", customer_key="c1", auth_key="ak")
    pay = await create_one_off_payment(db, fake_toss, cipher, service=svc,
        external_user_id="u1", order_id="ord-1", order_name="상품A", amount=5000)
    assert pay.status in ("DONE", "PENDING")
    # 단건결제 후에도 카드는 유지(영속)
    assert await get_card(db, service_id=svc.id, external_user_id="u1") is not None
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_one_off_payment.py -k requires_card -v` · Expected: FAIL

- [ ] **Step 3: 구현** — `create_one_off_payment`:
  - 시그니처에서 `auth_key`, `customer_key` 제거.
  - `CUSTOMER_KEY_RE` 검증 제거.
  - 등록 카드 조회: 없으면 `NotFoundError("등록된 카드가 없습니다...")`.
  - 빌링키 발급 블록(`bk = await toss.issue_billing_key(...)`) **삭제**.
  - `resolve_charge` 호출 시 `billing_key=cipher.decrypt(card.billing_key_encrypted), customer_key=card.customer_key`.
  - 결제 성공/실패/타임아웃 모든 경로의 **빌링키 best-effort 삭제 로직 제거**(카드 영속).
  - `order_name`은 이미 Payment에 저장(이전 작업) — 유지.

- [ ] **Step 4: 스키마 수정** — `OneOffPaymentRequest`에서 `auth_key`, `customer_key` 제거(`external_user_id`, `order_id`, `order_name`, `amount` 유지).

- [ ] **Step 5: 라우트 수정** — `app/api/v1/payments.py` one-off 라우트에서 auth_key/customer_key 인자 제거.

- [ ] **Step 6: 통과 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_one_off_payment.py -v` · Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add app/services/payments.py app/schemas/api.py app/api/v1/payments.py tests/integration/test_one_off_payment.py
git commit -m "feat: 단건결제를 등록 카드 참조 방식으로 변경(카드 영속)"
```

---

## Task 10: `change-card` 엔드포인트 제거(재등록으로 통합)

**Files:**
- Modify: `app/api/v1/subscriptions.py` (`/subscriptions/{id}/change-card` 라우트 제거)
- Modify: `app/services/subscriptions.py` (`change_card` 서비스 함수 제거)
- Modify: `app/schemas/api.py` (`CardChangeRequest` 제거)
- Test: 관련 테스트 제거/이전 — 카드 교체는 Task 4의 재등록 테스트가 대체

- [ ] **Step 1: 사용처 확인** — Run: `grep -rn "change_card\|change-card\|CardChangeRequest" app tests`

- [ ] **Step 2: 제거** — 위 3개 파일에서 change-card 라우트·서비스 함수·스키마를 삭제. import도 정리.

- [ ] **Step 3: 테스트 정리** — `test_subscription_manage.py`의 change_card 테스트를 제거하고, 카드 교체 검증은 `test_cards.py`의 재등록 테스트로 충분함을 주석으로 명시.

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/python -m pytest tests/integration/test_subscription_manage.py -v` · Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/api/v1/subscriptions.py app/services/subscriptions.py app/schemas/api.py tests/
git commit -m "refactor: 구독 change-card 제거(카드 재등록으로 통합)"
```

---

## Task 11: 어드민 카드 표시

**Files:**
- Modify: `app/admin/routes/subscriptions.py` (구독 상세에서 연결 카드 조회 전달)
- Modify: `app/admin/templates/subscriptions/detail.html` (카드 마스킹 정보 표시 — 기존 `sub.card_info` 자리)
- Test: `tests/e2e/` 해당 페이지 테스트(있으면) 갱신

- [ ] **Step 1: 사용처 확인** — Run: `grep -rn "card_info\|customer_key\|billing_key" app/admin` · 어드민에서 구독의 카드 정보를 쓰던 곳을 카드 테이블에서 읽도록 변경.

- [ ] **Step 2: 라우트 수정** — 구독 상세 핸들러에서 `card_service.get_card(db, service_id=sub.service_id, external_user_id=sub.external_user_id)`를 조회해 템플릿 컨텍스트(`card`)로 전달.

- [ ] **Step 3: 템플릿 수정** — `subscriptions/detail.html`에서 `sub.card_info` 참조를 `card.card_info`로 교체(없으면 `-`).

- [ ] **Step 4: 확인** — Run: 관련 e2e 테스트 또는 `.venv/bin/python -m pytest tests/e2e -k subscription -v` · Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/admin/ tests/
git commit -m "feat(admin): 구독 상세에 등록 카드 정보 표시"
```

---

## Task 12: 샘플 서비스 — 카드 등록 페이지 + 흐름 갱신

**Files:**
- Modify: `sample_service/` (카드 등록 페이지 추가, 구독·단건 흐름을 카드 선등록으로)
- 참고: `sample_service`의 기존 토스 SDK 연동·API 호출 코드 구조를 먼저 파악(`sample_service` 내 프론트/백 구성 확인).

- [ ] **Step 1: 구조 파악** — Run: `ls -R sample_service | head -50` 및 토스 SDK·API 호출 위치 grep.

- [ ] **Step 2: 카드 등록 페이지 추가** — 토스 SDK로 authKey 획득 → `POST /api/v1/cards` 호출(HMAC 서명은 샘플 서비스의 기존 서명 유틸 재사용).

- [ ] **Step 3: 구독/단건 흐름 수정** — 데모 UI를 "①카드 등록 → ②구독/결제" 2단계로 변경. 구독·단건 호출에서 auth_key 제거.

- [ ] **Step 4: 수동 확인** — 샘플 서비스 실행 절차(README) 따라 카드 등록 → 구독 → 단건결제 → 취소 시나리오를 수동 점검.

- [ ] **Step 5: 커밋**

```bash
git add sample_service/
git commit -m "feat(sample): 카드 등록 페이지 + 카드 기반 구독·결제 데모"
```

---

## Task 13: 전체 회귀 · 문서 · 워크로그

**Files:**
- Modify: docs (dev_manual 04/07/15/02, admin/03·05) + `manual.html` 재빌드
- Create: `docs/audit/2026-06-19-card-vault-worklog.md`

- [ ] **Step 1: 전체 테스트** — Run: `.venv/bin/python -m pytest tests/ -q` · Expected: 전부 PASS (실패 시 해당 Task로 돌아가 수정)

- [ ] **Step 2: 문서 갱신** — `auth_key` 흐름 설명을 카드 등록 흐름으로 교체:
  - `docs/dev_manual/04-subscription-create.md`, `07-one-off-payment.md`, `15-external-api-and-sample.md`, `02-database.md`(cards 테이블 + subscriptions card_id), `admin/03-services.md`/`05-subscriptions.md`.

- [ ] **Step 3: 매뉴얼 재빌드** — Run: `.venv/bin/python docs/dev_manual/build_html.py`

- [ ] **Step 4: 워크로그 작성** — `docs/audit/2026-06-19-card-vault-worklog.md`에 변경 요약·검증 결과 기록.

- [ ] **Step 5: 커밋**

```bash
git add docs/
git commit -m "docs: 카드 등록 기반 결제 흐름 매뉴얼·워크로그 갱신"
```

---

## Self-Review (작성자 점검 결과)

**스펙 커버리지:** cards 모델(T1)·subscriptions 변경(T2)·마이그레이션(T3)·카드 서비스 등록/교체(T4)·삭제 규칙 §6.1(T5)·카드 API(T6)·구독 생성 변경(T7)·갱신/수동결제(T8)·단건결제+취소 보존(T9)·change-card 제거(T10)·어드민(T11)·샘플(T12)·문서(T13) — 스펙 각 절에 대응 Task 존재.

**플레이스홀더:** 코드 스텝은 구체 코드 포함. 단, 일부 기존 함수 시그니처(`record_audit`, best-effort 빌링키 삭제 함수명, 테스트 fake 픽스처명, 라우터 등록 위치)는 "확인 후 맞춤" 지시로 표시 — 실제 구현 시 명시된 참고 파일에서 정확한 이름을 확인할 것.

**타입 일관성:** `get_card`/`register_or_replace_card`/`delete_card` 시그니처가 Task 4·5·7·8·9·11에서 동일하게 사용됨(`delete_card`는 cipher 인자 포함). `card.card_info` ↔ `CardResponse.card` 매핑 일관.

**주의:** T8 갱신 경로의 `cipher` 주입은 스케줄러 진입점 변경이 필요할 수 있음 — 실제 `app/scheduler/runner.py` 구조 확인 후 최소 변경으로 처리.
