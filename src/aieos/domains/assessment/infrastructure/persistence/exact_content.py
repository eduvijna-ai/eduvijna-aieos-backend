"""Exact immutable ContentVersion question universe for evaluation.

Does not follow published_version_id. Keeps answer keys for the evaluator.
Does not expose those answers through HTTP.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.engine import Connection

from aieos.domains.assessment.application.errors import (
    ContentNotFoundForAssessment,
    EvaluationContentUnsupported,
    EvaluationLineageConflict,
)
from aieos.domains.assessment.domain.evaluation_input import EvaluationContentQuestion
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.version import thaw_json_value
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyContentRepository,
    SqlAlchemyContentVersionRepository,
)
from aieos.domains.education.content_payloads_v1 import HomeworkV1, QuizV1
from aieos.domains.education.schema import (
    HOMEWORK_CONTENT_TYPE,
    HOMEWORK_SCHEMA_ID,
    HOMEWORK_SCHEMA_VERSION,
    QUIZ_CONTENT_TYPE,
    QUIZ_SCHEMA_ID,
    QUIZ_SCHEMA_VERSION,
    WORKSHEET_CONTENT_TYPE,
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION,
)
from aieos.domains.education.worksheet_v1 import WorksheetV1

_SUPPORTED: dict[tuple[str, str, int], object] = {
    (WORKSHEET_CONTENT_TYPE, WORKSHEET_SCHEMA_ID, WORKSHEET_SCHEMA_VERSION): WorksheetV1,
    (QUIZ_CONTENT_TYPE, QUIZ_SCHEMA_ID, QUIZ_SCHEMA_VERSION): QuizV1,
    (HOMEWORK_CONTENT_TYPE, HOMEWORK_SCHEMA_ID, HOMEWORK_SCHEMA_VERSION): HomeworkV1,
}


class SqlAlchemyAssessmentExactContentAdapter:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._execution_tenant_id = execution_tenant_id
        self._contents = SqlAlchemyContentRepository(connection, execution_tenant_id)
        self._versions = SqlAlchemyContentVersionRepository(connection)

    def load_evaluation_questions(
        self, *, content_id: UUID, content_version_id: UUID
    ) -> tuple[EvaluationContentQuestion, ...]:
        content = self._contents.get(ContentId(content_id))
        if content is None:
            raise ContentNotFoundForAssessment(
                "Content is not visible in the execution tenant"
            )
        version = self._versions.get(ContentVersionId(content_version_id))
        if version is None or version.tenant_id != self._execution_tenant_id:
            raise ContentNotFoundForAssessment(
                "exact ContentVersion is not visible in the execution tenant"
            )
        if version.content_id.value != content_id:
            raise EvaluationLineageConflict(
                "exact ContentVersion does not belong to the bound content_id"
            )
        content_type = str(content.content_type)
        schema_id = str(version.schema_id)
        schema_version = int(version.schema_version)
        model_cls = _SUPPORTED.get(
            (content_type, schema_id, schema_version)
        )
        if model_cls is None:
            raise EvaluationContentUnsupported(
                "exact ContentVersion is not evaluation-capable"
            )
        payload = thaw_json_value(version.payload.body)
        if not isinstance(payload, dict):
            raise EvaluationContentUnsupported(
                "exact ContentVersion payload is not a JSON object"
            )
        try:
            model = model_cls.model_validate(payload)
        except ValidationError as exc:
            raise EvaluationContentUnsupported(
                "exact ContentVersion payload is not a governed evaluation contract"
            ) from exc
        return tuple(
            EvaluationContentQuestion(
                question_id=question.id,
                question_type=question.question_type.value,
                options=tuple(question.options),
                answer=question.answer,
                objective_ids=tuple(question.objective_ids),
            )
            for question in model.questions
        )
