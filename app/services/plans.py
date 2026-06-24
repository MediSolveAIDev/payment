"""요금제(Plan) 생성·수정·삭제 서비스.

공개 함수 흐름:
  create_plan  → 유효성 검증 4종 → Plan INSERT → 감사 → 커밋
  update_plan  → _UNSET 센티널로 미전달/명시적 None 구분 → 유효성 재검증 → 감사 → 커밋
  archive_plan → 소프트 삭제(ARCHIVED) — 구독이 있어도 보관 가능
  delete_plan  → 구독 0건 확인 → 하드 삭제 → 감사 → 커밋
  list_plans   → 서비스 스코프 + 선택적 ACTIVE 필터
  get_plan     → 서비스 스코프 검증 포함

가격 변경은 다음 갱신 결제 시점에 즉시 반영된다(진행 중 주기에는 소급 없음).
감사 로그에 old_price/new_price를 함께 남기는 이유가 여기에 있다.
auto_renew=False(자동결제 안함)와 trial_enabled(체험)는 공존 가능하다(요청): 체험을 제공하면
체험 만료 시 첫 결제가 일어나고, 그 주기 종료 후 자동 갱신 없이 만료된다.
"""

import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import default_settings  # MINUTE 비운영 가드: environment 기본값 조회용(Task 3)
from app.core.errors import ConflictError, InputValidationError, NotFoundError
from app.models import (
    BillingCycle,
    DiscountType,
    FirstPaymentType,
    Plan,
    PlanStatus,
    Service,
    Subscription,
    SubscriptionStatus,
)
from app.notifications.service_notify import (
    EVENT_PLAN_ACTIVATED,
    EVENT_PLAN_ARCHIVED,
    EVENT_PLAN_BONUS_DAYS,
    EVENT_PLAN_DELETED,
)
from app.services.audit import record_audit


async def _notify_plan(db, notifier, service_id, *, event: str, status: str = "",
                       desc: str = "") -> None:
    """요금제 관련 서비스 알림 발송(best-effort). 사용자 비귀속이라 email/subscribe_id는 빈값.

    notifier 없으면 no-op. 서비스를 조회해 알림을 보낸다(URL 미등록이면 내부에서 무시).
    """
    if notifier is None:
        return
    service = await db.get(Service, service_id)
    if service is None:
        return
    await notifier.send(service, event=event, status=status, desc=desc)


def _discount_text(dtype: str | None, value: int | None) -> str:
    """할인 유형+값을 감사로그용 한 줄 문자열로 — '비율(정률 %)인지 값(정액 원)인지' 명확히(요청).

    NONE→"없음", FREE→"무료(0원)", DISCOUNT_PERCENT→"정률 N%", DISCOUNT_AMOUNT→"정액 N,NNN원".
    """
    if dtype in (None, "", FirstPaymentType.NONE):
        return "없음"
    if dtype == FirstPaymentType.FREE:
        return "무료(0원)"
    if dtype == DiscountType.DISCOUNT_PERCENT:
        return f"정률 {value or 0}%"
    if dtype == DiscountType.DISCOUNT_AMOUNT:
        return f"정액 {value or 0:,}원"
    return str(dtype)


