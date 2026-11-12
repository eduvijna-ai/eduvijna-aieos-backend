"""Runtime-governed worksheet learner-hint extraction (ADR-063 §8.2 B7)."""

from __future__ import annotations

from uuid import UUID

from aieos.domains.content.application.errors import (
    WorksheetLearnerHintAttestationInvalid,
    WorksheetLearnerHintAttestationRequired,
)
from aieos.domains.content.application.worksheet_hint_attestation_gate import (
    load_valid_attestation,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.review import ReviewDecisionCode
from aieos.domains.content.domain.version import thaw_json_value
from aieos.domains.education.schema import (
    WORKSHEET_CONTENT_TYPE,
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION,
    WORKSHEET_SCHEMA_VERSION_V2,
)
from aieos.domains.education.worksheet_hint_safety import version_requires_worksheet_hint_attestation
from aieos.domains.education.worksheet_v2 import WorksheetV2, parse_worksheet_v2
from aieos.domains.learning.application.errors import (
    ContentNotLearnerConsumable,
    ExactContentVersionNotFound,
)
from aieos.domains.learning.application.models import LearnerHintItem


def load_governed_worksheet_learner_hints(
    uow,
    *,
    execution_tenant_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
) -> tuple[LearnerHintItem, ...]:
    try:
        typed_content_id = ContentId(content_id)
        typed_version_id = ContentVersionId(content_version_id)
    except Exception as exc:
        raise ExactContentVersionNotFound(
            "exact assigned ContentVersion is not visible"
        ) from exc

    content = uow.contents.get(typed_content_id)
    version = uow.content_versions.get(typed_version_id)
    if content is None or version is None:
        raise ExactContentVersionNotFound(
            "exact assigned ContentVersion is not visible"
        )
    if (
        version.content_id.value != content_id
        or version.tenant_id != execution_tenant_id
    ):
        raise ExactContentVersionNotFound(
            "exact assigned ContentVersion is not visible"
        )
    if str(content.content_type) != WORKSHEET_CONTENT_TYPE:
        raise ContentNotLearnerConsumable(
            "exact assigned ContentVersion is not learner-consumable"
        )

    publication = uow.publications.get_for_version(typed_content_id, typed_version_id)
    if publication is None:
        raise ContentNotLearnerConsumable(
            "exact assigned ContentVersion is not learner-consumable"
        )
    decision = uow.reviews.get_for_version(typed_content_id, typed_version_id)
    if decision is None or decision.decision is not ReviewDecisionCode.APPROVE:
        raise ContentNotLearnerConsumable(
            "exact assigned ContentVersion is not learner-consumable"
        )

    schema_id = str(version.schema_id)
    schema_version = int(version.schema_version)
    if schema_id != WORKSHEET_SCHEMA_ID:
        raise ContentNotLearnerConsumable(
            "exact assigned ContentVersion is not learner-consumable"
        )
    if schema_version == WORKSHEET_SCHEMA_VERSION:
        return ()
    if schema_version != WORKSHEET_SCHEMA_VERSION_V2:
        raise ContentNotLearnerConsumable(
            "exact assigned ContentVersion is not learner-consumable"
        )

    payload = thaw_json_value(version.payload.body)
    if not isinstance(payload, dict):
        raise ContentNotLearnerConsumable(
            "exact assigned ContentVersion is not learner-consumable"
        )
    if version_requires_worksheet_hint_attestation(
        schema_id=schema_id,
        schema_version=schema_version,
        payload=payload,
    ):
        try:
            load_valid_attestation(uow.worksheet_hint_attestations, version)
        except (
            WorksheetLearnerHintAttestationRequired,
            WorksheetLearnerHintAttestationInvalid,
        ) as exc:
            raise ContentNotLearnerConsumable(
                "exact assigned ContentVersion is not learner-consumable"
            ) from exc

    try:
        worksheet = parse_worksheet_v2(dict(payload))
        WorksheetV2.model_validate(dict(payload))
    except Exception as exc:
        raise ContentNotLearnerConsumable(
            "exact assigned ContentVersion is not learner-consumable"
        ) from exc

    hints: list[LearnerHintItem] = []
    for question in worksheet.questions:
        hint = question.learner_hint
        if hint is not None:
            hints.append(LearnerHintItem(question_id=question.id, hint_text=hint))
    return tuple(hints)
