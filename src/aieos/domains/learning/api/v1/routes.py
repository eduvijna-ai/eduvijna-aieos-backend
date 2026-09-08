"""Learning / Student OS HTTP v1. Calls application services only."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from aieos.domains.learning.api.v1.dependencies import (
    cursor_codec,
    get_attempt_service,
    get_current_assignment_service,
    get_student_home_service,
    list_current_assignments_service,
    resolve_trusted_context,
    save_responses_service,
    start_attempt_service,
    submit_attempt_service,
)
from aieos.domains.learning.api.v1.models import (
    AttemptResponse,
    AttemptResponseItemResponse,
    AttemptResponsesReplaceRequest,
    LearnerObjectiveResponse,
    LearnerQuestionResponse,
    LearnerResourceResponse,
    StudentAssignmentListResponse,
    StudentAssignmentResponse,
    StudentHomeResponse,
)
from aieos.domains.learning.application.audit import api_mutation_audit_provenance
from aieos.domains.learning.application.current_assignments import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    GetCurrentAssignmentService,
    GetStudentHomeService,
    ListCurrentAssignmentsService,
)
from aieos.domains.learning.application.errors import InvalidLearnerRequest
from aieos.domains.learning.application.get_attempt import GetAttemptService
from aieos.domains.learning.application.models import (
    AttemptReadModel,
    LearnerResource,
    ResponseWrite,
    StudentAssignmentReadModel,
)
from aieos.domains.learning.application.save_responses import SaveResponsesService
from aieos.domains.learning.application.start_attempt import StartAttemptService
from aieos.domains.learning.application.submit_attempt import SubmitAttemptService
from aieos.platform.api.etag import encode_revision_etag
from aieos.platform.api.idempotency_key import parse_idempotency_key
from aieos.platform.api.if_match import parse_if_match
from aieos.platform.api.pagination import CursorCodec, StudentAssignmentCursor
from aieos.platform.api.problems import ProblemDetails
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.context import TrustedSecurityContext

router = APIRouter(prefix="/api/v1", tags=["learning"])


def _problem_responses(*statuses: int) -> dict[int, dict[str, object]]:
    return {
        status: {"model": ProblemDetails, "description": "RFC 9457 Problem Details"}
        for status in statuses
    }


_GET_RESPONSES = _problem_responses(400, 401, 403, 404, 422, 500, 503)
_LIST_RESPONSES = _problem_responses(400, 401, 403, 422, 500, 503)
_START_RESPONSES = _problem_responses(400, 401, 403, 404, 409, 422, 500, 503)
_MUTATION_RESPONSES = _problem_responses(
    400, 401, 403, 404, 409, 412, 422, 428, 500, 503
)


def _mutation_event_context(
    request: Request, context: TrustedSecurityContext
) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=request.state.correlation_id,
        causation_id=request.state.request_id,
        actor_principal_id=context.principal_id,
        effective_actor_id=context.principal_id,
    )


def _resource_response(resource: LearnerResource | None) -> LearnerResourceResponse | None:
    if resource is None:
        return None
    return LearnerResourceResponse(
        content_id=resource.content_id,
        content_version_id=resource.content_version_id,
        content_type=resource.content_type,
        schema_id=resource.schema_id,
        schema_version=resource.schema_version,
        title=resource.title,
        learning_objectives=[
            LearnerObjectiveResponse(id=item.id, text=item.text)
            for item in resource.learning_objectives
        ],
        instructions=resource.instructions,
        questions=[
            LearnerQuestionResponse(
                id=item.id,
                prompt=item.prompt,
                question_type=item.question_type,
                options=list(item.options),
            )
            for item in resource.questions
        ],
    )


def _assignment_response(
    model: StudentAssignmentReadModel,
) -> StudentAssignmentResponse:
    return StudentAssignmentResponse(
        assignment_id=model.assignment_id,
        class_ref=model.class_ref,
        available_from=model.available_from,
        due_at=model.due_at,
        lifecycle_state=model.lifecycle_state,
        currently_consumable=model.currently_consumable,
        content_id=model.content_id,
        content_version_id=model.content_version_id,
        attempt_summary=model.attempt_summary,
        attempt_id=model.attempt_id,
        resource=_resource_response(model.resource),
    )


def _attempt_response(model: AttemptReadModel) -> AttemptResponse:
    return AttemptResponse(
        attempt_id=model.attempt_id,
        teaching_assignment_id=model.teaching_assignment_id,
        content_id=model.content_id,
        content_version_id=model.content_version_id,
        class_ref=model.class_ref,
        attempt_number=model.attempt_number,
        lifecycle_state=model.lifecycle_state,
        started_at=model.started_at,
        last_saved_at=model.last_saved_at,
        submitted_at=model.submitted_at,
        submission_id=model.submission_id,
        aggregate_revision=model.aggregate_revision,
        responses=[
            AttemptResponseItemResponse(
                question_id=item.question_id,
                response_kind=item.response_kind,
                choice_value=item.choice_value,
                text_value=item.text_value,
                boolean_value=item.boolean_value,
            )
            for item in model.responses
        ],
    )


@router.get(
    "/student-os/home",
    response_model=StudentHomeResponse,
    operation_id="student_os_home",
    responses=_LIST_RESPONSES,
)
def student_os_home(
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[GetStudentHomeService, Depends(get_student_home_service)],
) -> StudentHomeResponse:
    model = service.get(context.tenant_id, context.principal_id)
    return StudentHomeResponse(
        current_assignment_count=model.current_assignment_count,
        items=[_assignment_response(item) for item in model.items],
    )


@router.get(
    "/student-os/assignments",
    response_model=StudentAssignmentListResponse,
    operation_id="student_os_assignment_list",
    responses=_LIST_RESPONSES,
)
def student_os_assignment_list(
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        ListCurrentAssignmentsService, Depends(list_current_assignments_service)
    ],
    codec: Annotated[CursorCodec, Depends(cursor_codec)],
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: str | None = None,
) -> StudentAssignmentListResponse:
    if limit is not None and limit > MAX_LIST_LIMIT:
        raise InvalidLearnerRequest("list limit exceeds the maximum of 100")
    page_size = DEFAULT_LIST_LIMIT if limit is None else limit
    after_updated_at = None
    after_assignment_id = None
    if cursor is not None:
        decoded = codec.decode_student_assignments(
            cursor, expected_tenant_id=context.tenant_id
        )
        after_updated_at = decoded.updated_at
        after_assignment_id = decoded.assignment_id
    result = service.list(
        context.tenant_id,
        context.principal_id,
        limit=page_size,
        after_updated_at=after_updated_at,
        after_assignment_id=after_assignment_id,
    )
    items = [_assignment_response(item) for item in result.items]
    next_cursor = None
    if result.has_more and result.items:
        last = result.items[-1]
        next_cursor = codec.encode_student_assignments(
            StudentAssignmentCursor(
                tenant_id=context.tenant_id,
                updated_at=last.updated_at,
                assignment_id=last.assignment_id,
            )
        )
    return StudentAssignmentListResponse(
        items=items,
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
    )


@router.get(
    "/student-os/assignments/{assignment_id}",
    response_model=StudentAssignmentResponse,
    operation_id="student_os_assignment_get",
    responses=_GET_RESPONSES,
)
def student_os_assignment_get(
    assignment_id: UUID,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[
        GetCurrentAssignmentService, Depends(get_current_assignment_service)
    ],
) -> StudentAssignmentResponse:
    model = service.get(context.tenant_id, context.principal_id, assignment_id)
    return _assignment_response(model)


@router.post(
    "/learning/assignments/{assignment_id}/attempts",
    response_model=AttemptResponse,
    status_code=201,
    operation_id="learning_attempt_start",
    responses=_START_RESPONSES,
)
def learning_attempt_start(
    assignment_id: UUID,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[StartAttemptService, Depends(start_attempt_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AttemptResponse:
    key = parse_idempotency_key(idempotency_key)
    model = service.start(
        context.tenant_id,
        context.principal_id,
        assignment_id,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["Location"] = f"/api/v1/learning/attempts/{model.attempt_id}"
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _attempt_response(model)


@router.get(
    "/learning/attempts/{attempt_id}",
    response_model=AttemptResponse,
    operation_id="learning_attempt_get",
    responses=_GET_RESPONSES,
)
def learning_attempt_get(
    attempt_id: UUID,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[GetAttemptService, Depends(get_attempt_service)],
) -> AttemptResponse:
    model = service.get(context.tenant_id, context.principal_id, attempt_id)
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _attempt_response(model)


@router.put(
    "/learning/attempts/{attempt_id}/responses",
    response_model=AttemptResponse,
    operation_id="learning_attempt_save_responses",
    responses=_MUTATION_RESPONSES,
)
def learning_attempt_save_responses(
    attempt_id: UUID,
    body: AttemptResponsesReplaceRequest,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[SaveResponsesService, Depends(save_responses_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> AttemptResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = parse_if_match(if_match)
    writes = tuple(
        ResponseWrite(
            question_id=item.question_id,
            response_kind=item.response_kind,
            choice_value=item.choice_value,
            text_value=item.text_value,
            boolean_value=item.boolean_value,
        )
        for item in body.responses
    )
    model = service.save(
        context.tenant_id,
        context.principal_id,
        attempt_id,
        writes,
        expected_aggregate_revision=expected,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _attempt_response(model)


@router.post(
    "/learning/attempts/{attempt_id}/actions/submit",
    response_model=AttemptResponse,
    operation_id="learning_attempt_submit",
    responses=_MUTATION_RESPONSES,
)
def learning_attempt_submit(
    attempt_id: UUID,
    request: Request,
    response: Response,
    context: Annotated[TrustedSecurityContext, Depends(resolve_trusted_context)],
    service: Annotated[SubmitAttemptService, Depends(submit_attempt_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> AttemptResponse:
    key = parse_idempotency_key(idempotency_key)
    expected = parse_if_match(if_match)
    model = service.submit(
        context.tenant_id,
        context.principal_id,
        attempt_id,
        expected_aggregate_revision=expected,
        idempotency_key=key,
        event_context=_mutation_event_context(request, context),
        audit_provenance=api_mutation_audit_provenance(context.principal_id),
    )
    response.headers["ETag"] = encode_revision_etag(int(model.aggregate_revision))
    return _attempt_response(model)
