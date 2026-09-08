"""Learning HTTP dependencies.

resolve_trusted_context is reused from the Content HTTP surface.
"""

from __future__ import annotations

from fastapi import Request

from aieos.domains.content.api.v1.dependencies import resolve_trusted_context
from aieos.domains.learning.application.current_assignments import (
    GetCurrentAssignmentService,
    GetStudentHomeService,
    ListCurrentAssignmentsService,
)
from aieos.domains.learning.application.errors import PersistenceOperationFailed
from aieos.domains.learning.application.get_attempt import GetAttemptService
from aieos.domains.learning.application.save_responses import SaveResponsesService
from aieos.domains.learning.application.start_attempt import StartAttemptService
from aieos.domains.learning.application.submit_attempt import SubmitAttemptService
from aieos.platform.api.pagination import CursorCodec

__all__ = [
    "cursor_codec",
    "get_attempt_service",
    "get_current_assignment_service",
    "get_student_home_service",
    "list_current_assignments_service",
    "resolve_trusted_context",
    "save_responses_service",
    "start_attempt_service",
    "submit_attempt_service",
]


def cursor_codec(request: Request) -> CursorCodec:
    return request.app.state.cursor_codec


def _require(request: Request, name: str):
    service = getattr(request.app.state, name, None)
    if service is None:
        raise PersistenceOperationFailed("Student learning commands are not composed")
    return service


def start_attempt_service(request: Request) -> StartAttemptService:
    return _require(request, "start_attempt_service")


def save_responses_service(request: Request) -> SaveResponsesService:
    return _require(request, "save_responses_service")


def submit_attempt_service(request: Request) -> SubmitAttemptService:
    return _require(request, "submit_attempt_service")


def get_attempt_service(request: Request) -> GetAttemptService:
    return _require(request, "get_attempt_service")


def list_current_assignments_service(request: Request) -> ListCurrentAssignmentsService:
    return _require(request, "list_current_assignments_service")


def get_current_assignment_service(request: Request) -> GetCurrentAssignmentService:
    return _require(request, "get_current_assignment_service")


def get_student_home_service(request: Request) -> GetStudentHomeService:
    return _require(request, "get_student_home_service")
