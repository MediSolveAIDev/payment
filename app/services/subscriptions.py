"""구독 생명주기 관리 — 생성·취소·재개·수동 결제·강제 취소.

서비스+사용자 당 EXPIRED를 제외한 '열린' 구독은 최대 1개로 제한한다.
빌링키는 cards 테이블(카드 보관함)에 사전 등록된 카드에서 조회한다.
구독 생성 시 별도 빌링키 발급을 하지 않으며, 갱신/재시도는 renewals.py가 담당한다.

Task 10: change_card 함수 제거 — 카드 교체는 POST /api/v1/cards(재등록)로 통합됨.
카드 재등록 시 구독의 card_id FK가 자동 갱신되므로 별도 구독 엔드포인트 불필요.
"""
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.crypto import AesGcmCipher
from app.core.errors import (
    ConflictError,
    InputValidationError,
    NotFoundError,
    PaymentFailedError,
)
# sha256_hex: Task 10에서 change_card 제거 후 이 모듈에서 더 이상 사용되지 않음
from app.models import (
    OPEN_SUBSCRIPTION_STATUSES,
    Payment,
    PaymentKind,
    PaymentStatus,
    PaymentType,
    Plan,
    PlanStatus,
    Service,
    Subscription,
    SubscriptionStatus,
)
from app.services.audit import record_audit
from app.services.billing_math import (
    compute_period_end,
    plan_first_amount,
    plan_recurring_amount,
)
from app.services.cards import get_card  # 카드 보관함 조회(Task 7 — 등록 카드 사용)
from app.services.payment_utils import (
    # CUSTOMER_KEY_RE: Task 10에서 change_card·_validate_inputs 제거 후 미사용
    # safe_delete_billing_key: Task 10에서 change_card 제거 후 미사용
    PENDING_GRACE_MESSAGE,
    resolve_charge,
)
from app.notifications.service_notify import (
    EVENT_SUBSCRIPTION_CREATED,
    EVENT_SUBSCRIPTION_EXTENDED,
    EVENT_SUBSCRIPTION_FORCE_CANCELED,
    EVENT_SUBSCRIPTION_STATUS,
)
from app.services.transitions import transition
from app.toss.client import TossClient
from app.toss.errors import TossError, TossTimeoutError
from app.toss.types import ChargeResult

logger = logging.getLogger("payment.subscriptions")


async def _notify(notifier, service, sub, *, event: str, pre_status="", status="",
                  order_id: str = "", desc: str = "") -> None:
    """구독 관련 서비스 알림 발송(best-effort). notifier 없으면 no-op."""
    if notifier is None or service is None:
        return
    await notifier.send(service, event=event, subscribe_id=str(sub.id), order_id=order_id,
                        pre_status=str(pre_status or ""), status=str(status or ""),
                        email=sub.external_user_id, desc=desc)

# 서비스+사용자 당 1개 구독 — EXPIRED만 제외한 '열린' 상태 집합.
OPEN_STATUSES = OPEN_SUBSCRIPTION_STATUSES

# 사용일 추가(외부 API) 적용 가능 상태 — '이용 중'만(어드민 요금제 보너스와 동일 규칙).
_USAGE_ADD_STATUSES = (SubscriptionStatus.ACTIVE, SubscriptionStatus.EXTENDED,
                       SubscriptionStatus.PAST_DUE)


def new_order_id(prefix: str) -> str:
    """토스 orderId 규칙: [A-Za-z0-9-_] 6~64자."""
    return f"{prefix}{uuid.uuid4().hex}"


async def get_open_subscription(db: AsyncSession, *, service_id: uuid.UUID,
                                external_user_id: str) -> Subscription | None:
    """현재 '살아 있는' 구독 반환(EXPIRED 제외).

    CANCELED도 기간이 남아 있으면 open으로 간주한다. 카드 변경·재개 등
    상태와 무관하게 슬롯 점유 여부만 확인할 때 사용한다.
    get_latest_subscription과의 차이: 이 함수는 상태 필터를 적용해 EXPIRED를
    걸러내고, get_latest_subscription은 상태 무관 최신 구독을 반환한다.
    """
    return await db.scalar(select(Subscription).where(
        Subscription.service_id == service_id,
        Subscription.external_user_id == external_user_id,
        Subscription.status.in_(OPEN_STATUSES)))


async def get_latest_subscription(db: AsyncSession, *, service_id: uuid.UUID,
                                  external_user_id: str) -> Subscription | None:
    """가장 최근에 생성된 구독 반환(상태 무관 — EXPIRED 포함).

    첫구독 여부 판정이 아닌 단순 이력 조회(예: 관리 화면 마지막 상태 표시) 용도.
    구독 중복 방지나 슬롯 점유 확인에는 get_open_subscription을 사용해야 한다.
    """
    return await db.scalar(select(Subscription).where(
        Subscription.service_id == service_id,
        Subscription.external_user_id == external_user_id,
    ).order_by(Subscription.created_at.desc()).limit(1))


