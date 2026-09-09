"""LearnerAssessmentEvaluation aggregate — append-only Assessment evidence.

One immutable LearnerSubmission evaluated against one exact ContentVersion
under one explicit evaluation policy version. Not ClassroomAssessment.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from aieos.domains.assessment.domain.errors import (
    InvalidLearnerAssessmentEvaluationError,
)
from aieos.domains.assessment.domain.evaluation_vocabulary import (
    EvaluationMethod,
    ItemOutcome,
    ObjectiveEvidenceResult,
    parse_evaluation_method,
    parse_evaluation_question_type,
    parse_evaluation_response_kind,
    parse_item_outcome,
    parse_objective_evidence_result,
)
from aieos.domains.assessment.domain.identities import EvaluationId, require_foreign_uuid

MAX_CLASS_REF_LENGTH: Final = 512
MAX_QUESTION_ID_LENGTH: Final = 128
MAX_OBJECTIVE_ID_LENGTH: Final = 128
MAX_POLICY_ID_LENGTH: Final = 128
MAX_QUESTION_TYPE_LENGTH: Final = 64
MAX_RESPONSE_KIND_LENGTH: Final = 64


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidLearnerAssessmentEvaluationError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidLearnerAssessmentEvaluationError(
            f"{label} must be timezone-aware"
        )
    return value


def _require_text(value: str, *, label: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidLearnerAssessmentEvaluationError(
            f"{label} must be a non-empty string"
        )
    stripped = value.strip()
    if len(stripped) > max_length:
        raise InvalidLearnerAssessmentEvaluationError(
            f"{label} must be at most {max_length} characters"
        )
    return stripped


def _require_objective_ids(value: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise InvalidLearnerAssessmentEvaluationError(
            "objective_ids must be a sequence of strings"
        )
    seen: set[str] = set()
    rows: list[str] = []
    for item in value:
        oid = _require_text(
            item, label="objective_id", max_length=MAX_OBJECTIVE_ID_LENGTH
        )
        if oid in seen:
            continue
        seen.add(oid)
        rows.append(oid)
    return tuple(rows)


def _assert_outcome_method_pairing(
    outcome: ItemOutcome, method: EvaluationMethod, response_kind: str | None
) -> None:
    if outcome is ItemOutcome.UNANSWERED:
        if method is not EvaluationMethod.NO_RESPONSE:
            raise InvalidLearnerAssessmentEvaluationError(
                "UNANSWERED requires evaluation_method NO_RESPONSE"
            )
        if response_kind is not None:
            raise InvalidLearnerAssessmentEvaluationError(
                "UNANSWERED must not carry response_kind"
            )
        return
    if response_kind is None:
        raise InvalidLearnerAssessmentEvaluationError(
            "response_kind is required when a snapshot response exists"
        )
    if outcome in {ItemOutcome.CORRECT, ItemOutcome.INCORRECT}:
        if method is not EvaluationMethod.DETERMINISTIC_CONTENT_ANSWER:
            raise InvalidLearnerAssessmentEvaluationError(
                "CORRECT/INCORRECT requires DETERMINISTIC_CONTENT_ANSWER"
            )
        return
    if outcome is ItemOutcome.OPEN_RESPONSE_UNEVALUATED:
        if method is not EvaluationMethod.OPEN_RESPONSE_BASELINE:
            raise InvalidLearnerAssessmentEvaluationError(
                "OPEN_RESPONSE_UNEVALUATED requires OPEN_RESPONSE_BASELINE"
            )
        return
    if outcome is ItemOutcome.UNEVALUATED_POLICY_REJECT:
        if method is not EvaluationMethod.POLICY_REJECT:
            raise InvalidLearnerAssessmentEvaluationError(
                "UNEVALUATED_POLICY_REJECT requires POLICY_REJECT"
            )
        return
    raise InvalidLearnerAssessmentEvaluationError("unsupported item outcome pairing")


@dataclass(frozen=True, slots=True)
class LearnerAssessmentEvaluationItem:
    """One exact-version question evaluation. Immutable after creation."""

    question_id: str
    question_type: str
    outcome: ItemOutcome
    evaluation_method: EvaluationMethod
    objective_ids: tuple[str, ...]
    response_kind: str | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(
            self,
            "question_id",
            _require_text(
                self.question_id,
                label="question_id",
                max_length=MAX_QUESTION_ID_LENGTH,
            ),
        )
        parsed_type = parse_evaluation_question_type(self.question_type)
        question_type = (
            parsed_type.value
            if hasattr(parsed_type, "value")
            else str(parsed_type)
        )
        if len(question_type) > MAX_QUESTION_TYPE_LENGTH:
            raise InvalidLearnerAssessmentEvaluationError(
                f"question_type must be at most {MAX_QUESTION_TYPE_LENGTH} characters"
            )
        set_(self, "question_type", question_type)
        set_(self, "outcome", parse_item_outcome(self.outcome))
        set_(
            self,
            "evaluation_method",
            parse_evaluation_method(self.evaluation_method),
        )
        set_(self, "objective_ids", _require_objective_ids(self.objective_ids))
        if self.response_kind is None:
            response_kind = None
        else:
            parsed_kind = parse_evaluation_response_kind(self.response_kind)
            response_kind = (
                parsed_kind.value
                if hasattr(parsed_kind, "value")
                else str(parsed_kind)
            )
            if len(response_kind) > MAX_RESPONSE_KIND_LENGTH:
                raise InvalidLearnerAssessmentEvaluationError(
                    f"response_kind must be at most {MAX_RESPONSE_KIND_LENGTH} characters"
                )
        set_(self, "response_kind", response_kind)
        _assert_outcome_method_pairing(
            self.outcome, self.evaluation_method, self.response_kind
        )


@dataclass(frozen=True, slots=True)
class LearnerAssessmentObjectiveEvidence:
    """Submission-scoped objective rollup. Not long-term learner truth."""

    objective_id: str
    result: ObjectiveEvidenceResult

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(
            self,
            "objective_id",
            _require_text(
                self.objective_id,
                label="objective_id",
                max_length=MAX_OBJECTIVE_ID_LENGTH,
            ),
        )
        set_(self, "result", parse_objective_evidence_result(self.result))


@dataclass(frozen=True, slots=True)
class LearnerAssessmentEvaluation:
    """Append-only evaluation fact. No aggregate_revision. No UPDATE path."""

    evaluation_id: EvaluationId
    tenant_id: UUID
    learner_principal_id: UUID
    submission_id: UUID
    attempt_id: UUID
    teaching_assignment_id: UUID
    content_id: UUID
    content_version_id: UUID
    class_ref: str
    evaluation_policy_id: str
    evaluation_policy_version: int
    evaluated_at: datetime
    created_at: datetime
    items: tuple[LearnerAssessmentEvaluationItem, ...]
    objective_evidence: tuple[LearnerAssessmentObjectiveEvidence, ...]

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        if not isinstance(self.evaluation_id, EvaluationId):
            raise InvalidLearnerAssessmentEvaluationError(
                "evaluation_id must be an EvaluationId"
            )
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.learner_principal_id, label="learner_principal_id"
        )
        require_foreign_uuid(self.submission_id, label="submission_id")
        require_foreign_uuid(self.attempt_id, label="attempt_id")
        require_foreign_uuid(
            self.teaching_assignment_id, label="teaching_assignment_id"
        )
        require_foreign_uuid(self.content_id, label="content_id")
        require_foreign_uuid(self.content_version_id, label="content_version_id")
        set_(
            self,
            "class_ref",
            _require_text(
                self.class_ref, label="class_ref", max_length=MAX_CLASS_REF_LENGTH
            ),
        )
        set_(
            self,
            "evaluation_policy_id",
            _require_text(
                self.evaluation_policy_id,
                label="evaluation_policy_id",
                max_length=MAX_POLICY_ID_LENGTH,
            ),
        )
        if (
            isinstance(self.evaluation_policy_version, bool)
            or not isinstance(self.evaluation_policy_version, int)
            or self.evaluation_policy_version < 1
        ):
            raise InvalidLearnerAssessmentEvaluationError(
                "evaluation_policy_version must be a positive integer"
            )
        evaluated_at = _require_aware(self.evaluated_at, label="evaluated_at")
        created_at = _require_aware(self.created_at, label="created_at")
        if created_at != evaluated_at:
            raise InvalidLearnerAssessmentEvaluationError(
                "created_at must equal evaluated_at for an immutable insert"
            )
        set_(self, "evaluated_at", evaluated_at)
        set_(self, "created_at", created_at)
        if not isinstance(self.items, Sequence) or isinstance(self.items, (str, bytes)):
            raise InvalidLearnerAssessmentEvaluationError("items must be a sequence")
        if not isinstance(self.objective_evidence, Sequence) or isinstance(
            self.objective_evidence, (str, bytes)
        ):
            raise InvalidLearnerAssessmentEvaluationError(
                "objective_evidence must be a sequence"
            )
        items = tuple(self.items)
        seen_questions: set[str] = set()
        for item in items:
            if not isinstance(item, LearnerAssessmentEvaluationItem):
                raise InvalidLearnerAssessmentEvaluationError(
                    "items must be LearnerAssessmentEvaluationItem values"
                )
            if item.question_id in seen_questions:
                raise InvalidLearnerAssessmentEvaluationError(
                    "evaluation items cannot contain duplicate question_id"
                )
            seen_questions.add(item.question_id)
        evidence = tuple(self.objective_evidence)
        seen_objectives: set[str] = set()
        for row in evidence:
            if not isinstance(row, LearnerAssessmentObjectiveEvidence):
                raise InvalidLearnerAssessmentEvaluationError(
                    "objective_evidence must be LearnerAssessmentObjectiveEvidence"
                )
            if row.objective_id in seen_objectives:
                raise InvalidLearnerAssessmentEvaluationError(
                    "objective_evidence cannot contain duplicate objective_id"
                )
            seen_objectives.add(row.objective_id)
        set_(self, "items", items)
        set_(self, "objective_evidence", evidence)

    @classmethod
    def issue(
        cls,
        *,
        tenant_id: UUID,
        learner_principal_id: UUID,
        submission_id: UUID,
        attempt_id: UUID,
        teaching_assignment_id: UUID,
        content_id: UUID,
        content_version_id: UUID,
        class_ref: str,
        evaluation_policy_id: str,
        evaluation_policy_version: int,
        evaluated_at: datetime,
        items: Sequence[LearnerAssessmentEvaluationItem],
        objective_evidence: Sequence[LearnerAssessmentObjectiveEvidence],
        evaluation_id: EvaluationId | None = None,
    ) -> LearnerAssessmentEvaluation:
        eid = EvaluationId.generate() if evaluation_id is None else evaluation_id
        return cls(
            evaluation_id=eid,
            tenant_id=tenant_id,
            learner_principal_id=learner_principal_id,
            submission_id=submission_id,
            attempt_id=attempt_id,
            teaching_assignment_id=teaching_assignment_id,
            content_id=content_id,
            content_version_id=content_version_id,
            class_ref=class_ref,
            evaluation_policy_id=evaluation_policy_id,
            evaluation_policy_version=evaluation_policy_version,
            evaluated_at=evaluated_at,
            created_at=evaluated_at,
            items=tuple(items),
            objective_evidence=tuple(objective_evidence),
        )
