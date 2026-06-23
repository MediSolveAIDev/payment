import pytest

from app.core.errors import ConflictError, InputValidationError, NotFoundError
from app.services.plans import archive_plan, create_plan, delete_plan, list_plans, update_plan
from tests.factories import create_service
from tests.factories import create_plan as make_plan
from tests.factories import create_subscription


async def test_create_plan_month(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, service_id=svc.id, name="베이직", price=9900,
                             billing_cycle="MONTH")
    assert plan.id is not None
    assert plan.status == "ACTIVE"
    assert plan.currency == "KRW"


async def test_create_plan_day_requires_cycle_days(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="일단위", price=1000,
                          billing_cycle="DAY")
    plan = await create_plan(db, service_id=svc.id, name="일단위", price=1000,
                             billing_cycle="DAY", cycle_days=15)
    assert plan.cycle_days == 15


async def test_create_plan_non_day_rejects_cycle_days(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="x", price=1000,
                          billing_cycle="MONTH", cycle_days=10)


async def test_create_plan_validates_price_and_discount(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="x", price=0, billing_cycle="MONTH")
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="x", price=1000, billing_cycle="MONTH",
                          first_payment_type="DISCOUNT_PERCENT", first_payment_value=150)
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="x", price=1000, billing_cycle="MONTH",
                          first_payment_type="DISCOUNT_AMOUNT", first_payment_value=None)


async def test_add_bonus_days(db, cipher):
    """보너스 사용일 추가 — 이용중(ACTIVE/EXTENDED/PAST_DUE)만 +N일, 그 외 상태는 제외, 상태 유지."""
    from datetime import timedelta
    from app.core.clock import utcnow
    from app.services.plans import add_bonus_days
    from tests.factories import create_subscription
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc)
    base = utcnow().replace(microsecond=0)
    act = await create_subscription(db, cipher, svc, plan, external_user_id="b-act",
                                    status="ACTIVE", period_end=base, next_billing_at=base)
    ext = await create_subscription(db, cipher, svc, plan, external_user_id="b-ext",
                                    status="EXTENDED", period_end=base, next_billing_at=base)
    pdue = await create_subscription(db, cipher, svc, plan, external_user_id="b-pdue",
                                     status="PAST_DUE", period_end=base, next_billing_at=None)
    # 대상 아님: 취소예약(CANCELED)·만료(EXPIRED)
    canc = await create_subscription(db, cipher, svc, plan, external_user_id="b-canc",
                                     status="CANCELED", period_end=base, next_billing_at=None)
    exp = await create_subscription(db, cipher, svc, plan, external_user_id="b-exp",
                                    status="EXPIRED", period_end=base, next_billing_at=None)
    affected = await add_bonus_days(db, plan_id=plan.id, service_id=svc.id, days=30,
                                    actor_user_id=None)
    assert affected == 3                       # ACTIVE + EXTENDED + PAST_DUE만
    for s in (act, ext, pdue, canc, exp):
        await db.refresh(s)
    assert act.current_period_end == base + timedelta(days=30)
    assert act.next_billing_at == base + timedelta(days=30)
    assert act.status == "ACTIVE"              # 상태 유지
    assert ext.current_period_end == base + timedelta(days=30) and ext.status == "EXTENDED"
    assert pdue.current_period_end == base + timedelta(days=30)
    assert pdue.next_billing_at is None        # None은 그대로
    assert canc.current_period_end == base     # CANCELED 제외(변경 없음)
    assert exp.current_period_end == base       # EXPIRED 제외(변경 없음)


async def test_add_bonus_days_rejects_non_positive(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc)
    from app.services.plans import add_bonus_days
    with pytest.raises(InputValidationError):
        await add_bonus_days(db, plan_id=plan.id, service_id=svc.id, days=0, actor_user_id=None)


async def test_update_plan(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc)
    updated = await update_plan(db, plan_id=plan.id, service_id=svc.id,
                                name="프로", price=19900)
    assert updated.name == "프로"
    assert updated.price == 19900


async def test_update_plan_billing_cycle_immutable(db, cipher):
    """결제 주기는 수정 불가(요청): 다른 필드를 수정해도 주기는 그대로 유지된다.

    update_plan은 더 이상 billing_cycle/cycle_days 인자를 받지 않는다(TypeError).
    """
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc, billing_cycle="DAY", cycle_days=15)
    # 이름/가격만 수정해도 결제 주기·주기일수는 변하지 않는다
    u = await update_plan(db, plan_id=plan.id, service_id=svc.id, name="프로", price=12000)
    assert u.billing_cycle == "DAY" and u.cycle_days == 15
    # 결제 주기를 인자로 넘기는 것 자체가 불가(파라미터 제거)
    with pytest.raises(TypeError):
        await update_plan(db, plan_id=plan.id, service_id=svc.id, billing_cycle="WEEK")