async def _is_first_subscription(db: AsyncSession, *, service_id: uuid.UUID,
                                 external_user_id: str) -> bool:
    """첫 구독 판정 — '혜택을 소진한' 과거 구독이 없을 때만 True.

    혜택 소진 구독 = (a) DONE 결제가 있는 구독, 또는
                     (b) 결제 시도 자체가 없는 구독(FREE/100% 할인으로 활성화된 것).
    신규 가입 첫 결제 실패는 구독·결제 행을 남기지 않으므로(감사로그만) 애초에
    조회되지 않아 재시도 시 첫구독 혜택이 유지된다. (과거 FAILED 결제만 가진
    레거시 구독이 있어도 (a)·(b) 어느 쪽도 아니어서 동일하게 혜택 유지.)
    무료 첫구독은 (b)에 걸리므로 만료 후 재구독해도 무료가 반복되지 않는다.
    """
    has_done_payment = exists().where(
        Payment.subscription_id == Subscription.id,
        Payment.status == PaymentStatus.DONE)
    has_any_payment = exists().where(Payment.subscription_id == Subscription.id)
    benefit_used = await db.scalar(select(exists(
        select(Subscription.id).where(
            Subscription.service_id == service_id,
            Subscription.external_user_id == external_user_id,
            or_(has_done_payment, ~has_any_payment)))))
    return not benefit_used


def _validate_external_user_id(external_user_id: str) -> None:
    """external_user_id 입력 검증.

    external_user_id: 외부 서비스가 사용하는 사용자 ID — DB 유니크 인덱스 대상이므로
    공백만 있는 문자열도 거부한다(strip 검사).
    Task 7: customer_key 검증은 카드 등록 시점(cards.py)으로 이동했으므로 제거.
    """
    if (not external_user_id or not external_user_id.strip()
            or len(external_user_id) > 255):
        raise InputValidationError("external_user_id가 올바르지 않습니다")


# Task 10: _validate_inputs 제거 — change_card 전용이었으며 해당 함수와 함께 삭제됨.
# customer_key 검증은 카드 등록 시점(cards.py)에서 수행한다.


