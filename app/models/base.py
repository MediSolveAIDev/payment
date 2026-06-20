"""모든 ORM 모델이 공유하는 기반 클래스 및 믹스인.

Base: Alembic 마이그레이션용 명명 규칙을 통일한 DeclarativeBase.
TimestampMixin: created_at / updated_at 두 컬럼을 공통으로 제공.
"""
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Alembic auto-generate 시 제약·인덱스 이름이 일관되도록 고정한 명명 규칙.
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """모든 SQLAlchemy 모델의 공통 베이스. 명명 규칙이 적용된 MetaData를 공유."""

    metadata = MetaData(naming_convention=convention)


class TimestampMixin:
    """생성/수정 시각을 자동 관리하는 믹스인.

    created_at: 레코드 최초 삽입 시 DB 서버 시각으로 채워짐(UTC).
    updated_at: 매 UPDATE마다 DB 서버 시각으로 갱신됨(UTC).
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())  # 삽입 시각(UTC, 자동 채움)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # 수정 시각(UTC, 매 UPDATE 갱신)