def _validate_plan_fields(*, price: int, billing_cycle: str, cycle_days: int | None,
                          cycle_minutes: int | None,
                          first_payment_type: str, first_payment_value: int | None,
                          environment: str,
                          is_create: bool = True) -> None:
    """기본 요금제 필드 검증.

    is_create: True(기본) = 요금제 신규 생성 시 호출, False = 수정(update) 시 호출.
    MINUTE 주기의 prod 환경 차단(비운영 전용 가드)은 is_create=True 일 때만 적용된다.
    이미 존재하는 MINUTE 요금제를 운영 서버에서 이름·가격만 수정하는 경우 거부되지 않도록
    is_create=False 로 넘기면 prod 가드를 건너뛴다.
    다른 MINUTE 규칙(cycle_minutes ≥ 5 필수, cycle_days 금지)과
    DAY/기타 주기 규칙은 생성·수정 모두 동일하게 적용된다.

    규칙(주기 관련):
    - price: 1원 이상(0·음수 불가)
    - billing_cycle: BillingCycle 열거값에 없으면 거부
    - DAY: cycle_days 1 이상 필수, cycle_minutes 전달 금지
    - MINUTE: cycle_minutes 5 이상 필수, cycle_days 전달 금지
             + 비운영 전용 가드(is_create=True 일 때만) —
               environment == "prod"이면 거부(테스트용 주기, Task 3)
    - 그 외(YEAR/MONTH/WEEK): cycle_days·cycle_minutes 둘 다 전달 금지
      (WEEK/MONTH/YEAR는 timedelta/relativedelta 고정값으로 계산하므로 days/minutes 불필요)
    - first_payment_type: FirstPaymentType 열거값
      - NONE/FREE → first_payment_value 전달 금지(값 없음 의미)
      - DISCOUNT_AMOUNT/DISCOUNT_PERCENT → first_payment_value 1 이상 필수
      - DISCOUNT_PERCENT → 추가로 100 이하(할인율 상한)
    """
    if price <= 0:
        raise InputValidationError("가격은 1원 이상이어야 합니다")
    if billing_cycle not in tuple(BillingCycle):
        raise InputValidationError(f"지원하지 않는 결제 주기입니다: {billing_cycle}")
    if billing_cycle == BillingCycle.DAY:
        # DAY: cycle_days 1 이상 필수, cycle_minutes는 MINUTE 전용이므로 금지
        if not cycle_days or cycle_days < 1:
            raise InputValidationError("DAY 주기는 cycle_days(1 이상)가 필요합니다")
        if cycle_minutes is not None:
            raise InputValidationError("cycle_minutes는 MINUTE 주기에서만 사용합니다")
    elif billing_cycle == BillingCycle.MINUTE:
        # MINUTE: 비운영 전용 가드 — prod 환경 차단은 생성(is_create=True) 시에만 적용.
        # 이미 생성된 MINUTE 요금제를 운영 서버에서 수정(is_create=False)할 때는 거부하지 않는다.
        if is_create and environment == "prod":
            raise InputValidationError("MINUTE 주기는 비운영 환경에서만 사용합니다")
        # MINUTE: cycle_minutes 5 이상 필수(최소 5분 가드, Task 3)
        if not cycle_minutes or cycle_minutes < 5:
            raise InputValidationError("MINUTE 주기는 cycle_minutes(5 이상)가 필요합니다")
        # MINUTE: cycle_days는 DAY 전용이므로 금지
        if cycle_days is not None:
            raise InputValidationError("cycle_days는 DAY 주기에서만 사용합니다")
    else:
        # YEAR/MONTH/WEEK: cycle_days·cycle_minutes 둘 다 금지
        if cycle_days is not None:
            raise InputValidationError("cycle_days는 DAY 주기에서만 사용합니다")
        if cycle_minutes is not None:
            raise InputValidationError("cycle_minutes는 MINUTE 주기에서만 사용합니다")
    # ── first_payment 검증(기존 로직 그대로 유지) ──
    if first_payment_type not in tuple(FirstPaymentType):
        raise InputValidationError(f"지원하지 않는 첫결제 유형입니다: {first_payment_type}")
    if first_payment_type in (FirstPaymentType.NONE, FirstPaymentType.FREE):
        if first_payment_value is not None:
            raise InputValidationError("첫결제 값은 할인 유형에서만 사용합니다")
    else:
        if first_payment_value is None or first_payment_value < 1:
            raise InputValidationError("할인 값은 1 이상이어야 합니다")
        if first_payment_type == FirstPaymentType.DISCOUNT_PERCENT and first_payment_value > 100:
            raise InputValidationError("할인율은 1~100 사이여야 합니다")


def _validate_trial(trial_enabled: bool, trial_days: int | None) -> None:
    """체험 기간 설정 검증.

    trial_enabled=True 이면 trial_days 1 이상 필수.
    trial_enabled=False 이면 trial_days를 전달하면 오류(활성화 없이 일수만 넘기는 실수 방지).
    """
    if trial_enabled:
        if not trial_days or trial_days < 1:
            raise InputValidationError("체험을 사용하려면 체험 일수(1 이상)가 필요합니다")
    elif trial_days is not None:
        raise InputValidationError("체험 일수는 체험 활성화 시에만 사용합니다")