async def create_subscription(db: AsyncSession, toss: TossClient, cipher: AesGcmCipher,
                              *, service: Service, plan_id: uuid.UUID,
                              external_user_id: str,
                              trial: bool = False, notifier=None) -> Subscription:
    """구독 생성 — 등록 카드 조회 → PENDING 선커밋 → 첫 결제(체험 제외) → 확정.

    Task 7 변경: auth_key·customer_key 파라미터 제거. 빌링키는 사전 등록된 카드
    (cards 테이블)에서 조회한다. 구독 생성 시 별도 빌링키 발급 없음.

    흐름:
    1. 입력 검증 · 요금제 유효성 · 중복 구독 확인
    2. 등록 카드 조회 — 없으면 NotFoundError(먼저 POST /cards 로 카드 등록 필요)
    3. 첫구독 여부 판정 → 결제 금액 결정
       - 체험(trial=True): amount=0 (만료 시 자동결제로 상시 할인가 청구)
       - 비체험 첫구독: plan_first_amount (정가 + 첫구독 할인)
       - 재구독: plan_recurring_amount (상시 할인가)
    4. Subscription 생성 + flush(DB 유니크 인덱스로 동시성 경쟁 최종 판정)
       - IntegrityError → ConflictError (카드는 영속적이므로 삭제하지 않음)
    5. 감사 로그 기록 + 1차 commit (유니크 슬롯/PENDING 내구성 선점)
    6. amount>0이면 resolve_charge로 결제 실행:
       - TossTimeoutError → PENDING 유지, 503 반환 (이중결제 방지; 배치 정산 처리)
       - TossError       → 구독·결제 행 삭제 + 2차 commit (카드는 보존)
       - 성공             → payment DONE + 2차 commit
    7. 체험/amount=0이면 결제 없이 그대로 반환 (1차 commit만)

    commit이 2회인 이유: 결제 전 1차 commit으로 슬롯을 내구성 있게 점유하고,
    결제 결과 확정 후 2차 commit으로 최종 상태를 기록한다.
    1차 commit 없이 결제하면 결제 성공 후 DB 장애 시 과금만 되고 구독이 없어진다.

    상태 전이:
    - 체험: (없음) → TRIAL
    - 비체험 성공: (없음) → ACTIVE
    - 첫 결제 실패: 구독·결제 행 삭제(미저장) — 감사로그만 남김(요청). 카드는 보존.
    - 타임아웃: (없음) → ACTIVE(payment PENDING — 배치 정산 대기)
    """
    # external_user_id 검증 (customer_key 검증은 카드 등록 시점에 완료됨)
    _validate_external_user_id(external_user_id)

    plan = await db.get(Plan, plan_id)
    if plan is None or plan.service_id != service.id or plan.status != PlanStatus.ACTIVE:
        raise NotFoundError("요금제를 찾을 수 없습니다")

    if trial and not (plan.trial_enabled and plan.trial_days and plan.trial_days >= 1):
        raise InputValidationError("이 요금제는 체험(Trial)을 제공하지 않습니다")

    if await get_open_subscription(db, service_id=service.id,
                                   external_user_id=external_user_id):
        raise ConflictError("이미 구독이 존재합니다")

    # 사전 등록된 카드 조회 — 구독 생성 전에 카드가 반드시 있어야 한다.
    # (빌링키는 cards 테이블에 암호화 보관; create_subscription에서 신규 발급하지 않음)
    card = await get_card(db, service_id=service.id, external_user_id=external_user_id)
    if card is None:
        raise NotFoundError("등록된 카드가 없습니다. 먼저 카드를 등록하세요")
    # 비활성 카드는 결제 불가 — 첫 결제(또는 체험 만료 후 첫 결제)가 불가능하므로 생성 자체를 차단
    if not card.is_active:
        raise ConflictError("비활성화된 카드로는 구독을 생성할 수 없습니다")

    is_first = await _is_first_subscription(db, service_id=service.id,
                                            external_user_id=external_user_id)
    # 체험은 가입 시 결제하지 않는다(만료 시 상시 할인가 자동결제).
    # 비체험 첫 결제 = 정가에 첫구독 할인만 적용(요청 005), 재구독은 상시 할인가.
    amount = 0 if trial else (
        plan_first_amount(plan) if is_first else plan_recurring_amount(plan))

    # 위 검증 SELECT들이 열어 둔 읽기 트랜잭션을 닫는다(감사 Phase 1 — 성능 H1).
    # rollback이 아닌 commit인 이유: rollback은 로드된 객체(plan, card)를 expire시켜
    # 이후 속성 접근이 비동기 세션에서 오류가 된다. 읽기 전용이라 commit은 무해하며
    # expire_on_commit=False 덕에 객체가 그대로 유지된다.
    # 동시 가입 경쟁의 최종 심판은 어차피 아래 flush의 DB 부분 유니크 인덱스다.
    await db.commit()

    now = utcnow()
    if trial:
        period_end = now + timedelta(days=plan.trial_days)
        status = SubscriptionStatus.TRIAL
    else:
        period_end = compute_period_end(now, plan.billing_cycle, plan.cycle_days)
        status = SubscriptionStatus.ACTIVE

    # Subscription은 빌링키 컬럼 없이 card_id FK만 보유(Task 2/3에서 컬럼 제거됨).
    sub = Subscription(
        service_id=service.id, plan_id=plan.id, external_user_id=external_user_id,
        card_id=card.id,  # 사전 등록된 카드 참조(빌링키는 cards 테이블에서 조회)
        status=status,
        current_period_start=now, current_period_end=period_end,
        next_billing_at=period_end)  # 체험: 체험 만료 시점이 첫 정기 결제일
    # 자동결제 안함(요청 013, 체험과 공존):
    #   - 체험이면 체험 만료 시 첫 결제가 일어나야 하므로 next_billing(=체험 만료)을 유지하고,
    #     그 첫 결제 성공 후 _advance_period가 next_billing=None으로 만료를 예약한다.
    #   - 체험이 아니면 첫 결제는 지금 끝났으므로 즉시 next_billing=None → 기간 종료 시 EXPIRED.
    if not plan.auto_renew and not trial:
        sub.next_billing_at = None
    db.add(sub)
    try:
        await db.flush()
    except IntegrityError:
        # 동시 요청 경쟁 — DB 부분 유니크 인덱스가 최종 심판.
        # 카드는 영속적(구독과 독립)이므로 삭제하지 않는다.
        await db.rollback()
        raise ConflictError("이미 구독이 존재합니다") from None

    payment: Payment | None = None
    if amount > 0:
        payment = Payment(subscription_id=sub.id, order_id=new_order_id("f"),
                          amount=amount, payment_type=PaymentType.FIRST,
                          order_name=plan.name,  # 결제정보 표시용 상품명 = 요금제명
                          status=PaymentStatus.PENDING,
                          idempotency_key=f"first-{sub.id}", requested_at=now,
                          kind=PaymentKind.SUBSCRIPTION,
                          service_id=service.id,
                          external_user_id=external_user_id)
        db.add(payment)
    await record_audit(db, actor_type="SERVICE", actor_service_id=service.id,
                       action="subscription.create",
                       target_type="subscription", target_id=str(sub.id),
                       detail={"external_user_id": external_user_id,
                               "plan_id": str(plan.id), "amount": amount,
                               "is_first": is_first, "trial": trial,
                               "card_id": str(card.id)})
    # 결제 전에 commit: 유니크 슬롯/PENDING 기록을 내구성 있게 선점
    await db.commit()

    if payment is not None:
        # 카드에서 빌링키·customerKey 추출 — cards 테이블에 암호화 보관된 값을 복호화
        billing_key = cipher.decrypt(card.billing_key_encrypted)
        customer_key = card.customer_key
        try:
            result = await resolve_charge(
                toss, billing_key=billing_key, customer_key=customer_key,
                amount=amount, order_id=payment.order_id, order_name=plan.name,
                idempotency_key=payment.idempotency_key)
        except TossTimeoutError as exc:
            # 결과 불명 — 절대 '실패 확정' 처리하지 않는다.
            # 결제는 PENDING, 구독 슬롯은 점유 유지(재시도 이중결제 차단).
            # 갱신 배치의 PENDING 정산 스윕이 추후 확정한다.
            await record_audit(db, actor_type="SERVICE", actor_service_id=service.id,
                               action="subscription.first_payment_unresolved",
                               target_type="subscription", target_id=str(sub.id),
                               detail={"order_id": payment.order_id})
            await db.commit()
            raise PaymentFailedError(PENDING_GRACE_MESSAGE,
                                     code="PAYMENT_UNRESOLVED",
                                     http_status=503) from exc
        except TossError as exc:
            # 확정 실패(카드 거절 등) — 카드는 영속적이므로 삭제하지 않는다.
            # 신규 가입 실패는 구독·결제 테이블에 흔적을 남기지 않는다(요청):
            # 1차 commit으로 선점했던 구독·PENDING 결제 행을 삭제하고 감사로그만 남긴다.
            # (결제행이 RESTRICT FK로 구독을 참조하므로 결제 → 구독 순으로 삭제)
            sub_id, order_id = sub.id, payment.order_id
            await db.delete(payment)
            await db.delete(sub)
            await record_audit(db, actor_type="SERVICE", actor_service_id=service.id,
                               action="subscription.first_payment_failed",
                               target_type="subscription", target_id=str(sub_id),
                               detail={"code": exc.code,
                                       "billing_key_deleted": False,  # 카드 보존 — 삭제 안 함
                                       "card_id": str(card.id),
                                       "external_user_id": external_user_id,
                                       "plan_id": str(plan.id), "amount": amount,
                                       "order_id": order_id, "persisted": False})
            await db.commit()
            raise PaymentFailedError(f"첫 결제 실패: {exc.message}", code=exc.code) from exc

        payment.status = PaymentStatus.DONE
        payment.toss_payment_key = result.payment_key
        payment.approved_at = utcnow()
        payment.raw_response = result.raw
        await db.commit()

    # 서비스 알림 — 새 구독자 발생. best-effort(구독은 이미 확정됨).
    await _notify(notifier, service, sub, event=EVENT_SUBSCRIPTION_CREATED,
                  status=sub.status, order_id=(payment.order_id if payment else ""),
                  desc=f"새 구독 생성(요금제 {plan.name})")
    return sub


