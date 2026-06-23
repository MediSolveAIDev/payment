"""PENDING 결제 정합성 — 유예 지난 PENDING을 토스 재조회로 DONE/FAILED 확정.

구독 결제(FIRST/RENEWAL/RETRY)와 단건 결제(subscription_id NULL) 모두 처리.
"""
import logging
import uuid
from datetime import datetime

from sqlalchemy import select

from app.core.clock import utcnow
from app.models import (
    Payment,
    PaymentStatus,
    PaymentType,
    Service,
    Subscription,
    SubscriptionStatus,
)
from app.services.audit import record_audit
from app.services.locks import (
    PENDING_RECONCILE_GRACE,
    DUE_STATUSES,
    acquire_lock,
    release_lock,
)
from app.services.transitions import transition
from app.toss.errors import TossError
from app.toss.provider import TossClientProvider  # 서비스별 토스 클라이언트 해석기 (Task 6)
from app.core.errors import TossKeyNotConfiguredError  # 키 미설정 예외 (Task 6)

logger = logging.getLogger("payment.reconciliation")


async def reconcile_pending(session_factory, redis, toss_provider, cipher, email_sender,
                             *, now: datetime, stats: dict) -> None:
    """결과불명으로 남은 PENDING 결제 정산 스윕(전 타입).

    유예(10분) 경과 후 토스 조회로 확정: DONE이면 결제 확정, 기록 없음이면
    FAILED 확정 + (FIRST + ACTIVE 구독이면) 만료 처리. 비DONE 진행 중 상태는 보류.
    단, 갱신 풀(ACTIVE/PAST_DUE)에 있는 구독의 RENEWAL/RETRY는 건드리지 않는다 —
    _renew_one이 같은 order_id/멱등키로 자체 수렴 처리한다.

    흐름:
    1. 별도 세션에서 Payment+Subscription을 LEFT OUTER JOIN으로 일괄 조회
       (단건 결제는 subscription_id=NULL이므로 OUTER JOIN 필요)
    2. 갱신 풀(RENEWAL/RETRY, 구독 상태 TRIAL/ACTIVE/PAST_DUE) 필터링 → skip
       FIRST 타입은 항상 이 함수에서 처리(갱신 배치가 FIRST를 재시도하지 않으므로)
    3. 나머지 건을 _reconcile_one_payment에 위임 (락 + 상세 처리)
    4. 한 항목 예외는 stats["errors"] 증가 후 계속 — 배치 전체를 중단시키지 않음

    toss_provider: 서비스별 토스 클라이언트 해석기 (Task 6). 각 결제 건에서
    서비스를 로드한 뒤 for_service()로 해석한다.
    """
    async with session_factory() as db:
        stuck = (await db.execute(
            select(Payment, Subscription)
            .outerjoin(Subscription, Payment.subscription_id == Subscription.id)
            .where(Payment.status == PaymentStatus.PENDING,
                   Payment.requested_at <= now - PENDING_RECONCILE_GRACE))).all()
    for stuck_payment, stuck_sub in stuck:
        if (stuck_payment.payment_type != PaymentType.FIRST
                and stuck_sub is not None and stuck_sub.status in DUE_STATUSES):
            continue  # _renew_one 수렴 경로가 처리(TRIAL 만료 결제 포함)
        try:
            # toss_provider를 그대로 전달 — 건별 서비스 로드 후 for_service() 해석 (Task 6)
            await _reconcile_one_payment(session_factory, redis, toss_provider, cipher,
                                         email_sender, stuck_payment.id, stats=stats)
        except Exception:  # noqa: BLE001 — 한 항목 실패가 배치 전체를 죽이면 안 됨
            logger.exception("정산 처리 실패: payment=%s", stuck_payment.id)
            stats["errors"] += 1


