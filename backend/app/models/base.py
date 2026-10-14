"""
SQLAlchemy declarative base with common mixins.

Every model inherits from :class:`BaseModel` which supplies a UUID primary
key, automatic creation / update timestamps, and a ``to_dict()`` helper.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass, mapped_column


class Base(MappedAsDataclass, DeclarativeBase):
    """Declarative base for all ORM models.

    ``MappedAsDataclass`` MUST precede ``DeclarativeBase`` in the base list —
    otherwise the declarative configurator processes ``__init_subclass__``
    first and the class kwarg ``kw_only=True`` is silently dropped, breaking
    dataclass field ordering (non-default field after default field).
    """

    type_annotation_map = {
        # Default UUID mapping uses the PostgreSQL native UUID type
        uuid.UUID: UUID(as_uuid=True),
    }


class BaseModel(Base):
    """Mixin that adds a UUID PK, ``created_at``, and ``updated_at`` to every table."""

    __abstract__ = True

    # NOTE: MappedAsDataclass turns mapped_column(default=...) into the
    # *dataclass field default verbatim* — for callables use default_factory
    # so each instance gets a fresh value instead of the function object.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default_factory=uuid.uuid4,
        sort_order=-100,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default_factory=lambda: datetime.now(timezone.utc),
        sort_order=1000,
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default_factory=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        sort_order=1001,
    )

    def to_dict(self, exclude: set[str] | None = None) -> dict[str, Any]:
        """Return a dictionary representation of the model instance.

        Parameters
        ----------
        exclude:
            A set of attribute names to omit from the output (e.g. password hashes).

        Notes
        -----
        The method walks ``__table__.columns`` so only persisted columns are
        included — relationships and hybrid properties are not serialised.
        """
        _exclude = exclude or set()
        return {
            c.name: getattr(self, c.name)
            for c in self.__table__.columns
            if c.name not in _exclude
        }