async def cancel_subscription(db: AsyncSession, *, service: Service, external_user_id: str,
                              actor_type: str = "SERVICE",
                              actor_user_id: uuid.UUID | None = None,
                              notifier=None) -> Subscription:
    """취소 — CANCELED로 전환. 일반 구독은 기간 만료까지 혜택 유지.
    체험(TRIAL) 취소는 즉시 종료(만료일=now → 다음 배치가 Expired 처리)."""
    sub = await db.scalar(select(Subscription).where(
        Subscription.service_id == service.id,
        Subscription.external_user_id == external_user_id,
        Subscription.status.in_((SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE,
                                 SubscriptionStatus.PAST_DUE))))
    if sub is None:
        existing = await get_open_subscription(db, service_id=service.id,
                                               external_user_id=external_user_id)
        if existing is not None and existing.status == SubscriptionStatus.CANCELED:
            raise ConflictError("이미 취소된 구독입니다")
        raise NotFoundError("구독을 찾을 수 없습니다")
    was_trial = sub.status == SubscriptionStatus.TRIAL
    pre_status = sub.status                        # 알림 PRE_STATUS
    transition(sub, SubscriptionStatus.CANCELED)  # next_billing=None 포함
    if was_trial:
        sub.current_period_end = utcnow()  # 체험 취소 → 즉시 만료
    await record_audit(db, actor_type=actor_type, actor_user_id=actor_user_id,
                       actor_service_id=service.id if actor_type == "SERVICE" else None,
                       action="subscription.cancel", target_type="subscription",
                       target_id=str(sub.id), detail={"trial": was_trial})
    await db.commit()
    # 서비스 알림 — 구독 취소(상태 변화). best-effort.
    await _notify(notifier, service, sub, event=EVENT_SUBSCRIPTION_STATUS,
                  pre_status=pre_status, status=sub.status, desc="구독 취소")
    return sub


