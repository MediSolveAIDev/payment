"""정기 갱신 배치 — 만료·취소·정지 구독 처리 + 결제 재시도.

process_due가 배치 1회 실행 진입점이며, 내부에서:
- CANCELED + 기간 만료 → _expire_canceled (EXPIRED 전환; 빌링키는 cards 테이블이 관리하므로 삭제하지 않음)
- SUSPENDED + 대기 일수 초과 → _expire_suspended (EXPIRED 전환; 빌링키는 cards 테이블이 관리하므로 삭제하지 않음)
- TRIAL/ACTIVE/PAST_DUE + next_billing_at 도래 → _renew_one (결제 + 상태 전이)
배치 종료 시 reconcile_pending으로 타임아웃 결제 PENDING 정산 스윕을 실행한다.
"""
import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.clock import utcnow
from app.core.config import default_settings
from app.core.crypto import AesGcmCipher
from app.models import (
    GlobalSettings,
    Payment,
    PaymentKind,
    PaymentStatus,
    PaymentType,
    Plan,
    Service,
    Subscription,
    SubscriptionStatus,
)
from app.notifications.email import EmailSender
from app.notifications.service_notify import (
    EVENT_SUBSCRIPTION_RENEWED,
    EVENT_SUBSCRIPTION_STATUS,
)
from app.services.app_settings import get_global_settings  # DB 전역설정 로드 (요청 013)
from app.services.audit import record_audit
from app.services.billing_math import compute_period_end, plan_recurring_amount
from app.services.cards import get_card  # 빌링키는 cards 테이블에서 조회(Card Vault)
from app.services.locks import (
    DUE_STATUSES,
    acquire_lock,
    release_lock,
)
from app.services.payment_utils import resolve_charge
from app.services.transitions import transition
from app.toss.client import TossClient
from app.toss.errors import TossError, TossTimeoutError

logger = logging.getLogger("payment.renewals")

# 기본값 폴백(요청 002). 요청 013부터 실제 값은 GlobalSettings(DB)에서 주입 — 아래는 비상 폴백.
DEFAULT_RETRY_LIMIT = 4
DEFAULT_RETRY_INTERVAL = timedelta(hours=12)
DEFAULT_SUSPENDED_GRACE = timedelta(days=30)

# 배치 1회당 카테고리별 처리 상한(감사 Phase 1 — 성능 H2).
# due가 폭주해도 한 배치가 끝없이 길어지지 않도록 끊는다 — 남은 건은
# 다음 주기(기본 5분)가 due 시각 오름차순으로 이어서 처리한다.
# .env(renewal_batch_limit)로 조정 가능.
BATCH_LIMIT = default_settings().renewal_batch_limit
# 배치 내 동시 처리 한도(감사 Phase 1 — 성능 H2). 구독별 Redis 락 +
# 결정적 order_id/토스 멱등키가 건별 동시성을 이미 방어하므로 병렬 실행이
# 안전하다. 토스 API·DB 부하를 고려해 보수적으로 시작 — 필요 시 상향.
BATCH_CONCURRENCY = 10


class _Cfg:
    """갱신 배치 설정 컨테이너.

    GlobalSettings(DB) 또는 Settings(.env) 객체를 받아 timedelta로 변환한다.
    None을 전달하면 하드코딩된 기본값 사용(레거시 테스트 경로 및 비상 폴백).
    요청 013부터 process_due는 항상 GlobalSettings로 초기화한다.
    """

    def __init__(self, settings: "GlobalSettings | None" = None) -> None:
        # GlobalSettings·Settings 모두 동일 속성명(retry_limit/retry_interval_hours/suspended_grace_days)을 가짐
        if settings is not None:
            self.retry_limit = settings.retry_limit
            self.retry_interval = timedelta(hours=settings.retry_interval_hours)
            self.suspended_grace = timedelta(days=settings.suspended_grace_days)
        else:
            # 기본값 폴백 — DB 연결 불가 등 비상 경로
            self.retry_limit = DEFAULT_RETRY_LIMIT
            self.retry_interval = DEFAULT_RETRY_INTERVAL
            self.suspended_grace = DEFAULT_SUSPENDED_GRACE


