import pytest


@pytest.fixture(autouse=True)
def _auto_clean(clean_db, clean_redis):
    """E2E 테스트 후 DB/Redis 초기화."""
