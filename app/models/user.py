"""User ORM model."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    """ 因为 Base 继承 DeclarativeBase，类创建时 SQLAlchemy 声明式机制（内部 registry）
    自动把表结构登记进 Base.metadata.tables["users"]
    所以：关键行是 class User(Base):；登记在整段类体跑完后由框架完成，不是某一行手写塞进 metadata。 """
    """Registered application user."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    username: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        # DB-side default: PostgreSQL evaluates NOW() on INSERT (not Python).
        server_default=func.now(),
        nullable=False,
    )
