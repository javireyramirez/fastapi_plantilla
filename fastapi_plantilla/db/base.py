from sqlalchemy.orm import DeclarativeBase

from fastapi_plantilla.db.meta import meta


class Base(DeclarativeBase):
    """Base for all models."""

    metadata = meta
