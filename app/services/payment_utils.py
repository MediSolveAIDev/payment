"""결제 실행 공통 유틸 — 구독 결제·갱신·단건 결제가 공유.

- resolve_charge: 토스 결제 실행 + 타임아웃 시 order_id 재조회로 결과 확정.
- safe_delete_billing_key: 빌링키 삭제(실패는 삼켜 고아 키만 남김).
- CUSTOMER_KEY_RE / PENDING_GRACE_MESSAGE: 입력 검증 정규식 / 결과 불명 안내 문구.
"""
import logging
import re

from app.core.security import sha256_hex
from app.toss.client import TossClient
from app.toss.errors import TossError, TossTimeoutError
from app.toss.types import ChargeResult

logger = logging.getLogger("payment.utils")

CUSTOMER_KEY_RE = re.compile(r"^[A-Za-z0-9\-_=.@]{2,300}$")

PENDING_GRACE_MESSAGE = (
    "결제 결과를 아직 확인하지 못했습니다. 이중 결제 방지를 위해 구독은 보류 "
    "상태로 유지됩니다 — 잠시 후 구독 상태를 조회해주세요.")


async def safe_delete_billing_key(toss: TossClient, billing_key: str) -> bool:
    """베스트 에포트 빌링키 삭제. 실패 시 False — 호출측은 암호문을 보존해
    운영자가 재시도할 수 있게 한다. 침묵 삭제 실패는 영구 고아 키를 만든다."""
    try:
        await toss.delete_billing_key(billing_key)
        return True
    except TossError as exc:
        if exc.http_status == 404:
            return True  # 이미 토스에서 삭제됨 — 성공으로 간주
        logger.warning("빌링키 삭제 실패(토스에 키 잔존 가능): hash=%s code=%s",
                       sha256_hex(billing_key)[:12], exc.code)
        return False


async def resolve_charge(toss: TossClient, *, billing_key: str, customer_key: str,
                         amount: int, order_id: str, order_name: str,
                         idempotency_key: str) -> ChargeResult:
    """결제 시도. 결과는 셋 중 하나로 수렴한다:
    - ChargeResult 반환: 승인 확정
    - TossError: 확정 실패 (카드 거절 등)
    - TossTimeoutError: 결과 불명 — 호출측은 절대 '실패 확정' 처리하면 안 됨
    """
    try:
        return await toss.charge(billing_key, customer_key, amount,
                                 order_id, order_name, idempotency_key)
    except TossTimeoutError as timeout_exc:
        try:
            found = await toss.get_payment_by_order_id(order_id)
        except TossError:
            found = None  # 재조회 자체가 실패 — 여전히 결과 불명
        if found is not None and found.status == "DONE":
            return found
        # 미발견/비DONE(승인 진행 중일 수 있음) — 결과 불명 유지
        raise timeout_exc
