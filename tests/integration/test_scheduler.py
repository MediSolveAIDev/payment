import pytest
from datetime import timedelta

from app.core.clock import utcnow
from app.scheduler.runner import GLOBAL_LOCK_KEY, run_renewals, start_scheduler
from app.services.auth import create_system_admin
from tests.factories import (
    create_card,
    create_plan,
    create_service,
    create_subscription,
)


async def test_run_renewals_processes_due(app, db, redis_client, cipher, fake_toss):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    # 카드 보관함(Task 8): 갱신 결제는 cards 테이블의 빌링키를 사용하므로
    # 구독에 등록 카드를 먼저 연결한다.
    card = await create_card(db, fake_toss, cipher, svc, external_user_id="u-sch")
    await create_subscription(db, cipher, svc, plan, external_user_id="u-sch",
                              card_id=card.id,
                              period_start=utcnow() - timedelta(days=31),
                              period_end=utcnow() - timedelta(minutes=1),
                              next_billing_at=utcnow() - timedelta(minutes=1))
    stats = await run_renewals(app)
    assert stats["renewed"] == 1
    assert len(fake_toss.charges) == 1


async def test_run_renewals_skips_when_global_lock_held(app, db, redis_client,
                                                        cipher, fake_toss):
    await redis_client.set(GLOBAL_LOCK_KEY, "1", ex=60)
    assert await run_renewals(app) is None
    assert fake_toss.charges == []


async def test_start_scheduler_registers_interval_job(app):
    scheduler = start_scheduler(app)
    try:
        assert len(scheduler.get_jobs()) == 1
    finally:
        scheduler.shutdown(wait=False)


async def test_create_system_admin(db):
    user = await create_system_admin(db, email="root@medisolveai.com",
                                     password="RootPassword1!")
    assert user.role == "SYSTEM_ADMIN"
    assert user.status == "ACTIVE"


async def test_create_system_admin_duplicate_email_conflicts(db):
    from app.core.errors import ConflictError
    await create_system_admin(db, email="dup-admin@medisolveai.com",
                              password="RootPassword1!")
    with pytest.raises(ConflictError):
        await create_system_admin(db, email="dup-admin@medisolveai.com",
                                  password="RootPassword1!")


async def test_create_system_admin_weak_password_rejected(db):
    from app.core.errors import InputValidationError
    with pytest.raises(InputValidationError):
        await create_system_admin(db, email="weak@medisolveai.com", password="short")
