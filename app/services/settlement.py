"""정산 집계 — 기간 내 승인(DONE)·취소(CANCELED) 결제(approved_at 기준)를 서비스별로 합산.

취소된 단건 결제도 집계에 포함한다(요청: 취소내역 정산 반영):
  - 총매출(amount) = 승인된 결제 원금 합(DONE + CANCELED) — 일단 청구된 금액.
  - 환불(refund_amount) = 취소로 고객에게 돌려준 금액 합(canceled_amount).
  - 순매출(net_amount) = 총매출 − 환불 = 서비스가 실제로 보유하는 금액(취소 수수료 포함).
취소는 단건(ONE_OFF) 결제에만 발생하므로 환불은 일반결제 쪽에서만 잡힌다.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Payment, PaymentKind, PaymentStatus, Plan, Service, Subscription

# 정산 집계 대상 상태 — 승인 완료(DONE) + 취소(CANCELED). 실패·대기는 제외.
_SETTLED_STATUSES = (PaymentStatus.DONE, PaymentStatus.CANCELED)


@dataclass
class SettlementRow:
    service_id: uuid.UUID
    service_name: str
    count: int          # 정산 대상 결제 건수(DONE + CANCELED)
    amount: int         # 총매출(KRW) — 승인 원금 합(DONE + CANCELED)
    sub_amount: int     # 구독 결제 합계(총매출 기준)
    one_off_amount: int  # 일반(단건) 결제 합계(총매출 기준)
    sub_count: int = 0       # 구독 결제 건수
    one_off_count: int = 0   # 일반(단건) 결제 건수
    refund_amount: int = 0   # 환불 합계(취소된 결제의 canceled_amount 합)

    @property
    def net_amount(self) -> int:
        """순매출 = 총매출 − 환불(서비스가 실제 보유하는 금액)."""
        return self.amount - self.refund_amount


async def settlement_summary(db: AsyncSession, scope: list[uuid.UUID] | None,
                             start: datetime | None, end: datetime | None,
                             plan_name: str | None = None,
                             ) -> tuple[int, int, list[SettlementRow]]:
    """(총 건수, 총매출, 서비스별 집계 — 총매출 내림차순). 반개구간 [start, end).

    총매출은 DONE+CANCELED 원금 합이며, 각 행의 net_amount로 순매출(총매출−환불)을 얻는다.
    """
    amount_sum = func.coalesce(func.sum(Payment.amount), 0)
    # 환불 합계 — 취소된 결제의 canceled_amount(없으면 0)
    refund_sum = func.coalesce(func.sum(func.coalesce(Payment.canceled_amount, 0)), 0)
    sub_sum = func.coalesce(func.sum(case(
        (Payment.kind == PaymentKind.SUBSCRIPTION, Payment.amount), else_=0)), 0)
    oo_sum = func.coalesce(func.sum(case(
        (Payment.kind == PaymentKind.ONE_OFF, Payment.amount), else_=0)), 0)
    sub_cnt = func.coalesce(func.sum(case(
        (Payment.kind == PaymentKind.SUBSCRIPTION, 1), else_=0)), 0)
    oo_cnt = func.coalesce(func.sum(case(
        (Payment.kind == PaymentKind.ONE_OFF, 1), else_=0)), 0)
    q = (select(Service.id, Service.name, func.count(Payment.id),
                amount_sum, sub_sum, oo_sum, sub_cnt, oo_cnt, refund_sum)
         .select_from(Payment)
         .join(Service, Payment.service_id == Service.id)
         .where(Payment.status.in_(_SETTLED_STATUSES))
         .group_by(Service.id, Service.name)
         .order_by(amount_sum.desc(), Service.name))
    if plan_name:
        q = (q.join(Subscription, Payment.subscription_id == Subscription.id)
              .join(Plan, Subscription.plan_id == Plan.id)
              .where(Plan.name == plan_name))
    if start:
        q = q.where(Payment.approved_at >= start)
    if end:
        q = q.where(Payment.approved_at < end)
    if scope is not None:
        q = q.where(Payment.service_id.in_(scope))
    rows = [SettlementRow(sid, name, int(c), int(a), int(sa), int(oo), int(sc), int(oc),
                          refund_amount=int(rf))
            for sid, name, c, a, sa, oo, sc, oc, rf in (await db.execute(q)).all()]
    return (sum(r.count for r in rows), sum(r.amount for r in rows), rows)