def _validate_recurring_discount(discount_type: str, discount_value: int | None) -> None:
    """상시 할인 검증. FREE는 허용하지 않는다(정기 결제는 무료가 될 수 없음).

    DiscountType 열거값:
    - NONE: 할인 없음. discount_value 전달 금지.
    - DISCOUNT_AMOUNT: 원 단위 차감. 1 이상 필수.
    - DISCOUNT_PERCENT: 비율 차감. 1~100 사이 필수.

    FirstPaymentType의 FREE는 첫 회차 0원을 허용하지만,
    DiscountType에는 FREE가 없다 — 매 회차 0원 정기 결제는 의미 없으므로 설계상 제외.
    """
    if discount_type == DiscountType.NONE:
        if discount_value is not None:
            raise InputValidationError("상시 할인 값은 할인 유형에서만 사용합니다")
        return
    if discount_type not in (DiscountType.DISCOUNT_AMOUNT,
                             DiscountType.DISCOUNT_PERCENT):
        raise InputValidationError(f"지원하지 않는 상시 할인 유형입니다: {discount_type}")
    if discount_value is None or discount_value < 1:
        raise InputValidationError("상시 할인 값은 1 이상이어야 합니다")
    if discount_type == DiscountType.DISCOUNT_PERCENT and discount_value > 100:
        raise InputValidationError("할인율은 1~100 사이여야 합니다")


async def create_plan(db: AsyncSession, *, service_id: uuid.UUID, name: str, price: int,
                      billing_cycle: str, cycle_days: int | None = None,
                      cycle_minutes: int | None = None,   # MINUTE 주기 분 수(5 이상); 비운영 전용(Task 3)
                      first_payment_type: str = "NONE",
                      first_payment_value: int | None = None,
                      recurring_discount_type: str = "NONE",
                      recurring_discount_value: int | None = None,
                      trial_enabled: bool = False, trial_days: int | None = None,
                      auto_renew: bool = True,            # 자동결제 여부(요청 013): False=첫 주기 후 만료
                      extra_info: dict | None = None,     # 추가정보(요청 013): 서비스 측 설명 key/value
                      environment: str | None = None,     # MINUTE 비운영 가드용(None=현재 실행 환경, Task 3)
                      actor_user_id: uuid.UUID | None = None,
                      admin_notifier=None) -> Plan:
    """요금제 생성.

    흐름:
    1. 이름 공백 검증
    2. environment 결정 — 명시 전달 시 그대로, None이면 default_settings().environment 사용
    3. 기본 필드 검증(_validate_plan_fields) — 주기/할인 규칙(MINUTE 가드 포함, Task 3)
    4. 상시 할인 검증(_validate_recurring_discount) — FREE 거부
    5. 체험 검증(_validate_trial)
    6. Plan 객체 생성 — trial_enabled=False 이면 trial_days는 None으로 저장
       (enabled=False에 days가 남아 있으면 조회 시 혼란을 일으키므로)
    7. 감사 로그 기록 → 커밋(단일 트랜잭션)

    가격은 다음 갱신부터 반영되므로, 생성 후 update_plan으로 수정하면
    이미 구독 중인 사용자의 현재 주기는 영향받지 않는다.
    auto_renew=False(자동결제 안함)는 체험과 공존 가능 — 체험 만료 후 첫 결제가 일어나고
    그 주기 종료 시 만료(체험 없으면 첫 주기 종료 시 만료).
    MINUTE 주기는 비운영(dev/test) 환경 전용이며 prod에서 생성 시 InputValidationError(Task 3).
    """
    if not name or not name.strip():
        raise InputValidationError("요금제 이름은 필수입니다")
    # environment 미전달 시 현재 실행 환경(APP_ENV/.env)을 기본값으로 사용(Task 3)
    env = environment if environment is not None else default_settings().environment
    # is_create=True: 생성 시에만 MINUTE prod 가드 적용(Task 3 리뷰 반영)
    _validate_plan_fields(price=price, billing_cycle=billing_cycle, cycle_days=cycle_days,
                          cycle_minutes=cycle_minutes,
                          first_payment_type=first_payment_type,
                          first_payment_value=first_payment_value,
                          environment=env, is_create=True)
    _validate_recurring_discount(recurring_discount_type, recurring_discount_value)
    _validate_trial(trial_enabled, trial_days)
    plan = Plan(service_id=service_id, name=name.strip(), price=price,
                billing_cycle=billing_cycle, cycle_days=cycle_days,
                cycle_minutes=cycle_minutes,               # MINUTE 주기 분 수 저장(Task 3)
                first_payment_type=first_payment_type,
                first_payment_value=first_payment_value,
                recurring_discount_type=recurring_discount_type,
                recurring_discount_value=recurring_discount_value,
                trial_enabled=trial_enabled, trial_days=trial_days if trial_enabled else None,
                auto_renew=auto_renew,               # 자동결제 여부(요청 013)
                extra_info=extra_info if extra_info is not None else {})  # 추가정보(요청 013)
    db.add(plan)
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="plan.create", target_type="plan",
                       detail={"name": plan.name, "price": price,
                               "trial_days": plan.trial_days,
                               "auto_renew": auto_renew})  # auto_renew를 감사 로그에 기록(요청 013)
    await db.commit()
    # 시스템 관리자 전원에게 '새 요금제 등록' 알림 메일(best-effort, 커밋 후라 실패해도 무해)
    if admin_notifier is not None:
        await admin_notifier.plan_created(db, plan=plan, actor_user_id=actor_user_id)
    return plan


