"""카드(결제수단) 서비스 — 등록/교체·조회·삭제.

빌링키는 토스에서 발급해 AES-GCM으로 암호화 저장한다. (service, external_user_id)당
1건이며 재등록은 같은 행을 교체한다(옛 토스 빌링키는 best-effort 삭제).

흐름 — register_or_replace_card:
1. customer_key / external_user_id 입력 검증
2. 토스에서 빌링키 발급(issue_billing_key)
3. 기존 카드 조회 — 있으면 교체, 없으면 신규 삽입
4. 감사 로그(card.register / card.replace) 기록 + commit
5. 교체 시 기존 빌링키를 best-effort 삭제(실패해도 교체는 유효)

흐름 — delete_card:
1. get_card로 카드 조회 — 없으면 NotFoundError
2. 해당 카드를 참조하는 billing-active 구독 존재 여부 확인 — 있으면 ConflictError
3. 빌링키 복호화 → db.delete(card) → 감사 로그 → commit
4. 커밋 후 토스 빌링키 best-effort 삭제
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import AesGcmCipher
from app.core.errors import ConflictError, InputValidationError, NotFoundError
from app.core.security import sha256_hex
from app.models import Card, Service
from app.models.enums import SubscriptionStatus
from app.models.subscription import Subscription
from app.notifications.service_notify import (
    EVENT_CARD_ACTIVATED,
    EVENT_CARD_DEACTIVATED,
    EVENT_CARD_DELETED,
    EVENT_CARD_REGISTERED,
    EVENT_CARD_REPLACED,
)
from app.services.audit import record_audit
from app.services.payment_utils import CUSTOMER_KEY_RE, safe_delete_billing_key
from app.toss.client import TossClient

# 카드 삭제를 차단하는 구독 상태 집합 (spec §6.1).
# 이 상태에서는 구독이 빌링키를 실제로 사용 중이거나 곧 사용할 예정이므로
# 카드를 삭제하면 다음 자동결제 시 결제 불능이 된다.
CARD_DELETE_BLOCKING_STATUSES: frozenset[SubscriptionStatus] = frozenset({
    SubscriptionStatus.TRIAL,      # 체험 — 만료 시 첫 정기 결제 예정
    SubscriptionStatus.ACTIVE,     # 정상 이용 중
    SubscriptionStatus.PAST_DUE,   # 결제 실패/유예 — 재시도 예정
    SubscriptionStatus.SUSPENDED,  # 강제 정지 — 수동 결제 대기
    SubscriptionStatus.EXTENDED,   # 운영자 연장 — 새 만료일에 자동결제 갱신
})

logger = logging.getLogger("cards")


async def _notify_card(db: AsyncSession, notifier, card: Card, *, event: str,
                       service: Service | None = None, desc: str = "") -> None:
    """카드 관련 서비스 알림 발송(best-effort). 카드 화면 표시번호를 DESC에 포함한다.

    notifier가 없으면(테스트 직접 호출 등) no-op. service 미전달 시 card.service_id로 조회.
    email은 카드 사용자 식별자(external_user_id), STATUS는 활성/비활성 표시.
    """
    if notifier is None:
        return
    svc = service or await db.get(Service, card.service_id)
    if svc is None:
        return
    number = (card.card_info or {}).get("number") if card.card_info else None
    full_desc = f"{desc} {number}".strip() if number else desc
    await notifier.send(svc, event=event, email=card.external_user_id,
                        status=("ACTIVE" if card.is_active else "INACTIVE"),
                        desc=full_desc)


def _card_audit_detail(card: Card, **extra) -> dict:
    """카드 감사로그 detail 공통 빌더 — 사용자·서비스·마스킹 카드번호·발급사를 담는다.

    등록/교체/삭제/활성토글 등 모든 카드 이벤트가 동일한 상세 정보를 남기도록 한다.
    빌링키 암호문·해시는 감사로그에 넣지 않는다(민감/불필요). card_info가 없으면
    카드번호·발급사 키를 생략한다. service_id는 _events_tab의 서비스 스코프 필터에 쓰인다.
    """
    info = card.card_info or {}
    detail: dict = {"external_user_id": card.external_user_id,
                    "service_id": str(card.service_id)}
    if info.get("number"):
        detail["card_number"] = info["number"]   # 마스킹 번호(이미 화면 노출 수준)
    if info.get("issuerCode"):
        detail["issuer"] = info["issuerCode"]
    detail.update(extra)
    return detail


async def get_card(db: AsyncSession, *, service_id: uuid.UUID, external_user_id: str) -> Card | None:
    """(service_id, external_user_id)로 카드를 조회한다.

    등록된 카드가 없으면 None을 반환한다(예외 없음).

    Args:
        db: 현재 요청의 AsyncSession.
        service_id: 카드가 속한 서비스 UUID.
        external_user_id: 외부 서비스의 사용자 ID 문자열.
    """
    return await db.scalar(
        select(Card).where(
            Card.service_id == service_id,
            Card.external_user_id == external_user_id,
        )
    )


async def set_card_active(
    db: AsyncSession,
    *,
    card_id: uuid.UUID,
    is_active: bool,
    actor_user_id: uuid.UUID | None = None,
    notifier=None,
) -> Card:
    """카드의 활성/비활성 상태를 설정한다(어드민 토글).

    비활성(is_active=False) 카드는 이후 모든 결제(구독 자동연장·첫구독·재시도·일반결제)
    경로에서 차단된다. 상태 변경 시 감사로그(card.activate / card.deactivate)를 남긴다.
    이미 같은 상태이면 변경 없이 그대로 반환한다(감사로그도 남기지 않음 — 멱등).

    Args:
        db: 현재 요청의 AsyncSession.
        card_id: 상태를 바꿀 카드 UUID.
        is_active: 설정할 활성 여부(True=활성, False=비활성).
        actor_user_id: 토글을 수행한 관리자 사용자 UUID(감사로그 행위자).

    Returns:
        갱신된 Card 인스턴스.

    Raises:
        NotFoundError: 카드를 찾을 수 없을 때.
    """
    card = await db.get(Card, card_id)
    if card is None:
        raise NotFoundError("카드를 찾을 수 없습니다")
    # 멱등: 이미 원하는 상태면 아무 것도 하지 않는다(중복 감사로그 방지)
    if card.is_active == is_active:
        return card
    card.is_active = is_active
    await record_audit(
        db,
        actor_type="USER",
        actor_user_id=actor_user_id,
        action="card.activate" if is_active else "card.deactivate",
        target_type="card",
        target_id=str(card.id),
        detail=_card_audit_detail(card, is_active=is_active),
    )
    await db.commit()
    # 서비스 알림 — 관리자 카드 활성/비활성. best-effort.
    await _notify_card(db, notifier, card,
                       event=(EVENT_CARD_ACTIVATED if is_active else EVENT_CARD_DEACTIVATED),
                       desc=("카드 활성화" if is_active else "카드 비활성화"))
    return card


async def register_or_replace_card(
    db: AsyncSession,
    toss: TossClient,
    cipher: AesGcmCipher,
    *,
    service: Service,
    external_user_id: str,
    customer_key: str,
    auth_key: str,
    notifier=None,
) -> Card:
    """카드를 등록하거나 기존 카드를 교체한다.

    (service, external_user_id)당 1건을 유지한다. 이미 카드가 있으면
    기존 행을 업데이트(교체)하고 옛 빌링키를 best-effort 삭제한다.

    Args:
        db: 현재 요청의 AsyncSession.
        toss: 토스 클라이언트(실제 또는 Fake).
        cipher: AES-GCM 암호화 인스턴스.
        service: 카드가 속할 서비스 모델.
        external_user_id: 외부 서비스의 사용자 ID(최대 255자).
        customer_key: 토스 customerKey(2~300자 영숫자·특수문자).
        auth_key: 토스 authKey(빌링키 발급용 일회성 키).

    Returns:
        등록 또는 교체된 Card 인스턴스.

    Raises:
        InputValidationError: customer_key 형식 오류 또는 external_user_id 비어있거나 초과.
    """
    # customer_key 형식 검증 — payment_utils와 동일한 정규식 사용
    if not CUSTOMER_KEY_RE.fullmatch(customer_key or ""):
        raise InputValidationError("customer_key 형식이 올바르지 않습니다")
    # external_user_id 길이 검증
    if not external_user_id or len(external_user_id) > 255:
        raise InputValidationError("external_user_id가 올바르지 않습니다")

    # 토스에서 빌링키 발급(auth_key + customer_key → BillingKeyResult)
    bk = await toss.issue_billing_key(auth_key, customer_key)

    # 기존 카드 조회 — 교체(UPDATE) vs 신규 삽입(INSERT) 분기
    existing = await get_card(db, service_id=service.id, external_user_id=external_user_id)
    old_billing_key: str | None = None

    if existing is not None:
        # 교체: 기존 행 갱신 — 옛 빌링키는 커밋 후 best-effort 삭제
        old_billing_key = cipher.decrypt(existing.billing_key_encrypted)
        existing.customer_key = customer_key                            # customerKey 갱신
        existing.billing_key_encrypted = cipher.encrypt(bk.billing_key)  # 새 빌링키 암호화 저장
        existing.billing_key_hash = sha256_hex(bk.billing_key)          # 해시 갱신(중복탐지용)
        existing.card_info = bk.card                                    # 카드 표시 정보 갱신
        card, action = existing, "card.replace"
    else:
        # 신규 등록: Card 행 생성
        card = Card(
            service_id=service.id,
            external_user_id=external_user_id,
            customer_key=customer_key,
            billing_key_encrypted=cipher.encrypt(bk.billing_key),  # 빌링키 AES-GCM 암호화
            billing_key_hash=sha256_hex(bk.billing_key),           # SHA-256 해시(조회·중복 탐지용)
            card_info=bk.card,                                      # 토스 카드 표시 정보(마스킹 번호 등)
        )
        db.add(card)
        action = "card.register"
        # 동시성 경쟁 방어 — registry.py와 동일한 패턴:
        # SELECT 후 INSERT 사이에 동시 요청이 같은 (service_id, external_user_id)로
        # INSERT를 시도하면 uq_cards_service_user 유니크 제약이 위반된다.
        # DB 유니크 제약이 최종 심판이므로 여기서 IntegrityError를 잡아 처리한다.
        # 패자 요청이 발급한 토스 빌링키는 orphan이 되지 않도록 best-effort 삭제한다.
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            # 고아 빌링키가 토스에 남지 않도록 best-effort 삭제
            await safe_delete_billing_key(toss, bk.billing_key)
            raise ConflictError("이미 등록된 카드가 있습니다") from None

    # 감사 로그 — actor_type="SERVICE"로 기록(외부 API 호출 컨텍스트)
    # 신규 등록의 경우 위 try/except 블록에서 이미 flush 완료 — 교체는 여기서 flush
    # card.id가 확정된 뒤 감사 로그에 기록한다
    await db.flush()
    await record_audit(
        db,
        actor_type="SERVICE",
        actor_service_id=service.id,
        action=action,
        target_type="card",
        target_id=str(card.id),
        detail=_card_audit_detail(card),
    )
    await db.commit()

    # 교체 시 기존 빌링키 best-effort 삭제 — 실패해도 교체는 이미 커밋되어 유효
    if old_billing_key:
        deleted = await safe_delete_billing_key(toss, old_billing_key)
        if not deleted:
            logger.warning(
                "이전 빌링키 삭제 실패(토스에 키 잔존 가능): service_id=%s external_user_id=%s",
                service.id,
                external_user_id,
            )

    # 서비스 알림 — 카드 등록(신규) 또는 변경(교체). best-effort.
    await _notify_card(db, notifier, card, service=service,
                       event=(EVENT_CARD_REGISTERED if action == "card.register"
                              else EVENT_CARD_REPLACED),
                       desc=("카드 등록" if action == "card.register" else "카드 변경"))
    return card


async def delete_card(
    db: AsyncSession,
    toss: TossClient,
    cipher: AesGcmCipher,
    *,
    service_id: uuid.UUID,
    external_user_id: str,
    notifier=None,
) -> None:
    """등록된 카드를 삭제한다.

    삭제 전에 해당 카드를 참조하는 활성 구독이 있는지 확인한다.
    billing-active 상태(TRIAL, ACTIVE, PAST_DUE, SUSPENDED, EXTENDED)의 구독이
    이 카드를 사용 중이면 ConflictError를 발생시킨다.

    삭제 순서:
    1. 카드 조회 — 없으면 NotFoundError
    2. 활성 구독 존재 확인 — 있으면 ConflictError
    3. 빌링키 복호화 (커밋 전에 평문 확보)
    4. db.delete(card) + 감사 로그 기록 + commit
    5. 커밋 후 토스 빌링키 best-effort 삭제 (실패해도 카드 삭제는 유효)

    Args:
        db: 현재 요청의 AsyncSession.
        toss: 토스 클라이언트(실제 또는 Fake).
        cipher: AES-GCM 복호화 인스턴스.
        service_id: 카드가 속한 서비스 UUID.
        external_user_id: 외부 서비스의 사용자 ID 문자열.

    Raises:
        NotFoundError: 등록된 카드가 없을 때.
        ConflictError: billing-active 상태의 구독이 카드를 참조할 때.
    """
    # 1. 카드 조회 — 없으면 NotFoundError
    card = await get_card(db, service_id=service_id, external_user_id=external_user_id)
    if card is None:
        raise NotFoundError("등록된 카드가 없습니다")

    # 2. 활성 구독 차단 확인 — CARD_DELETE_BLOCKING_STATUSES 중 하나의 구독이
    #    이 카드를 참조하면 삭제를 거부한다
    blocking_sub = await db.scalar(
        select(Subscription).where(
            Subscription.card_id == card.id,
            Subscription.status.in_(CARD_DELETE_BLOCKING_STATUSES),
        )
    )
    if blocking_sub is not None:
        raise ConflictError("활성 구독이 사용 중인 카드는 삭제할 수 없습니다")

    # 3. 비-차단 구독(CANCELED/EXPIRED)의 card_id를 NULL로 초기화.
    #    subscriptions.card_id FK가 RESTRICT이므로 카드를 삭제하기 전에
    #    참조를 제거해야 DB 제약 위반 없이 DELETE가 실행된다.
    #    (billing-active 구독은 위 단계에서 이미 차단됐으므로 여기 도달한 구독은
    #     모두 CANCELED 또는 EXPIRED 상태이다)
    dangling_subs = (
        await db.scalars(
            select(Subscription).where(Subscription.card_id == card.id)
        )
    ).all()
    for sub in dangling_subs:
        sub.card_id = None  # FK 참조 해제 — CANCELED/EXPIRED 구독은 카드가 없어도 무방

    # 4. 커밋 전에 빌링키 평문 확보 (카드 삭제 후에는 암호문에 접근 불가)
    billing_key = cipher.decrypt(card.billing_key_encrypted)

    # 5. 카드 삭제 + 감사 로그 + commit
    await db.delete(card)
    await record_audit(
        db,
        actor_type="SERVICE",
        actor_service_id=service_id,
        action="card.delete",
        target_type="card",
        target_id=str(card.id),
        detail=_card_audit_detail(card),
    )
    await db.commit()

    # 서비스 알림 — 사용자 카드 삭제. best-effort(card 인스턴스의 캐시된 값 사용).
    await _notify_card(db, notifier, card, event=EVENT_CARD_DELETED, desc="카드 삭제")

    # 5. 커밋 후 토스 빌링키 best-effort 삭제 — 실패해도 카드 삭제는 이미 커밋 완료
    deleted = await safe_delete_billing_key(toss, billing_key)
    if not deleted:
        logger.warning(
            "카드 삭제 후 토스 빌링키 삭제 실패(토스에 키 잔존 가능): "
            "service_id=%s external_user_id=%s",
            service_id,
            external_user_id,
        )
