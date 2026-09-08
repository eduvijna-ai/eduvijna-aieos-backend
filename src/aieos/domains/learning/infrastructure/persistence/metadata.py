"""SQLAlchemy metadata for the Learning PostgreSQL schema."""

from __future__ import annotations

from sqlalchemy import MetaData

LEARNING_SCHEMA = "learning"

learning_metadata = MetaData(schema=LEARNING_SCHEMA)
