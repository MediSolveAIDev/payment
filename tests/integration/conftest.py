import pytest


@pytest.fixture(autouse=True)
def _auto_clean(clean_db, clean_redis):
    """통합 테스트는 매 테스트 후 DB/Redis 초기화."""
