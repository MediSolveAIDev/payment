"""시간 유틸리티 모듈.

저장·비교는 항상 UTC, 화면 출력만 KST로 변환하는 규약을 강제한다.
한국은 DST(서머타임)가 없으므로 고정 오프셋(UTC+9)으로 충분하며
tzdata 패키지 의존 없이 결정적으로 동작한다.
"""

from datetime import UTC, datetime, timedelta, timezone

# 표시용 한국 시간대. 한국은 DST가 없으므로 고정 오프셋(+9)으로 충분하며
# tzdata 의존 없이 결정적으로 동작한다. 저장은 항상 UTC, 변환은 출력 시점에만.
KST = timezone(timedelta(hours=9))


def utcnow() -> datetime:
    """현재 UTC 시각을 timezone-aware datetime으로 반환한다.

    ``datetime.utcnow()``는 naive datetime을 반환해 UTC임을 보장하지 않으므로
    사용하지 않는다. 이 함수로 통일해 테스트에서 monkeypatch하기 쉽게 한다.
    """
    return datetime.now(UTC)


def kst_format(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """UTC datetime을 KST로 변환해 문자열로 포맷. None이면 '-'.
    tz 없는(naive) 값은 UTC로 간주한다(저장 규약)."""
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(KST).strftime(fmt)