async def _perform_manual_charge(db: AsyncSession, toss: TossClient,
                                 cipher: AesGcmCipher, *, sub: Subscription, plan: Plan,
                                 actor_type: str, actor_user_id: uuid.UUID | None = None,
                                 actor_service_id: uuid.UUID | None = None,
                                 notifier=None) -> Subscription:
    """SUSPENDED/PAST_DUE 구독에 빌링키로 즉시 재청구하는 공통 코어.

    호출자(외부 서비스 manual_charge_subscription / 어드민 admin_retry_payment)가
    이미 구독·요금제를 조회하고 상태가 SUSPENDED|PAST_DUE임을 확정한 뒤 호출한다.
    성공 시 ACTIVE 복귀 + 결제 기준일(Billing Anchor)을 결제 시점으로 리셋한다.

    감사 로그 행위자(actor_*)는 파라미터로 받아 외부 서비스(SERVICE)와
    관리자(USER) 호출 양쪽에서 동일 로직을 재사용한다.
    """
    manual_pre_status = sub.status                 # 알림 PRE_STATUS(복구 전 상태)
    # 빌링키는 cards 테이블(카드 보관함)에서 조회한다(Card Vault). 카드가 없으면 청구 불가.
    card = await get_card(db, service_id=sub.service_id,
                          external_user_id=sub.external_user_id)
    if card is None or sub.card_id is None:
        raise PaymentFailedError("등록된 카드가 없습니다. 카드를 다시 등록해주세요.",
                                 code="NO_BILLING_KEY")
    # 비활성 카드는 청구 불가 — 수동 재결제도 차단(활성화 후 다시 시도해야 함)
    if not card.is_active:
        raise PaymentFailedError("비활성화된 카드입니다. 카드를 활성화한 뒤 다시 시도해주세요.",
                                 code="CARD_INACTIVE")
    amount = plan_recurring_amount(plan)  # 상시 할인가
    now = utcnow()
    order_id = new_order_id("m")  # manual
    payment = Payment(subscription_id=sub.id, order_id=order_id, amount=amount,
                      payment_type=PaymentType.RETRY, status=PaymentStatus.PENDING,
                      order_name=plan.name,  # 결제정보 표시용 상품명 = 요금제명
                      idempotency_key=f"manual-{order_id}", requested_at=now,
                      kind=PaymentKind.SUBSCRIPTION,
                      service_id=sub.service_id,
                      external_user_id=sub.external_user_id)
    db.add(payment)
    await db.commit()  # 결제 전 내구성 확보

    # 모든 감사 기록이 동일한 행위자 정보를 쓰도록 공통 kwargs로 묶는다.
    actor = {"actor_type": actor_type, "actor_user_id": actor_user_id,
             "actor_service_id": actor_service_id}
    # 카드에서 빌링키·customerKey 복호화(Card Vault)
    billing_key = cipher.decrypt(card.billing_key_encrypted)
    try:
        result = await resolve_charge(
            toss, billing_key=billing_key, customer_key=card.customer_key,
            amount=amount, order_id=order_id, order_name=plan.name,
            idempotency_key=payment.idempotency_key)
    except TossTimeoutError as exc:
        await record_audit(db, **actor,
                           action="subscription.manual_pay_unresolved",
                           target_type="subscription", target_id=str(sub.id),
                           detail={"order_id": order_id})
        await db.commit()
        raise PaymentFailedError(PENDING_GRACE_MESSAGE, code="PAYMENT_UNRESOLVED",
                                 http_status=503) from exc
    except TossError as exc:
        payment.status = PaymentStatus.FAILED
        payment.failure_code = exc.code
        payment.failure_message = exc.message
        await record_audit(db, **actor,
                           action="subscription.manual_pay_failed",
                           target_type="subscription", target_id=str(sub.id),
                           detail={"code": exc.code})
        await db.commit()
        raise PaymentFailedError(f"결제 실패: {exc.message}", code=exc.code) from exc

    payment.status = PaymentStatus.DONE
    payment.toss_payment_key = result.payment_key
    payment.approved_at = utcnow()
    payment.raw_response = result.raw
    # ACTIVE 복귀(retry_count=0·suspended_at=None은 transition이 처리)
    # + 기준일 리셋(결제 시점부터 새 주기)
    transition(sub, SubscriptionStatus.ACTIVE)
    sub.current_period_start = now
    sub.current_period_end = compute_period_end(now, plan.billing_cycle, plan.cycle_days)
    sub.next_billing_at = sub.current_period_end
    await record_audit(db, **actor,
                       action="subscription.manual_pay",
                       target_type="subscription", target_id=str(sub.id),
                       detail={"order_id": order_id, "amount": amount,
                               "external_user_id": sub.external_user_id,
                               "old_status": "SUSPENDED/PAST_DUE",
                               "new_status": "ACTIVE"})
    await db.commit()
    # 서비스 알림 — 수동 결제로 ACTIVE 복귀(상태 변화). best-effort.
    service = await db.get(Service, sub.service_id)
    await _notify(notifier, service, sub, event=EVENT_SUBSCRIPTION_STATUS,
                  pre_status=manual_pre_status, status=sub.status, order_id=order_id,
                  desc=f"수동 결제 {amount:,}원 — 이용중 복귀")
    return sub


