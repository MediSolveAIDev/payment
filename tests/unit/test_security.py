import pytest

from app.core.security import (
    constant_time_equals,
    generate_hmac_secret,
    generate_service_api_key,
    generate_setup_token,
    hash_password,
    sha256_hex,
    sign_request,
    verify_password,
)


def test_api_key_format_and_uniqueness():
    k1, k2 = generate_service_api_key(), generate_service_api_key()
    assert k1.startswith("svc_") and len(k1) > 30
    assert k1 != k2


def test_secret_and_token_generation():
    assert len(generate_hmac_secret()) >= 48
    assert generate_setup_token() != generate_setup_token()


def test_sha256_hex_deterministic():
    assert sha256_hex("abc") == sha256_hex("abc")
    assert len(sha256_hex("abc")) == 64


def test_sign_request_changes_with_each_component():
    base = dict(secret="s3cret", method="POST", path="/api/v1/subscriptions",
                timestamp="1717570800", nonce="n-1", body=b'{"a":1}')
    sig = sign_request(**base)
    assert sig == sign_request(**base)  # 결정적
    for field, value in [("method", "GET"), ("path", "/x"), ("timestamp", "1"),
                         ("nonce", "n-2"), ("body", b'{"a":2}'), ("secret", "other")]:
        changed = {**base, field: value}
        assert sign_request(**changed) != sig


def test_sign_request_known_answer_vector():
    """와이어 포맷 고정 — 외부 서비스가 동일 알고리즘을 구현해야 하므로
    조인 순서/구분자가 바뀌면 이 테스트가 깨져야 한다."""
    sig = sign_request(secret="s3cret", method="POST", path="/api/v1/subscriptions",
                       timestamp="1717570800", nonce="n-1", body=b'{"a":1}')
    assert sig == "1ab7c32c5c2eeb068e21f0ef6677cce6a74e96b945ffa95a1d4dc16e2cc2e325"
    # method는 대문자 정규화
    assert sign_request(secret="s3cret", method="post", path="/api/v1/subscriptions",
                        timestamp="1717570800", nonce="n-1", body=b'{"a":1}') == sig


def test_sign_request_rejects_newline_injection():
    """개행으로 필드 경계를 옮기는 충돌 공격 차단."""
    base = dict(secret="k", method="POST", path="/a", timestamp="t", nonce="n", body=b"")
    for field in ["method", "path", "timestamp", "nonce"]:
        for evil in ["a\nb", "a\rb"]:
            with pytest.raises(ValueError):
                sign_request(**{**base, field: evil})


def test_constant_time_equals():
    assert constant_time_equals("abc", "abc")
    assert not constant_time_equals("abc", "abd")


def test_password_hash_and_verify():
    h = hash_password("CorrectHorse9!")
    assert h != "CorrectHorse9!"
    assert verify_password("CorrectHorse9!", h)
    assert not verify_password("wrong", h)
