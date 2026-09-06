"""Structured Model Gateway output for Teacher OS Assistant v1.

Provider-neutral Pydantic contract. Never carries credentials, JWTs,
authorization rows, or infrastructure metadata.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TeacherAssistantAnswerV1(BaseModel):
    """READ / REASON / SUGGEST only. Never an executable command."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=8000)
    suggested_questions: list[str] = Field(default_factory=list, max_length=6)
    suggested_next_step: str | None = Field(default=None, max_length=500)

    @classmethod
    def development_fake(cls, input_text: str) -> TeacherAssistantAnswerV1:
        """Deterministic NON_PRODUCTION stub used by FakeStructuredModelGateway."""
        lower = input_text.lower()
        if "today" in lower or "focus" in lower or "mission" in lower:
            answer = (
                "Based on today's mission context, prioritize the highest-urgency "
                "teacher action shown in your mission projection. Review pending "
                "items first when present; otherwise continue the most recent "
                "Teaching Work. This is guidance only — no Publish, Assign, Teach, "
                "Assess, or Memory change is performed."
            )
            questions = [
                "Summarize the current teaching work.",
                "What preparation is already available?",
                "Why does this class need remediation?",
            ]
            next_step = "Open Today's Mission and confirm the hero action yourself."
        elif "remediat" in lower or "why does this class" in lower:
            answer = (
                "Remediation appears when a class-level assessment indicates the "
                "class still needs support. Use the remediation Teaching Work and "
                "its origin facts as context. The Assistant does not create "
                "remediation or record assessment — confirm any Improve action on "
                "the Improve surface."
            )
            questions = [
                "Summarize the current teaching work.",
                "How should I adjust tomorrow's lesson?",
                "What should I focus on today?",
            ]
            next_step = "Open Improve only if you choose to create remediation yourself."
        elif "adjust" in lower or "tomorrow" in lower:
            answer = (
                "Adjust tomorrow's lesson using the selected Teaching Work goal, "
                "class label, subject/topic, and any Teacher Memory preferences "
                "already saved. Suggestions here do not change Prepare defaults "
                "or Memory."
            )
            questions = [
                "What preparation is already available?",
                "Summarize the current teaching work.",
                "What should I focus on today?",
            ]
            next_step = "Open Prepare on the selected Teaching Work to refine intentionally."
        elif "prepar" in lower or "available" in lower:
            answer = (
                "Preparation artefacts listed in context (titles and stewardship "
                "only) are what is already available for the selected Teaching Work. "
                "Review and Publish remain explicit teacher actions on their own "
                "surfaces."
            )
            questions = [
                "Summarize the current teaching work.",
                "What should I focus on today?",
                "How should I adjust tomorrow's lesson?",
            ]
            next_step = "Open Review or Library if you want to inspect artefacts."
        elif "summar" in lower or "current teaching work" in lower:
            answer = (
                "The current Teaching Work context includes intent, goal, class, "
                "subject/topic, and related assignment/execution/assessment facts "
                "when authorized. Treat conversation history as chat only — not "
                "business authority."
            )
            questions = [
                "What preparation is already available?",
                "What should I focus on today?",
                "Why does this class need remediation?",
            ]
            next_step = "Open Work for the selected Teaching Work to inspect details."
        else:
            answer = (
                "I can help you reason about today's mission, selected Teaching Work, "
                "preparation, remediation origin, and Teacher Memory preferences. "
                "I will not Publish, Assign, start Teach, record Assessment, create "
                "remediation, or change Memory."
            )
            questions = [
                "What should I focus on today?",
                "Summarize the current teaching work.",
                "What preparation is already available?",
                "Why does this class need remediation?",
                "How should I adjust tomorrow's lesson?",
            ]
            next_step = "Ask a concrete teaching question, or open the relevant Teacher OS surface."

        return cls(
            answer=answer,
            suggested_questions=questions[:5],
            suggested_next_step=next_step,
        )
