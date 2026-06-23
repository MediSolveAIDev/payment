"""감사 Phase 2(보안 보강) 회귀 테스트.

- M-2: 어드민 로그인 IP rate limit
- M-3: 보안 응답 헤더(X-Frame-Options 등)
- L-1: 무인증 서비스 목록 토글(public_service_list_enabled)
- L-3: 단건 결제 금액 상한
- L-5: 어드민 세션 절대 만료
"""
import pytest

from app.admin.routes.auth import LOGIN_RATE_LIMIT_PER_MINUTE
from app.core.errors import InputValidationError
from app.services import auth as auth_service
from app.services import payments as payment_service
from app.toss.fake import FakeTossClient
from tests.factories import create_service, create_user
from tests.helpers import admin_login


async def test_login_rate_limit_blocks_after_threshold(client, db):
    """[M-2] 같은 IP에서 분당 상한 초과 시 인증 로직 진입 전에 차단된다."""
    user, pw = await create_user(db)
    # 상한까지는 정상적으로 '인증 실패' 응답(존재하지 않는 비밀번호)
    for _ in range(LOGIN_RATE_LIMIT_PER_MINUTE):
        resp = await client.post("/admin/login",
                                 data={"email": user.email, "password": "wrong-pw-123"})
        assert resp.status_code == 200
    # 상한 초과 — rate limit 메시지로 차단(올바른 비밀번호여도 거부)
    resp = await client.post("/admin/login",
                             data={"email": user.email, "password": pw})
    assert resp.status_code == 200
    assert "시도가 너무 많습니다" in resp.text


async def test_security_headers_present(client):
    """[M-3] 모든 응답에 보안 헤더가 부착된다(클릭재킹·스니핑 방어)."""
    resp = await client.get("/admin/login")
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "same-origin"
    # dev 환경이므로 HSTS는 없어야 한다(prod 전용)
    assert "strict-transport-security" not in resp.headers


async def test_service_list_can_be_disabled(client, settings):
    """[L-1] public_service_list_enabled=False면 무인증 서비스 목록이 404."""
    assert (await client.get("/api/v1/services")).status_code == 200  # 기본 노출
    settings.public_service_list_enabled = False
    try:
        assert (await client.get("/api/v1/services")).status_code == 404
    finally:
        settings.public_service_list_enabled = True  # 다른 테스트 영향 방지


async def test_one_off_amount_over_cap_rejected(db, cipher):
    """[L-3] 단건 결제 금액 상한(ONE_OFF_MAX_AMOUNT) 초과는 토스 호출 전에 거부.

    카드 보관함 전환 이후 auth_key/customer_key 파라미터 제거.
    금액 상한 검사는 카드 존재 여부 확인보다 먼저 실행되므로,
    카드 미등록 상태여도 InputValidationError가 발생해야 한다.
    """
    svc, _, _ = await create_service(db, cipher)
    fake = FakeTossClient()
    with pytest.raises(InputValidationError):
        await payment_service.create_one_off_payment(
            db, fake, cipher, service=svc, external_user_id="u-cap@e.com",
            order_id="oo-cap-1", order_name="고액",
            amount=payment_service.ONE_OFF_MAX_AMOUNT + 1)
    assert not fake.charges, "상한 초과 요청이 토스까지 전달되면 안 됨"


async def test_session_absolute_expiry(client, db, redis_client, settings):
    """[L-5] 유휴 TTL이 계속 연장돼도 절대 수명을 초과한 세션은 파기된다."""
    user, pw = await create_user(db)
    session_id = await admin_login(client, user.email, pw)
    # 활동 중에는 유효
    assert (await client.get("/admin")).status_code == 200
    # 생성 시각을 절대 수명 이전으로 되돌려 '오래된 세션'을 시뮬레이션
    await redis_client.hset(
        f"session:{session_id}", "created_at",
        str(0))  # epoch 0 — 어떤 설정값으로도 절대 수명 초과
    resp = await client.get("/admin", follow_redirects=False)
    assert resp.status_code in (302, 303)          # 로그인으로 리다이렉트
    assert "/admin/login" in resp.headers["location"]
    # 세션 자체가 파기되었는지 확인
    data = await auth_service.get_session(redis_client, settings, session_id)
    assert data is None