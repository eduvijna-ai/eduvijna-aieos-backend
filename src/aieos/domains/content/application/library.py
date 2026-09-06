"""Teacher OS Library read services. No mutations. No Library SoR."""

from __future__ import annotations

from uuid import UUID

from aieos.domains.content.application.errors import (
    LibraryInvalidRequest,
    LibraryItemNotFound,
)
from aieos.domains.content.application.library_models import (
    ListTeacherLibraryQuery,
    TeacherLibraryDetail,
    TeacherLibraryPage,
    TeacherLibraryVersion,
)
from aieos.domains.content.application.ports import ContentUnitOfWorkFactory
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.states import StewardshipState

_MAX_LIBRARY_LIMIT = 100
_ALLOWED_STEWARDSHIP = frozenset(s.value for s in StewardshipState)


class ListTeacherLibraryService:
    def __init__(self, uow_factory: ContentUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def list(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        query: ListTeacherLibraryQuery,
    ) -> TeacherLibraryPage:
        if query.limit < 1 or query.limit > _MAX_LIBRARY_LIMIT:
            raise LibraryInvalidRequest(
                "library limit must be an integer from 1 to 100"
            )
        if query.stewardship_state is not None:
            if query.stewardship_state not in _ALLOWED_STEWARDSHIP:
                raise LibraryInvalidRequest(
                    "library stewardship_state must be a known stewardship state"
                )
        if query.content_type is not None and not query.content_type.strip():
            raise LibraryInvalidRequest("library content_type must be non-empty")
        with self._uow_factory(execution_tenant_id) as uow:
            rows = uow.library.list_page(
                owner_principal_id=principal_id,
                limit=query.limit + 1,
                content_type=query.content_type,
                stewardship_state=query.stewardship_state,
                published_only=query.published_only,
                after_updated_at=query.after_updated_at,
                after_content_id=query.after_content_id,
            )
        has_more = len(rows) > query.limit
        items = tuple(rows[: query.limit])
        return TeacherLibraryPage(items=items, has_more=has_more)


class GetTeacherLibraryItemService:
    def __init__(self, uow_factory: ContentUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        content_id: ContentId,
    ) -> TeacherLibraryDetail:
        with self._uow_factory(execution_tenant_id) as uow:
            found = uow.library.get_item(
                content_id,
                owner_principal_id=principal_id,
            )
        if found is None:
            raise LibraryItemNotFound(
                "Library item is not visible or does not exist for this teacher"
            )
        return found


class GetTeacherLibraryVersionService:
    """Owner-scoped open/preview. Generic content version GET is tenant-wide only."""

    def __init__(self, uow_factory: ContentUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
    ) -> TeacherLibraryVersion:
        with self._uow_factory(execution_tenant_id) as uow:
            found = uow.library.get_version(
                content_id,
                version_id,
                owner_principal_id=principal_id,
            )
        if found is None:
            raise LibraryItemNotFound(
                "Library version is not visible or does not exist for this teacher"
            )
        return found