async def test_delete_plan_blocked_when_subscription_exists(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc)
    await create_subscription(db, cipher, svc, plan)
    with pytest.raises(ConflictError):
        await delete_plan(db, plan_id=plan.id, service_id=svc.id)


async def test_delete_plan_without_subscriptions(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc)
    await delete_plan(db, plan_id=plan.id, service_id=svc.id)
    assert await list_plans(db, service_id=svc.id) == []


async def test_archive_plan_hides_from_active_list(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc)
    await archive_plan(db, plan_id=plan.id, service_id=svc.id)
    assert await list_plans(db, service_id=svc.id, only_active=True) == []
    assert len(await list_plans(db, service_id=svc.id)) == 1


async def test_activate_plan_restores_to_active_list(db, cipher):
    """보관된 요금제를 activate_plan으로 다시 활성화하면 ACTIVE 목록에 복귀한다."""
    from app.models import PlanStatus
    from app.services.plans import activate_plan
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc)
    await archive_plan(db, plan_id=plan.id, service_id=svc.id)
    restored = await activate_plan(db, plan_id=plan.id, service_id=svc.id)
    assert restored.status == PlanStatus.ACTIVE
    assert len(await list_plans(db, service_id=svc.id, only_active=True)) == 1


async def test_plan_scoped_to_service(db, cipher):
    svc_a, _, _ = await create_service(db, cipher, name="svc-a2")
    svc_b, _, _ = await create_service(db, cipher, name="svc-b2")
    plan = await make_plan(db, svc_a)
    with pytest.raises(NotFoundError):
        await update_plan(db, plan_id=plan.id, service_id=svc_b.id, name="해킹")


async def test_update_only_discount_value(db, cipher):
    """할인 값만 변경해도 반영된다 (fpv 센티널 버그 방지)."""
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc, first_payment_type="DISCOUNT_AMOUNT",
                           first_payment_value=1000)
    updated = await update_plan(db, plan_id=plan.id, service_id=svc.id,
                                first_payment_value=2000)
    assert updated.first_payment_type == "DISCOUNT_AMOUNT"
    assert updated.first_payment_value == 2000


async def test_update_type_to_none_clears_value(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc, first_payment_type="DISCOUNT_AMOUNT",
                           first_payment_value=1000)
    updated = await update_plan(db, plan_id=plan.id, service_id=svc.id,
                                first_payment_type="NONE")
    assert updated.first_payment_type == "NONE"
    assert updated.first_payment_value is None


async def test_update_price_audited_with_old_and_new(db, cipher):
    from sqlalchemy import select

    from app.models import AuditLog
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc, price=9900)
    await update_plan(db, plan_id=plan.id, service_id=svc.id, price=19900)
    log = await db.scalar(select(AuditLog).where(AuditLog.action == "plan.update"))
    assert log.detail["old_price"] == 9900
    assert log.detail["new_price"] == 19900


async def test_zero_discount_value_rejected(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="x", price=1000,
                          billing_cycle="MONTH",
                          first_payment_type="DISCOUNT_AMOUNT", first_payment_value=0)


async def test_expired_subscription_also_blocks_plan_delete(db, cipher):
    """이력 보존 — EXPIRED 구독만 있어도 요금제 삭제 불가."""
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, status="EXPIRED")
    with pytest.raises(ConflictError):
        await delete_plan(db, plan_id=plan.id, service_id=svc.id)


async def test_recurring_discount_type_enum_values(db, cipher):
    """DiscountType 값이 기존 문자열과 동일 — DB 마이그레이션 불필요 근거."""
    from app.models.enums import DiscountType, FirstPaymentType
    # 저장 문자열 동일성
    assert DiscountType.NONE == "NONE"
    assert DiscountType.DISCOUNT_AMOUNT == "DISCOUNT_AMOUNT"
    assert DiscountType.DISCOUNT_PERCENT == "DISCOUNT_PERCENT"
    assert DiscountType.DISCOUNT_AMOUNT == FirstPaymentType.DISCOUNT_AMOUNT  # StrEnum 문자열 동등
    # FREE는 상시 할인에 없음
    assert not hasattr(DiscountType, "FREE")


# ─── 요청 013 Task 7: auto_renew / extra_info 테스트 ─────────────────────────


