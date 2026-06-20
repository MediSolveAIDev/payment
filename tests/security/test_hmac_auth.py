import json
import time
import uuid as uuid_mod

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.services.registry import rotate_keys
from tests.factories import create_card, create_plan, create_service
from tests.helpers import api_request, signed_headers


async def test_body_tampering_rejected(client, db, cipher):
    """서명한 본문과 다른 본문을 보내면 401 (본문 무결성)."""
    svc, api_key, secret = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    base = {"external_user_id": "u-1", "plan_id": str(plan.id),
            "auth_key": "a", "customer_key": "ck-1"}
    good_body = json.dumps(base).encode()
    evil_body = json.dumps({**base, "customer_key": "ck-EVIL"}).encode()
    headers = signed_headers(api_key, secret, "POST", "/api/v1/subscriptions", good_body)
    resp = await client.post("/api/v1/subscriptions", content=evil_body, headers=headers)
    assert resp.status_code == 401


async def test_signature_for_other_path_rejected(client, db, cipher):
    """다른 경로용 서명 재사용 → 401."""
    svc, api_key, secret = await create_service(db, cipher)
    headers = signed_headers(api_key, secret, "GET", "/api/v1/plans")
    resp = await client.get("/api/v1/payments/u-1", headers=headers)
    assert resp.status_code == 401


async def test_future_timestamp_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    future = str(int(time.time()) + 3600)
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, secret,
                             timestamp=future)
    assert resp.status_code == 401


async def test_nonce_scope_is_per_service(client, db, cipher):
    """nonce는 서비스별 스코프 — 다른 서비스의 정상 요청을 막지 않는다."""
    svc_a, key_a, sec_a = await create_service(db, cipher, name="nonce-a")
    svc_b, key_b, sec_b = await create_service(db, cipher, name="nonce-b")
    shared_nonce = str(uuid_mod.uuid4())
    r1 = await api_request(client, "GET", "/api/v1/plans", key_a, sec_a,
                           nonce=shared_nonce)
    r2 = await api_request(client, "GET", "/api/v1/plans", key_b, sec_b,
                           nonce=shared_nonce)
    assert r1.status_code == 200 and r2.status_code == 200


async def test_rate_limit_returns_429(settings, engine, fake_toss, email_sender,
                                      db, cipher):
    limited = settings.model_copy(update={"rate_limit_per_minute": 3})
    svc, api_key, secret = await create_service(db, cipher)
    application = create_app(limited, toss_client=fake_toss,
                             email_sender=email_sender, engine=engine)
    statuses = []
    async with LifespanManager(application):
        async with AsyncClient(transport=ASGITransport(app=application),
                               base_url="http://test") as c:
            for _ in range(4):
                resp = await api_request(c, "GET", "/api/v1/plans", api_key, secret)
                statuses.append(resp.status_code)
    assert statuses == [200, 200, 200, 429]


async def test_payment_rate_limit_stricter(settings, engine, fake_toss, email_sender,
                                           db, cipher):
    """결제 전용 rate limit: 분당 1건 초과 시 두 번째 구독 요청은 429.

    카드 보관함 전환 이후 auth_key/customer_key를 구독 본문에서 제거한다.
    카드 등록도 payment_rate_limit 대상이므로, rate limit 계수 외부에서 DB 헬퍼로
    직접 카드를 등록한 뒤 rate_limit_payment_per_minute=1 앱으로 구독을 생성한다.
    rate limit 의도는 유지: 첫 번째는 201, 두 번째(같은 분)는 429이어야 한다.
    """
    limited = settings.model_copy(update={"rate_limit_payment_per_minute": 1})
    svc, api_key, secret = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    # 카드 보관함 전환 후 구독 전 카드 선등록 필수.
    # POST /api/v1/cards도 payment_rate_limit 대상이므로, HTTP API 우회하여
    # 서비스 레이어 헬퍼(create_card)로 DB에 직접 삽입해 rate limit 카운터를 소비하지 않는다.
    await create_card(db, fake_toss, cipher, svc,
                      external_user_id="u-rl1", customer_key="ck-rl1", auth_key="a")
    await create_card(db, fake_toss, cipher, svc,
                      external_user_id="u-rl2", customer_key="ck-rl2", auth_key="a")
    application = create_app(limited, toss_client=fake_toss,
                             email_sender=email_sender, engine=engine)
    async with LifespanManager(application):
        async with AsyncClient(transport=ASGITransport(app=application),
                               base_url="http://test") as c:
            first = await api_request(
                c, "POST", "/api/v1/subscriptions", api_key, secret,
                json_body={"external_user_id": "u-rl1", "plan_id": str(plan.id)})
            second = await api_request(
                c, "POST", "/api/v1/subscriptions", api_key, secret,
                json_body={"external_user_id": "u-rl2", "plan_id": str(plan.id)})
    assert first.status_code == 201
    assert second.status_code == 429


async def test_rotated_key_invalidates_old(client, db, cipher):
    svc, old_key, old_secret = await create_service(db, cipher)
    new_key, new_secret = await rotate_keys(db, cipher, svc.id)
    old_resp = await api_request(client, "GET", "/api/v1/plans", old_key, old_secret)
    assert old_resp.status_code == 401
    new_resp = await api_request(client, "GET", "/api/v1/plans", new_key, new_secret)
    assert new_resp.status_code == 200


async def test_error_responses_do_not_leak_internals(client):
    resp = await client.get("/api/v1/plans")  # 인증 없음
    body = resp.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message"}


async def test_nonce_replay_rejected_with_baseline(client, db, cipher):
    """동일 (서비스, nonce) 재전송 차단 — 먼저 정상 200, 동일 헤더 재전송 401."""
    svc, api_key, secret = await create_service(db, cipher)
    await create_plan(db, svc, name="베이직", price=9900)
    headers = signed_headers(api_key, secret, "GET", "/api/v1/plans")
    first = await client.get("/api/v1/plans", headers=headers)
    assert first.status_code == 200  # 정상 요청은 통과(해피패스 증명)
    replay = await client.get("/api/v1/plans", headers=headers)
    assert replay.status_code == 401  # 동일 nonce 재사용 차단


async def test_stale_timestamp_rejected(client, db, cipher):
    """허용 윈도우보다 오래된 타임스탬프(재전송된 옛 요청) 거부."""
    svc, api_key, secret = await create_service(db, cipher)
    stale = str(int(time.time()) - 3600)
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, secret,
                             timestamp=stale)
    assert resp.status_code == 401


async def test_ip_not_in_whitelist_rejected(app, db, cipher):
    """루프백이 아닌 비등록 IP(203.0.113.5)의 호출은 403.

    127.0.0.1/::1(같은 서버)은 화이트리스트와 무관하게 항상 허용되므로,
    거부 동작은 비루프백 소스 IP로 검증한다.
    """
    from tests.helpers import client_from_ip
    svc, api_key, secret = await create_service(db, cipher, allowed_ips=["10.0.0.1"])
    async with client_from_ip(app, "203.0.113.5") as ext:
        resp = await api_request(ext, "GET", "/api/v1/plans", api_key, secret)
    assert resp.status_code == 403