async def manual_charge_subscription(db: AsyncSession, toss: TossClient,
                                     cipher: AesGcmCipher, *, service: Service,
                                     external_user_id: str, notifier=None) -> Subscription:
    """SUSPENDED 또는 PAST_DUE(실패중) 구독의 수동 결제 — 외부 서비스가 호출.

    SUSPENDED: 자동결제가 완전히 중지된 정지 상태에서 호출한다.
    PAST_DUE: 자동결제 재시도 중(실패중)인 상태에서 사용자가 직접 결제할 때 호출한다.
    두 상태 모두 성공 시 ACTIVE로 복귀하고 기준일을 결제 시점으로 리셋한다(_perform_manual_charge).
    """
    # SUSPENDED(정지) 또는 PAST_DUE(실패중) 구독을 허용한다.
    sub = await db.scalar(select(Subscription).where(
        Subscription.service_id == service.id,
        Subscription.external_user_id == external_user_id,
        Subscription.status.in_((SubscriptionStatus.SUSPENDED,
                                 SubscriptionStatus.PAST_DUE))))
    if sub is None:
        raise NotFoundError("정지/미수 상태의 구독을 찾을 수 없습니다")
    plan = await db.get(Plan, sub.plan_id)
    return await _perform_manual_charge(db, toss, cipher, sub=sub, plan=plan,
                                        actor_type="SERVICE", actor_service_id=service.id,
                                        notifier=notifier)


async def admin_retry_payment(db: AsyncSession, toss: TossClient, cipher: AesGcmCipher,
                              *, subscription_id: uuid.UUID,
                              service_scope: list[uuid.UUID] | None,
                              actor_user_id: uuid.UUID, notifier=None) -> Subscription:
    """admin 화면에서 결제 실패(PAST_DUE)·정지(SUSPENDED) 구독을 담당자가 즉시 재결제.

    service_scope=None이면 전체(SYSTEM_ADMIN), 목록이면 담당 서비스만 허용
    (목록 밖이면 NotFoundError — 존재 여부 비노출). SUSPENDED/PAST_DUE 외 상태는
    ConflictError. 성공 시 ACTIVE 복귀 + 기준일 리셋, 감사 로그는 actor_type=USER로 남긴다.
    """
    sub = await db.get(Subscription, subscription_id)
    if sub is None or (service_scope is not None and sub.service_id not in service_scope):
        raise NotFoundError("구독을 찾을 수 없습니다")
    if sub.status not in (SubscriptionStatus.SUSPENDED, SubscriptionStatus.PAST_DUE):
        raise ConflictError("결제 처리할 수 있는 상태가 아닙니다(실패/정지 상태만 가능)")
    plan = await db.get(Plan, sub.plan_id)
    return await _perform_manual_charge(db, toss, cipher, sub=sub, plan=plan,
                                        actor_type="USER", actor_user_id=actor_user_id,
                                        notifier=notifier)


