"""Assessment-owned copies of Learning/Teaching facts used for evaluation.

These are not Learning or Teaching aggregates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.assessment.domain.evaluation_input import EvaluationSubmittedResponse


@dataclass(frozen=True, slots=True)
class EvaluationSubmissionView:
    submission_id: UUID
    tenant_id: UUID
    attempt_id: UUID
    learner_principal_id: UUID
    teaching_assignment_id: UUID
    content_id: UUID
    content_version_id: UUID
    class_ref: str
    responses: tuple[EvaluationSubmittedResponse, ...]
    submitted_at: datetime


@dataclass(frozen=True, slots=True)
class EvaluationAssignmentView:
    assignment_id: UUID
    tenant_id: UUID
    class_ref: str
    content_id: UUID
    content_version_id: UUID
    teacher_principal_id: UUID
    lifecycle_state: str
