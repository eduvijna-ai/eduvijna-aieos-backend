"""Teacher Memory preference vocabulary (schema_version = 1).

Closed typed preference classes only. Arbitrary JSON is rejected.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aieos.domains.teaching.domain.errors import InvalidTeacherMemoryError

TEACHER_MEMORY_SCHEMA_VERSION: Final = 1


class TeachingStyle(StrEnum):
    BALANCED = "balanced"
    DIRECT_INSTRUCTION = "direct_instruction"
    INQUIRY_LED = "inquiry_led"
    COLLABORATIVE = "collaborative"


class PreferredDifficulty(StrEnum):
    SUPPORTIVE = "supportive"
    STANDARD = "standard"
    CHALLENGING = "challenging"


class PreparationDetail(StrEnum):
    CONCISE = "concise"
    BALANCED = "balanced"
    DETAILED = "detailed"


class OutputFormat(StrEnum):
    STRUCTURED = "structured"
    PRINT_FRIENDLY = "print_friendly"


class TeacherMemoryPreferences(BaseModel):
    """Explicit teacher-controlled preference profile payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    teaching_style: TeachingStyle = TeachingStyle.BALANCED
    preferred_difficulty: PreferredDifficulty = PreferredDifficulty.STANDARD
    preparation_detail: PreparationDetail = PreparationDetail.BALANCED
    output_format: OutputFormat = OutputFormat.STRUCTURED
    include_differentiation: bool = False


DEFAULT_TEACHER_MEMORY_PREFERENCES = TeacherMemoryPreferences()


def default_preferences() -> TeacherMemoryPreferences:
    return TeacherMemoryPreferences()


def preferences_to_storage(preferences: TeacherMemoryPreferences) -> dict[str, Any]:
    return preferences.model_dump(mode="json")


def parse_preferences(value: Any) -> TeacherMemoryPreferences:
    if not isinstance(value, dict):
        raise InvalidTeacherMemoryError("preferences must be a JSON object")
    try:
        return TeacherMemoryPreferences.model_validate(value)
    except ValidationError as exc:
        raise InvalidTeacherMemoryError(
            "preferences failed typed validation"
        ) from exc


TeachingStyleLiteral = Literal[
    "balanced", "direct_instruction", "inquiry_led", "collaborative"
]
PreferredDifficultyLiteral = Literal["supportive", "standard", "challenging"]
PreparationDetailLiteral = Literal["concise", "balanced", "detailed"]
OutputFormatLiteral = Literal["structured", "print_friendly"]


class TeacherMemoryPreferencesRequest(BaseModel):
    """HTTP/body preference payload (same vocabulary as storage)."""

    model_config = ConfigDict(extra="forbid")

    teaching_style: TeachingStyleLiteral = Field(default="balanced")
    preferred_difficulty: PreferredDifficultyLiteral = Field(default="standard")
    preparation_detail: PreparationDetailLiteral = Field(default="balanced")
    output_format: OutputFormatLiteral = Field(default="structured")
    include_differentiation: bool = False

    def to_domain(self) -> TeacherMemoryPreferences:
        return TeacherMemoryPreferences.model_validate(self.model_dump())