async def _notify_sub(db, notifier, sub: Subscription, *, event: str,
                      pre_status="", status="", desc: str = "", order_id: str = "") -> None:
    """구독 관련 서비스 알림 발송(best-effort). 스케줄러 경로 공통 헬퍼.

    notifier가 없으면(직접 호출 테스트 등) no-op. 서비스를 조회해 알림을 보낸다
    (URL 미등록이면 notifier.send 내부에서 무시). email은 구독 사용자 식별자(external_user_id).
    """
    if notifier is None:
        return
    service = await db.get(Service, sub.service_id)
    if service is None:
        return
    await notifier.send(service, event=event, subscribe_id=str(sub.id), order_id=order_id,
                        pre_status=str(pre_status or ""), status=str(status or ""),
                        email=sub.external_user_id, desc=desc)


def _renewal_order_id(sub: Subscription) -> str:
    """(구독, 기간, 시도)에 대해 결정적 — 크래시 후 재실행해도 같은 주문/멱등키."""
    return f"r{sub.id.hex}p{int(sub.current_period_end.timestamp())}a{sub.retry_count}"


def _advance_period(sub: Subscription, plan: Plan) -> None:
    """갱신 성공 후 구독 기간을 한 주기 전진.

    TRIAL→ACTIVE, PAST_DUE→ACTIVE 상태 전이도 포함한다.
    retry_count 초기화는 여기서 일괄 처리 — 호출측이 별도로 초기화하지 않는다.
    """
    # 상태 전이는 중앙 헬퍼로 — 허용 검증 + retry_count=0·suspended_at=None 초기화 포함
    transition(sub, SubscriptionStatus.ACTIVE)
    new_start = sub.current_period_end
    sub.current_period_start = new_start
    sub.current_period_end = compute_period_end(new_start, plan.billing_cycle, plan.cycle_days)
    sub.next_billing_at = sub.current_period_end
    # 자동결제 안함(요청 013): 이번 결제가 마지막 — 다음 갱신을 예약하지 않아
    # 이 주기 종료 시 _expire_non_renewing이 EXPIRED 처리(체험 만료 후 첫 결제 케이스 포함).
    if not plan.auto_renew:
        sub.next_billing_at = None