async def resume_subscription(db: AsyncSession, *, service: Service,
                              external_user_id: str,
                              actor_type: str = "SERVICE",
                              actor_user_id: uuid.UUID | None = None,
                              notifier=None) -> Subscription:
    """만료 전 취소 철회. 미수금(retry_count>0)이면 PAST_DUE로 복귀해 즉시 재시도.

    상태 전이:
    - CANCELED → ACTIVE (retry_count=0, 정상 구독 — 기존 기간 끝에 자동 갱신)
    - CANCELED → PAST_DUE (retry_count>0, 미수금 있음 — next_billing_at=now로 즉시 재시도)
    이미 기간이 만료된 CANCELED는 재개 불가(재구독으로 처리해야 함).
    """
    sub = await db.scalar(select(Subscription).where(
        Subscription.service_id == service.id,
        Subscription.external_user_id == external_user_id,
        Subscription.status == SubscriptionStatus.CANCELED))
    if sub is None:
        raise NotFoundError("취소된 구독이 없습니다")
    now = utcnow()
    if sub.current_period_end <= now:
        raise ConflictError("만료된 구독은 재개할 수 없습니다")
    resume_pre_status = sub.status                 # 알림 PRE_STATUS(CANCELED)
    if sub.retry_count > 0:
        transition(sub, SubscriptionStatus.PAST_DUE)
        sub.next_billing_at = now          # 미수금 — 즉시 재시도 예약
    else:
        transition(sub, SubscriptionStatus.ACTIVE)
        sub.next_billing_at = sub.current_period_end  # 기존 기간 끝에 자동 갱신
        # 자동결제 안함 요금제는 재개해도 자동 갱신을 예약하지 않는다(현 주기 종료 시 만료).
        plan = await db.get(Plan, sub.plan_id)
        if plan is not None and not plan.auto_renew:
            sub.next_billing_at = None
    await record_audit(db, actor_type=actor_type, actor_user_id=actor_user_id,
                       actor_service_id=service.id if actor_type == "SERVICE" else None,
                       action="subscription.resume",
                       target_type="subscription", target_id=str(sub.id))
    await db.commit()
    # 서비스 알림 — 구독 재개(상태 변화). best-effort.
    await _notify(notifier, service, sub, event=EVENT_SUBSCRIPTION_STATUS,
                  pre_status=resume_pre_status, status=sub.status, desc="구독 재개")
    return sub


# Task 10: change_card 함수 제거.
# 카드 교체는 POST /api/v1/cards(재등록 — register_or_replace_card)로 처리한다.
# 재등록 시 cards 테이블의 해당 행이 갱신되고, 구독은 card_id FK로 자동 참조하므로
# 별도 구독 레이어 함수가 필요 없다. PAST_DUE 즉시재시도 로직도 cards.py에서 담당.


async def add_usage_days(db: AsyncSession, *, service: Service, external_user_id: str,
                         days: int, actor_type: str = "SERVICE",
                         actor_user_id: uuid.UUID | None = None) -> Subscription:
    """외부 서비스가 자기 사용자 구독에 사용일을 추가한다(요청). 반환: 갱신된 Subscription.

    이용 중(ACTIVE·EXTENDED·PAST_DUE) 구독의 만료일(current_period_end)과
    다음 결제일(next_billing_at)을 days만큼 미룬다. **상태는 변경하지 않는다.**
    next_billing_at이 None이면 그대로 둔다. 토스 결제 호출은 없다(날짜만 변경).
    어드민 요금제 보너스(add_bonus_days)와 동일한 의미를 단건(구독)으로 제공한다.
    """
    if not 1 <= days <= 3650:
        raise InputValidationError("추가 일수는 1~3650 사이여야 합니다")
    sub = await get_open_subscription(db, service_id=service.id,
                                      external_user_id=external_user_id)
    if sub is None:
        raise NotFoundError("구독을 찾을 수 없습니다")
    if sub.status not in _USAGE_ADD_STATUSES:
        raise ConflictError("사용일을 추가할 수 있는 구독 상태가 아닙니다(이용 중만 가능)")
    delta = timedelta(days=days)
    old_end = sub.current_period_end
    sub.current_period_end = old_end + delta
    if sub.next_billing_at is not None:
        sub.next_billing_at = sub.next_billing_at + delta   # 조기 청구 방지 위해 함께 미룸
    await record_audit(db, actor_type=actor_type, actor_user_id=actor_user_id,
                       actor_service_id=service.id if actor_type == "SERVICE" else None,
                       action="subscription.usage_added",
                       target_type="subscription", target_id=str(sub.id),
                       detail={"external_user_id": external_user_id, "days": days,
                               "old_period_end": old_end.isoformat(),
                               "new_period_end": sub.current_period_end.isoformat()})
    await db.commit()
    return sub


