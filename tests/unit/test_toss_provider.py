"""TossClientProvider 단위 테스트.

override_client 주입·키 미설정 예외·시크릿별 캐시 동작을 검증한다.
"""
import pytest
from app.core.crypto import AesGcmCipher
from app.core.errors import TossKeyNotConfiguredError
from app.toss.provider import TossClientProvider


class _Svc:
    """테스트용 최소 서비스 스텁 — toss_secret_key_encrypted 속성만 갖는다."""

    def __init__(self, enc):
        self.toss_secret_key_encrypted = enc


def _cipher():
    """테스트용 AesGcmCipher — 32바이트 고정 키(base64)."""
    import base64
    return AesGcmCipher(base64.b64encode(b"0" * 32).decode())


def test_override_client_returned_for_any_service():
    """override_client가 있으면 키 없는 서비스도 sentinel을 반환해야 한다(테스트 모드)."""
    sentinel = object()
    p = TossClientProvider(_cipher(), "https://api.tosspayments.com", override_client=sentinel)
    assert p.for_service(_Svc(None)) is sentinel          # 키 없어도 override 반환(테스트 모드)


def test_missing_key_raises():
    """암호화 키가 설정되지 않은 서비스는 TossKeyNotConfiguredError를 발생시켜야 한다."""
    p = TossClientProvider(_cipher(), "https://api.tosspayments.com")
    with pytest.raises(TossKeyNotConfiguredError):
        p.for_service(_Svc(None))


def test_builds_and_caches_per_secret():
    """동일 시크릿에 대해 factory는 1회만 호출되고 클라이언트 인스턴스가 캐시되어야 한다."""
    cipher = _cipher()
    built = []

    def factory(secret, base_url):
        built.append(secret)
        return object()

    p = TossClientProvider(cipher, "https://api.tosspayments.com", factory=factory)
    svc = _Svc(cipher.encrypt("sk_test_abc"))
    c1 = p.for_service(svc)
    c2 = p.for_service(_Svc(cipher.encrypt("sk_test_abc")))   # 동일 시크릿 → 캐시 재사용
    assert c1 is c2
    assert built == ["sk_test_abc"]                            # 팩토리는 1회만 호출