async def process_due(session_factory: async_sessionmaker, redis: Redis, toss: TossClient,
                      cipher: AesGcmCipher, email_sender: EmailSender,
                      *, now: datetime | None = None, notifier=None) -> dict:
    """갱신 배치 1회 실행. 스케줄러/관리 명령에서 호출.

    재시도 설정(retry_limit/retry_interval_hours/suspended_grace_days)은
    GlobalSettings(DB) 단일 행에서 로드한다(요청 013). 배치 실행마다 최신값이 적용됨.

    흐름:
    1. 별도 세션에서 GlobalSettings 로드 후 due ID 목록을 한 번에 조회 (읽기 전용, 락 없음)
       - canceled_due: CANCELED + 기간 만료
       - suspended_due: SUSPENDED + 대기 일수(suspended_grace) 초과
       - renew_due: TRIAL/ACTIVE/PAST_DUE + next_billing_at 도래
       - non_renewing_due: ACTIVE + 자동결제 안함 + 기간 만료
       각 목록은 due 시각 오름차순 + BATCH_LIMIT 상한 — 폭주 시 다음 주기가 이어서 처리.
    2. 전 카테고리 작업을 세마포어(BATCH_CONCURRENCY)로 묶어 병렬 실행
       (감사 Phase 1 — 성능 H2). 카테고리 간 대상 상태가 서로 겹치지 않아
       (CANCELED/SUSPENDED/DUE/ACTIVE+non-renewing) 순서 의존성이 없고,
       건별 동시성은 구독별 Redis 락 + 결정적 order_id/멱등키가 방어한다.
       한 항목 실패는 errors 집계 후 계속 — 배치 전체를 중단시키지 않음.
    3. reconcile_pending으로 유예 경과 PENDING 결제 정산
    4. stats 딕셔너리 반환 (renewed/failed/suspended/expired/skipped/unresolved/errors)

    now를 파라미터로 받는 이유: 테스트에서 시간을 주입하기 위함.
    """
    now = now or utcnow()
    stats = {"renewed": 0, "failed": 0, "suspended": 0, "expired": 0, "skipped": 0,
             "unresolved": 0, "reconciled": 0, "errors": 0}
    async with session_factory() as db:
        gs = await get_global_settings(db)   # 재시도 한계·간격·유예를 DB 전역설정에서 로드 (요청 013)
        cfg = _Cfg(gs)
        # 각 카테고리는 due 시각 오름차순으로 BATCH_LIMIT까지만 — 적체 시 오래된
        # 건부터 처리하고 나머지는 다음 주기에 넘긴다(감사 Phase 1 — 성능 H2).
        canceled_due = list((await db.scalars(select(Subscription.id).where(
            Subscription.status == SubscriptionStatus.CANCELED,
            Subscription.current_period_end <= now)
            .order_by(Subscription.current_period_end).limit(BATCH_LIMIT))).all())
        # SUSPENDED 대기 일수 초과 → 만료
        suspended_due = list((await db.scalars(select(Subscription.id).where(
            Subscription.status == SubscriptionStatus.SUSPENDED,
            Subscription.suspended_at.is_not(None),
            Subscription.suspended_at <= now - cfg.suspended_grace)
            .order_by(Subscription.suspended_at).limit(BATCH_LIMIT))).all())
        # TRIAL 만료 / 정기·재시도 결제 대상(next_billing_at이 설정된 구독만 — None은 non_renewing)
        renew_due = list((await db.scalars(select(Subscription.id).where(
            Subscription.status.in_(DUE_STATUSES),
            Subscription.next_billing_at.is_not(None),
            Subscription.next_billing_at <= now)
            .order_by(Subscription.next_billing_at).limit(BATCH_LIMIT))).all())
        # 자동결제 안함(auto_renew=False) 구독: ACTIVE + next_billing None + 기간 만료 (요청 013)
        non_renewing_due = list((await db.scalars(select(Subscription.id).where(
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.next_billing_at.is_(None),
            Subscription.current_period_end <= now)
            .order_by(Subscription.current_period_end).limit(BATCH_LIMIT))).all())
    for name, ids in (("canceled", canceled_due), ("suspended", suspended_due),
                      ("renew", renew_due), ("non_renewing", non_renewing_due)):
        if len(ids) >= BATCH_LIMIT:
            # 상한 도달 — 침묵 누락처럼 보이지 않도록 명시적으로 남긴다
            logger.warning("배치 상한 도달(%s): %d건 — 잔여분은 다음 주기에 처리",
                           name, BATCH_LIMIT)

    # 카테고리별 처리 함수에 공통 인자를 미리 바인딩 — 작업 항목은 sub_id 하나만 남긴다.
    handlers = {
        "취소 만료": (canceled_due, partial(
            _expire_canceled, session_factory, redis, toss, cipher,
            now=now, stats=stats, notifier=notifier)),
        "정지 만료": (suspended_due, partial(
            _expire_suspended, session_factory, redis, toss, cipher,
            now=now, cfg=cfg, stats=stats, notifier=notifier)),
        "갱신": (renew_due, partial(
            _renew_one, session_factory, redis, toss, cipher, email_sender,
            now=now, cfg=cfg, stats=stats, notifier=notifier)),
        "비자동갱신 만료": (non_renewing_due, partial(
            _expire_non_renewing, session_factory, redis, toss, cipher,
            now=now, stats=stats, notifier=notifier)),
    }
    sem = asyncio.Semaphore(BATCH_CONCURRENCY)

    async def _run_one(label: str, handler, sub_id: uuid.UUID) -> None:
        """세마포어 안에서 1건 처리. 한 항목 실패가 배치 전체를 죽이면 안 됨."""
        async with sem:
            try:
                await handler(sub_id)
            except Exception:  # noqa: BLE001
                logger.exception("%s 처리 실패: sub=%s", label, sub_id)
                stats["errors"] += 1

    # 전 카테고리를 하나의 병렬 풀로 실행 — 카테고리 간 상태 집합이 겹치지 않아
    # (CANCELED/SUSPENDED/DUE/ACTIVE+non-renewing) 한 구독이 두 카테고리에
    # 동시에 들어올 수 없으므로 순서 의존성이 없다.
    await asyncio.gather(*(
        _run_one(label, handler, sub_id)
        for label, (ids, handler) in handlers.items()
        for sub_id in ids))
    from app.services.reconciliation import reconcile_pending
    await reconcile_pending(session_factory, redis, toss, cipher, email_sender,
                            now=now, stats=stats)
    return stats


