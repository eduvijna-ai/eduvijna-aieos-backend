"""Fail-closed attestation validation for hint-bearing worksheet@2 APPROVE/publish."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from aieos.domains.content.application.errors import (
    WorksheetLearnerHintAttestationInvalid,
    WorksheetLearnerHintAttestationRequired,
    WorksheetLearnerHintPublicationBlocked,
)
from aieos.domains.content.application.ports import (
    ContentUnitOfWork,
    WorksheetHintAttestationRepository,
)
from aieos.domains.content.domain.version import ContentVersion
from aieos.domains.content.domain.worksheet_hint_attestation import (
    HintQuestionAttestationOutcome,
    WorksheetLearnerHintVersionAttestationV1,
)
from aieos.domains.education.worksheet_hint_digest import (
    worksheet_hint_attestation_digest_from_version_payload,
)
from aieos.domains.education.worksheet_hint_qa import (
    WORKSHEET_HINT_QA_POLICY_VERSION,
    assess_worksheet_hint_qa,
)
from aieos.domains.education.worksheet_hint_safety import (
    version_requires_worksheet_hint_attestation,
)
from aieos.domains.content.domain.review import ReviewDecisionCode
from aieos.domains.education.worksheet_v2 import parse_worksheet_v2
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)


def _payload_mapping(version: ContentVersion) -> Mapping[str, object]:
    body = version.payload.body
    if not isinstance(body, Mapping):
        return {}
    return body


def hinted_question_ids(payload: Mapping[str, object]) -> list[str]:
    worksheet = parse_worksheet_v2(dict(payload))
    return [question.id for question in worksheet.questions if question.learner_hint is not None]


def outcomes_from_command(
    hinted_ids: Sequence[str],
    submitted: Sequence[Mapping[str, object]],
) -> tuple[HintQuestionAttestationOutcome, ...]:
    if len(submitted) != len(hinted_ids):
        raise WorksheetLearnerHintAttestationInvalid(
            "attestation outcomes must cover every hinted question exactly once"
        )
    by_id: dict[str, HintQuestionAttestationOutcome] = {}
    for item in submitted:
        question_id = item.get("question_id")
        if not isinstance(question_id, str):
            raise WorksheetLearnerHintAttestationInvalid("question_id is required")
        if question_id in by_id:
            raise WorksheetLearnerHintAttestationInvalid("duplicate question_id in outcomes")
        for key in ("hard_block_clear", "human_review_pass"):
            if key not in item or not isinstance(item[key], bool):
                raise WorksheetLearnerHintAttestationInvalid(
                    f"{key} must be an explicit boolean"
                )
        by_id[question_id] = HintQuestionAttestationOutcome(
            question_id=question_id,
            hard_block_clear=bool(item["hard_block_clear"]),
            human_review_pass=bool(item["human_review_pass"]),
        )
    ordered: list[HintQuestionAttestationOutcome] = []
    for question_id in hinted_ids:
        outcome = by_id.get(question_id)
        if outcome is None:
            raise WorksheetLearnerHintAttestationInvalid(
                "attestation outcomes must match hinted question IDs for this version"
            )
        ordered.append(outcome)
    extra = set(by_id) - set(hinted_ids)
    if extra:
        raise WorksheetLearnerHintAttestationInvalid(
            "attestation outcomes contain unknown question IDs"
        )
    return tuple(ordered)


def assert_attestation_outcomes_against_qa(
    *,
    payload: Mapping[str, object],
    outcomes: Sequence[HintQuestionAttestationOutcome],
) -> None:
    assessments = {item.question_id: item for item in assess_worksheet_hint_qa(payload)}
    for outcome in outcomes:
        assessment = assessments.get(outcome.question_id)
        if assessment is None:
            raise WorksheetLearnerHintAttestationInvalid(
                "attestation includes a question without a learner_hint"
            )
        if not assessment.hard_block_clear or not outcome.hard_block_clear:
            raise WorksheetLearnerHintAttestationInvalid(
                "hard_block_clear must be true when mechanical QA passes"
            )
        if assessment.flags and not outcome.human_review_pass:
            raise WorksheetLearnerHintAttestationInvalid(
                "human_review_pass is required when QA flags are present"
            )
        if not outcome.human_review_pass:
            raise WorksheetLearnerHintAttestationInvalid(
                "human_review_pass must be true for attestation"
            )


def load_valid_attestation(
    repo: WorksheetHintAttestationRepository,
    version: ContentVersion,
) -> WorksheetLearnerHintVersionAttestationV1:
    attestation = repo.get_for_version(version.content_id, version.version_id)
    if attestation is None:
        raise WorksheetLearnerHintAttestationRequired(
            "hint-bearing worksheet requires learner-hint attestation before APPROVE"
        )
    if attestation.tenant_id != version.tenant_id:
        raise WorksheetLearnerHintAttestationInvalid("attestation tenant mismatch")
    expected_digest = worksheet_hint_attestation_digest_from_version_payload(
        _payload_mapping(version)
    )
    if attestation.payload_digest != expected_digest:
        raise WorksheetLearnerHintAttestationInvalid("attestation payload_digest mismatch")
    if attestation.qa_policy_version != WORKSHEET_HINT_QA_POLICY_VERSION:
        raise WorksheetLearnerHintAttestationInvalid("attestation QA policy version mismatch")
    if attestation.author_principal_id != version.created_by_principal_id:
        raise WorksheetLearnerHintAttestationInvalid("attestation author mismatch")
    assert_attestation_outcomes_against_qa(
        payload=_payload_mapping(version),
        outcomes=attestation.per_question_outcomes,
    )
    return attestation


def enforce_worksheet_hint_review_interlock(
    uow: ContentUnitOfWork,
    version: ContentVersion,
    *,
    reviewer_principal_id: UUID,
    principal_classification: CurrentPrincipalClassificationAuthority | None = None,
    effective_actor_id: UUID | None = None,
) -> None:
    if not version_requires_worksheet_hint_attestation(
        schema_id=str(version.schema_id),
        schema_version=int(version.schema_version),
        payload=_payload_mapping(version),
    ):
        return
    if principal_classification is not None:
        principal_classification.require_current_human_principal(reviewer_principal_id)
    if effective_actor_id is not None and effective_actor_id != reviewer_principal_id:
        raise WorksheetLearnerHintAttestationInvalid(
            "effective actor must align with reviewer principal"
        )
    attestation = load_valid_attestation(uow.worksheet_hint_attestations, version)
    if attestation.reviewer_principal_id != reviewer_principal_id:
        raise WorksheetLearnerHintAttestationInvalid(
            "reviewer principal does not match stored attestation"
        )
    if attestation.reviewer_principal_id == attestation.author_principal_id:
        raise WorksheetLearnerHintAttestationInvalid(
            "reviewer must be distinct from hint version author"
        )


def enforce_worksheet_hint_publish_interlock(
    uow: ContentUnitOfWork,
    version: ContentVersion,
    *,
    publisher_principal_id: UUID,
    principal_classification: CurrentPrincipalClassificationAuthority | None = None,
    effective_actor_id: UUID | None = None,
) -> None:
    if not version_requires_worksheet_hint_attestation(
        schema_id=str(version.schema_id),
        schema_version=int(version.schema_version),
        payload=_payload_mapping(version),
    ):
        return
    if principal_classification is not None:
        principal_classification.require_current_human_principal(publisher_principal_id)
    if effective_actor_id is not None and effective_actor_id != publisher_principal_id:
        raise WorksheetLearnerHintPublicationBlocked(
            "effective actor must align with publisher principal"
        )
    attestation = load_valid_attestation(uow.worksheet_hint_attestations, version)
    decision = uow.reviews.get_for_version(version.content_id, version.version_id)
    if decision is None or decision.decision is not ReviewDecisionCode.APPROVE:
        raise WorksheetLearnerHintPublicationBlocked(
            "publish requires exact-version APPROVE ReviewDecision"
        )
    if decision.reviewer_principal_id != attestation.reviewer_principal_id:
        raise WorksheetLearnerHintPublicationBlocked(
            "approve reviewer does not match stored attestation reviewer"
        )
    if publisher_principal_id in {
        attestation.author_principal_id,
        attestation.reviewer_principal_id,
    }:
        raise WorksheetLearnerHintPublicationBlocked(
            "publisher must be distinct from hint author and reviewer"
        )
