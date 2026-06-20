"""구독 상태 전이 헬퍼 단위 테스트 (감사 Phase 4 — S1).

DB 없이 ORM 객체만으로 허용/거부 전이와 보편 불변식을 검증한다.
통합 테스트(test_renewals 등)가 실제 경로를 덮고, 여기서는 규칙 자체를 못박는다.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.models import Subscription, SubscriptionStatus
from app.services.transitions import (
    ALLOWED_TRANSITIONS,
    InvalidStateTransition,
    transition,
)

NOW = datetime(2026, 6, 11, tzinfo=timezone.utc)


def _sub(status: SubscriptionStatus, **kw) -> Subscription:
    """전이 테스트용 최소 Subscription 객체(DB 미사용)."""
    return Subscription(id=uuid.uuid4(), status=status,
                        retry_count=kw.get("retry_count", 0),
                        suspended_at=kw.get("suspended_at"),
                        next_billing_at=kw.get("next_billing_at", NOW))


def test_expired_is_terminal():
    """EXPIRED는 종단 — 어떤 상태로도 전이할 수 없다(재이용은 신규 구독)."""
    assert ALLOWED_TRANSITIONS[SubscriptionStatus.EXPIRED] == frozenset()
    sub = _sub(SubscriptionStatus.EXPIRED)
    with pytest.raises(InvalidStateTransition):
        transition(sub, SubscriptionStatus.ACTIVE)


def test_invalid_transition_rejected():
    """허용 테이블에 없는 전이는 InvalidStateTransition — 예: SUSPENDED→PAST_DUE.

    SUSPENDED는 자동 재시도가 중지된 상태라 PAST_DUE(재시도 풀)로 돌아갈 수 없다 —
    복귀는 수동 결제 성공(→ACTIVE)뿐이다.
    """
    sub = _sub(SubscriptionStatus.SUSPENDED, suspended_at=NOW)
    with pytest.raises(InvalidStateTransition):
        transition(sub, SubscriptionStatus.PAST_DUE)
    assert sub.status == SubscriptionStatus.SUSPENDED  # 거부 시 상태 불변


def test_suspend_records_timestamp_and_stops_billing():
    """SUSPENDED 진입 불변식 — suspended_at 기록 + 자동결제 중지."""
    sub = _sub(SubscriptionStatus.PAST_DUE, retry_count=4)
    old = transition(sub, SubscriptionStatus.SUSPENDED, now=NOW)
    assert old == SubscriptionStatus.PAST_DUE
    assert sub.status == SubscriptionStatus.SUSPENDED
    assert sub.suspended_at == NOW
    assert sub.next_billing_at is None
    assert sub.retry_count == 4  # 재시도 이력은 보존(수동결제 복귀 시 ACTIVE가 초기화)


def test_activate_resets_failure_traces():
    """ACTIVE 복귀 불변식 — retry_count=0, suspended_at=None."""
    sub = _sub(SubscriptionStatus.SUSPENDED, retry_count=4, suspended_at=NOW)
    transition(sub, SubscriptionStatus.ACTIVE)
    assert sub.retry_count == 0
    assert sub.suspended_at is None


def test_cancel_and_expire_stop_billing():
    """CANCELED/EXPIRED 진입 불변식 — next_billing_at=None."""
    sub = _sub(SubscriptionStatus.ACTIVE, next_billing_at=NOW)
    transition(sub, SubscriptionStatus.CANCELED)
    assert sub.next_billing_at is None
    transition(sub, SubscriptionStatus.EXPIRED)
    assert sub.status == SubscriptionStatus.EXPIRED


def test_self_transitions_for_renewal_and_retry():
    """갱신(ACTIVE→ACTIVE)·재시도 실패(PAST_DUE→PAST_DUE)는 자기 전이로 허용."""
    active = _sub(SubscriptionStatus.ACTIVE)
    transition(active, SubscriptionStatus.ACTIVE)
    past_due = _sub(SubscriptionStatus.PAST_DUE, retry_count=1)
    transition(past_due, SubscriptionStatus.PAST_DUE)
    assert past_due.retry_count == 1  # PAST_DUE 자기 전이는 카운터를 건드리지 않음


def test_resume_paths_from_canceled():
    """만료 전 재개 — CANCELED→ACTIVE(정상) / CANCELED→PAST_DUE(미수금)."""
    transition(_sub(SubscriptionStatus.CANCELED), SubscriptionStatus.ACTIVE)
    transition(_sub(SubscriptionStatus.CANCELED), SubscriptionStatus.PAST_DUE)


def test_all_statuses_have_transition_entry():
    """모든 상태가 허용 테이블에 등재 — 새 상태 추가 시 이 테스트가 누락을 잡는다."""
    assert set(ALLOWED_TRANSITIONS) == set(SubscriptionStatus)