async def _expire_subscription(session_factory, redis, toss, cipher, sub_id: uuid.UUID,
                               *, reason: str, should_expire: Callable[[Subscription], bool],
                               stats: dict, notifier=None) -> None:
    """구독을 EXPIRED로 종료 — 정지/취소 만료 공통 로직.

    Redis 락으로 배치 중복 실행 경쟁을 막고, FOR UPDATE로 행을 잠근다.
    `should_expire(sub)`가 True일 때만 만료 처리(상태·시점 판정은 호출측이 주입).
    카드 보관함(Card Vault) 도입 이후 빌링키는 구독이 아니라 cards 테이블이
    소유한다 — 카드는 영속적 자원이며 삭제는 delete_card(활성 구독 차단 규칙 포함)가
    전담한다. 따라서 구독 만료 시 빌링키를 삭제하지 않는다(카드는 그대로 보존).
    감사 detail.reason은 호출 경위(suspended_timeout / canceled_period_end)를 기록한다.
    """
    lock_key = f"lock:renew:{sub_id}"
    token = await acquire_lock(redis, lock_key)
    if token is None:
        stats["skipped"] += 1
        return
    try:
        async with session_factory() as db:
            sub = await db.get(Subscription, sub_id, with_for_update=True)
            if sub is None or not should_expire(sub):
                stats["skipped"] += 1
                return
            pre_status = sub.status                       # 알림 PRE_STATUS용(전이 전 상태)
            transition(sub, SubscriptionStatus.EXPIRED)  # 종단 — next_billing=None 포함
            await record_audit(db, actor_type="SYSTEM", action="subscription.expired",
                               target_type="subscription", target_id=str(sub.id),
                               detail={"reason": reason})
            await db.commit()
            # 서비스 알림 — 구독 만료(상태 변화). best-effort.
            await _notify_sub(db, notifier, sub, event=EVENT_SUBSCRIPTION_STATUS,
                              pre_status=pre_status, status=SubscriptionStatus.EXPIRED,
                              desc=f"구독 만료({reason})")
        stats["expired"] += 1
    finally:
        await release_lock(redis, lock_key, token)


async def _expire_suspended(session_factory, redis, toss, cipher,
                            sub_id: uuid.UUID, *, now, cfg: _Cfg, stats: dict,
                            notifier=None) -> None:
    """SUSPENDED 대기 일수(suspended_grace) 초과 → EXPIRED 전환.

    카드 보관함(Card Vault) 도입 이후 빌링키는 cards 테이블에서 관리된다.
    구독 만료 시 빌링키를 삭제하지 않는다 — 카드는 영속적 자원이며 삭제는 delete_card가 전담.
    판정: 상태가 SUSPENDED이고 suspended_at이 (now - grace) 이하일 때만 만료.
    실제 종료 처리는 _expire_subscription에 위임한다.
    """
    await _expire_subscription(
        session_factory, redis, toss, cipher, sub_id, reason="suspended_timeout",
        stats=stats, notifier=notifier,
        should_expire=lambda sub: (
            sub.status == SubscriptionStatus.SUSPENDED
            and sub.suspended_at is not None
            and sub.suspended_at <= now - cfg.suspended_grace))


async def _expire_canceled(session_factory, redis, toss, cipher,
                           sub_id: uuid.UUID, *, now: datetime, stats: dict,
                           notifier=None) -> None:
    """CANCELED 구독의 기간 만료(current_period_end <= now) → EXPIRED 전환.

    취소 후 혜택 유지 기간이 끝난 구독을 최종 종료한다.
    빌링키는 cards 테이블에서 관리하므로 구독 만료 시 삭제하지 않는다.
    처리는 _expire_subscription에 위임.
    """
    await _expire_subscription(
        session_factory, redis, toss, cipher, sub_id, reason="canceled_period_end",
        stats=stats, notifier=notifier,
        should_expire=lambda sub: (
            sub.status == SubscriptionStatus.CANCELED
            and sub.current_period_end <= now))


