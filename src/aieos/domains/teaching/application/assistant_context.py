"""Server-side Teacher OS Assistant context composer.

Reads only currently authorized, teacher-owned / applicable facts.
Never trusts client context snapshots. Missing optional sources degrade gracefully.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from aieos.domains.assessment.application.models import ListClassroomAssessmentsQuery
from aieos.domains.assessment.application.queries import ListClassroomAssessmentsService
from aieos.domains.teaching.application.artifacts import ListTeachingWorkArtifactsService
from aieos.domains.teaching.application.assistant_models import (
    AssistantArtifactContext,
    AssistantAssessmentContext,
    AssistantAssignmentContext,
    AssistantExecutionContext,
    AssistantMemoryContext,
    AssistantMissionContext,
    AssistantRemediationContext,
    AssistantWorkContext,
    ComposedAssistantContext,
)
from aieos.domains.teaching.application.errors import (
    TeacherMemoryNotFound,
    TeachingWorkForbidden,
    TeachingWorkNotFound,
)
from aieos.domains.teaching.application.memory_queries import GetTeacherMemoryService
from aieos.domains.teaching.application.mission import GetTeacherOsTodayMissionService
from aieos.domains.teaching.application.owner_resolution import (
    HumanPrincipalClassificationGate,
    require_human_teacher_owner,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.application.queries import GetTeachingWorkService
from aieos.domains.teaching.domain.identities import WorkId
from aieos.platform.security.audit import SecurityAuditExecutionChannel

_ARTIFACT_LIMIT = 8
_ASSIGNMENT_LIMIT = 5
_EXECUTION_LIMIT = 5
_ASSESSMENT_LIMIT = 5


def _enum_text(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


class ComposeTeacherOsAssistantContextService:
    """Compose bounded Assistant context after HUMAN gate."""

    def __init__(
        self,
        *,
        teaching_uow_factory: TeachingUnitOfWorkFactory,
        principal_classification: HumanPrincipalClassificationGate,
        mission_service: GetTeacherOsTodayMissionService,
        get_work_service: GetTeachingWorkService,
        get_memory_service: GetTeacherMemoryService,
        list_artifacts_service: ListTeachingWorkArtifactsService | None = None,
        list_assessments_service: ListClassroomAssessmentsService | None = None,
    ) -> None:
        self._uow_factory = teaching_uow_factory
        self._principal_classification = principal_classification
        self._mission = mission_service
        self._get_work = get_work_service
        self._get_memory = get_memory_service
        self._list_artifacts = list_artifacts_service
        self._list_assessments = list_assessments_service

    def compose(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        mission_date: date,
        teaching_work_id: UUID | None,
    ) -> tuple[UUID, ComposedAssistantContext]:
        teacher_principal_id = require_human_teacher_owner(
            calling_principal_id=principal_id,
            classification=self._principal_classification,
            effective_actor_id=None,
            execution_channel=SecurityAuditExecutionChannel.API,
        )

        mission_ctx: AssistantMissionContext | None = None
        try:
            mission = self._mission.get(
                execution_tenant_id,
                teacher_principal_id,
                mission_date=mission_date,
            )
            continue_work = mission.preparation.continue_work
            mission_ctx = AssistantMissionContext(
                mission_date=mission.mission_date,
                hero_action_kind=mission.hero_action.kind_value,
                pending_review_count=mission.review.pending_count,
                active_work_count=mission.preparation.active_work_count,
                continue_work_id=(
                    None if continue_work is None else continue_work.work_id.value
                ),
                continue_work_goal=(
                    None if continue_work is None else continue_work.goal_text
                ),
            )
        except Exception:
            mission_ctx = None

        work_ctx: AssistantWorkContext | None = None
        remediation_ctx: AssistantRemediationContext | None = None
        artifacts: list[AssistantArtifactContext] = []
        assignments: list[AssistantAssignmentContext] = []
        executions: list[AssistantExecutionContext] = []
        assessments: list[AssistantAssessmentContext] = []

        selected_work_id = teaching_work_id
        if selected_work_id is None and mission_ctx is not None:
            selected_work_id = mission_ctx.continue_work_id

        if selected_work_id is not None:
            # Re-read + re-authorize. Foreign teacher / missing work fail closed.
            work = self._get_work.get(
                execution_tenant_id,
                teacher_principal_id,
                WorkId(selected_work_id),
            )
            work_ctx = AssistantWorkContext(
                work_id=work.work_id.value,
                intent_type=work.intent_type,
                goal_text=work.goal_text,
                class_label=work.class_label,
                subject=work.subject,
                topic=work.topic,
                target_date=work.target_date,
                aggregate_revision=int(work.aggregate_revision),
            )

            with self._uow_factory(execution_tenant_id) as uow:
                origin = uow.remediation_origins.get(WorkId(selected_work_id))
                if origin is not None:
                    remediation_ctx = AssistantRemediationContext(
                        source_assessment_id=origin.source_assessment_id,
                        source_class_ref=origin.source_class_ref,
                        class_result_level_snapshot=_enum_text(
                            origin.source_class_result_level_snapshot
                        ),
                        source_work_id=(
                            None
                            if origin.source_work_id is None
                            else origin.source_work_id.value
                        ),
                        source_execution_id=(
                            None
                            if origin.source_execution_id is None
                            else origin.source_execution_id.value
                        ),
                    )
                assignment_rows = uow.assignments.list_for_teacher(
                    teacher_principal_id=teacher_principal_id,
                    limit=_ASSIGNMENT_LIMIT,
                    source_work_id=WorkId(selected_work_id),
                )
                execution_rows = uow.executions.list_for_teacher(
                    teacher_principal_id=teacher_principal_id,
                    limit=_EXECUTION_LIMIT,
                    work_id=WorkId(selected_work_id),
                )

            for row in assignment_rows:
                assignments.append(
                    AssistantAssignmentContext(
                        assignment_id=row.assignment_id.value,
                        class_ref=row.class_ref,
                        status=_enum_text(row.lifecycle_state),
                        content_id=row.content_id,
                        content_version_id=row.content_version_id,
                    )
                )
            for row in execution_rows:
                executions.append(
                    AssistantExecutionContext(
                        execution_id=row.execution_id.value,
                        class_ref=row.class_ref,
                        status=_enum_text(row.lifecycle_state),
                        assignment_id=None,
                    )
                )

            if self._list_artifacts is not None:
                try:
                    result = self._list_artifacts.list(
                        execution_tenant_id,
                        teacher_principal_id,
                        WorkId(selected_work_id),
                    )
                    for item in result.items[:_ARTIFACT_LIMIT]:
                        artifacts.append(
                            AssistantArtifactContext(
                                content_id=item.content_id,
                                version_id=item.version_id,
                                title=item.title,
                                content_type=item.content_type,
                                stewardship_state=item.stewardship_state,
                                artifact_kind=item.artifact_kind,
                            )
                        )
                except (TeachingWorkNotFound, TeachingWorkForbidden):
                    artifacts = []
                except Exception:
                    artifacts = []

            if self._list_assessments is not None:
                class_ref = None
                if assignments:
                    class_ref = assignments[0].class_ref
                elif executions:
                    class_ref = executions[0].class_ref
                if class_ref is not None:
                    try:
                        listed = self._list_assessments.list(
                            execution_tenant_id,
                            teacher_principal_id,
                            ListClassroomAssessmentsQuery(
                                limit=_ASSESSMENT_LIMIT,
                                class_ref=class_ref,
                                work_id=selected_work_id,
                            ),
                        )
                        for item in listed.items[:_ASSESSMENT_LIMIT]:
                            assessments.append(
                                AssistantAssessmentContext(
                                    assessment_id=item.assessment_id,
                                    class_ref=item.class_ref,
                                    status=_enum_text(item.lifecycle_state),
                                    class_result_level=item.class_result_level,
                                )
                            )
                    except Exception:
                        assessments = []

        memory_ctx: AssistantMemoryContext | None = None
        try:
            memory = self._get_memory.get(execution_tenant_id, teacher_principal_id)
            prefs = memory.preferences
            memory_ctx = AssistantMemoryContext(
                memory_id=memory.memory_id,
                schema_version=memory.schema_version,
                teaching_style=_enum_text(prefs.teaching_style),
                preferred_difficulty=_enum_text(prefs.preferred_difficulty),
                preparation_detail=_enum_text(prefs.preparation_detail),
                output_format=_enum_text(prefs.output_format),
                include_differentiation=bool(prefs.include_differentiation),
            )
        except TeacherMemoryNotFound:
            memory_ctx = None

        return teacher_principal_id, ComposedAssistantContext(
            mission=mission_ctx,
            work=work_ctx,
            remediation=remediation_ctx,
            artifacts=tuple(artifacts),
            assignments=tuple(assignments),
            executions=tuple(executions),
            assessments=tuple(assessments),
            memory=memory_ctx,
        )
