"""Groq structured-output adapter via the OpenAI-compatible Chat Completions API.

Provider identity remains groq. Only this package may import the OpenAI client
for Groq transport. Domain code must not import this module.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from aieos.platform.ai.config import GroqProviderConfig
from aieos.platform.ai.gateway import (
    ModelAdapterContractFailed,
    ModelGenerationFailed,
    ModelOutputIncomplete,
    ModelOutputInvalid,
    ModelOutputMissing,
    ModelProviderUnavailable,
    ModelRequestRejected,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE_STATUS_CODES = frozenset({401, 403, 404, 429, 500, 502, 503, 504})
_BOUNDED_IDENTIFIER_MAX_LEN = 64
_SCHEMA_NAME_MAX_LEN = 64
_SCHEMA_NAME_RE = re.compile(r"[^A-Za-z0-9_]+")
_INCOMPLETE_FINISH_REASONS = frozenset({"length", "max_tokens"})
_JSON_VALIDATE_FAILED_CODE = "json_validate_failed"
_JSON_VALIDATE_ATTEMPTS = 3


def _safe_scalar(value: object) -> str | int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        if isinstance(value, str) and len(value) > 128:
            return None
        return value
    return None


def _bounded_identifier(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if len(value) > _BOUNDED_IDENTIFIER_MAX_LEN:
        return None
    if all(ch.isalnum() or ch in "_-/" for ch in value):
        return value
    return None


def _extract_provider_error_scalars(body: object) -> dict[str, str | int | float | bool]:
    """Allowlisted scalar extractor. Never returns nested bodies or messages."""
    out: dict[str, str | int | float | bool] = {}
    if not isinstance(body, Mapping):
        return out
    error = body.get("error")
    source = error if isinstance(error, Mapping) else body
    for key in ("type", "code", "schema_kind"):
        scalar = _safe_scalar(source.get(key))
        if scalar is not None:
            out[f"provider_error_{key}"] = scalar
    return out


def bounded_schema_name(output_type: type[BaseModel]) -> str:
    raw = output_type.__name__ or "structured_output"
    cleaned = _SCHEMA_NAME_RE.sub("_", raw).strip("_")
    if not cleaned:
        cleaned = "structured_output"
    if cleaned[0].isdigit():
        cleaned = f"schema_{cleaned}"
    return cleaned[:_SCHEMA_NAME_MAX_LEN]


def _enforce_object_schema(node: object) -> None:
    if isinstance(node, list):
        for item in node:
            _enforce_object_schema(item)
        return
    if not isinstance(node, dict):
        return
    node_type = node.get("type")
    if node_type == "object" or "properties" in node:
        properties = node.get("properties")
        if isinstance(properties, dict):
            node["additionalProperties"] = False
            required = node.get("required")
            if not isinstance(required, list):
                required = []
            for key in properties:
                if key not in required:
                    required.append(key)
            node["required"] = required
            for child in properties.values():
                _enforce_object_schema(child)
    for key in ("$defs", "definitions"):
        defs = node.get(key)
        if isinstance(defs, dict):
            for value in defs.values():
                _enforce_object_schema(value)
    for key in ("items", "additionalProperties"):
        if key in node:
            _enforce_object_schema(node[key])
    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        if key in node:
            _enforce_object_schema(node[key])


def json_schema_for_output_type(output_type: type[BaseModel]) -> dict[str, Any]:
    schema = json.loads(json.dumps(output_type.model_json_schema()))
    _enforce_object_schema(schema)
    return schema


def _usage_tokens(response: object) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    out: dict[str, int] = {}
    mapping = (
        ("input_tokens", ("input_tokens", "prompt_tokens")),
        ("output_tokens", ("output_tokens", "completion_tokens")),
        ("total_tokens", ("total_tokens",)),
    )
    for dest, sources in mapping:
        for source in sources:
            value = getattr(usage, source, None)
            if isinstance(value, int):
                out[dest] = value
                break
    return out


def _inspect_response_metadata(response: object) -> dict[str, object]:
    metadata: dict[str, object] = {}
    response_id = getattr(response, "id", None)
    if isinstance(response_id, str) and len(response_id) <= 128:
        metadata["provider_response_id"] = response_id
    model_id = _bounded_identifier(getattr(response, "model", None))
    if model_id is not None:
        metadata["model_id"] = model_id
    metadata.update(_usage_tokens(response))
    choices = getattr(response, "choices", None)
    choice_count = len(choices) if isinstance(choices, list) else 0
    metadata["choice_count"] = choice_count
    if isinstance(choices, list) and choices:
        first = choices[0]
        finish_reason = _bounded_identifier(getattr(first, "finish_reason", None))
        if finish_reason is not None:
            metadata["finish_reason"] = finish_reason
        message = getattr(first, "message", None)
        if message is not None:
            message_role = _bounded_identifier(getattr(message, "role", None))
            if message_role is not None:
                metadata["message_role"] = message_role
            refusal_present = getattr(message, "refusal", None) not in (None, "")
            metadata["refusal_present"] = refusal_present
    return metadata


def _log_ai_diagnostic(*, classification: str, **fields: object) -> None:
    payload: dict[str, object] = {
        "provider": "groq",
        "operation": "chat.completions.create",
        "classification": classification,
    }
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            if isinstance(value, str) and len(value) > 128:
                continue
            payload[key] = value
        elif isinstance(value, tuple) and all(isinstance(item, str) for item in value):
            payload[key] = value
    safe_bits = []
    for key in (
        "classification",
        "exception_class",
        "http_status",
        "provider_error_type",
        "provider_error_code",
        "provider_error_schema_kind",
    ):
        value = payload.get(key)
        if value is not None:
            safe_bits.append(f"{key}={value}")
    _LOGGER.warning(
        "groq_structured_generation_failed " + " ".join(safe_bits),
        extra={"aieos_ai": payload},
    )


def _log_failure(
    *,
    classification: str,
    exception_class: str,
    http_status: int | None = None,
    provider_error_type: str | int | float | bool | None = None,
    provider_error_code: str | int | float | bool | None = None,
    provider_request_id: str | None = None,
) -> None:
    _log_ai_diagnostic(
        classification=classification,
        exception_class=exception_class,
        http_status=http_status,
        provider_error_type=provider_error_type,
        provider_error_code=provider_error_code,
        provider_request_id=provider_request_id,
    )


def _assistant_message_content(response: object) -> str | None:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        return None
    message = getattr(choices[0], "message", None)
    if message is None:
        return None
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        return None
    return content


def _status_error_code(exc: APIStatusError) -> str | None:
    scalars = _extract_provider_error_scalars(getattr(exc, "body", None))
    code = scalars.get("provider_error_code")
    if isinstance(code, str) and code:
        return code
    return None


def _raise_mapped_status_error(exc: APIStatusError) -> None:
    status = int(exc.status_code)
    scalars = _extract_provider_error_scalars(getattr(exc, "body", None))
    request_id = getattr(exc, "request_id", None)
    if not isinstance(request_id, str):
        request_id = None
    if status in _UNAVAILABLE_STATUS_CODES:
        _log_failure(
            classification="model_provider_unavailable",
            exception_class=type(exc).__name__,
            http_status=status,
            provider_error_type=scalars.get("provider_error_type"),
            provider_error_code=scalars.get("provider_error_code"),
            provider_request_id=request_id,
        )
        raise ModelProviderUnavailable("Groq provider unavailable") from exc
    if 400 <= status < 500:
        _log_failure(
            classification="model_request_rejected",
            exception_class=type(exc).__name__,
            http_status=status,
            provider_error_type=scalars.get("provider_error_type"),
            provider_error_code=scalars.get("provider_error_code"),
            provider_request_id=request_id,
        )
        raise ModelRequestRejected("Groq request rejected") from exc
    _log_failure(
        classification="model_generation_failed",
        exception_class=type(exc).__name__,
        http_status=status,
        provider_error_type=scalars.get("provider_error_type"),
        provider_error_code=scalars.get("provider_error_code"),
        provider_request_id=request_id,
    )
    raise ModelGenerationFailed("Groq generation failed") from exc


class GroqStructuredModelGateway:
    """Structured generation via Groq OpenAI-compatible chat.completions."""

    def __init__(self, config: GroqProviderConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client = client or OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=0,
        )

    def generate_structured[T: BaseModel](
        self, request: StructuredGenerationRequest[T]
    ) -> StructuredGenerationResult[T]:
        max_tokens = min(request.max_output_tokens, self._config.max_output_tokens)
        schema_name = bounded_schema_name(request.output_type)
        schema = json_schema_for_output_type(request.output_type)
        create_kwargs = {
            "model": self._config.model_id,
            "messages": [
                {"role": "system", "content": request.instructions},
                {"role": "user", "content": request.input_text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            "max_completion_tokens": max_tokens,
        }
        response: Any = None
        try:
            for attempt in range(1, _JSON_VALIDATE_ATTEMPTS + 1):
                try:
                    response = self._client.chat.completions.create(**create_kwargs)
                    break
                except APIStatusError as exc:
                    if (
                        int(exc.status_code) == 400
                        and _status_error_code(exc) == _JSON_VALIDATE_FAILED_CODE
                        and attempt < _JSON_VALIDATE_ATTEMPTS
                    ):
                        _log_ai_diagnostic(
                            classification="groq_json_validate_retry",
                            exception_class=type(exc).__name__,
                            http_status=400,
                            provider_error_code=_JSON_VALIDATE_FAILED_CODE,
                            attempt=attempt,
                        )
                        continue
                    _raise_mapped_status_error(exc)
        except (APIConnectionError, APITimeoutError) as exc:
            _log_failure(
                classification="model_provider_unavailable",
                exception_class=type(exc).__name__,
            )
            raise ModelProviderUnavailable("Groq provider unavailable") from exc
        except (TypeError, ValueError) as exc:
            _log_failure(
                classification="model_adapter_contract_failed",
                exception_class=type(exc).__name__,
            )
            raise ModelAdapterContractFailed("Groq adapter contract failed") from exc
        except Exception as exc:  # noqa: BLE001 — residual SDK failure
            if isinstance(exc, (ModelProviderUnavailable, ModelRequestRejected, ModelGenerationFailed)):
                raise
            _log_failure(
                classification="model_generation_failed",
                exception_class=type(exc).__name__,
            )
            raise ModelGenerationFailed("Groq generation failed") from exc
        if response is None:
            _log_failure(
                classification="model_generation_failed",
                exception_class="MissingGroqResponse",
            )
            raise ModelGenerationFailed("Groq generation failed")

        metadata = _inspect_response_metadata(response)
        finish_reason = metadata.get("finish_reason")
        if finish_reason in _INCOMPLETE_FINISH_REASONS:
            _log_ai_diagnostic(classification="model_output_incomplete", **metadata)
            raise ModelOutputIncomplete("Groq structured output incomplete")

        content = _assistant_message_content(response)
        if content is None:
            _log_ai_diagnostic(classification="model_output_missing", **metadata)
            raise ModelOutputMissing("Groq structured output missing")

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            _log_ai_diagnostic(
                classification="model_output_invalid",
                exception_class=type(exc).__name__,
                **metadata,
            )
            raise ModelOutputInvalid("Groq structured output invalid") from exc

        try:
            parsed = request.output_type.model_validate(payload)
        except ValidationError as exc:
            _log_ai_diagnostic(
                classification="model_output_invalid",
                exception_class=type(exc).__name__,
                **metadata,
            )
            raise ModelOutputInvalid("Groq structured output invalid") from exc

        usage = _usage_tokens(response)
        response_id = getattr(response, "id", None)
        model_id = getattr(response, "model", None) or self._config.model_id

        return StructuredGenerationResult(
            provider_id=self._config.provider_id,
            model_id=str(model_id),
            provider_response_id=None if response_id is None else str(response_id),
            parsed_output=parsed,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            total_tokens=usage.get("total_tokens"),
        )
