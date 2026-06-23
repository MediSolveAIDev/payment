"""카드(결제수단) 서비스 통합 테스트 — register_or_replace_card / get_card / delete_card.

TDD 순서:
1. 이 파일을 먼저 작성(FAIL 확인)
2. app/services/cards.py 구현 후 PASS 확인
"""
import pytest

from app.core.errors import ConflictError, InputValidationError, NotFoundError
from app.services.cards import delete_card, get_card, register_or_replace_card
from app.toss.errors import TossError
from app.toss.fake import FakeTossClient
from tests.factories import create_plan, create_service, create_subscription


@pytest.fixture
def fake():
    """로컬 FakeTossClient 픽스처 — 각 테스트마다 초기화된 독립 인스턴스."""
    return FakeTossClient()


async def test_register_card_stores_encrypted_billing_key(db, cipher, fake):
    """카드를 등록하면 빌링키가 암호화 저장되고 평문과 달라야 한다."""
    svc, _, _ = await create_service(db, cipher)
    card = await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id="u1@e.com",
        customer_key="cust-1", auth_key="authkey-1")

    # 외부 사용자 ID가 정확히 저장됐는지 확인
    assert card.external_user_id == "u1@e.com"
    # 빌링키가 암호화되어 저장됐는지 확인 — 평문과 달라야 함
    assert card.billing_key_encrypted and card.billing_key_encrypted != "authkey-1"
    # SHA-256 해시도 생성됐는지 확인
    assert card.billing_key_hash


async def test_replace_card_reuses_same_row(db, cipher, fake):
    """같은 (service, external_user_id)로 재등록 시 새 행 대신 기존 행을 교체한다."""
    svc, _, _ = await create_service(db, cipher)
    # 최초 등록
    card = await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id="u1@e.com",
        customer_key="cust-1", auth_key="authkey-1")

    # 재등록 — 같은 (service, external_user_id)
    card2 = await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id="u1@e.com",
        customer_key="cust-1", auth_key="authkey-2")

    # 행이 교체됐으므로 id가 같아야 함(새 행 아님)
    assert card2.id == card.id


async def test_replace_card_updates_billing_key(db, cipher, fake):
    """카드 교체 시 빌링키가 새 값으로 갱신된다."""
    svc, _, _ = await create_service(db, cipher)
    card = await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id="u1@e.com",
        customer_key="cust-1", auth_key="authkey-1")
    old_hash = card.billing_key_hash

    card2 = await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id="u1@e.com",
        customer_key="cust-1", auth_key="authkey-2")

    # 빌링키가 새 값으로 바뀌었는지 확인(해시 비교)
    assert card2.billing_key_hash != old_hash


async def test_get_card_returns_none_when_not_found(db, cipher):
    """등록되지 않은 카드는 None을 반환한다."""
    svc, _, _ = await create_service(db, cipher)
    result = await get_card(db, service_id=svc.id, external_user_id="ghost-user@e.com")
    assert result is None


async def test_get_card_returns_registered_card(db, cipher, fake):
    """등록된 카드는 get_card로 조회할 수 있다."""
    svc, _, _ = await create_service(db, cipher)
    card = await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id="u2@e.com",
        customer_key="cust-2", auth_key="authkey-1")

    fetched = await get_card(db, service_id=svc.id, external_user_id="u2@e.com")
    # 조회된 카드가 등록된 카드와 같아야 함
    assert fetched is not None
    assert fetched.id == card.id


async def test_register_card_invalid_customer_key_raises(db, cipher, fake):
    """customer_key 형식이 올바르지 않으면 InputValidationError가 발생한다."""
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(InputValidationError):
        await register_or_replace_card(
            db, fake, cipher, service=svc, external_user_id="u1@e.com",
            customer_key="!!invalid!!", auth_key="authkey-1")


async def test_register_card_empty_external_user_id_raises(db, cipher, fake):
    """external_user_id가 빈 문자열이면 InputValidationError가 발생한다."""
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(InputValidationError):
        await register_or_replace_card(
            db, fake, cipher, service=svc, external_user_id="",
            customer_key="cust-1", auth_key="authkey-1")


async def test_different_users_get_separate_cards(db, cipher, fake):
    """같은 서비스의 다른 사용자는 각자 카드를 가질 수 있다."""
    svc, _, _ = await create_service(db, cipher)
    card_u1 = await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id="user-a@e.com",
        customer_key="cust-A", auth_key="authkey-A")
    card_u2 = await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id="user-b@e.com",
        customer_key="cust-B", auth_key="authkey-B")

    # 서로 다른 행으로 저장됐는지 확인
    assert card_u1.id != card_u2.id
    assert card_u1.external_user_id == "user-a@e.com"
    assert card_u2.external_user_id == "user-b@e.com"