async def _get_plan(db: AsyncSession, plan_id: uuid.UUID, service_id: uuid.UUID) -> Plan:
    """서비스 스코프를 포함한 단일 요금제 조회.

    plan이 존재하더라도 service_id가 다르면 NotFoundError — 타 서비스 요금제에
    접근하는 것을 방지하고, 존재 여부 자체를 노출하지 않는다.
    """
    plan = await db.get(Plan, plan_id)
    if plan is None or plan.service_id != service_id:
        raise NotFoundError("요금제를 찾을 수 없습니다")
    return plan


_UNSET = object()  # '미지정'과 '명시적 None'을 구분하는 센티널


def _resolve_unset(new, current):
    """_UNSET이면 기존 값 유지, 아니면 새 값(부분 수정용)."""
    return current if new is _UNSET else new


def _resolve_coupled_value(new_value, new_type, current_value, *, clears: tuple):
    """타입/값 쌍의 부분 수정에서 값(value)을 결정.

    - 값이 전달됨 → 그대로
    - 값 미전달·타입만 전달 → 타입이 clears에 속하면 None(값 제거 대상 타입 집합 — 호출 측이 지정), 아니면 기존 값 유지
    - 둘 다 미전달 → 기존 값
    """
    if new_value is not _UNSET:
        return new_value
    if new_type is not _UNSET:
        return None if new_type in clears else current_value
    return current_value


