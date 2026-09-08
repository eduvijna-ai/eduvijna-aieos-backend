"""Pure Learning-domain submit transition.

Produces an updated LearnerAttempt + immutable LearnerSubmission from an
IN_PROGRESS attempt and current typed response items.

This is NOT an authorized Student submit command. It does not perform:

- current membership authorization
- ACTIVE HUMAN Principal authorization
- TeachingAssignment ACTIVE validation
- available_from validation
- TeachingAssignment row serialization

Those checks belong to S01-I03.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from aieos.domains.learning.domain.attempt import LearnerAttempt
from aieos.domains.learning.domain.errors import (
    AttemptAlreadySubmittedError,
    InvalidAttemptResponseError,
    InvalidLearnerSubmissionError,
)
from aieos.domains.learning.domain.identities import SubmissionId
from aieos.domains.learning.domain.lifecycle import AttemptLifecycleState
from aieos.domains.learning.domain.response_item import AttemptResponseItem
from aieos.domains.learning.domain.submission import (
    LearnerSubmission,
    canonical_response_snapshot,
)


def transition_in_progress_attempt_to_submitted(
    attempt: LearnerAttempt,
    responses: Sequence[AttemptResponseItem],
    *,
    submitted_at: datetime,
    assignment_revision_at_submit: int,
    due_at_at_submit: datetime | None,
    submission_id: SubmissionId | None = None,
) -> tuple[LearnerAttempt, LearnerSubmission]:
    """Pure IN_PROGRESS → SUBMITTED transition with an immutable snapshot.

    Not an authorized Student command. Server-controlled ``submitted_at`` and
    assignment snapshot fields are supplied by the caller.
    """
    if attempt.lifecycle_state is not AttemptLifecycleState.IN_PROGRESS:
        raise AttemptAlreadySubmittedError(
            "pure submit transition requires an IN_PROGRESS LearnerAttempt"
        )
    for item in responses:
        if not isinstance(item, AttemptResponseItem):
            raise InvalidAttemptResponseError(
                "submit responses must be AttemptResponseItem values"
            )
        if item.attempt_id != attempt.attempt_id:
            raise InvalidAttemptResponseError(
                "response item attempt_id must match the parent LearnerAttempt"
            )
    sid = SubmissionId.generate() if submission_id is None else submission_id
    submitted_attempt = attempt.bind_submitted_submission(
        submission_id=sid,
        submitted_at=submitted_at,
    )
    snapshot = canonical_response_snapshot(responses)
    submission = LearnerSubmission(
        submission_id=sid,
        tenant_id=attempt.tenant_id,
        attempt_id=attempt.attempt_id,
        learner_principal_id=attempt.learner_principal_id,
        teaching_assignment_id=attempt.teaching_assignment_id,
        content_id=attempt.content_id,
        content_version_id=attempt.content_version_id,
        class_ref=attempt.class_ref,
        response_snapshot=snapshot,
        submitted_at=submitted_attempt.submitted_at,
        assignment_revision_at_submit=assignment_revision_at_submit,
        due_at_at_submit=due_at_at_submit,
        created_at=submitted_attempt.submitted_at,
    )
    _assert_identity_parity(submitted_attempt, submission)
    return submitted_attempt, submission


def _assert_identity_parity(
    attempt: LearnerAttempt, submission: LearnerSubmission
) -> None:
    pairs: tuple[tuple[object, object, str], ...] = (
        (attempt.tenant_id, submission.tenant_id, "tenant_id"),
        (attempt.attempt_id, submission.attempt_id, "attempt_id"),
        (
            attempt.learner_principal_id,
            submission.learner_principal_id,
            "learner_principal_id",
        ),
        (
            attempt.teaching_assignment_id,
            submission.teaching_assignment_id,
            "teaching_assignment_id",
        ),
        (attempt.content_id, submission.content_id, "content_id"),
        (
            attempt.content_version_id,
            submission.content_version_id,
            "content_version_id",
        ),
        (attempt.class_ref, submission.class_ref, "class_ref"),
        (attempt.submission_id, submission.submission_id, "submission_id"),
        (attempt.submitted_at, submission.submitted_at, "submitted_at"),
    )
    for left, right, label in pairs:
        if left != right:
            raise InvalidLearnerSubmissionError(
                f"LearnerSubmission.{label} must exactly match the parent attempt"
            )