async def test_auto_renew_false_allows_trial(db, cipher):
    """auto_renew=False + trial_enabled=True 공존 허용(요청) — 체험 후 첫 결제, 그 주기 후 만료."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, service_id=svc.id, name="체험+비갱신", price=1000,
                             billing_cycle="MONTH", auto_renew=False,
                             trial_enabled=True, trial_days=7)
    assert plan.auto_renew is False and plan.trial_enabled is True and plan.trial_days == 7


async def test_auto_renew_false_allowed_without_trial(db, cipher):
    """auto_renew=False이지만 trial 없으면 정상 생성된다."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, service_id=svc.id, name="비갱신 요금제", price=5000,
                             billing_cycle="MONTH", auto_renew=False)
    assert plan.auto_renew is False


async def test_extra_info_stored(db, cipher):
    """extra_info key/value가 DB에 정상 저장된다."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, service_id=svc.id, name="P", price=1000,
                             billing_cycle="MONTH",
                             extra_info={"설명": "베이직", "용량": "10GB"})
    assert plan.extra_info["용량"] == "10GB"
    assert plan.extra_info["설명"] == "베이직"


async def test_extra_info_defaults_to_empty(db, cipher):
    """extra_info 미전달 시 빈 dict가 기본값으로 저장된다."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, service_id=svc.id, name="Q", price=2000,
                             billing_cycle="MONTH")
    assert plan.extra_info == {}


class _FakeForm:
    """_collect_extra_info 테스트용 폼 스텁 — getlist(key)로 병렬 목록 반환 (요청 013)."""

    def __init__(self, **lists):
        self._lists = lists

    def getlist(self, key):
        return self._lists.get(key, [])


def test_collect_extra_info_valid():
    """_collect_extra_info — 키/값 행을 dict로 수집(빈 행 무시, 순서 유지)."""
    from app.admin.routes.plans import _collect_extra_info
    form = _FakeForm(extra_key=["용량", "사용자수", ""],
                     extra_value=["10GB", "5명", ""])
    assert _collect_extra_info(form) == {"용량": "10GB", "사용자수": "5명"}


def test_collect_extra_info_empty_key_error():
    """_collect_extra_info — 값만 있고 키가 비면 InputValidationError."""
    from app.admin.routes.plans import _collect_extra_info
    form = _FakeForm(extra_key=[""], extra_value=["키없는값"])
    with pytest.raises(InputValidationError):
        _collect_extra_info(form)


def test_collect_extra_info_duplicate_key_last_wins():
    """_collect_extra_info — 키 중복 시 마지막 값이 우선한다."""
    from app.admin.routes.plans import _collect_extra_info
    form = _FakeForm(extra_key=["용량", "용량"], extra_value=["10GB", "20GB"])
    assert _collect_extra_info(form) == {"용량": "20GB"}


# ─── Task 3: MINUTE 검증(최소 5분·비운영 가드) ───────────────────────────────


async def test_create_minute_plan_dev(db, cipher):
    """비운영(dev) 환경에서 MINUTE + cycle_minutes>=5 요금제 생성 허용."""
    svc, _, _ = await create_service(db, cipher)
    # environment="dev"이면 MINUTE 주기 허용, cycle_minutes=5(최솟값)로 정상 생성 확인
    plan = await create_plan(db, service_id=svc.id, name="분테스트", price=1000,
                             billing_cycle="MINUTE", cycle_minutes=5, environment="dev")
    assert plan.billing_cycle == "MINUTE"
    assert plan.cycle_minutes == 5


async def test_create_minute_plan_min_5(db, cipher):
    """MINUTE 주기에서 cycle_minutes가 5 미만이면 InputValidationError."""
    svc, _, _ = await create_service(db, cipher)
    # cycle_minutes=4 → 최솟값(5) 미달 → 거부
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="x", price=1000,
                          billing_cycle="MINUTE", cycle_minutes=4, environment="dev")


async def test_create_minute_plan_rejected_in_prod(db, cipher):
    """MINUTE 주기는 운영(prod) 환경에서 InputValidationError — 비운영 전용 가드."""
    svc, _, _ = await create_service(db, cipher)
    # environment="prod" → MINUTE 거부
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="x", price=1000,
                          billing_cycle="MINUTE", cycle_minutes=5, environment="prod")


async def test_cycle_minutes_forbidden_on_non_minute(db, cipher):
    """MINUTE 이외 주기에 cycle_minutes 전달 시 InputValidationError."""
    svc, _, _ = await create_service(db, cipher)
    # MONTH 주기에 cycle_minutes 전달 → 금지
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="x", price=1000,
                          billing_cycle="MONTH", cycle_minutes=5, environment="dev")
