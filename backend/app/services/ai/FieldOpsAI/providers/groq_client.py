
"""
groq_client.py

Provider-execution client for FieldOps Commander AI.

Responsibilities
----------------
- Execute AI requests using the configured provider.
- Return the raw provider response.
- Translate provider failures into a safe application error.

The client never:

- Builds prompts
- Renders Jinja2 templates
- Selects fallback communication
- Restores placeholders
- Parses structured responses
- Updates the database
- Sends notifications

Fallback selection belongs to the business workflow, such as
CommunicationService.
"""

from __future__ import annotations

import logging

from typing import Any, Dict, List

from app.services.ai.FieldOpsAI.providers.base_provider import (
    BaseAIProvider,
    ProviderExecutionError,
)
from app.services.ai.FieldOpsAI.providers.provider_factory import (
    ProviderFactory,
)
from app.services.ai.FieldOpsAI.schemas.ai_task import (
    AITask,
)
from app.services.ai.FieldOpsAI.schemas.provider import (
    GenerationResult,
    UsageStats,
)


logger = logging.getLogger(__name__)


class AIProviderExecutionError(ProviderExecutionError):
    """
    Raised when the configured AI provider cannot return a
    usable response.

    Preserves status_code and is_retryable for CircuitBreaker error classification.
    """


class GroqClient:
    """
    Execute requests through the configured AI provider.

    The class name is retained for compatibility with the
    existing orchestrator. ProviderFactory may later return
    another provider without changing this interface.
    """

    def __init__(
        self,
        *,
        provider: BaseAIProvider | None = None,
    ) -> None:
        """
        Initialize the provider client.

        Dependency injection allows tests to use a fake provider
        without making a real Groq request.
        """

        self.provider = (
            provider
            if provider is not None
            else ProviderFactory.create_provider()
        )

    # ---------------------------------------------------------

    def generate_result(
        self,
        task: AITask,
        messages: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> GenerationResult:
        """
        Execute one provider request and return a typed GenerationResult.

        Parameters
        ----------
        task
            AI task being executed.
        messages
            Sanitized system and user messages sent to the provider.
        context
            Sanitized structured context.

        Returns
        -------
        GenerationResult
            Typed completion result containing text and usage stats.
        """
        if not isinstance(task, AITask):
            raise TypeError("task must be an AITask.")

        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a non-empty list.")

        if not isinstance(context, dict):
            raise TypeError("context must be a dictionary.")

        _ = context
        provider_name = self._safe_provider_name()

        logger.info(
            "Executing AI provider request. "
            "Provider=%s Task=%s",
            provider_name,
            task.value,
        )

        try:
            if hasattr(self.provider, "generate_result"):
                res = self.provider.generate_result(messages=messages)
                if not isinstance(res.text, str):
                    raise AIProviderExecutionError(
                        "AI provider returned an invalid response.",
                        status_code=None,
                        is_retryable=False,
                    )
                normalized_text = res.text.strip()
                if not normalized_text:
                    raise AIProviderExecutionError(
                        "AI provider returned an empty response.",
                        status_code=None,
                        is_retryable=False,
                    )
                logger.info(
                    "AI provider request completed. "
                    "Provider=%s Task=%s",
                    provider_name,
                    task.value,
                )

                return GenerationResult(
                    text=normalized_text,
                    provider_name=res.provider_name,
                    model_name=res.model_name,
                    usage=res.usage,
                )
            else:
                raw_text = self.provider.generate_completion(messages=messages)
                if not isinstance(raw_text, str):
                    raise AIProviderExecutionError(
                        "AI provider returned an invalid response.",
                        status_code=None,
                        is_retryable=False,
                    )
                normalized_text = raw_text.strip()
                if not normalized_text:
                    raise AIProviderExecutionError(
                        "AI provider returned an empty response.",
                        status_code=None,
                        is_retryable=False,
                    )

                p_name = self.provider.provider_name() if hasattr(self.provider, "provider_name") else "Groq"
                m_name = self.provider.model_name() if hasattr(self.provider, "model_name") else "llama-3.3-70b-versatile"
                if hasattr(self.provider, "get_usage"):
                    usage = self.provider.get_usage()
                else:
                    usage = UsageStats(
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                        request_count=1,
                        latency_ms=0.0,
                        cost_usd=0.0,
                    )

                return GenerationResult(
                    text=normalized_text,
                    provider_name=p_name,
                    model_name=m_name,
                    usage=usage,
                )

        except AIProviderExecutionError:
            raise

        except ProviderExecutionError as provider_error:
            logger.warning(
                "AI provider execution failed. "
                "Provider=%s Task=%s",
                provider_name,
                task.value,
            )

            raise AIProviderExecutionError(
                "AI provider execution failed.",
                status_code=provider_error.status_code,
                is_retryable=provider_error.is_retryable,
            ) from None
        except Exception as exc:
            logger.warning(
                "AI provider execution failed. "
                "Provider=%s Task=%s",
                provider_name,
                task.value,
            )

            status_code = getattr(exc, "status_code", None)
            is_retryable = getattr(exc, "is_retryable", False)
            if not isinstance(is_retryable, bool):
                is_retryable = False

            raise AIProviderExecutionError(
                "AI provider execution failed.",
                status_code=status_code if isinstance(status_code, int) else None,
                is_retryable=is_retryable,
            ) from None

    def generate(
        self,
        task: AITask,
        messages: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> str:
        """
        Execute one provider request and return the raw text response.
        Compatibility wrapper around generate_result.
        """
        result = self.generate_result(
            task=task,
            messages=messages,
            context=context,
        )

        return result.text

    # ---------------------------------------------------------

    def _safe_provider_name(
        self,
    ) -> str:
        """
        Return a safe provider name for operational logging.

        Provider metadata failures must never prevent request
        execution.
        """

        try:
            provider_name = (
                self.provider.provider_name()
            )

        except Exception:
            return "unknown"

        if not isinstance(
            provider_name,
            str,
        ):
            return "unknown"

        normalized_name = provider_name.strip()

        return (
            normalized_name
            if normalized_name
            else "unknown"
        )