"""데이터베이스 엔진·세션 팩토리 생성 유틸리티.

SQLAlchemy 비동기 엔진과 세션 팩토리를 앱 시작 시 한 번 생성해
``app.state``에 보관한다. 스키마 마이그레이션은 Alembic이 담당하며
이 모듈은 런타임 연결만 설정한다.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def create_engine(database_url: str, *, pool_size: int = 10, max_overflow: int = 20,
                  pool_timeout: int = 30, pool_recycle: int = 1800) -> AsyncEngine:
    """비동기 SQLAlchemy 엔진을 생성한다.

    ``pool_pre_ping=True``로 유휴 커넥션의 유효성을 쿼리 전에 확인해
    장시간 방치 후 끊어진 커넥션으로 인한 오류를 방지한다.

    커넥션 풀을 명시 설정한다(감사 Phase 1 — 성능 M3). SQLAlchemy 기본값
    (pool_size=5, max_overflow=10)은 토스 API 지연 등으로 커넥션 점유가
    길어질 때 동시 처리량이 15로 캡핑되는 문제가 있어, 기본을 상향하고
    운영 환경에서 .env(DB_POOL_SIZE 등)로 조정할 수 있게 한다.

    - pool_size: 상시 유지 커넥션 수
    - max_overflow: 피크 시 추가 허용 커넥션 수 (총 최대 = pool_size + max_overflow)
    - pool_timeout: 풀 고갈 시 커넥션 대기 한도(초) — 초과 시 즉시 오류로 드러나게 함
    - pool_recycle: 커넥션 재활용 주기(초) — DB/LB의 유휴 연결 종료보다 짧게 유지
    """
    return create_async_engine(
        database_url, pool_pre_ping=True,
        pool_size=pool_size, max_overflow=max_overflow,
        pool_timeout=pool_timeout, pool_recycle=pool_recycle)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """비동기 세션 팩토리를 생성한다.

    ``expire_on_commit=False``: 커밋 후에도 ORM 객체 속성이 만료되지 않아
    ``await session.refresh(obj)`` 없이 커밋된 값을 그대로 참조할 수 있다.
    비동기 컨텍스트에서 lazy-load 트리거를 피하기 위해 반드시 False로 설정한다.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
