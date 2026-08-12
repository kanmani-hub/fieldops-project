"""
Tests for the provider-only GroqClient.

No real Groq API request is made.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

import pytest

from app.services.ai.FieldOpsAI.providers.groq_client import (
    AIProviderExecutionError,
    GroqClient,
)
from app.services.ai.FieldOpsAI.schemas.ai_task import (
    AITask,
)


class FakeProvider:
    """
    Controlled provider replacement used by unit tests.
    """

    def __init__(
        self,
        *,
        response: Any = (
            '{"message": "Safe response"}'
        ),
        error: Exception | None = None,
        name: str = "FakeProvider",
    ) -> None:
        self.response = response
        self.error = error
        self.name = name

        self.call_count = 0

        self.received_messages: (
            Sequence[Dict[str, Any]]
            | None
        ) = None

    def generate_completion(
        self,
        messages: Sequence[
            Dict[str, Any]
        ],
        temperature: Optional[
            float
        ] = None,
        max_tokens: Optional[
            int
        ] = None,
    ) -> str:
        """
        Return the configured response or raise an error.
        """

        _ = temperature
        _ = max_tokens

        self.call_count += 1
        self.received_messages = messages

        if self.error is not None:
            raise self.error

        return self.response

    def provider_name(
        self,
    ) -> str:
        return self.name

    def model_name(
        self,
    ) -> str:
        return "fake-model"

    def health_check(
        self,
    ) -> bool:
        return True


def build_messages() -> list[
    dict[str, str]
]:
    """
    Build sanitized provider messages.
    """

    return [
        {
            "role": "system",
            "content": (
                "Return valid JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                "Customer: {{customer_name}}"
            ),
        },
    ]


def build_context() -> dict[str, Any]:
    """
    Build sanitized structured context.
    """

    return {
        "customer_name": (
            "{{customer_name}}"
        ),
        "job_id": "{{job_id}}",
        "job_status": "ASSIGNED",
    }


def build_client(
    provider: FakeProvider,
) -> GroqClient:
    """
    Build a client with an injected fake provider.
    """

    return GroqClient(
        provider=provider,  # type: ignore[arg-type]
    )


def test_generate_returns_trimmed_provider_response(
) -> None:
    """
    Successful provider text is returned without surrounding
    whitespace.
    """

    provider = FakeProvider(
        response=(
            '  {"message": "Safe response"}  '
        )
    )

    response = build_client(
        provider
    ).generate(
        task=AITask.COMMUNICATION,
        messages=build_messages(),
        context=build_context(),
    )

    assert response == (
        '{"message": "Safe response"}'
    )

    assert provider.call_count == 1

    assert (
        provider.received_messages
        == build_messages()
    )


def test_provider_failure_raises_safe_error(
) -> None:
    """
    Provider failures are not converted into Jinja output.
    """

    provider = FakeProvider(
        error=RuntimeError(
            "Secret provider failure details."
        )
    )

    with pytest.raises(
        AIProviderExecutionError,
        match="AI provider execution failed",
    ) as captured:
        build_client(
            provider
        ).generate(
            task=AITask.COMMUNICATION,
            messages=build_messages(),
            context=build_context(),
        )

    assert (
        "Secret provider failure details"
        not in str(
            captured.value
        )
    )

    assert provider.call_count == 1


def test_empty_provider_response_is_rejected(
) -> None:
    """
    Empty text cannot continue to response parsing.
    """

    provider = FakeProvider(
        response="   "
    )

    with pytest.raises(
        AIProviderExecutionError,
        match="empty response",
    ):
        build_client(
            provider
        ).generate(
            task=AITask.PLANNING,
            messages=build_messages(),
            context=build_context(),
        )


def test_non_string_provider_response_is_rejected(
) -> None:
    """
    Provider implementations must return text.
    """

    provider = FakeProvider(
        response={
            "message": "not raw text",
        }
    )

    with pytest.raises(
        AIProviderExecutionError,
        match="invalid response",
    ):
        build_client(
            provider
        ).generate(
            task=AITask.DISPATCH,
            messages=build_messages(),
            context=build_context(),
        )


def test_client_has_no_jinja_fallback_provider(
) -> None:
    """
    Template fallback no longer belongs to GroqClient.
    """

    client = build_client(
        FakeProvider()
    )

    assert not hasattr(
        client,
        "jinja_provider",
    )

    assert not hasattr(
        client,
        "_fallback",
    )

    assert not hasattr(
        client,
        "_resolve_template",
    )


def test_invalid_messages_are_rejected_before_provider_call(
) -> None:
    """
    An empty message list must not reach the provider.
    """

    provider = FakeProvider()

    with pytest.raises(
        ValueError,
        match="non-empty list",
    ):
        build_client(
            provider
        ).generate(
            task=AITask.COMMUNICATION,
            messages=[],
            context=build_context(),
        )

    assert provider.call_count == 0