"""Mechanical hard blocks and FLAG-only human-review heuristics (ADR-063 §5.1–5.2 T2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from aieos.domains.education.worksheet_hint_safety import (
    LearnerHintHardBlockError,
    enforce_worksheet_hint_hard_blocks,
)
from aieos.domains.education.worksheet_v2 import WorksheetV2, parse_worksheet_v2

WORKSHEET_HINT_QA_POLICY_VERSION = "O63-03-v1"

# Documented deterministic overlap threshold (token Jaccard vs answer/explanation).
SIMILARITY_FLAG_JACCARD_THRESHOLD = 0.55

_PROFANITY_PATTERN = re.compile(
    r"(?i)\b(damn|hell|shit|fuck|bastard|crap)\b",
)
_PHONE_PATTERN = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
_READING_LEVEL_WORD_THRESHOLD = {
    "easy": 6.5,
    "medium": 8.5,
    "hard": 11.0,
}


class HintReviewFlag(StrEnum):
    ANSWER_SIMILARITY = "answer_similarity"
    EXPLANATION_SIMILARITY = "explanation_similarity"
    PROFANITY = "profanity"
    OBVIOUS_PII = "obvious_pii"
    READING_LEVEL = "reading_level"


@dataclass(frozen=True, slots=True)
class HintQuestionQaAssessment:
    question_id: str
    hard_block_clear: bool
    flags: tuple[HintReviewFlag, ...]


def _token_set(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.casefold()) if token}


def _jaccard(left: str, right: str) -> float:
    a = _token_set(left)
    b = _token_set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _average_word_length(text: str) -> float:
    words = re.findall(r"[a-zA-Z']+", text)
    if not words:
        return 0.0
    return sum(len(word) for word in words) / len(words)


def assess_worksheet_hint_qa(raw_payload: object) -> list[HintQuestionQaAssessment]:
    """Run hard blocks and FLAG-only heuristics for reviewer attestation."""
    worksheet = (
        raw_payload
        if isinstance(raw_payload, WorksheetV2)
        else parse_worksheet_v2(raw_payload)  # type: ignore[arg-type]
    )
    try:
        enforce_worksheet_hint_hard_blocks(worksheet)
    except LearnerHintHardBlockError:
        raise

    assessments: list[HintQuestionQaAssessment] = []
    for question in worksheet.questions:
        hint = question.learner_hint
        if hint is None:
            continue
        flags: list[HintReviewFlag] = []
        if _jaccard(hint, question.answer) >= SIMILARITY_FLAG_JACCARD_THRESHOLD:
            flags.append(HintReviewFlag.ANSWER_SIMILARITY)
        if _jaccard(hint, question.explanation) >= SIMILARITY_FLAG_JACCARD_THRESHOLD:
            flags.append(HintReviewFlag.EXPLANATION_SIMILARITY)
        if _PROFANITY_PATTERN.search(hint):
            flags.append(HintReviewFlag.PROFANITY)
        if _PHONE_PATTERN.search(hint):
            flags.append(HintReviewFlag.OBVIOUS_PII)
        threshold = _READING_LEVEL_WORD_THRESHOLD.get(question.difficulty.value)
        if threshold is not None and _average_word_length(hint) > threshold:
            flags.append(HintReviewFlag.READING_LEVEL)
        assessments.append(
            HintQuestionQaAssessment(
                question_id=question.id,
                hard_block_clear=True,
                flags=tuple(flags),
            )
        )
    return assessments
