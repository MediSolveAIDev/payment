import json
import time
import uuid

from httpx import ASGITransport, AsyncClient

from app.core.security import sign_request


def client_from_ip(app, ip: str, port: int = 12345) -> AsyncClient:
    """주어진 소스 IP에서 들어오는 것처럼 보이는 테스트 클라이언트.

    ASGITransport(client=(ip, port))로 scope["client"]를 지정하면
    request.client.host == ip 가 되어 IP 화이트리스트 검사를 실제처럼 테스트할 수 있다.
    (기본 client 픽스처의 소스 IP는 127.0.0.1 — 루프백이라 항상 허용된다.)
    """
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, port)),
                       base_url="http://test")


def signed_headers(api_key: str, secret: str, method: str, path: str,
                   body: bytes = b"", *, timestamp: str | None = None,
                   nonce: str | None = None, signature: str | None = None) -> dict:
    ts = timestamp if timestamp is not None else str(int(time.time()))
    nc = nonce if nonce is not None else str(uuid.uuid4())
    sig = signature if signature is not None else sign_request(
        secret, method, path, ts, nc, body)
    return {
        "X-Service-Key": api_key,
        "X-Timestamp": ts,
        "X-Nonce": nc,
        "X-Signature": sig,
        "Content-Type": "application/json",
    }


async def api_request(client, method: str, path: str, api_key: str, secret: str,
                      json_body: dict | None = None, **header_overrides):
    body = json.dumps(json_body).encode() if json_body is not None else b""
    headers = signed_headers(api_key, secret, method, path, body, **header_overrides)
    return await client.request(method, path, content=body or None, headers=headers)


async def admin_login(client, email: str, password: str) -> str:
    """admin 로그인 후 세션 ID 반환. 쿠키는 client에 자동 저장됨."""
    resp = await client.post("/admin/login", data={"email": email, "password": password})
    assert resp.status_code == 303, f"login failed: {resp.status_code} {resp.text[:200]}"
    return resp.cookies["admin_session"]


async def get_csrf(redis_client, session_id: str) -> str:
    return await redis_client.hget(f"session:{session_id}", "csrf_token")
