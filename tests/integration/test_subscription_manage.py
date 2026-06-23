# 카드 교체는 카드 재등록(POST /api/v1/cards)으로 통합 — tests/integration/test_cards.py 에서 검증
"""구독 생명주기 서비스 레이어 통합 테스트 — 취소·재개·수동결제·강제취소 등.

Task 10: change_card 관련 테스트(test_change_card, test_change_card_on_past_due_schedules_immediate_retry,
test_change_card_issue_failure_keeps_old_key, test_change_card_survives_old_key_delete_failure)는
이 파일에서 제거됐다. 카드 교체(재등록)는 POST /api/v1/cards 엔드포인트가 담당하며,
관련 테스트는 tests/integration/test_cards.py 에서 관리된다.
"""
from datetime import timedelta

import pytest

from app.core.clock import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.services import subscriptions as subs
from tests.factories import create_plan, create_service, create_subscription


async def test_cancel_active_subscription(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-c@e.com")
    sub = await subs.cancel_subscription(db, service=svc, external_user_id="u-c@e.com")
    assert sub.status == "CANCELED"
    assert sub.next_billing_at is None


async def test_cancel_past_due_stops_retries(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-pd@e.com",
                              status="PAST_DUE", retry_count=2)
    sub = await subs.cancel_subscription(db, service=svc, external_user_id="u-pd@e.com")
    assert sub.status == "CANCELED"
    assert sub.next_billing_at is None


async def test_cancel_already_canceled_conflicts(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-cc@e.com",
                              status="CANCELED")
    with pytest.raises(ConflictError):
        await subs.cancel_subscription(db, service=svc, external_user_id="u-cc@e.com")


async def test_cancel_nonexistent_not_found(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(NotFoundError):
        await subs.cancel_subscription(db, service=svc, external_user_id="ghost@e.com")


async def test_resume_before_period_end(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    end = utcnow() + timedelta(days=10)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-r@e.com",
                              status="CANCELED", period_end=end, next_billing_at=None)
    sub = await subs.resume_subscription(db, service=svc, external_user_id="u-r@e.com")
    assert sub.status == "ACTIVE"
    assert sub.next_billing_at == sub.current_period_end


async def test_resume_no_auto_renew_keeps_no_next_billing(db, cipher):
    """자동결제 안함 요금제는 재개해도 자동 갱신을 예약하지 않는다(현 주기 종료 시 만료)."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, auto_renew=False)
    end = utcnow() + timedelta(days=10)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-nr@e.com",
                              status="CANCELED", period_end=end, next_billing_at=None)
    sub = await subs.resume_subscription(db, service=svc, external_user_id="u-nr@e.com")
    assert sub.status == "ACTIVE"
    assert sub.next_billing_at is None   # 자동결제 안함 — 갱신 예약 없음


async def test_resume_canceled_past_due_resumes_retry(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    end = utcnow() + timedelta(days=10)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-rpd@e.com",
                              status="CANCELED", retry_count=1,
                              period_end=end, next_billing_at=None)
    sub = await subs.resume_subscription(db, service=svc, external_user_id="u-rpd@e.com")
    assert sub.status == "PAST_DUE"
    assert sub.next_billing_at is not None
    assert sub.next_billing_at <= utcnow()  # 즉시 재시도 대상


async def test_resume_after_period_end_conflicts(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    start = utcnow() - timedelta(days=40)
    end = utcnow() - timedelta(days=1)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-late@e.com",
                              status="CANCELED", period_start=start, period_end=end,
                              next_billing_at=None)
    with pytest.raises(ConflictError):
        await subs.resume_subscription(db, service=svc, external_user_id="u-late@e.com")


async def test_get_latest_subscription_returns_most_recent(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-g@e.com",
                              status="EXPIRED")
    newer = await create_subscription(db, cipher, svc, plan, external_user_id="u-g@e.com",
                                      status="ACTIVE")
    found = await subs.get_latest_subscription(db, service_id=svc.id,
                                               external_user_id="u-g@e.com")
    assert found.id == newer.id


