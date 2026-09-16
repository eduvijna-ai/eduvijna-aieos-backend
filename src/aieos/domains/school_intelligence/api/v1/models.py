"""Principal School Intelligence HTTP v1 request/response models.

Completely distinct from Teacher Assessment Intelligence representations.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AssignmentLifecycleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: int = Field(ge=0)
    closed: int = Field(ge=0)
    cancelled: int = Field(ge=0)


class EvaluationCoverageAmongSubmittedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submitted_count: int = Field(ge=0)
    current_policy_evaluated_count: int = Field(ge=0)


class SchoolIntelligenceTimeWindowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    start: datetime | None
    end: datetime


class SchoolIntelligenceEvaluationPolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    policy_version: int = Field(ge=1)


class PrincipalSchoolIntelligenceSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    in_scope_class_count: int = Field(ge=0)
    classes_with_assignment_activity_count: int = Field(ge=0)
    teaching_assignment_count: int = Field(ge=0)
    assignment_lifecycle: AssignmentLifecycleResponse
    learner_submission_count: int = Field(ge=0)
    current_policy_evaluation_count: int = Field(ge=0)
    submitted_but_not_current_policy_evaluated_count: int = Field(ge=0)
    evaluation_coverage_among_submitted: EvaluationCoverageAmongSubmittedResponse
    classes_with_recorded_classroom_assessment_count: int = Field(ge=0)
    assignments_with_recorded_classroom_assessment_count: int = Field(ge=0)
    completed_teaching_execution_count: int = Field(ge=0)
    remediation_activity_count: int = Field(ge=0)


class PrincipalSchoolIntelligenceClassCardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_ref: str
    display_label: str
    has_assignment_activity: bool
    teaching_assignment_count: int = Field(ge=0)
    assignment_lifecycle: AssignmentLifecycleResponse
    learner_submission_count: int = Field(ge=0)
    current_policy_evaluation_count: int = Field(ge=0)
    submitted_but_not_current_policy_evaluated_count: int = Field(ge=0)
    evaluation_coverage_among_submitted: EvaluationCoverageAmongSubmittedResponse
    has_recorded_classroom_assessment: bool
    assignments_with_recorded_classroom_assessment_count: int = Field(ge=0)
    completed_teaching_execution_count: int = Field(ge=0)
    remediation_activity_count: int = Field(ge=0)


class PrincipalSchoolIntelligenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    projection_mode: str
    time_window: SchoolIntelligenceTimeWindowResponse
    evaluation_policy: SchoolIntelligenceEvaluationPolicyResponse
    sources: list[str]
    summary: PrincipalSchoolIntelligenceSummaryResponse
    classes: list[PrincipalSchoolIntelligenceClassCardResponse]