async def update_plan(db: AsyncSession, *, plan_id: uuid.UUID, service_id: uuid.UUID,
                      name: str | None = None, price: int | None = None,
                      first_payment_type=_UNSET,
                      first_payment_value=_UNSET,
                      recurring_discount_type=_UNSET,
                      recurring_discount_value=_UNSET,
                      trial_enabled=_UNSET, trial_days=_UNSET,
                      # 결제 주기(billing_cycle/cycle_days)는 수정 불가(요청) — 인자로 받지 않고 항상 기존 값 유지.
                      auto_renew=_UNSET,   # 자동결제 여부(요청 013): _UNSET=유지, bool=변경
                      extra_info=_UNSET,   # 추가정보(요청 013): _UNSET=유지, dict=교체
                      actor_user_id: uuid.UUID | None = None) -> Plan:
    """요금제 부분 수정.

    _UNSET 센티널:
    - 인자가 _UNSET이면 "호출자가 전달하지 않음" → 기존 값 유지
    - None을 명시적으로 넘기면 "값을 제거하겠다는 의도"로 처리
    - 이로써 Optional 파라미터에서 '미전달'과 '명시적 None 전달'을 구분한다.

    first_payment_value / recurring_discount_value 연동 규칙:
    - 값만 바꾸고 타입은 안 바꿀 때: 넘긴 값 그대로 적용
    - 타입만 바꾸고 값은 안 바꿀 때:
        NONE/FREE → 값 자동 제거(None)
        할인 유형 → 기존 값 유지(타입 전환 시 값을 지우지 않음)
    - 둘 다 안 바꿀 때: 기존 값 유지
    값 결정은 _resolve_coupled_value로 수행한다.

    가격 변경 감사:
    - price 변경은 다음 갱신 결제액에 즉시 반영되므로
      old_price/new_price를 감사 로그에 명시적으로 기록한다.

    billing_cycle/cycle_days(결제 주기)는 **수정 불가**(요청) — 생성 시에만 정하며, 수정 시
    인자로 받지 않고 기존 값을 그대로 유지한다. 주기를 바꾸려면 새 요금제를 생성해야 한다.
    auto_renew(요청 013): 자동결제 여부. 체험과 공존 가능(배타 검증 없음).
    extra_info(요청 013): dict를 교체; None 전달 시 {}로 초기화.

    [비소급 주의] auto_renew/extra_info 변경은 신규 구독부터 적용되며,
    이미 생성된 구독의 next_billing_at에는 소급 적용되지 않는다
    (auto_renew는 create_subscription 시점에만 반영).
    """
    plan = await _get_plan(db, plan_id, service_id)
    new_name = name if name is not None else plan.name
    new_price = price if price is not None else plan.price
    new_fpt = _resolve_unset(first_payment_type, plan.first_payment_type)
    new_fpv = _resolve_coupled_value(first_payment_value, first_payment_type,
                                     plan.first_payment_value,
                                     clears=(FirstPaymentType.NONE, FirstPaymentType.FREE))
    new_rdt = _resolve_unset(recurring_discount_type, plan.recurring_discount_type)
    new_rdv = _resolve_coupled_value(recurring_discount_value, recurring_discount_type,
                                     plan.recurring_discount_value,
                                     clears=(DiscountType.NONE,))
    new_trial_enabled = plan.trial_enabled if trial_enabled is _UNSET else bool(trial_enabled)
    if trial_days is not _UNSET:
        new_trial_days = trial_days
    elif trial_enabled is not _UNSET:
        # 체험 토글만 바뀐 경우: 끄면 일수 제거, 켜면 기존 일수 유지
        new_trial_days = plan.trial_days if new_trial_enabled else None
    else:
        new_trial_days = plan.trial_days
    # 자동결제 여부 결정(요청 013): _UNSET이면 기존 값 유지
    new_auto_renew = plan.auto_renew if auto_renew is _UNSET else bool(auto_renew)
    # 추가정보 결정(요청 013): _UNSET이면 기존 값 유지, None이면 {}로 초기화
    if extra_info is _UNSET:
        new_extra_info = plan.extra_info
    else:
        new_extra_info = extra_info if extra_info is not None else {}
    # 결제 주기(billing_cycle/cycle_days/cycle_minutes)는 수정 불가(요청) — 항상 기존 값 유지.
    # 유효성 재검증도 기존 주기 기준으로 수행한다.
    # environment는 update_plan에서 변경 불가이므로 기존 환경값(dev/test/prod)을 그대로 전달.
    new_billing_cycle = plan.billing_cycle
    new_cycle_days = plan.cycle_days
    new_cycle_minutes = plan.cycle_minutes  # MINUTE 주기 분 수 — 수정 불가(Task 3)
    # is_create=False: 수정 시에는 MINUTE prod 가드를 적용하지 않음(Task 3 리뷰 반영).
    # 이미 생성된 MINUTE 요금제를 prod 서버에서 이름·가격만 바꿀 때 거부되지 않도록 한다.
    _validate_plan_fields(price=new_price, billing_cycle=new_billing_cycle,
                          cycle_days=new_cycle_days,
                          cycle_minutes=new_cycle_minutes,  # 기존 값 그대로 재검증(Task 3)
                          first_payment_type=new_fpt,
                          first_payment_value=new_fpv,
                          environment=default_settings().environment,
                          is_create=False)  # 수정 시 prod 가드 스킵(Task 3 리뷰)
    _validate_recurring_discount(new_rdt, new_rdv)
    _validate_trial(new_trial_enabled, new_trial_days)
    # 요금제 수정 내역을 감사 로그에 상세히 — 모든 항목을 변경 전/후(old_/new_)로 기록(요청).
    # detail_summary가 값이 실제로 바뀐 항목만 "라벨 전 → 후"로 표시한다.
    # (결제 주기는 수정 불가이므로 기록 대상에서 제외)
    final_trial_days = new_trial_days if new_trial_enabled else None
    audit_detail = {
        "old_name": plan.name, "new_name": new_name,
        "old_price": plan.price, "new_price": new_price,
        # 첫결제/상시 할인은 유형+값을 결합해 '정률 N% / 정액 N원'으로 기록 — 비율/값 구분 명확(요청)
        "old_first_payment": _discount_text(plan.first_payment_type, plan.first_payment_value),
        "new_first_payment": _discount_text(new_fpt, new_fpv),
        "old_recurring_discount": _discount_text(plan.recurring_discount_type,
                                                 plan.recurring_discount_value),
        "new_recurring_discount": _discount_text(new_rdt, new_rdv),
        "old_trial_enabled": plan.trial_enabled, "new_trial_enabled": new_trial_enabled,
        "old_trial_days": plan.trial_days, "new_trial_days": final_trial_days,
        "old_auto_renew": plan.auto_renew, "new_auto_renew": new_auto_renew,
        "old_extra_info": plan.extra_info or {}, "new_extra_info": new_extra_info,
    }
    plan.name, plan.price = new_name, new_price
    # 결제 주기는 변경하지 않음(수정 불가) — plan.billing_cycle/cycle_days 그대로 유지
    plan.first_payment_type, plan.first_payment_value = new_fpt, new_fpv
    plan.recurring_discount_type, plan.recurring_discount_value = new_rdt, new_rdv
    plan.trial_enabled = new_trial_enabled
    plan.trial_days = new_trial_days if new_trial_enabled else None
    plan.auto_renew = new_auto_renew        # 자동결제 여부 업데이트(요청 013)
    plan.extra_info = new_extra_info        # 추가정보 업데이트(요청 013)
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="plan.update", target_type="plan", target_id=str(plan.id),
                       detail=audit_detail)
    await db.commit()
    return plan


