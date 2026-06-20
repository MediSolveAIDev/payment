"""AES-256-GCM 대칭 암호화 모듈.

빌링키(토스페이먼트 자동결제용)와 서비스별 HMAC secret을 DB에 저장하기 전에
암호화하고, 읽어낼 때 복호화하는 단일 책임을 가진다.
키는 ``Settings.encryption_key`` (base64 인코딩된 32바이트)로 공급된다.
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class AesGcmCipher:
    """빌링키·HMAC secret 저장용 AES-256-GCM 암호화.

    GCM 모드는 암호문 무결성을 인증 태그로 보장하므로 복호화 시
    변조된 데이터를 자동으로 감지한다(AEAD).
    """

    def __init__(self, key_b64: str) -> None:
        """base64 인코딩된 32바이트 키로 초기화한다.

        키 길이가 32바이트(AES-256)가 아니면 즉시 ValueError를 발생시켜
        잘못된 키로 운영되는 상황을 조기에 차단한다.
        """
        key = base64.b64decode(key_b64)
        if len(key) != 32:
            raise ValueError("ENCRYPTION_KEY must decode to 32 bytes")
        self._aesgcm = AESGCM(key)

    def encrypt(self, plaintext: str) -> str:
        """평문을 암호화해 base64 문자열로 반환한다.

        12바이트 난수 nonce를 암호문 앞에 붙여 하나의 base64 토큰으로 인코딩한다.
        nonce를 매번 새로 생성하므로 같은 평문도 호출마다 다른 토큰이 나온다.
        반환 형식: base64(nonce[12] + ciphertext + tag[16])
        """
        nonce = os.urandom(12)
        ct = self._aesgcm.encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ct).decode()

    def decrypt(self, token: str) -> str:
        """``encrypt``가 반환한 base64 토큰을 복호화해 원문 문자열로 반환한다.

        토큰 앞 12바이트를 nonce로, 나머지를 암호문+인증태그로 분리한다.
        인증 태그 검증에 실패하면 cryptography 라이브러리가 예외를 발생시킨다.
        """
        raw = base64.b64decode(token)
        return self._aesgcm.decrypt(raw[:12], raw[12:], None).decode()
