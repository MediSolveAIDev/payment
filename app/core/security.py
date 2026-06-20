"""인증·서명·암호화 유틸리티 모듈.

서비스 API 키 생성, HMAC-SHA256 요청 서명, Argon2id 비밀번호 해시 등
보안 관련 순수 함수를 모아 제공한다. 상태를 갖지 않으며 외부 I/O 없음.
"""

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id 해시 인스턴스. 기본 파라미터(time_cost=3, memory_cost=65536)를 사용해
# 브루트포스 공격에 충분한 연산 비용을 유지한다.
_ph = PasswordHasher()


def generate_service_api_key() -> str:
    """외부 서비스용 API 키를 생성한다.

    ``svc_`` 접두사로 키 종류를 식별할 수 있게 하고,
    32바이트 URL-safe 난수로 충분한 엔트로피를 확보한다.
    """
    return "svc_" + secrets.token_urlsafe(32)


def generate_hmac_secret() -> str:
    """서비스별 HMAC 서명용 시크릿을 생성한다.

    48바이트(384비트) URL-safe 난수를 사용해 SHA-256 출력 대비
    충분한 키 길이를 유지한다. DB 저장 전 AesGcmCipher로 암호화된다.
    """
    return secrets.token_urlsafe(48)


def generate_setup_token() -> str:
    """초기 설정 링크 등 일회성 토큰을 생성한다.

    32바이트(256비트) URL-safe 난수. 이메일 링크에 포함해 본인 확인에 사용된다.
    """
    return secrets.token_urlsafe(32)


def sha256_hex(value: str) -> str:
    """문자열의 SHA-256 해시를 16진수 문자열로 반환한다.

    API 키 원문 대신 해시값만 DB에 저장할 때 사용해 유출 시 원문 노출을 방지한다.
    """
    return hashlib.sha256(value.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    """두 문자열을 타이밍 공격에 안전하게 비교한다.

    ``hmac.compare_digest``는 두 값의 길이가 달라도 일정한 시간에 비교를 완료해
    응답 시간 차이로 비밀값을 추측하는 타이밍 공격을 방지한다.
    """
    return hmac.compare_digest(a.encode(), b.encode())


def sign_request(secret: str, method: str, path: str, timestamp: str,
                 nonce: str, body: bytes) -> str:
    """외부 API 요청 서명: HMAC-SHA256(secret, canonical string).

    구분자가 개행이므로 어떤 구성요소에도 개행이 들어오면 서로 다른 입력이
    같은 canonical string을 만들 수 있다(필드 간 바이트 이동 공격) — 거부한다.
    """
    for name, component in [("method", method), ("path", path),
                            ("timestamp", timestamp), ("nonce", nonce)]:
        if "\n" in component or "\r" in component:
            raise ValueError(f"sign_request: {name}에 개행 문자를 허용하지 않습니다")
    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def hash_password(password: str) -> str:
    """비밀번호를 Argon2id로 해시해 저장용 문자열로 반환한다.

    Argon2id는 사이드채널 공격과 GPU 병렬 공격에 모두 강한 최신 KDF로
    OWASP 권장 알고리즘이다. 솔트는 라이브러리가 자동 생성한다.
    """
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """평문 비밀번호와 저장된 Argon2id 해시를 비교한다.

    불일치·손상·형식 불일치 모두 False를 반환해 인증 실패로 처리한다.
    """
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        # 손상된/형식이 다른 해시는 로그인 실패로 처리 (500 방지)
        return False
