"""TOS-CX01-I03 mocked Groq structured-output adapter.

No network. No live API key. Mocked OpenAI-compatible client only.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import BaseModel, ConfigDict, Field

from aieos.domains.education.worksheet_v1 import WorksheetV1
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
)
from aieos.platform.ai.providers.groq.adapter import (
    GroqStructuredModelGateway,
    _extract_provider_error_scalars,
    bounded_schema_name,
    json_schema_for_output_type,
)
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model

_PROHIBITED_LOG_SUBSTRINGS = (
    "gsk_",
    "Authorization",
    "Bearer",
    "api_key",
    "AIEOS_GROQ_API_KEY",
    "instructions",
    "input_text",
    "error message",
)


class _TinyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)


def _config() -> GroqProviderConfig:
    return GroqProviderConfig(
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        api_key="offline-dummy-key",
        base_url="https://api.groq.com/openai/v1",
        max_output_tokens=8000,
        timeout_seconds=180.0,
    )


def _request() -> StructuredGenerationRequest[WorksheetV1]:
    return StructuredGenerationRequest(
        capability_id="education.generate_worksheet",
        instructions="offline-test-instructions-must-never-appear-in-logs",
        input_text="offline-test-input-must-never-appear-in-logs",
        output_type=WorksheetV1,
        max_output_tokens=4000,
    )


def _tiny_request() -> StructuredGenerationRequest[_TinyOutput]:
    return StructuredGenerationRequest(
        capability_id="education.generate_worksheet",
        instructions="offline-test-instructions-must-never-appear-in-logs",
        input_text="offline-test-input-must-never-appear-in-logs",
        output_type=_TinyOutput,
        max_output_tokens=4000,
    )


def _completion(
    *,
    content: str | None,
    finish_reason: str = "stop",
    usage: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="chatcmpl_ok",
        model="openai/gpt-oss-120b",
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    role="assistant",
                    content=content,
                    refusal=None,
                ),
            )
        ],
        usage=usage
        or SimpleNamespace(prompt_tokens=11, completion_tokens=22, total_tokens=33),
    )


def _api_status(code: int, *, body: dict[str, object] | None = None) -> APIStatusError:
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    payload = body if body is not None else {"error": {"type": "invalid_request", "code": "bad"}}
    resp = httpx.Response(code, request=req, json=payload)
    return APIStatusError("error", response=resp, body=payload)


def _gateway(mock_client: Any) -> GroqStructuredModelGateway:
    return GroqStructuredModelGateway(_config(), client=mock_client)


class TestGroqAdapterContract:
    def test_valid_structured_response_maps_result(self) -> None:
        valid = valid_worksheet_model()
        client = MagicMock()
        client.chat.completions.create.return_value = _completion(
            content=json.dumps(valid.model_dump(mode="json"))
        )
        result = _gateway(client).generate_structured(_request())
        assert result.provider_id == "groq"
        assert result.model_id == "openai/gpt-oss-120b"
        assert result.provider_response_id == "chatcmpl_ok"
        assert result.input_tokens == 11
        assert result.output_tokens == 22
        assert result.total_tokens == 33
        assert isinstance(result.parsed_output, WorksheetV1)

    def test_request_uses_chat_completions_strict_json_schema(self) -> None:
        valid = valid_worksheet_model()
        client = MagicMock()
        client.chat.completions.create.return_value = _completion(
            content=json.dumps(valid.model_dump(mode="json"))
        )
        _gateway(client).generate_structured(_request())
        assert client.chat.completions.create.call_count == 1
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "openai/gpt-oss-120b"
        assert kwargs["messages"] == [
            {
                "role": "system",
                "content": "offline-test-instructions-must-never-appear-in-logs",
            },
            {
                "role": "user",
                "content": "offline-test-input-must-never-appear-in-logs",
            },
        ]
        assert kwargs["max_completion_tokens"] == 4000
        response_format = kwargs["response_format"]
        assert response_format["type"] == "json_schema"
        json_schema = response_format["json_schema"]
        assert json_schema["strict"] is True
        assert json_schema["name"] == bounded_schema_name(WorksheetV1)
        assert json_schema["schema"] == json_schema_for_output_type(WorksheetV1)
        assert "tools" not in kwargs
        assert "stream" not in kwargs

    def test_client_uses_groq_base_url_and_zero_retries(self) -> None:
        gateway = GroqStructuredModelGateway(_config())
        assert str(gateway._client.base_url).rstrip("/") == "https://api.groq.com/openai/v1"
        assert gateway._client.max_retries == 0
        assert gateway._config.provider_id == "groq"

    def test_invalid_json_is_output_invalid(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _completion(content="{not-json")
        with pytest.raises(ModelOutputInvalid):
            _gateway(client).generate_structured(_tiny_request())

    def test_pydantic_validation_failure_is_output_invalid(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _completion(content="{}")
        with pytest.raises(ModelOutputInvalid):
            _gateway(client).generate_structured(_tiny_request())

    def test_missing_content_is_output_missing(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _completion(content=None)
        with pytest.raises(ModelOutputMissing):
            _gateway(client).generate_structured(_tiny_request())

    def test_no_choices_is_output_missing(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            id="chatcmpl_empty",
            model="openai/gpt-oss-120b",
            choices=[],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0, total_tokens=1),
        )
        with pytest.raises(ModelOutputMissing):
            _gateway(client).generate_structured(_tiny_request())

    def test_length_finish_reason_is_incomplete(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _completion(
            content='{"label":"x"}', finish_reason="length"
        )
        with pytest.raises(ModelOutputIncomplete):
            _gateway(client).generate_structured(_tiny_request())

    @pytest.mark.parametrize("status", [400, 409, 422])
    def test_http_4xx_request_rejected(self, status: int) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = _api_status(status)
        with pytest.raises(ModelRequestRejected):
            _gateway(client).generate_structured(_tiny_request())

    @pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 502, 503, 504])
    def test_http_unavailable_family(self, status: int) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = _api_status(status)
        with pytest.raises(ModelProviderUnavailable):
            _gateway(client).generate_structured(_tiny_request())

    def test_connection_and_timeout_unavailable(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = APIConnectionError(
            request=httpx.Request(
                "POST", "https://api.groq.com/openai/v1/chat/completions"
            )
        )
        with pytest.raises(ModelProviderUnavailable):
            _gateway(client).generate_structured(_tiny_request())
        client.chat.completions.create.side_effect = APITimeoutError(
            request=httpx.Request(
                "POST", "https://api.groq.com/openai/v1/chat/completions"
            )
        )
        with pytest.raises(ModelProviderUnavailable):
            _gateway(client).generate_structured(_tiny_request())

    def test_type_error_is_adapter_contract_failed(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = TypeError("unexpected keyword")
        with pytest.raises(ModelAdapterContractFailed):
            _gateway(client).generate_structured(_tiny_request())

    def test_residual_exception_is_generation_failed(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("unexpected sdk failure")
        with pytest.raises(ModelGenerationFailed):
            _gateway(client).generate_structured(_tiny_request())


class TestSanitizedDiagnostics:
    def test_extractor_allowlists_only_type_and_code(self) -> None:
        body = {
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_value",
                "message": "SECRET_MESSAGE_MUST_NOT_EXTRACT",
                "param": "model",
            }
        }
        scalars = _extract_provider_error_scalars(body)
        assert scalars == {
            "provider_error_type": "invalid_request_error",
            "provider_error_code": "invalid_value",
        }
        assert "message" not in scalars
        assert "SECRET" not in str(scalars)

    def test_failure_log_contains_no_prohibited_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = MagicMock()
        body = {
            "error": {
                "type": "invalid_request_error",
                "code": "bad_request",
                "message": "gsk_secret-key-value Authorization Bearer leak",
            }
        }
        client.chat.completions.create.side_effect = _api_status(400, body=body)
        captured: list[tuple[str, dict[str, object]]] = []

        def _warning(msg: object, *args: object, **kwargs: object) -> None:
            extra = kwargs.get("extra")
            payload = extra.get("aieos_ai", {}) if isinstance(extra, dict) else {}
            captured.append((str(msg), dict(payload) if isinstance(payload, dict) else {}))

        monkeypatch.setattr(
            "aieos.platform.ai.providers.groq.adapter._LOGGER.warning",
            _warning,
        )
        with pytest.raises(ModelRequestRejected):
            _gateway(client).generate_structured(_tiny_request())
        assert len(captured) == 1
        message, aieos = captured[0]
        assert message == "groq_structured_generation_failed"
        assert aieos["provider"] == "groq"
        assert aieos["operation"] == "chat.completions.create"
        assert aieos["classification"] == "model_request_rejected"
        assert aieos["http_status"] == 400
        blob = f"{message} {aieos}"
        for needle in _PROHIBITED_LOG_SUBSTRINGS:
            assert needle not in blob
        assert "offline-test-instructions" not in blob
        assert "offline-test-input" not in blob
        assert "SECRET" not in blob
        assert "gsk_secret" not in blob
