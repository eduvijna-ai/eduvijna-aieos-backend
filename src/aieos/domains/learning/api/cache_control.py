"""HTTP cache policy for attempt-scoped learner-hint reads (ADR-063 §8.2)."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_LEARNER_HINTS_SUFFIX = "/learner-hints"
_NO_STORE = "private, no-store"


class LearnerHintsCacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        if request.url.path.endswith(_LEARNER_HINTS_SUFFIX):
            response.headers["Cache-Control"] = _NO_STORE
        return response