async def _expire_non_renewing(session_factory, redis, toss, cipher,
                               sub_id: uuid.UUID, *, now: datetime, stats: dict,
                               notifier=None) -> None:
    """자동결제 안함(auto_renew=False) 구독의 기간 종료 → EXPIRED 전환 (요청 013).

    빌링키는 cards 테이블에서 관리하므로 구독 만료 시 삭제하지 않는다.
    판정: ACTIVE 상태 + next_billing_at이 None(자동갱신 없음 표시) + 기간 만료
    이 조합은 auto_renew=False로 생성된 구독의 만료 경로이며,
    CANCELED(next_billing=None) 구독은 _expire_canceled가 이미 처리하므로
    ACTIVE만 대상으로 한정한다.
    처리는 _expire_subscription에 위임한다.
    """
    await _expire_subscription(
        session_factory, redis, toss, cipher, sub_id, reason="non_renewing_period_end",
        stats=stats, notifier=notifier,
        should_expire=lambda sub: (
            sub.status == SubscriptionStatus.ACTIVE          # 정상 상태
            and sub.next_billing_at is None                  # 자동갱신 없음 표시
            and sub.current_period_end <= now))              # 기간 만료


async def _renew_one(session_factory, redis, toss, cipher, email_sender,
                     sub_id: uuid.UUID, *, now: datetime, cfg: _Cfg, stats: dict,
                     notifier=None) -> None:
    """TRIAL/ACTIVE/PAST_DUE 구독 1건 갱신 결제.

    토스 호출(최대 65초) 동안 FOR UPDATE 행 잠금·풀 커넥션을 쥐지 않도록
    트랜잭션을 3단계로 분리한다(감사 Phase 1 — 성능 H1). 외부 호출 사이의
    동시성은 ① 구독별 Redis 락(이 함수 전체를 감쌈, TTL 300s > 토스 65s),
    ② 결정적 order_id + 토스 멱등키, ③ 3단계의 FOR UPDATE 재취득·재검증이 방어한다.

    흐름:
    1. Redis 락 획득 — 실패 시 skipped(다른 인스턴스/이전 배치가 처리 중)
    2. [1단계 트랜잭션] with_for_update로 구독 검증 (상태·next_billing_at; 빌링키는 cards 테이블에서 조회)
       - 결정적 order_id 생성 — (sub.id, period_end, retry_count) 조합
         → 크래시 후 재실행해도 같은 주문/멱등키로 수렴(이중결제 차단)
       - 같은 order_id의 DONE 결제가 이미 있으면 재결제 없이 기간만 전진(방어적 복구)
       - PENDING 결제 없으면 생성
       - commit — PENDING 내구성 확보 + 행 잠금·커넥션 반납(외부 호출 전 필수)
    3. [2단계: 외부 호출 — DB 비점유] resolve_charge 실행:
       - TossTimeoutError → 결과 불명. payment PENDING·sub 불변 유지.
         next_billing_at 그대로이므로 다음 배치가 같은 order_id/멱등키로 재시도 →
         토스 멱등 재생으로 수렴(이중결제 방어).
       - TossError(ALREADY_PROCESSED_PAYMENT) → order_id로 재조회해 DONE이면 성공 취급
       - 그 외 TossError → 실패로 3단계 진입
    4. [3단계 트랜잭션] FOR UPDATE 재취득 + 재검증 후 결과 확정:
       - payment가 더 이상 PENDING이 아니면(웹훅/정산이 먼저 확정) 중복 적용 금지 → skip
       - 구독이 외부 호출 사이에 갱신 풀을 벗어났으면(취소 등) 기간을 전진시키지
         않고 결제 결과만 기록(성공 시 requires_review 감사 — 환불 검토 대상)
       - 성공 → payment DONE + _advance_period (상태 전이 포함) + commit
       - 실패 → _handle_charge_failure (PAST_DUE 재시도 / SUSPENDED 정지)

    상태 전이(성공 시):
    TRIAL → ACTIVE, ACTIVE → ACTIVE, PAST_DUE → ACTIVE
    """
    lock_key = f"lock:renew:{sub_id}"
    token = await acquire_lock(redis, lock_key)
    if token is None:
        stats["skipped"] += 1
        return
    try:
        async with session_factory() as db:
            # ── 1단계: FOR UPDATE 검증 + PENDING 선기록 (짧은 트랜잭션) ──
            sub = await db.get(Subscription, sub_id, with_for_update=True)
            if (sub is None
                    or sub.status not in DUE_STATUSES
                    or sub.next_billing_at is None or sub.next_billing_at > now):
                stats["skipped"] += 1
                return
            plan = await db.get(Plan, sub.plan_id)
            service = await db.get(Service, sub.service_id)
            # 빌링키는 cards 테이블에서 조회(Card Vault). 카드가 없으면 청구 불가.
            card = await get_card(db, service_id=sub.service_id,
                                  external_user_id=sub.external_user_id)
            order_id = _renewal_order_id(sub)

            payment = await db.scalar(select(Payment).where(Payment.order_id == order_id))
            if payment is not None and payment.status == PaymentStatus.DONE:
                # 방어적 복구: 같은 주문이 이미 DONE으로 기록돼 있으면(향후 웹훅/수동
                # 정정 등) 재결제 없이 기간만 전진
                _advance_period(sub, plan)
                await record_audit(db, actor_type="SYSTEM", action="subscription.renewed",
                                   target_type="subscription", target_id=str(sub.id),
                                   detail={"recovered": True})
                await db.commit()
                stats["renewed"] += 1
                return
            amount = plan_recurring_amount(plan)  # 상시 할인가(2회차~ 및 체험 전환)
            if payment is None:
                payment = Payment(
                    subscription_id=sub.id, order_id=order_id, amount=amount,
                    payment_type=(PaymentType.RENEWAL if sub.retry_count == 0
                                  else PaymentType.RETRY),
                    order_name=plan.name,  # 결제정보 표시용 상품명 = 요금제명
                    status=PaymentStatus.PENDING,
                    idempotency_key=f"renew-{order_id}", requested_at=now,
                    kind=PaymentKind.SUBSCRIPTION,
                    service_id=sub.service_id,
                    external_user_id=sub.external_user_id)
                db.add(payment)
            # 카드(빌링키)가 없으면 청구 자체가 불가능 → 토스 호출 없이 '결제 실패'로
            # 다룬다. 새 상태를 만들지 않고 기존 실패 처리 경로(_handle_charge_failure:
            # PAST_DUE 재시도 / SUSPENDED 정지)를 그대로 재사용한다(이중결제 위험 없음).
            # 카드가 없거나(미등록/삭제) 비활성이면 청구 불가 → 토스 호출 없이 '결제 실패'로
            # 다룬다. 비활성 카드는 NO_BILLING_KEY 대신 CARD_INACTIVE 코드로 구분 기록하되,
            # 후속 처리(PAST_DUE 재시도 / SUSPENDED 정지)는 동일 경로를 재사용한다.
            if card is None or sub.card_id is None or not card.is_active:
                # 비활성 카드와 미등록 카드를 합성 TossError 코드로 구분(감사·메시지용)
                exc = (TossError("CARD_INACTIVE", "비활성화된 카드입니다")
                       if card is not None and not card.is_active
                       else TossError("NO_BILLING_KEY", "등록된 카드가 없습니다"))
                # PENDING 결제 행을 먼저 내구성 있게 남긴 뒤(실제 실패 경로와 동일),
                # 합성 TossError로 실패 처리에 위임한다.
                await db.commit()
                await db.refresh(payment, with_for_update=True)
                await db.refresh(sub, with_for_update=True)
                await _handle_charge_failure(
                    db, toss, email_sender, sub, service, payment, billing_key="",
                    exc=exc, now=now, cfg=cfg, stats=stats, notifier=notifier)
                return
            # 빌링키·customerKey는 cards 테이블에서 복호화해 사용(Card Vault)
            billing_key = cipher.decrypt(card.billing_key_encrypted)
            idempotency_key = payment.idempotency_key
            customer_key = card.customer_key
            # 외부 호출 전 commit — PENDING 내구성 확보 + FOR UPDATE 행 잠금과
            # 커넥션을 토스 응답(최대 65초)까지 쥐지 않도록 반납(감사 Phase 1 — 성능 H1).
            await db.commit()

            # ── 2단계: 외부(토스) 호출 — DB 트랜잭션/커넥션 비점유 ──
            result = None            # ChargeResult — 승인 확정 시 설정
            failure = None           # TossError — 확정 실패 시 설정
            recovered_via = None     # 멱등 재생 외 경로로 성공을 복구한 경우 표시
            try:
                result = await resolve_charge(
                    toss, billing_key=billing_key, customer_key=customer_key,
                    amount=amount, order_id=order_id, order_name=plan.name,
                    idempotency_key=idempotency_key)
            except TossTimeoutError:
                # 결과 불명 — 절대 '실패 확정' 처리하지 않는다. 실패로 처리하면
                # retry_count 증가 → 다음 시도가 다른 order_id/멱등키로 나가
                # 원결제가 실제 승인됐을 경우 이중결제가 된다.
                # payment는 PENDING, sub은 불변(여전히 due) → 다음 배치에서
                # 같은 order_id/멱등키로 재시도해 토스 멱등 재생으로 수렴.
                await record_audit(db, actor_type="SYSTEM",
                                   action="subscription.renewal_unresolved",
                                   target_type="subscription", target_id=str(sub_id),
                                   detail={"order_id": order_id})
                await db.commit()
                stats["unresolved"] += 1
                return
            except TossError as exc:
                if exc.code == "ALREADY_PROCESSED_PAYMENT":
                    # 멱등 재생이 안 된 비정상 케이스 — 재조회로 실제 결과 확인
                    try:
                        found = await toss.get_payment_by_order_id(order_id)
                    except TossError:
                        found = None
                    if found is not None and found.status == "DONE":
                        result = found
                        recovered_via = "already_processed"
                if result is None:
                    failure = exc

            # ── 3단계: FOR UPDATE 재취득 + 재검증 후 확정 (새 트랜잭션) ──
            # 외부 호출 동안 행 잠금이 풀려 있었으므로, 다른 경로(웹훅·정산·취소)가
            # 상태를 바꿨을 수 있다 — 반드시 재취득 후 재검증한다.
            sub = await db.get(Subscription, sub_id, with_for_update=True)
            await db.refresh(payment, with_for_update=True)
            if payment.status != PaymentStatus.PENDING:
                # 외부 호출 사이에 다른 경로(웹훅/정산 스윕)가 이미 확정 — 중복 적용 금지
                await db.rollback()
                stats["skipped"] += 1
                return
            still_due = sub is not None and sub.status in DUE_STATUSES

            if failure is not None:
                if still_due:
                    await _handle_charge_failure(db, toss, email_sender, sub, service,
                                                 payment, billing_key, failure, now=now,
                                                 cfg=cfg, stats=stats, notifier=notifier)
                else:
                    # 호출 사이에 구독이 갱신 풀을 벗어남(취소/만료 등) —
                    # 구독 상태는 건드리지 않고 결제만 실패 확정한다.
                    payment.status = PaymentStatus.FAILED
                    payment.failure_code = failure.code
                    payment.failure_message = failure.message
                    await record_audit(db, actor_type="SYSTEM",
                                       action="subscription.payment_failed",
                                       target_type="subscription", target_id=str(sub_id),
                                       detail={"code": failure.code,
                                               "sub_left_due_pool": True})
                    await db.commit()
                    stats["failed"] += 1
                return

            payment.status = PaymentStatus.DONE
            payment.toss_payment_key = result.payment_key
            payment.approved_at = utcnow()
            payment.raw_response = result.raw
            renew_pre_status = sub.status if sub else ""   # 알림 PRE_STATUS(전진 전 상태)
            if still_due:
                _advance_period(sub, plan)
                detail = ({"recovered_via": recovered_via} if recovered_via
                          else {"order_id": order_id, "amount": amount})
                await record_audit(db, actor_type="SYSTEM", action="subscription.renewed",
                                   target_type="subscription", target_id=str(sub_id),
                                   detail=detail)
            else:
                # 결제는 승인됐지만 구독이 호출 사이에 갱신 풀을 벗어남(취소 등).
                # 기간을 전진시키지 않고 운영자 환불 검토 표시만 남긴다 —
                # reconciliation의 orphaned 결제 처리와 동일한 정책.
                await record_audit(db, actor_type="SYSTEM", action="subscription.renewed",
                                   target_type="subscription", target_id=str(sub_id),
                                   detail={"order_id": order_id, "amount": amount,
                                           "requires_review": True,
                                           "sub_status": (sub.status if sub else None)})
            await db.commit()
            # 서비스 알림 — 구독 자동결제 발생. best-effort(실패해도 갱신은 확정됨).
            if still_due and sub is not None:
                await _notify_sub(db, notifier, sub, event=EVENT_SUBSCRIPTION_RENEWED,
                                  pre_status=renew_pre_status, status=sub.status,
                                  order_id=order_id, desc=f"자동결제 {amount:,}원")
            stats["renewed"] += 1
    finally:
        await release_lock(redis, lock_key, token)


