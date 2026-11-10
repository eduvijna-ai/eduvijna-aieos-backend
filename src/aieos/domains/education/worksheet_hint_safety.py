"""Worksheet@2 learner_hint bypass closure, hard blocks, and review interlock (ADR-063 T1)."""

from __future__ import annotations

import re
from collections.abc import Mapping
_WORKSHEET_SCHEMA_ID = "education.worksheet"
_WORKSHEET_SCHEMA_VERSION_V2 = 2

_URL_PATTERN = re.compile(
    r"(?i)(https?://|www\.|[a-z0-9][a-z0-9\-]*\.[a-z]{2,}(/|\s|$))",
)
_EMAIL_PATTERN = re.compile(r"(?i)[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}")
_MAILTO_PATTERN = re.compile(r"(?i)mailto:")
_HTML_PATTERN = re.compile(r"<\s*/?\s*[a-zA-Z]")
_MARKDOWN_PATTERNS = (
    re.compile(r"\*\*.+\*\*"),
    re.compile(r"__.+__"),
    re.compile(r"`[^`]+`"),
    re.compile(r"\[[^\]]+\]\([^)]+\)"),
    re.compile(r"(?m)^\s*#{1,6}\s"),
)
_PROHIBITED_VOCAB = re.compile(
    r"(?i)\b(score|graded|grade|grades|grading|mastery|evaluation|answer\s*key)\b",
)


class LearnerHintValidationError(ValueError):
    """Schema-level learner_hint rejection without leaking payload details."""


class LearnerHintHardBlockError(ValueError):
    """ADR §5.2 mechanical hard block for a non-empty learner_hint."""


def worksheet_v2_payload_has_learner_hint_key(payload: Mapping[str, object]) -> bool:
    """Return True when any question object contains a learner_hint key (§7.5)."""
    questions = payload.get("questions")
    if not isinstance(questions, (list, tuple)):
        return False
    for item in questions:
        if isinstance(item, Mapping) and "learner_hint" in item:
            return True
    return False


def assert_generic_worksheet_v2_hint_append_forbidden(
    *,
    schema_id: str,
    schema_version: int,
    payload: Mapping[str, object],
) -> None:
    """Fail closed before idempotency/persistence when generic append carries hint keys."""
    if schema_id != _WORKSHEET_SCHEMA_ID or schema_version != _WORKSHEET_SCHEMA_VERSION_V2:
        return
    if worksheet_v2_payload_has_learner_hint_key(payload):
        raise LearnerHintValidationError(
            "worksheet learner hints cannot be appended via generic content append"
        )


def version_requires_worksheet_hint_attestation(
    *,
    schema_id: str,
    schema_version: int,
    payload: Mapping[str, object],
) -> bool:
    """Hint-bearing worksheet@2 versions require §5.3 attestation before APPROVE/publish."""
    if schema_id != _WORKSHEET_SCHEMA_ID or schema_version != _WORKSHEET_SCHEMA_VERSION_V2:
        return False
    return worksheet_v2_payload_has_learner_hint_key(payload)


def validate_learner_hint_string(value: object) -> str:
    """Normalize and validate optional learner_hint text (ADR §5.1)."""
    if not isinstance(value, str):
        raise LearnerHintValidationError("learner_hint must be a plain string")
    trimmed = value.strip()
    if not trimmed:
        raise LearnerHintValidationError("learner_hint must be non-empty after trim")
    if len(trimmed) > 500:
        raise LearnerHintValidationError("learner_hint exceeds maximum length")
    if _HTML_PATTERN.search(trimmed):
        raise LearnerHintValidationError("learner_hint must not contain HTML")
    for pattern in _MARKDOWN_PATTERNS:
        if pattern.search(trimmed):
            raise LearnerHintValidationError("learner_hint must not contain Markdown")
    if _URL_PATTERN.search(trimmed) or _MAILTO_PATTERN.search(trimmed):
        raise LearnerHintValidationError("learner_hint must not contain URLs")
    if _EMAIL_PATTERN.search(trimmed):
        raise LearnerHintValidationError("learner_hint must not contain email addresses")
    if _PROHIBITED_VOCAB.search(trimmed):
        raise LearnerHintValidationError("learner_hint contains prohibited vocabulary")
    return trimmed


def enforce_worksheet_hint_hard_blocks(worksheet: object) -> None:
    """ADR §5.2 mechanical hard blocks (T1 — similarity/attestation deferred to T2)."""
    teacher_notes = (worksheet.teacher_notes or "").strip().casefold()
    for question in worksheet.questions:
        hint = question.learner_hint
        if hint is None:
            continue
        normalized = hint.strip()
        _enforce_question_hint_hard_blocks(question, normalized)
        if teacher_notes and normalized.casefold() == teacher_notes:
            raise LearnerHintHardBlockError(
                "learner_hint must not duplicate teacher_notes"
            )


def _enforce_question_hint_hard_blocks(question: object, hint: str) -> None:
    answer = question.answer.strip().casefold()
    explanation = question.explanation.strip().casefold()
    normalized = hint.casefold()
    if normalized == answer:
        raise LearnerHintHardBlockError("learner_hint must not equal answer")
    if normalized == explanation:
        raise LearnerHintHardBlockError("learner_hint must not equal explanation")
    if _URL_PATTERN.search(hint) or _EMAIL_PATTERN.search(hint):
        raise LearnerHintHardBlockError("learner_hint must not contain URL or email")
    if question.question_type.value == "multiple_choice":
        for option in question.options:
            option_text = option.strip()
            if option_text and option_text in hint:
                raise LearnerHintHardBlockError(
                    "learner_hint must not contain a multiple-choice option verbatim"
                )
