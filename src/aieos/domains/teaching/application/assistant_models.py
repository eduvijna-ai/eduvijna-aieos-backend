"""Teacher OS Assistant v1 application contracts (not HTTP DTOs)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from aieos.domains.teaching.domain.identities import WorkId


MAX_HISTORY_TURNS = 12
MAX_MESSAGE_LENGTH = 4000
MAX_HISTORY_CONTENT_LENGTH = 2000
MAX_CONTEXT_TEXT_CHARS = 12000
ASSISTANT_MAX_OUTPUT_TOKENS = 2000


@dataclass(frozen=True, slots=True)
class AssistantHistoryTurn:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class TeacherOsAssistantCommand:
    message: str
    history: tuple[AssistantHistoryTurn, ...]
    teaching_work_id: UUID | None
    mission_date: date


@dataclass(frozen=True, slots=True)
class AssistantMissionContext:
    mission_date: date
    hero_action_kind: str
    pending_review_count: int
    active_work_count: int
    continue_work_id: UUID | None
    continue_work_goal: str | None


@dataclass(frozen=True, slots=True)
class AssistantWorkContext:
    work_id: UUID
    intent_type: str
    goal_text: str
    class_label: str | None
    subject: str | None
    topic: str | None
    target_date: date
    aggregate_revision: int


@dataclass(frozen=True, slots=True)
class AssistantRemediationContext:
    source_assessment_id: UUID
    source_class_ref: str
    class_result_level_snapshot: str
    source_work_id: UUID | None
    source_execution_id: UUID | None


@dataclass(frozen=True, slots=True)
class AssistantArtifactContext:
    content_id: UUID
    version_id: UUID
    title: str
    content_type: str
    stewardship_state: str
    artifact_kind: str | None


@dataclass(frozen=True, slots=True)
class AssistantAssignmentContext:
    assignment_id: UUID
    class_ref: str
    status: str
    content_id: UUID
    content_version_id: UUID


@dataclass(frozen=True, slots=True)
class AssistantExecutionContext:
    execution_id: UUID
    class_ref: str
    status: str
    assignment_id: UUID | None


@dataclass(frozen=True, slots=True)
class AssistantAssessmentContext:
    assessment_id: UUID
    class_ref: str
    status: str
    class_result_level: str | None


@dataclass(frozen=True, slots=True)
class AssistantMemoryContext:
    memory_id: UUID
    schema_version: int
    teaching_style: str
    preferred_difficulty: str
    preparation_detail: str
    output_format: str
    include_differentiation: bool


@dataclass(frozen=True, slots=True)
class ComposedAssistantContext:
    """Server-composed, authorized, bounded context for one assistant turn."""

    mission: AssistantMissionContext | None
    work: AssistantWorkContext | None
    remediation: AssistantRemediationContext | None
    artifacts: tuple[AssistantArtifactContext, ...]
    assignments: tuple[AssistantAssignmentContext, ...]
    executions: tuple[AssistantExecutionContext, ...]
    assessments: tuple[AssistantAssessmentContext, ...]
    memory: AssistantMemoryContext | None


@dataclass(frozen=True, slots=True)
class TeacherOsAssistantResult:
    answer: str
    suggested_questions: tuple[str, ...]
    suggested_next_step: str | None
    teaching_work_id: WorkId | None
    context_summary: str
    generated_at: datetime
