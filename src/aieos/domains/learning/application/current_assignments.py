"""Student current-assignment read façade.

Visibility is current HUMAN learner + membership ClassRefs + tenant +
ACTIVE TeachingAssignment with available_from <= now. Teacher ownership
is not used. due_at in the past remains consumable while ACTIVE.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from aieos.domains.learning.application.command_support import (
    load_learner_resource,
    require_human_learner,
    require_visible_current_assignment,
)
from aieos.domains.learning.application.errors import AssignmentNotFound, InvalidLearnerRequest
from aieos.domains.learning.application.learner_membership import (
    ListCurrentLearnerMembershipsService,
    SchoolContextLearnerMembershipReader,
)
from aieos.domains.learning.application.models import (
    ATTEMPT_SUMMARY_IN_PROGRESS,
    StudentAssignmentListResult,
    StudentAssignmentReadModel,
    StudentHomeReadModel,
    currently_consumable,
    derive_attempt_summary,
)
from aieos.platform.runtime.student_learning_command import (
    SqlAlchemyStudentLearningCommandUnitOfWorkFactory,
)
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100
HOME_SLICE = 5
_CANDIDATE_FETCH_LIMIT = 500


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


class ListCurrentAssignmentsService:
    def __init__(
        self,
        uow_factory: SqlAlchemyStudentLearningCommandUnitOfWorkFactory,
        membership_reader: SchoolContextLearnerMembershipReader,
        classification: CurrentPrincipalClassificationAuthority,
    ) -> None:
        self._uow_factory = uow_factory
        self._membership_reader = membership_reader
        self._classification = classification

    def list(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        now: datetime | None = None,
        include_resource: bool = False,
    ) -> StudentAssignmentListResult:
        require_human_learner(self._classification, principal_id)
        if limit < 1 or limit > MAX_LIST_LIMIT:
            raise InvalidLearnerRequest("list limit exceeds the maximum of 100")
        observed = _now(now)
        memberships = ListCurrentLearnerMembershipsService(self._membership_reader).list(
            execution_tenant_id, principal_id
        )
        class_refs = tuple(item.class_ref for item in memberships)
        with self._uow_factory(execution_tenant_id) as uow:
            rows = uow.list_assignments_for_class_refs(
                class_refs, limit=_CANDIDATE_FETCH_LIMIT
            )
            visible = [row for row in rows if currently_consumable(row, observed)]
            has_more = len(visible) > limit
            page = visible[:limit]
            attempts = uow.attempts.list_for_learner(
                principal_id, [item.assignment_id for item in page]
            )
            by_assignment: dict[UUID, list] = {}
            for attempt in attempts:
                by_assignment.setdefault(attempt.teaching_assignment_id, []).append(
                    attempt
                )
            items: list[StudentAssignmentReadModel] = []
            for assignment in page:
                related = tuple(by_assignment.get(assignment.assignment_id, ()))
                summary = derive_attempt_summary(related)
                in_progress = next(
                    (
                        item
                        for item in related
                        if item.lifecycle_state.value == ATTEMPT_SUMMARY_IN_PROGRESS
                    ),
                    None,
                )
                submitted = next(
                    (
                        item
                        for item in related
                        if item.lifecycle_state.value == "SUBMITTED"
                    ),
                    None,
                )
                attempt_id = None
                if in_progress is not None:
                    attempt_id = in_progress.attempt_id.value
                elif submitted is not None:
                    attempt_id = submitted.attempt_id.value
                resource = None
                if include_resource:
                    resource = load_learner_resource(uow, assignment)
                items.append(
                    StudentAssignmentReadModel(
                        assignment_id=assignment.assignment_id,
                        class_ref=assignment.class_ref,
                        available_from=assignment.available_from,
                        due_at=assignment.due_at,
                        lifecycle_state=assignment.lifecycle_state,
                        currently_consumable=True,
                        content_id=assignment.content_id,
                        content_version_id=assignment.content_version_id,
                        attempt_summary=summary,
                        attempt_id=attempt_id,
                        resource=resource,
                    )
                )
        return StudentAssignmentListResult(items=tuple(items), has_more=has_more)


class GetCurrentAssignmentService:
    def __init__(
        self,
        uow_factory: SqlAlchemyStudentLearningCommandUnitOfWorkFactory,
        membership_reader: SchoolContextLearnerMembershipReader,
        classification: CurrentPrincipalClassificationAuthority,
    ) -> None:
        self._uow_factory = uow_factory
        self._membership_reader = membership_reader
        self._classification = classification

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        assignment_id: UUID,
        *,
        now: datetime | None = None,
    ) -> StudentAssignmentReadModel:
        require_human_learner(self._classification, principal_id)
        observed = _now(now)
        memberships = ListCurrentLearnerMembershipsService(self._membership_reader).list(
            execution_tenant_id, principal_id
        )
        class_refs = {item.class_ref for item in memberships}
        with self._uow_factory(execution_tenant_id) as uow:
            assignment = uow.get_assignment(assignment_id)
            if assignment is None:
                raise AssignmentNotFound("TeachingAssignment is not visible")
            require_visible_current_assignment(assignment, class_refs, now=observed)
            related = tuple(
                uow.attempts.list_for_learner_assignment(
                    principal_id, assignment.assignment_id
                )
            )
            summary = derive_attempt_summary(related)
            in_progress = next(
                (
                    item
                    for item in related
                    if item.lifecycle_state.value == ATTEMPT_SUMMARY_IN_PROGRESS
                ),
                None,
            )
            submitted = next(
                (item for item in related if item.lifecycle_state.value == "SUBMITTED"),
                None,
            )
            attempt_id = None
            if in_progress is not None:
                attempt_id = in_progress.attempt_id.value
            elif submitted is not None:
                attempt_id = submitted.attempt_id.value
            resource = load_learner_resource(uow, assignment)
        return StudentAssignmentReadModel(
            assignment_id=assignment.assignment_id,
            class_ref=assignment.class_ref,
            available_from=assignment.available_from,
            due_at=assignment.due_at,
            lifecycle_state=assignment.lifecycle_state,
            currently_consumable=True,
            content_id=assignment.content_id,
            content_version_id=assignment.content_version_id,
            attempt_summary=summary,
            attempt_id=attempt_id,
            resource=resource,
        )


class GetStudentHomeService:
    def __init__(self, list_service: ListCurrentAssignmentsService) -> None:
        self._list_service = list_service

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        now: datetime | None = None,
    ) -> StudentHomeReadModel:
        listed = self._list_service.list(
            execution_tenant_id,
            principal_id,
            limit=MAX_LIST_LIMIT,
            now=now,
        )
        return StudentHomeReadModel(
            current_assignment_count=len(listed.items),
            items=listed.items[:HOME_SLICE],
        )
