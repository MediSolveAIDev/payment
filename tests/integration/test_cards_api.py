"""카드(결제수단) 외부 API 통합 테스트 — POST/GET/DELETE /api/v1/cards.

client 픽스처는 FakeTossClient를 주입한 앱을 사용하므로
실제 토스 서버 없이 빌링키 발급·삭제를 시뮬레이션할 수 있다(conftest.py 참고).
"""
import pytest

from tests.factories import create_service
from tests.helpers import api_request


async def test_register_then_get_card(client, db, cipher):
    """카드 등록(POST 201) 후 조회(GET 200) — billingKey 미노출 확인."""
    svc, api_key, secret = await create_service(db, cipher)

    # 카드 등록 — POST /api/v1/cards
    body = {"external_user_id": "u1@e.com", "customer_key": "cust-1", "auth_key": "ak-1"}
    resp = await api_request(client, "POST", "/api/v1/cards", api_key, secret,
                             json_body=body)
    assert resp.status_code == 201, resp.text
    # 응답에 billingKey가 포함되지 않아야 한다(보안 핵심)
    assert "billingKey" not in resp.text
    assert "billing_key" not in resp.text

    # 등록된 카드 조회 — GET /api/v1/cards/u1
    got = await api_request(client, "GET", "/api/v1/cards/u1@e.com", api_key, secret)
    assert got.status_code == 200, got.text
    data = got.json()
    assert data["external_user_id"] == "u1@e.com"
    # card 마스킹 정보가 있어야 한다(FakeTossClient는 card_info를 반환함)
    assert data["card"] is not None


async def test_delete_card(client, db, cipher):
    """카드 등록 후 삭제(DELETE 204) — 이후 조회 시 404."""
    svc, api_key, secret = await create_service(db, cipher)

    # 카드 등록 — customer_key 최소 2자(토스 스펙)
    await api_request(client, "POST", "/api/v1/cards", api_key, secret,
                      json_body={"external_user_id": "u9@e.com", "customer_key": "ck",
                                 "auth_key": "ak"})

    # 카드 삭제 — DELETE /api/v1/cards/u9
    resp = await api_request(client, "DELETE", "/api/v1/cards/u9@e.com", api_key, secret)
    assert resp.status_code == 204, resp.text

    # 삭제 후 조회 시 404를 반환해야 한다
    gone = await api_request(client, "GET", "/api/v1/cards/u9@e.com", api_key, secret)
    assert gone.status_code == 404


async def test_get_card_not_found(client, db, cipher):
    """등록되지 않은 카드 조회 시 404 반환."""
    svc, api_key, secret = await create_service(db, cipher)
    resp = await api_request(client, "GET", "/api/v1/cards/ghost-user@e.com", api_key, secret)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_register_replaces_existing_card(client, db, cipher):
    """같은 (service, external_user_id)로 재등록 시 교체(201)."""
    svc, api_key, secret = await create_service(db, cipher)
    body = {"external_user_id": "u2@e.com", "customer_key": "cust-2", "auth_key": "ak-2a"}

    # 최초 등록
    first = await api_request(client, "POST", "/api/v1/cards", api_key, secret,
                              json_body=body)
    assert first.status_code == 201

    # 재등록 — 교체(201, 동일 사용자ID)
    second = await api_request(client, "POST", "/api/v1/cards", api_key, secret,
                               json_body={**body, "auth_key": "ak-2b"})
    assert second.status_code == 201

    # 조회 시 카드가 정상적으로 존재해야 한다
    got = await api_request(client, "GET", "/api/v1/cards/u2@e.com", api_key, secret)
    assert got.status_code == 200


async def test_register_card_requires_auth(client, db, cipher):
    """인증 헤더 없이 POST /cards 호출 시 401."""
    resp = await client.post("/api/v1/cards",
                             json={"external_user_id": "u@e.com", "customer_key": "c",
                                   "auth_key": "ak"})
    assert resp.status_code == 401


async def test_delete_card_not_found(client, db, cipher):
    """등록되지 않은 카드 삭제 시 404 반환."""
    svc, api_key, secret = await create_service(db, cipher)
    resp = await api_request(client, "DELETE", "/api/v1/cards/nobody@e.com", api_key, secret)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
