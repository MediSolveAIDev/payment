import base64

import pytest
from cryptography.exceptions import InvalidTag

from app.core.crypto import AesGcmCipher

KEY = base64.b64encode(b"0" * 32).decode()


def test_encrypt_decrypt_roundtrip():
    cipher = AesGcmCipher(KEY)
    assert cipher.decrypt(cipher.encrypt("billing-key-123")) == "billing-key-123"


def test_roundtrip_edge_cases():
    cipher = AesGcmCipher(KEY)
    for plaintext in ["", "한글🔐 unicode", "x" * 1024]:
        assert cipher.decrypt(cipher.encrypt(plaintext)) == plaintext


def test_ciphertext_differs_each_time():
    cipher = AesGcmCipher(KEY)
    assert cipher.encrypt("same") != cipher.encrypt("same")  # 랜덤 nonce


def test_tampered_ciphertext_raises():
    cipher = AesGcmCipher(KEY)
    token = cipher.encrypt("secret")
    raw = bytearray(base64.b64decode(token))
    raw[-1] ^= 0xFF
    with pytest.raises(InvalidTag):
        cipher.decrypt(base64.b64encode(bytes(raw)).decode())


def test_wrong_key_length_rejected():
    with pytest.raises(ValueError):
        AesGcmCipher(base64.b64encode(b"short").decode())
