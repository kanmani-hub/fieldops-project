"""
test_groq_provider.py

Unit test suite for hardened GroqProvider, deadline management, 429 retries, and usage tracking.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List
from unittest.mock import MagicMock
import pytest

from app.services.ai.FieldOpsAI.providers.groq_client import (
    AIProviderExecutionError,
    GroqClient,
)
from app.services.ai.FieldOpsAI.schemas.ai_task import (
    AITask,
)

from app.services.ai.FieldOpsAI.config.config_loader import ConfigLoader
from app.services.ai.FieldOpsAI.providers.base_provider import (
    BaseAIProvider,
    ProviderConfigurationError,
    ProviderExecutionError,
)
from app.services.ai.FieldOpsAI.providers.groq_provider import (
    ALLOWED_MODEL,
    GroqProvider,
)
from app.services.ai.FieldOpsAI.schemas.provider import GenerationResult, UsageStats


class FakeClock:
    def __init__(self, start_time: float = 1000.0) -> None:
        self._time = start_time

    def __call__(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds


class FakeSleep:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.delays: List[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.clock.advance(seconds)


def make_mock_response(text: str = "Safe response", prompt_tokens: int = 10, completion_tokens: int = 20):
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = text
    mock_resp.choices = [mock_choice]
    mock_resp.usage.prompt_tokens = prompt_tokens
    mock_resp.usage.completion_tokens = completion_tokens
    mock_resp.usage.total_tokens = prompt_tokens + completion_tokens
    return mock_resp


# 1. BaseAIProvider Subclass Check
def test_groq_provider_subclass_check() -> None:
    mock_client = MagicMock()
    provider = GroqProvider(client=mock_client)
    assert isinstance(provider, BaseAIProvider)


# 2 & 3. Model Restriction Validation
def test_exact_allowed_model_accepted() -> None:
    mock_client = MagicMock()
    provider = GroqProvider(client=mock_client)
    assert provider.model_name() == ALLOWED_MODEL


def test_other_model_rejected() -> None:
    mock_config = MagicMock()
    mock_config.model_name = "gpt-4"
    with pytest.raises(ProviderConfigurationError, match="Unsupported model"):
        GroqProvider(client=MagicMock(), config=mock_config)


# 4. Missing API Key Validation
def test_missing_api_key_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError, match="GROQ_API_KEY"):
        GroqProvider(client=None)


# 5 & 6. Successful Completion & Model Usage
def test_successful_completion_returns_normalized_text() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = make_mock_response("  Hello World  ")
    provider = GroqProvider(client=mock_client)

    result = provider.generate_result(messages=[{"role": "user", "content": "hi"}])
    assert isinstance(result, GenerationResult)
    assert result.text == "Hello World"
    assert result.model_name == ALLOWED_MODEL
    mock_client.chat.completions.create.assert_called_once()
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["model"] == ALLOWED_MODEL


# 7. Timeout Application
def test_five_second_timeout_applied() -> None:
    clock = FakeClock(1000.0)
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = make_mock_response("Response")
    provider = GroqProvider(client=mock_client, clock=clock)

    provider.generate_result(messages=[{"role": "user", "content": "test"}])
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["timeout"] <= 5.0


# 8, 9 & 10. 429 Rate Limit Retries & Backoff Delays
def test_429_retries_and_backoff_delays() -> None:
    clock = FakeClock(1000.0)
    sleeper = FakeSleep(clock)

    mock_client = MagicMock()
    err_429 = Exception("Rate limit")
    err_429.status_code = 429
    success_resp = make_mock_response("Success after retry")

    # 429 twice, then success
    mock_client.chat.completions.create.side_effect = [err_429, err_429, success_resp]

    provider = GroqProvider(client=mock_client, clock=clock, sleep_fn=sleeper)
    result = provider.generate_result(messages=[{"role": "user", "content": "hi"}])

    assert result.text == "Success after retry"
    assert mock_client.chat.completions.create.call_count == 3
    assert sleeper.delays == [1.0, 2.0]
    assert result.usage.request_count == 3


def test_third_429_raises_retryable_error() -> None:
    clock = FakeClock(1000.0)
    sleeper = FakeSleep(clock)

    mock_client = MagicMock()
    err_429 = Exception("Rate limit")
    err_429.status_code = 429

    mock_client.chat.completions.create.side_effect = [err_429, err_429, err_429]

    provider = GroqProvider(client=mock_client, clock=clock, sleep_fn=sleeper)
    with pytest.raises(ProviderExecutionError) as exc_info:
        provider.generate_result(messages=[{"role": "user", "content": "hi"}])

    assert exc_info.value.status_code == 429
    assert exc_info.value.is_retryable is True
    assert mock_client.chat.completions.create.call_count == 3


# 11, 12, 13. Error Classification (401, Timeout, 5xx)
def test_401_no_retry() -> None:
    mock_client = MagicMock()
    err_401 = Exception("Unauthorized")
    err_401.status_code = 401
    mock_client.chat.completions.create.side_effect = err_401

    provider = GroqProvider(client=mock_client)
    with pytest.raises(ProviderExecutionError) as exc_info:
        provider.generate_result(messages=[{"role": "user", "content": "hi"}])

    assert exc_info.value.status_code == 401
    assert exc_info.value.is_retryable is False
    assert mock_client.chat.completions.create.call_count == 1


def test_timeout_maps_to_retryable_error() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = TimeoutError("Connection timed out")

    provider = GroqProvider(client=mock_client)
    with pytest.raises(ProviderExecutionError) as exc_info:
        provider.generate_result(messages=[{"role": "user", "content": "hi"}])

    assert exc_info.value.is_retryable is True


def test_5xx_maps_to_retryable_error() -> None:
    mock_client = MagicMock()
    err_503 = Exception("Service Unavailable")
    err_503.status_code = 503
    mock_client.chat.completions.create.side_effect = err_503

    provider = GroqProvider(client=mock_client)
    with pytest.raises(ProviderExecutionError) as exc_info:
        provider.generate_result(messages=[{"role": "user", "content": "hi"}])

    assert exc_info.value.status_code == 503
    assert exc_info.value.is_retryable is True


# 14. Malformed/Blank Response
def test_blank_or_malformed_response_rejected() -> None:
    mock_client = MagicMock()
    mock_resp = make_mock_response("")
    mock_client.chat.completions.create.return_value = mock_resp

    provider = GroqProvider(client=mock_client)
    with pytest.raises(ProviderExecutionError, match="empty or invalid"):
        provider.generate_result(messages=[{"role": "user", "content": "hi"}])


# 15, 16, 17. Token Usage & Cost
def test_usage_extraction_and_request_count() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = make_mock_response("OK", prompt_tokens=15, completion_tokens=25)

    provider = GroqProvider(client=mock_client)
    result = provider.generate_result(messages=[{"role": "user", "content": "hi"}])

    assert result.usage.prompt_tokens == 15
    assert result.usage.completion_tokens == 25
    assert result.usage.total_tokens == 40
    assert result.usage.request_count == 1
    assert result.usage.cost_usd == 0.0


# 18. Thread-Safe Cumulative Usage
def test_cumulative_usage_thread_safe() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = make_mock_response("OK", prompt_tokens=10, completion_tokens=10)
    provider = GroqProvider(client=mock_client)

    threads = []
    for _ in range(5):
        t = threading.Thread(target=provider.generate_result, kwargs={"messages": [{"role": "user", "content": "hi"}]})
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    cumulative = provider.get_usage()
    assert cumulative.prompt_tokens == 50
    assert cumulative.completion_tokens == 50
    assert cumulative.total_tokens == 100
    assert cumulative.request_count == 5


# 19 & 20. Health Check & Models Endpoint
def test_health_check_uses_models_endpoint() -> None:
    mock_client = MagicMock()
    m1 = MagicMock()
    m1.id = ALLOWED_MODEL
    mock_client.models.list.return_value.data = [m1]

    provider = GroqProvider(client=mock_client)
    assert provider.get_models() == [ALLOWED_MODEL]
    assert provider.health_check() is True
    assert mock_client.models.list.call_count == 2


# 21 & 22. Log Privacy (No raw exception, API key, or prompt content in logs)
def test_log_privacy(caplog) -> None:
    caplog.set_level(logging.DEBUG)

    secret_prompt = "SECRET_USER_PROMPT_123"
    secret_key = "gsk_SECRET_API_KEY_XYZ"

    mock_client = MagicMock()
    err_500 = Exception("Internal DB Connection Crash Details")
    err_500.status_code = 500
    mock_client.chat.completions.create.side_effect = err_500

    provider = GroqProvider(client=mock_client)
    with pytest.raises(ProviderExecutionError):
        provider.generate_result(messages=[{"role": "user", "content": secret_prompt}])

    for record in caplog.records:
        msg = record.getMessage()
        assert secret_prompt not in msg
        assert secret_key not in msg
        assert "Internal DB Connection Crash" not in msg

def test_health_false_when_allowed_model_missing() -> None:
    mock_client = MagicMock()

    other_model = MagicMock()
    other_model.id = "different-model"

    mock_client.models.list.return_value.data = [
        other_model
    ]

    provider = GroqProvider(
        client=mock_client
    )

    assert provider.get_models() == []
    assert provider.health_check() is False

def test_health_false_when_models_endpoint_fails() -> None:
    mock_client = MagicMock()

    mock_client.models.list.side_effect = RuntimeError(
        "Sensitive infrastructure error"
    )

    provider = GroqProvider(
        client=mock_client
    )

    assert provider.get_models() == []
    assert provider.health_check() is False

def test_malformed_response_counts_attempt_once() -> None:
    mock_client = MagicMock()

    malformed_response = MagicMock()
    malformed_response.choices = []

    mock_client.chat.completions.create.return_value = (
        malformed_response
    )

    provider = GroqProvider(
        client=mock_client
    )

    with pytest.raises(
        ProviderExecutionError
    ):
        provider.generate_result(
            messages=[
                {
                    "role": "user",
                    "content": "test",
                }
            ]
        )

    usage = provider.get_usage()

    assert usage.request_count == 1

def test_inconsistent_total_tokens_is_normalized() -> None:
    mock_client = MagicMock()

    response = make_mock_response(
        text="Safe response",
        prompt_tokens=15,
        completion_tokens=25,
    )

    # Simulate an incorrect total from the provider.
    response.usage.total_tokens = 999

    mock_client.chat.completions.create.return_value = (
        response
    )

    provider = GroqProvider(
        client=mock_client
    )

    result = provider.generate_result(
        messages=[
            {
                "role": "user",
                "content": "test",
            }
        ]
    )

    assert result.usage.prompt_tokens == 15
    assert result.usage.completion_tokens == 25
    assert result.usage.total_tokens == 40

def test_groq_client_preserves_metadata_without_leaking_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = MagicMock()

    provider.provider_name.return_value = "Groq"

    provider.generate_result.side_effect = (
        ProviderExecutionError(
            "SECRET provider database information",
            status_code=503,
            is_retryable=True,
        )
    )

    client = GroqClient(
        provider=provider
    )

    caplog.set_level(
        logging.WARNING
    )

    with pytest.raises(
        AIProviderExecutionError
    ) as exc_info:
        client.generate_result(
            task=AITask.COMMUNICATION,
            messages=[
                {
                    "role": "user",
                    "content": "sanitized prompt",
                }
            ],
            context={},
        )

    assert str(exc_info.value) == (
        "AI provider execution failed."
    )

    assert exc_info.value.status_code == 503
    assert exc_info.value.is_retryable is True

    assert (
        "SECRET provider database information"
        not in caplog.text
    )