async def test_replace_deletes_old_billing_key_best_effort(db, cipher, fake):
    """카드 교체 시 기존 빌링키가 best-effort로 삭제된다(실패해도 교체는 유효)."""
    svc, _, _ = await create_service(db, cipher)
    # 최초 등록 — fake.issued[0].billing_key 가 첫 번째 빌링키
    await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id="u3@e.com",
        customer_key="cust-3", auth_key="authkey-1")
    first_bk = fake.issued[0]["billing_key"]

    # 교체 등록
    card2 = await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id="u3@e.com",
        customer_key="cust-3", auth_key="authkey-2")

    # 이전 빌링키가 삭제 요청됐는지 확인(best-effort)
    assert first_bk in fake.deleted, "교체 시 기존 빌링키 삭제 호출되어야 함"
    # 교체 자체는 성공했는지 확인
    assert card2.id is not None


async def test_billing_key_issue_failure_no_card_created(db, cipher, fake):
    """빌링키 발급 실패 시 TossError가 전파되고 카드 행이 생성되지 않는다."""
    svc, _, _ = await create_service(db, cipher)
    # 빌링키 발급이 항상 실패하도록 주입 — test_subscription_create.py와 동일한 패턴
    fake.fail_issue_with = TossError("INVALID_AUTH_KEY", "잘못된 인증키", 400)

    with pytest.raises(TossError):
        await register_or_replace_card(
            db, fake, cipher, service=svc, external_user_id="u-fail@e.com",
            customer_key="cust-fail", auth_key="bad-auth")

    # 빌링키 발급 전에 실패했으므로 카드 행이 생성되지 않아야 함
    assert await get_card(db, service_id=svc.id, external_user_id="u-fail@e.com") is None


# ── delete_card 테스트 (Task 5 TDD) ─────────────────────────────────────────


async def test_delete_card_blocked_when_active_subscription(db, cipher, fake):
    """활성 구독(ACTIVE)이 참조 중인 카드는 삭제할 수 없다 — ConflictError."""
    svc, _, _ = await create_service(db, cipher)
    # 카드 등록
    card = await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id="u-del-1@e.com",
        customer_key="cust-d1", auth_key="authkey-d1")
    # ACTIVE 구독 생성 — 등록된 카드 참조
    plan = await create_plan(db, svc)
    await create_subscription(
        db, cipher, svc, plan,
        external_user_id="u-del-1@e.com",
        status="ACTIVE",
        card_id=card.id,  # 삭제 대상 카드를 구독이 참조
    )

    # ACTIVE 구독이 카드를 사용 중이므로 삭제 시 ConflictError가 발생해야 함
    with pytest.raises(ConflictError):
        await delete_card(
            db, fake, cipher,
            service_id=svc.id,
            external_user_id="u-del-1@e.com",
        )


async def test_delete_card_allowed_when_canceled(db, cipher, fake):
    """CANCELED 구독만 있으면 카드 삭제가 허용되고 이후 get_card는 None을 반환한다."""
    svc, _, _ = await create_service(db, cipher)
    # 카드 등록
    card = await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id="u-del-2@e.com",
        customer_key="cust-d2", auth_key="authkey-d2")
    # CANCELED 구독 생성 — 차단 대상이 아닌 상태
    plan = await create_plan(db, svc)
    await create_subscription(
        db, cipher, svc, plan,
        external_user_id="u-del-2@e.com",
        status="CANCELED",
        card_id=card.id,
    )

    # CANCELED 구독은 차단 대상이 아니므로 삭제가 성공해야 함
    await delete_card(
        db, fake, cipher,
        service_id=svc.id,
        external_user_id="u-del-2@e.com",
    )
    # 삭제 후 카드 조회 결과가 None이어야 함
    assert await get_card(db, service_id=svc.id, external_user_id="u-del-2@e.com") is None


async def test_delete_card_not_found(db, cipher, fake):
    """등록된 카드가 없을 때 delete_card는 NotFoundError를 발생시킨다."""
    svc, _, _ = await create_service(db, cipher)

    # 카드를 등록하지 않은 상태에서 삭제 시 NotFoundError가 발생해야 함
    with pytest.raises(NotFoundError):
        await delete_card(
            db, fake, cipher,
            service_id=svc.id,
            external_user_id="ghost-user@e.com",
        )