async def force_cancel_subscription(db: AsyncSession, *, subscription_id: uuid.UUID,
                                    service_scope: list[uuid.UUID] | None,
                                    actor_user_id: uuid.UUID, notifier=None) -> Subscription:
    """admin 화면에서 강제취소. service_scope(담당 서비스 목록)가 있으면 소속만 허용.

    service_scope=None이면 슈퍼 관리자 — 모든 구독 취소 가능.
    service_scope=[...]이면 담당자 — 목록에 없는 서비스 구독은 NotFoundError.
    ACTIVE·PAST_DUE·EXTENDED 만 허용; TRIAL·CANCELED·SUSPENDED·EXPIRED는 ConflictError.
    취소 즉시 next_billing_at=None으로 자동갱신을 차단하고, 기간 만료 시 배치가 EXPIRED 처리.
    """
    sub = await db.get(Subscription, subscription_id)
    if sub is None or (service_scope is not None and sub.service_id not in service_scope):
        raise NotFoundError("구독을 찾을 수 없습니다")
    # ACTIVE·PAST_DUE에 더해 EXTENDED(연장처리)도 강제취소 허용(연장된 구독도 취소 가능)
    if sub.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE,
                          SubscriptionStatus.EXTENDED):
        raise ConflictError("취소할 수 없는 상태입니다")
    status_before = sub.status   # 변경 전 상태 캡처(감사 상세)
    plan = await db.get(Plan, sub.plan_id)
    transition(sub, SubscriptionStatus.CANCELED)  # next_billing=None 포함
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="subscription.force_cancel", target_type="subscription",
                       target_id=str(sub.id),
                       detail={"external_user_id": sub.external_user_id,
                               "plan_name": plan.name if plan else None,
                               "old_status": status_before,
                               "new_status": SubscriptionStatus.CANCELED})
    await db.commit()
    # 서비스 알림 — 관리자 강제 구독취소. best-effort.
    service = await db.get(Service, sub.service_id)
    await _notify(notifier, service, sub, event=EVENT_SUBSCRIPTION_FORCE_CANCELED,
                  pre_status=status_before, status=sub.status,
                  desc=f"관리자 강제취소(요금제 {plan.name if plan else '-'})")
    return sub


async def extend_subscription(db: AsyncSession, *, subscription_id: uuid.UUID,
                              service_scope: list[uuid.UUID] | None,
                              new_end: datetime,
                              actor_user_id: uuid.UUID, notifier=None) -> Subscription:
    """admin 화면에서 구독 만료일을 수동 연장(요청). 상태를 EXTENDED(연장처리)로 전환.

    - service_scope=None=슈퍼관리자, 목록이면 담당 서비스만(아니면 NotFoundError).
    - 열린 구독 5개(TRIAL/ACTIVE/PAST_DUE/SUSPENDED/CANCELED)만 연장 가능.
      EXPIRED(완전 종료)는 ConflictError — 재구독으로 처리.
    - new_end는 미래 날짜여야 한다(과거/현재면 InputValidationError).
    - 만료일(current_period_end)과 다음 결제일(next_billing_at)을 모두 new_end로 설정.
      → 그 시점에 갱신 배치가 자동결제로 갱신(성공 시 ACTIVE)한다(DUE_STATUSES에 EXTENDED 포함).
    - 변경 전/후(상태·만료일·다음결제)를 감사 로그에 상세히 남긴다.
    """
    sub = await db.get(Subscription, subscription_id)
    if sub is None or (service_scope is not None and sub.service_id not in service_scope):
        raise NotFoundError("구독을 찾을 수 없습니다")
    if sub.status == SubscriptionStatus.EXPIRED:
        raise ConflictError("만료된 구독은 연장할 수 없습니다. 재구독으로 처리하세요.")
    if sub.status not in OPEN_STATUSES:   # 방어(이론상 EXPIRED 외엔 모두 열린 상태)
        raise ConflictError("연장할 수 없는 상태입니다")
    if new_end <= utcnow():
        raise InputValidationError("연장 만료일은 미래 날짜여야 합니다")
    # 변경 전 값 캡처(감사 전/후)
    old_status = sub.status
    old_end = sub.current_period_end
    old_next = sub.next_billing_at
    transition(sub, SubscriptionStatus.EXTENDED)   # 중앙 전이 검증 경유
    sub.retry_count = 0          # 활성 연장 — 실패/정지 흔적 정리
    sub.suspended_at = None
    sub.current_period_end = new_end
    sub.next_billing_at = new_end   # 만료일=다음결제일(요청) — 그 시점에 자동결제 갱신
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="subscription.extended", target_type="subscription",
                       target_id=str(sub.id),
                       detail={"external_user_id": sub.external_user_id,
                               "old_status": old_status,
                               "new_status": SubscriptionStatus.EXTENDED,
                               "old_period_end": old_end.isoformat() if old_end else None,
                               "new_period_end": new_end.isoformat(),
                               "old_next_billing_at": old_next.isoformat() if old_next else None,
                               "new_next_billing_at": new_end.isoformat()})
    await db.commit()
    # 서비스 알림 — 만료일 연장. best-effort.
    service = await db.get(Service, sub.service_id)
    await _notify(notifier, service, sub, event=EVENT_SUBSCRIPTION_EXTENDED,
                  pre_status=old_status, status=sub.status,
                  desc=f"만료일 연장 → {new_end.date().isoformat()}")
    return sub