async def archive_plan(db: AsyncSession, *, plan_id: uuid.UUID, service_id: uuid.UUID,
                       actor_user_id: uuid.UUID | None = None, notifier=None) -> Plan:
    """요금제 보관(소프트 삭제).

    구독이 있어도 보관 가능 — 신규 구독만 막고 기존 구독은 유지.
    하드 delete_plan은 구독 0건일 때만 가능하므로, 구독이 있으면 이 함수를 권장.
    """
    plan = await _get_plan(db, plan_id, service_id)
    plan.status = PlanStatus.ARCHIVED
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="plan.archive", target_type="plan", target_id=str(plan.id),
                       detail={"name": plan.name})
    await db.commit()
    # 서비스 알림 — 요금제 비활성화. best-effort.
    await _notify_plan(db, notifier, service_id, event=EVENT_PLAN_ARCHIVED,
                       status="ARCHIVED", desc=f"요금제 비활성화({plan.name})")
    return plan


async def activate_plan(db: AsyncSession, *, plan_id: uuid.UUID, service_id: uuid.UUID,
                        actor_user_id: uuid.UUID | None = None, notifier=None) -> Plan:
    """보관된 요금제를 다시 활성화(ARCHIVED → ACTIVE).

    archive_plan의 역동작 — 신규 구독을 다시 받을 수 있는 상태로 되돌린다.
    (이미 ACTIVE여도 멱등적으로 ACTIVE 유지.)
    """
    plan = await _get_plan(db, plan_id, service_id)
    plan.status = PlanStatus.ACTIVE
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="plan.activate", target_type="plan", target_id=str(plan.id),
                       detail={"name": plan.name})
    await db.commit()
    # 서비스 알림 — 요금제 활성화. best-effort.
    await _notify_plan(db, notifier, service_id, event=EVENT_PLAN_ACTIVATED,
                       status="ACTIVE", desc=f"요금제 활성화({plan.name})")
    return plan


