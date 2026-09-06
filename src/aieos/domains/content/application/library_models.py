"""Teacher OS Library application read models and queries.

Library is a read/reuse projection over Generic Content — not a SoR.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping
from uuid import UUID

from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)


@dataclass(frozen=True, slots=True)
class TeacherLibraryItem:
    content_id: ContentId
    content_type: str
    title: str
    created_at: datetime
    updated_at: datetime
    stewardship_state: str
    current_version_id: ContentVersionId | None
    published_version_id: ContentVersionId | None
    teaching_work_id: UUID | None
    review_version_id: ContentVersionId | None


@dataclass(frozen=True, slots=True)
class TeacherLibraryDetail:
    content_id: ContentId
    content_type: str
    title: str
    created_at: datetime
    updated_at: datetime
    stewardship_state: str
    current_version_id: ContentVersionId | None
    published_version_id: ContentVersionId | None
    teaching_work_id: UUID | None
    review_version_id: ContentVersionId | None
    aggregate_revision: AggregateRevision


@dataclass(frozen=True, slots=True)
class TeacherLibraryVersion:
    content_id: ContentId
    version_id: ContentVersionId
    version_number: VersionNumber
    content_type: str
    title: str
    stewardship_state: str
    schema_id: str
    schema_version: int
    payload: Mapping[str, object]
    payload_sha256: str
    origin: str
    created_at: datetime
    published_version_id: ContentVersionId | None
    current_version_id: ContentVersionId | None
    teaching_work_id: UUID | None
    aggregate_revision: AggregateRevision


@dataclass(frozen=True, slots=True)
class ListTeacherLibraryQuery:
    limit: int
    content_type: str | None = None
    stewardship_state: str | None = None
    published_only: bool = False
    after_updated_at: datetime | None = None
    after_content_id: ContentId | None = None


@dataclass(frozen=True, slots=True)
class TeacherLibraryPage:
    items: tuple[TeacherLibraryItem, ...]
    has_more: bool
