"""전역 설정(Settings)·default_settings() 검증 — .env로 옮긴 운영 상수의 배선 확인."""
from app.core import config
from app.core.config import Settings


def test_new_tunable_fields_have_expected_defaults():
    """기존 하드코딩 리터럴과 동일한 기본값을 유지(동작 불변)."""
    s = Settings()
    assert s.max_failed_logins == 5
    assert s.account_lock_minutes == 15
    assert s.min_password_length == 10
    assert s.password_link_ttl_hours == 48
    assert s.one_off_max_amount == 100_000_000
    assert s.admin_login_rate_limit_per_minute == 10
    assert s.hmac_nonce_ttl_seconds == 600
    assert s.toss_read_timeout_seconds == 65.0
    assert s.toss_connect_timeout_seconds == 5.0


def test_fields_override_via_constructor():
    """Settings(...) 생성자 override가 동작(=.env/환경변수로 주입 가능)."""
    s = Settings(max_failed_logins=2, one_off_max_amount=5000, password_link_ttl_hours=12)
    assert s.max_failed_logins == 2
    assert s.one_off_max_amount == 5000
    assert s.password_link_ttl_hours == 12


def test_default_settings_reads_environment(monkeypatch):
    """default_settings()가 환경변수(.env)를 읽는다 — 모듈 상수 경로(payments/auth 등) 배선 검증."""
    monkeypatch.setenv("ONE_OFF_MAX_AMOUNT", "5000")
    config.default_settings.cache_clear()
    try:
        assert config.default_settings().one_off_max_amount == 5000
    finally:
        # 캐시를 비워 다른 테스트가 깨끗한(환경변수 미설정) 인스턴스를 받도록 한다
        config.default_settings.cache_clear()