async def delete_plan(db: AsyncSession, *, plan_id: uuid.UUID, service_id: uuid.UUID,
                      actor_user_id: uuid.UUID | None = None, notifier=None) -> None:
    """요금제 하드 삭제.

    구독이 1건이라도 있으면 ConflictError — 스펙 규칙(FK RESTRICT 보조 방어선).
    구독이 있는 경우 archive_plan으로 안내한다.
    감사 로그는 delete 전에 기록하고 commit을 한 번만 수행한다.
    """
    plan = await _get_plan(db, plan_id, service_id)
    count = await db.scalar(select(func.count()).select_from(Subscription)
                            .where(Subscription.plan_id == plan_id))
    if count:
        raise ConflictError("구독이 있는 요금제는 삭제할 수 없습니다. 보관(아카이브)을 사용하세요.")
    plan_name = plan.name   # 삭제 전 이름 캡처(감사 기록용)
    await db.delete(plan)
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="plan.delete", target_type="plan", target_id=str(plan_id),
                       detail={"name": plan_name})
    await db.commit()
    # 서비스 알림 — 요금제 삭제. best-effort.
    await _notify_plan(db, notifier, service_id, event=EVENT_PLAN_DELETED,
                       desc=f"요금제 삭제({plan_name})")


# 보너스 사용일 추가 상한(일) — 비정상적으로 큰 값 방지(약 10년).
_MAX_BONUS_DAYS = 3650


# 사용일추가 적용 대상 상태(요청) — 현재 이용 중인 구독만: 활성·연장처리·미수.
# 체험(TRIAL)·정지(SUSPENDED)·취소예약(CANCELED)·만료(EXPIRED)는 제외.
_BONUS_TARGET_STATUSES = (SubscriptionStatus.ACTIVE, SubscriptionStatus.EXTENDED,
                          SubscriptionStatus.PAST_DUE)


async def add_bonus_days(db: AsyncSession, *, plan_id: uuid.UUID, service_id: uuid.UUID,
                         days: int, actor_user_id: uuid.UUID | None = None,
                         notifier=None) -> int:
    """요금제에 보너스 사용일을 일괄 추가(요청). 반환: 적용된 구독 수.

    이 요금제를 쓰는 **현재 이용 중인 구독(ACTIVE·EXTENDED·PAST_DUE)**의 만료일
    (current_period_end)과 다음 결제일(next_billing_at)을 days만큼 미룬다.
    체험·정지·취소예약·만료 구독은 대상이 아니다. **상태는 변경하지 않는다.**
    next_billing_at이 NULL인 구독은 SQL에서 NULL+interval=NULL로 그대로 유지된다.
    보너스 기간 동안 조기 청구되지 않도록 다음 결제일도 함께 미룬다.
    """
    if days < 1:
        raise InputValidationError("추가 일수는 1 이상이어야 합니다")
    if days > _MAX_BONUS_DAYS:
        raise InputValidationError(f"추가 일수는 {_MAX_BONUS_DAYS}일을 넘을 수 없습니다")
    plan = await _get_plan(db, plan_id, service_id)   # 스코프 검증 포함
    delta = timedelta(days=days)
    # 단일 bulk UPDATE — 이용 중(활성/연장처리/미수) 구독의 만료일·다음결제일을 +days
    result = await db.execute(
        update(Subscription)
        .where(Subscription.plan_id == plan_id,
               Subscription.status.in_(_BONUS_TARGET_STATUSES))
        .values(current_period_end=Subscription.current_period_end + delta,
                next_billing_at=Subscription.next_billing_at + delta))
    affected = result.rowcount if result.rowcount and result.rowcount > 0 else 0
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="plan.bonus_days", target_type="plan", target_id=str(plan_id),
                       detail={"plan_name": plan.name, "days": days,
                               "affected_count": affected})
    await db.commit()
    # 서비스 알림 — 요금제 사용일 추가. best-effort.
    await _notify_plan(db, notifier, service_id, event=EVENT_PLAN_BONUS_DAYS,
                       desc=f"요금제 사용일 +{days}일({plan.name}, 적용 {affected}건)")
    return affected


async def list_plans(db: AsyncSession, *, service_id: uuid.UUID,
                     only_active: bool = False) -> list[Plan]:
    """서비스 소속 요금제 목록. only_active=True 이면 ACTIVE 상태만 반환."""
    query = select(Plan).where(Plan.service_id == service_id).order_by(Plan.created_at)
    if only_active:
        query = query.where(Plan.status == PlanStatus.ACTIVE)
    return list((await db.scalars(query)).all())


async def get_plan(db: AsyncSession, *, plan_id: uuid.UUID, service_id: uuid.UUID) -> Plan:
    """단일 요금제 조회(서비스 스코프 포함). 내부 _get_plan의 공개 래퍼."""
    return await _get_plan(db, plan_id, service_id)