async def _handle_charge_failure(db, toss, email_sender, sub: Subscription,
                                 service: Service, payment: Payment, billing_key: str,
                                 exc: TossError, *, now: datetime, cfg: _Cfg,
                                 stats: dict, notifier=None) -> None:
    """갱신 결제 실패 처리 — retry_count에 따라 PAST_DUE 재시도 또는 SUSPENDED 정지.

    상태 전이:
    - retry_count < retry_limit: PAST_DUE (재시도 예약; next_billing_at = now + interval)
    - retry_count >= retry_limit: SUSPENDED (접근 차단; next_billing_at = None, 자동결제 중지)
      → suspended_at 기록, 이메일 발송. suspended_grace 경과 시 _expire_suspended가 EXPIRED 처리.

    SUSPENDED 시 빌링키를 삭제하지 않는 이유: 수동 결제(manual_charge_subscription)를 위해
    결제수단을 보존해야 한다.
    billing_key 파라미터를 받지만 현재 사용하지 않음 — 향후 SUSPENDED 즉시 삭제 정책 변경 대비.
    """
    payment.status = PaymentStatus.FAILED
    payment.failure_code = exc.code
    payment.failure_message = exc.message
    fail_pre_status = sub.status                       # 알림 PRE_STATUS(전이 전 상태)

    if sub.retry_count >= cfg.retry_limit:
        # 최종 실패 → SUSPENDED(강제 정지, 접근 차단). 빌링키는 수동 결제를 위해
        # 보존하고, 자동 결제는 중지(next_billing_at=None). 대기 일수 초과 시 EXPIRED.
        transition(sub, SubscriptionStatus.SUSPENDED, now=now)  # suspended_at 기록 포함
        await record_audit(db, actor_type="SYSTEM", action="subscription.suspended",
                           target_type="subscription", target_id=str(sub.id),
                           detail={"reason": "retries_exhausted", "code": exc.code,
                                   "grace_days": cfg.suspended_grace.days})
        await db.commit()
        await email_sender.send(
            service.manager_email,
            f"[결제시스템] 구독 정지 안내 — {service.name}",
            f"사용자 {sub.external_user_id}의 구독이 결제 재시도 {cfg.retry_limit}회 "
            f"실패로 정지(접근 차단)되었습니다.\n사유: [{exc.code}] {exc.message}\n"
            f"{cfg.suspended_grace.days}일 내 수동 결제가 없으면 만료됩니다 — "
            f"카드 변경 또는 즉시 결제를 안내해주세요.")
        # 서비스 알림 — 구독 정지(상태 변화). best-effort.
        await _notify_sub(db, notifier, sub, event=EVENT_SUBSCRIPTION_STATUS,
                          pre_status=fail_pre_status, status=SubscriptionStatus.SUSPENDED,
                          desc=f"재시도 소진 정지([{exc.code}] {exc.message})")
        stats["suspended"] += 1
    else:
        sub.retry_count += 1
        transition(sub, SubscriptionStatus.PAST_DUE)
        sub.next_billing_at = now + cfg.retry_interval  # 재시도 예약(정책은 호출측 소관)
        await record_audit(db, actor_type="SYSTEM", action="subscription.payment_failed",
                           target_type="subscription", target_id=str(sub.id),
                           detail={"code": exc.code, "retry_count": sub.retry_count})
        await db.commit()
        await email_sender.send(
            service.manager_email,
            f"[결제시스템] 결제 실패 안내 — {service.name}",
            f"사용자 {sub.external_user_id}의 갱신 결제가 실패했습니다 "
            f"(재시도 {sub.retry_count}/{cfg.retry_limit}).\n사유: [{exc.code}] {exc.message}")
        # 서비스 알림 — 결제 실패로 미수(PAST_DUE) 전환(상태 변화). best-effort.
        await _notify_sub(db, notifier, sub, event=EVENT_SUBSCRIPTION_STATUS,
                          pre_status=fail_pre_status, status=SubscriptionStatus.PAST_DUE,
                          desc=f"갱신 결제 실패 재시도 {sub.retry_count}/{cfg.retry_limit}")
        stats["failed"] += 1


