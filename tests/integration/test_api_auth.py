import time

from tests.factories import create_plan, create_service
from tests.helpers import api_request, signed_headers


async def test_valid_signed_request_returns_plans(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    await create_plan(db, svc, name="베이직", price=9900)
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, secret)
    assert resp.status_code == 200
    body = resp.json()
    assert body["plans"][0]["name"] == "베이직"
    assert body["plans"][0]["price"] == 9900


async def test_missing_auth_headers_rejected(client, db, cipher):
    resp = await client.get("/api/v1/plans")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


async def test_unknown_api_key_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    resp = await api_request(client, "GET", "/api/v1/plans", "svc_wrong-key", secret)
    assert resp.status_code == 401


async def test_bad_signature_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, "wrong-secret")
    assert resp.status_code == 401


async def test_stale_timestamp_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    stale = str(int(time.time()) - 3600)
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, secret,
                             timestamp=stale)
    assert resp.status_code == 401


async def test_nonce_replay_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    headers = signed_headers(api_key, secret, "GET", "/api/v1/plans")
    first = await client.get("/api/v1/plans", headers=headers)
    assert first.status_code == 200
    replay = await client.get("/api/v1/plans", headers=headers)  # 같은 헤더 재사용
    assert replay.status_code == 401


async def test_ip_not_in_whitelist_rejected(app, db, cipher):
    """루프백이 아닌 비등록 IP(203.0.113.5)의 호출은 403."""
    from tests.helpers import client_from_ip
    svc, api_key, secret = await create_service(db, cipher, allowed_ips=["10.0.0.1"])
    async with client_from_ip(app, "203.0.113.5") as ext:
        resp = await api_request(ext, "GET", "/api/v1/plans", api_key, secret)
    assert resp.status_code == 403


async def test_empty_whitelist_allows_any_ip(app, db, cipher):
    """허용 IP 목록이 비어 있으면 IP 제한 없음 — 외부 IP(203.0.113.5)도 허용(200)."""
    from tests.helpers import client_from_ip
    svc, api_key, secret = await create_service(db, cipher, allowed_ips=[])
    async with client_from_ip(app, "203.0.113.5") as ext:
        resp = await api_request(ext, "GET", "/api/v1/plans", api_key, secret)
    assert resp.status_code == 200


async def test_loopback_always_allowed(client, db, cipher):
    """127.0.0.1(같은 서버)은 화이트리스트에 없어도 항상 허용된다."""
    # 기본 테스트 클라이언트의 소스 IP는 127.0.0.1 (allowed_ips에 없음)
    svc, api_key, secret = await create_service(db, cipher, allowed_ips=["10.0.0.1"])
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, secret)
    assert resp.status_code == 200


async def test_inactive_service_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    svc.status = "INACTIVE"
    await db.commit()
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, secret)
    assert resp.status_code == 401


async def test_health_endpoint_is_public(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_bad_signature_does_not_burn_nonce(client, db, cipher, redis_client):
    """서명 검증 실패 요청은 nonce를 소비하지 않는다 (메모리 DoS 방지)."""
    from tests.helpers import signed_headers
    svc, api_key, secret = await create_service(db, cipher)
    headers = signed_headers(api_key, secret, "GET", "/api/v1/plans")
    headers["X-Signature"] = "deadbeef" * 8  # 위조 서명
    resp = await client.get("/api/v1/plans", headers=headers)
    assert resp.status_code == 401
    # nonce 키가 만들어지지 않았어야 함
    assert await redis_client.get(f"nonce:{svc.id}:{headers['X-Nonce']}") is None


async def test_openapi_hidden_in_prod(settings, engine, fake_toss, email_sender):
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    prod = settings.model_copy(update={"environment": "prod"})
    application = create_app(prod, toss_client=fake_toss,
                             email_sender=email_sender, engine=engine)
    async with LifespanManager(application):
        async with AsyncClient(transport=ASGITransport(app=application),
                               base_url="http://test") as c:
            assert (await c.get("/openapi.json")).status_code == 404
            assert (await c.get("/docs")).status_code == 404
            assert (await c.get("/health")).status_code == 200