async def _reconcile_one_payment(session_factory, redis, toss_provider, cipher, email_sender,
                                 payment_id: uuid.UUID, *, stats: dict) -> None:
    """PENDING 결제 1건을 토스 재조회로 확정.

    토스 조회(최대 65초) 동안 FOR UPDATE 행 잠금·풀 커넥션을 쥐지 않도록
    조회 전 트랜잭션을 닫는다(감사 Phase 1 — 성능 H1). 동시성은
    ① 결제별 Redis 락(lock:reconcile), ② 조회 후 FOR UPDATE 재취득 +
    PENDING 재검증이 방어한다.

    흐름:
    1. lock:reconcile:{payment_id} 락 획득 — 실패 시 skipped
    2. [읽기 트랜잭션] Payment 조회 + PENDING 검증, order_id 추출 후 트랜잭션 종료
       구독 상태 검증 — 갱신 풀(TRIAL/ACTIVE/PAST_DUE)의 RENEWAL/RETRY는
       _renew_one에 맡기고 조기 반환(FIRST는 항상 여기서 처리)
    3. [외부 호출 — DB 비점유] toss.get_payment_by_order_id(order_id)
       서비스를 로드한 뒤 toss_provider.for_service(service)로 클라이언트 해석 (Task 6).
       TossKeyNotConfiguredError 발생 시 → 조회 불가, 다음 주기에 재시도(결과 불명 유지).
       - TossError → 조회 실패, 다음 주기에 재시도(결과 불명 유지)
    4. [확정 트랜잭션] with_for_update로 Payment 재취득 + PENDING 재검증
       (외부 호출 사이에 다른 경로가 확정했을 수 있음) 후:
       - found.status == "DONE" → 결제 확정 처리
       - found is None → 유예 후에도 미체결, FAILED 확정
       - 그 외(비DONE 진행 중) → 건드리지 않음, 다음 주기 재확인
    5. DONE 확정 후 orphaned 여부 판정:
       RENEWAL/RETRY인데 구독이 이미 CANCELED/EXPIRED이면 기간을 제공하지 못한 결제 →
       담당자 이메일로 환불 검토 요청
    6. NOT_FOUND 확정 + FIRST + 구독 ACTIVE이면 → 구독 EXPIRED (빌링키는 cards 테이블에서 관리하므로 삭제하지 않음)

    단건 결제(subscription_id=NULL)는 sub=None으로 처리되어 구독 관련 분기를 건너뛴다.
    락 키가 lock:renew와 다른 네임스페이스(lock:reconcile)를 사용하므로
    _renew_one과 같은 구독을 동시에 잠글 수 없다 — 구독 행 락(with_for_update)으로 보완.
    """
    lock_key = f"lock:reconcile:{payment_id}"
    token = await acquire_lock(redis, lock_key)
    if token is None:
        stats["skipped"] += 1
        return
    try:
        async with session_factory() as db:
            # ── 1단계: 읽기 검증 (짧은 트랜잭션 — 외부 호출 전에 닫는다) ──
            payment = await db.get(Payment, payment_id)
            if payment is None or payment.status != PaymentStatus.PENDING:
                return
            sub = (await db.get(Subscription, payment.subscription_id)
                   if payment.subscription_id else None)
            if (payment.payment_type != PaymentType.FIRST and sub is not None
                    and sub.status in DUE_STATUSES):
                return  # 락 획득 사이에 갱신 풀로 복귀 — _renew_one에 맡김
            # 토스 조회는 전역 고유 toss_order_id로 — order_id는 서비스 내 고유라
            # 토스 측 식별자가 아니다(감사 Phase 2 — 보안 M-1)
            order_id = payment.toss_order_id
            # rollback 이후 ORM 객체가 detached/expired되어 lazy-load가 불가하므로
            # service_id를 미리 로컬 변수에 저장해 둔다 (Task 6).
            payment_service_id = payment.service_id
            # 읽기 트랜잭션 종료 — 토스 조회(최대 65초) 동안 커넥션을 풀에 반납
            # (감사 Phase 1 — 성능 H1)
            await db.rollback()

            # ── 2단계: 외부(토스) 조회 — DB 트랜잭션/커넥션 비점유 ──
            # 서비스별 토스 클라이언트 해석 — 토스 호출 직전에 for_service()로 해석 (Task 6).
            # rollback 후라 별도 세션에서 서비스를 조회하고, for_service()로 클라이언트 해석.
            # 키 미설정(TossKeyNotConfiguredError)이면 조회 불가 → 다음 주기에 재시도.
            if payment_service_id is not None:
                async with session_factory() as svc_db:
                    _svc = await svc_db.get(Service, payment_service_id)
                try:
                    toss = toss_provider.for_service(_svc)
                except TossKeyNotConfiguredError:
                    return  # 키 미설정 — 다음 주기에 재시도(결과 불명 유지)
            else:
                # 단건 결제(service_id=None)는 전역 provider override 또는 에러
                try:
                    toss = toss_provider.for_service(None)
                except (TossKeyNotConfiguredError, Exception):
                    return  # 해석 불가 — 다음 주기에 재시도
            try:
                found = await toss.get_payment_by_order_id(order_id)
            except TossError:
                return  # 조회 실패 — 다음 주기에 재시도

            # ── 3단계: FOR UPDATE 재취득 + 재검증 후 확정 (새 트랜잭션) ──
            payment = await db.get(Payment, payment_id, with_for_update=True)
            if payment is None or payment.status != PaymentStatus.PENDING:
                await db.rollback()
                return  # 외부 호출 사이에 다른 경로가 이미 확정 — 중복 적용 금지
            sub = (await db.get(Subscription, payment.subscription_id)
                   if payment.subscription_id else None)
            if found is not None and found.status == "DONE":
                payment.status = PaymentStatus.DONE
                payment.toss_payment_key = found.payment_key
                payment.approved_at = utcnow()
                payment.raw_response = found.raw
                orphaned = (payment.payment_type != PaymentType.FIRST
                            and sub is not None
                            and sub.status in (SubscriptionStatus.CANCELED,
                                               SubscriptionStatus.EXPIRED))
                await record_audit(
                    db, actor_type="SYSTEM", action="payment.reconciled_done",
                    target_type="payment", target_id=str(payment.id),
                    detail=({"requires_review": True, "sub_status": sub.status}
                            if orphaned else None))
                await db.commit()
                if orphaned:
                    # 기간을 제공하지 못한 돈 — 환불 검토를 위해 담당자 호출
                    service = await db.get(Service, sub.service_id)
                    await email_sender.send(
                        service.manager_email,
                        "[결제시스템] 수동 확인 필요 — 취소된 구독의 갱신 결제 확정",
                        f"구독이 이미 {sub.status} 상태인데 갱신 결제가 토스에서 "
                        f"승인 확정되었습니다. 구독 기간이 제공되지 않은 결제이므로 "
                        f"환불 여부를 검토해주세요.\n"
                        f"주문번호: {payment.order_id}\n"
                        f"사용자: {sub.external_user_id}\n"
                        f"금액: {payment.amount}")
                stats["reconciled"] += 1
            elif found is None:
                # 유예 후에도 토스에 기록 없음 — 결제 미체결 확정
                payment.status = PaymentStatus.FAILED
                payment.failure_code = "RECONCILE_NOT_FOUND"
                payment.failure_message = "유예 기간 내 토스에서 결제를 확인하지 못했습니다"
                if (payment.payment_type == PaymentType.FIRST and sub is not None
                        and sub.status == SubscriptionStatus.ACTIVE):
                    # 카드 보관함(Card Vault) 도입 이후 빌링키는 cards 테이블이 소유한다.
                    # 카드는 영속적 자원이며 삭제는 delete_card가 전담하므로,
                    # 구독 만료 시 빌링키를 삭제하지 않는다(카드는 그대로 보존).
                    transition(sub, SubscriptionStatus.EXPIRED)  # 종단 — next_billing=None 포함
                    await record_audit(
                        db, actor_type="SYSTEM", action="subscription.expired",
                        target_type="subscription", target_id=str(sub.id),
                        detail={"reason": "first_payment_reconcile_not_found"})
                    stats["expired"] += 1
                await record_audit(db, actor_type="SYSTEM",
                                   action="payment.reconciled_failed",
                                   target_type="payment", target_id=str(payment.id))
                await db.commit()
                stats["reconciled"] += 1
            # 비DONE 상태(승인 진행 중 등)는 건드리지 않음 — 다음 주기 재확인
    finally:
        await release_lock(redis, lock_key, token)
