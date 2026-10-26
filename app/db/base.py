"""Declarative base."""

from __future__ import annotations

from sqlalchemy import JSON

# JSON variant usable on both PostgreSQL (jsonb) and SQLite for tests.
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator


class JSONType(TypeDecorator):
    """Portable JSON column: jsonb on PostgreSQL, JSON on SQLite/others."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSONType, list: JSONType}
