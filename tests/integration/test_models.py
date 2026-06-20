import pytest
from sqlalchemy.exc import IntegrityError

from app.core.clock import utcnow
from app.models import Card, Plan, Service, Subscription
from app.services.billing_math import compute_period_end
from tests.factories import create_service


async def _mk_service(db, name="svc-a"):
    svc = Service(name=name, allowed_ips=["127.0.0.1"], manager_email=f"{name}@x.com",
                  api_key_hash=f"hash-{name}", hmac_secret_encrypted="enc")
    db.add(svc)
    await db.flush()
    return svc


async def _mk_plan(db, svc):
    plan = Plan(service_id=svc.id, name="basic", price=10000, billing_cycle="MONTH")
    db.add(plan)
    await db.flush()
    return plan


async def _mk_card(db, svc, *, suffix="1"):
    """테스트용 Card 행 직접 삽입 — 카드 보관함 전환 후 Subscription의 card_id FK 충족용."""
    card = Card(
        service_id=svc.id,
        external_user_id="u1",
        customer_key=f"ck-{suffix}",
        billing_key_encrypted=f"enc-{suffix}",
        billing_key_hash=f"hash-bk-{suffix}",
    )
    db.add(card)
    await db.flush()
    return card


def _mk_sub(svc, plan, card, status="ACTIVE"):
    """Subscription 직접 생성 헬퍼.

    카드 보관함 전환(Task 7+) 이후 Subscription은 customer_key 컬럼이 없고
    card_id FK만 보유한다. card 파라미터로 등록된 Card 인스턴스를 받아 card_id를 설정한다.
    """
    now = utcnow()
    return Subscription(
        service_id=svc.id, plan_id=plan.id, external_user_id="u1",
        card_id=card.id,  # cards.id FK — 카드 보관함 전환 후 필수
        status=status,
        current_period_start=now,
        current_period_end=compute_period_end(now, "MONTH"),
        next_billing_at=compute_period_end(now, "MONTH"),
    )


async def test_one_subscription_per_service_user_enforced_by_db(db):
    """같은 서비스+사용자의 열린 구독(ACTIVE·CANCELED)이 2건 삽입되면 DB 제약 위반."""
    svc = await _mk_service(db)
    plan = await _mk_plan(db, svc)
    # 카드 보관함 전환 후 card_id FK가 필수 — 카드를 먼저 생성한다
    card = await _mk_card(db, svc, suffix="1")
    db.add(_mk_sub(svc, plan, card, "ACTIVE"))
    await db.flush()
    db.add(_mk_sub(svc, plan, card, "CANCELED"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_expired_subscription_allows_resubscribe(db):
    """EXPIRED 구독이 있을 때 새 ACTIVE 구독 삽입은 허용된다."""
    svc = await _mk_service(db, "svc-b")
    plan = await _mk_plan(db, svc)
    # 카드 보관함 전환 후 card_id FK가 필수 — 카드를 먼저 생성한다
    card = await _mk_card(db, svc, suffix="2")
    db.add(_mk_sub(svc, plan, card, "EXPIRED"))
    await db.flush()
    db.add(_mk_sub(svc, plan, card, "ACTIVE"))
    await db.flush()  # 에러 없어야 함


async def test_service_cancel_policy_defaults(db, cipher):
    """Service 신규 생성 시 취소정책 컬럼이 기본값(허용=True, 수수료율=0)이어야 한다."""
    svc, _, _ = await create_service(db, cipher)
    assert svc.cancellation_enabled is True
    assert svc.cancellation_fee_percent == 0


async def test_card_unique_per_service_user(db):
    """(service_id, external_user_id)당 카드 1건 — 중복은 IntegrityError."""
    # 테스트 전용 서비스 생성 — _mk_service 헬퍼 재사용
    svc = await _mk_service(db, "svc-card-uq")

    # 첫 번째 카드 등록: 정상 삽입이어야 함
    db.add(Card(
        service_id=svc.id,
        external_user_id="u1",
        customer_key="c1",
        billing_key_encrypted="enc1",
        billing_key_hash="h1",
        card_info={"number": "1234-****-****-5678"},
    ))
    await db.flush()

    # 동일 (service_id, external_user_id)로 두 번째 카드 등록 시도 — IntegrityError 기대
    db.add(Card(
        service_id=svc.id,
        external_user_id="u1",
        customer_key="c2",
        billing_key_encrypted="enc2",
        billing_key_hash="h2",
    ))
    with pytest.raises(IntegrityError):
        await db.flush()
