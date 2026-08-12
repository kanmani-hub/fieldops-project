"""
test_orchestrator_failover.py

Unit tests for AIOrchestrator provider failover,
circuit breaker, token budget, cleanup safety,
privacy boundaries, and injected-client compatibility.
"""

from __future__ import annotations

from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from app.services.ai.FieldOpsAI.providers.base_provider import (
    BaseAIProvider,
    ProviderConfigurationError,
    ProviderExecutionError,
)
from app.services.ai.FieldOpsAI.providers.budget import (
    BudgetExceededError,
)
from app.services.ai.FieldOpsAI.providers.provider_failover import (
    FailoverAttempt,
    FailoverExecutionResult,
    ProviderFailoverExecutor,
    ProviderFailoverExhaustedError,
)
from app.services.ai.FieldOpsAI.runtime.circuit_breaker import (
    CircuitOpenError,
    CircuitPermit,
)
from app.services.ai.FieldOpsAI.runtime.orchestrator import (
    AIOrchestrator,
)
from app.services.ai.FieldOpsAI.schemas.ai_task import (
    AITask,
)
from app.services.ai.FieldOpsAI.schemas.provider import (
    GenerationResult,
    ProviderHealth,
    UsageStats,
)


# ==========================================================
# Fake Provider
# ==========================================================


class FakeProvider(BaseAIProvider):
    """
    Small deterministic provider used only by tests.
    """

    def __init__(
        self,
        name: str = "groq",
        model: str = "llama-3.3-70b-versatile",
    ) -> None:
        self._name = name
        self._model = model

    def generate_completion(
        self,
        messages,
        temperature=None,
        max_tokens=None,
    ) -> str:
        return '{"summary": "test"}'

    def provider_name(self) -> str:
        return self._name

    def model_name(self) -> str:
        return self._model

    def health_check(self) -> bool:
        return True


# ==========================================================
# Fake Provider Client
# ==========================================================


class FakeProviderClient:
    """
    Explicit provider client used instead of relying on
    undefined MagicMock attributes.
    """

    def __init__(
        self,
        *,
        provider: BaseAIProvider,
        result: GenerationResult | None = None,
        error: Exception | None = None,
        callback: Callable[..., GenerationResult] | None = None,
    ) -> None:
        self.provider = provider
        self.result = result
        self.error = error
        self.callback = callback
        self.call_count = 0

    def generate_result(
        self,
        task: AITask,
        messages: list[dict[str, str]],
        context: dict[str, Any],
    ) -> GenerationResult:
        self.call_count += 1

        if self.callback is not None:
            return self.callback(
                task,
                messages,
                context,
            )

        if self.error is not None:
            raise self.error

        if self.result is None:
            raise RuntimeError(
                "FakeProviderClient has no configured result."
            )

        return self.result


# ==========================================================
# Result Helper
# ==========================================================


def make_gen_result(
    provider_name: str = "groq",
    text: str = '{"summary": "ok"}',
    model: str = "llama-3.3-70b-versatile",
) -> GenerationResult:
    return GenerationResult(
        text=text,
        provider_name=provider_name,
        model_name=model,
        usage=UsageStats(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            request_count=1,
            latency_ms=50.0,
            cost_usd=0.0,
        ),
    )


# ==========================================================
# Isolated Orchestrator Helper
# ==========================================================


def make_isolated_orchestrator(
    *,
    client: Any | None = None,
    failover_executor: Any | None = None,
    health_monitor: Any | None = None,
    provider_client_factory: Any | None = None,
    sanitizer: Any | None = None,
    budget_manager: Any | None = None,
    circuit_breaker: Any | None = None,
    provider_cache: Any | None = None,
) -> AIOrchestrator:
    """
    Create an AIOrchestrator with external infrastructure
    replaced by deterministic test dependencies.
    """

    mock_health = (
        health_monitor
        if health_monitor is not None
        else MagicMock()
    )

    mock_health.get_snapshot.return_value = None

    healthy_snapshot = MagicMock()
    healthy_snapshot.status = ProviderHealth.HEALTHY

    mock_health.check_provider.return_value = (
        healthy_snapshot
    )

    if circuit_breaker is None:
        mock_circuit = MagicMock()

        mock_circuit.check_permission.return_value = (
            CircuitPermit(
                provider_scope="groq",
            )
        )

        circuit_breaker = mock_circuit

    if budget_manager is None:
        mock_budget = MagicMock()

        mock_budget.config.per_request = {
            "general": 4096,
            "sentiment": 4096,
            "sms": 4096,
            "email": 4096,
            "push": 4096,
            "portal": 4096,
        }

        mock_budget.reserve.return_value = (
            "res_123"
        )

        budget_manager = mock_budget

    if provider_cache is None:
        mock_cache = MagicMock()

        # These orchestration tests are not cache tests.
        # Every test must begin with a deterministic cache miss.
        mock_cache.get.return_value = None
        mock_cache.set.return_value = False
        mock_cache.delete.return_value = False
        mock_cache.invalidate_provider_namespace.return_value = 0

        provider_cache = mock_cache

    # When no client or factory is supplied, create a
    # deterministic successful provider client.
    if (
        client is None
        and provider_client_factory is None
    ):
        provider = FakeProvider("groq")

        client = FakeProviderClient(
            provider=provider,
            result=make_gen_result("groq"),
        )

    # Avoid production provider construction when a custom
    # provider-client factory is supplied without a failover
    # executor.
    if (
        failover_executor is None
        and client is None
    ):
        mock_config = MagicMock()
        mock_config.provider_fallback_order = [
            "groq"
        ]

        mock_factory = MagicMock()

        mock_factory.create_provider.return_value = (
            FakeProvider("groq")
        )

        failover_executor = (
            ProviderFailoverExecutor(
                provider_factory=mock_factory,
                health_monitor=None,
                config=mock_config,
            )
        )

    return AIOrchestrator(
        client=client,
        budget_manager=budget_manager,
        circuit_breaker=circuit_breaker,
        failover_executor=failover_executor,
        provider_health_monitor=mock_health,
        sanitizer=sanitizer,
        provider_client_factory=provider_client_factory,
        provider_cache=provider_cache,
    )


# ==========================================================
# 1. Prompt built once
# ==========================================================


def test_prompt_built_once_across_attempts() -> None:
    mock_builder = MagicMock()
    mock_builder.build.return_value = (
        "System Instructions"
    )

    mock_failover = MagicMock()

    generation_result = make_gen_result(
        "openai"
    )

    mock_failover.execute.return_value = (
        FailoverExecutionResult(
            generation_result=generation_result,
            selected_provider="openai",
            attempts=(
                FailoverAttempt(
                    provider_name="groq",
                    attempted=True,
                    skipped=False,
                    succeeded=False,
                    retryable=True,
                ),
                FailoverAttempt(
                    provider_name="openai",
                    attempted=True,
                    skipped=False,
                    succeeded=True,
                    retryable=False,
                ),
            ),
            failover_occurred=True,
        )
    )

    orchestrator = make_isolated_orchestrator(
        failover_executor=mock_failover
    )

    orchestrator.prompt_builder = mock_builder

    orchestrator.execute(
        AITask.PLANNING,
        {
            "customer_name": "Alice Smith",
        },
    )

    mock_builder.build.assert_called_once()


# ==========================================================
# 2. Context sanitized once
# ==========================================================


def test_context_sanitized_once() -> None:
    mock_sanitizer = MagicMock()
    mock_result = MagicMock()

    mock_result.sanitized_data = {
        "customer_name": "NAME_1"
    }

    mock_result.placeholder_map = MagicMock()
    mock_result.replacement_count = 1

    mock_sanitizer.sanitize.return_value = (
        mock_result
    )

    mock_sanitizer.sanitize_prompt.return_value = (
        "sanitized prompt",
        mock_result.placeholder_map,
    )

    mock_sanitizer.restore_data.return_value = (
        '{"summary": "ok"}'
    )

    orchestrator = make_isolated_orchestrator(
        sanitizer=mock_sanitizer
    )

    orchestrator.execute(
        AITask.PLANNING,
        {
            "customer_name": "Alice",
        },
    )

    mock_sanitizer.sanitize.assert_called_once()


# ==========================================================
# 3. Original context never reaches provider
# ==========================================================


def test_original_context_never_reaches_provider() -> None:
    received_contexts: list[
        dict[str, Any]
    ] = []

    def client_factory(
        provider: BaseAIProvider,
    ) -> FakeProviderClient:
        def generate(
            task: AITask,
            messages: list[dict[str, str]],
            context: dict[str, Any],
        ) -> GenerationResult:
            received_contexts.append(context)

            return make_gen_result(
                provider.provider_name()
            )

        return FakeProviderClient(
            provider=provider,
            callback=generate,
        )

    orchestrator = make_isolated_orchestrator(
        provider_client_factory=client_factory
    )

    raw_context = {
        "phone": "555-1234",
        "secret_key": "123",
    }

    orchestrator.execute(
        AITask.PLANNING,
        raw_context,
    )

    assert len(received_contexts) == 1

    assert "555-1234" not in str(
        received_contexts[0]
    )


# ==========================================================
# 4. Primary provider success
# ==========================================================


def test_primary_provider_success() -> None:
    provider = FakeProvider("groq")

    client = FakeProviderClient(
        provider=provider,
        result=make_gen_result(
            "groq",
            '{"summary": "primary ok"}',
        ),
    )

    orchestrator = make_isolated_orchestrator(
        client=client
    )

    result = orchestrator.execute(
        AITask.PLANNING,
        {
            "job_id": "123",
        },
    )

    assert "primary ok" in str(result)
    assert client.call_count == 1


# ==========================================================
# 5. Retryable primary failure invokes secondary
# ==========================================================


def test_retryable_primary_failure_invokes_secondary() -> None:
    mock_config = MagicMock()

    mock_config.provider_fallback_order = [
        "groq",
        "openai",
    ]

    called_providers: list[str] = []

    def client_factory(
        provider: BaseAIProvider,
    ) -> FakeProviderClient:
        def generate(
            task: AITask,
            messages: list[dict[str, str]],
            context: dict[str, Any],
        ) -> GenerationResult:
            provider_name = (
                provider.provider_name()
            )

            called_providers.append(
                provider_name
            )

            if provider_name == "groq":
                raise ProviderExecutionError(
                    "Groq rate limit.",
                    status_code=429,
                    is_retryable=True,
                )

            return make_gen_result(
                "openai",
                '{"summary": "secondary ok"}',
            )

        return FakeProviderClient(
            provider=provider,
            callback=generate,
        )

    mock_factory = MagicMock()

    mock_factory.create_provider.side_effect = (
        lambda name,
        config=None,
        provider_kwargs=None: FakeProvider(
            name
        )
    )

    failover_executor = (
        ProviderFailoverExecutor(
            provider_factory=mock_factory,
            health_monitor=None,
            config=mock_config,
        )
    )

    orchestrator = make_isolated_orchestrator(
        failover_executor=failover_executor,
        provider_client_factory=client_factory,
    )

    result = orchestrator.execute(
        AITask.PLANNING,
        {
            "job_id": "123",
        },
    )

    assert called_providers == [
        "groq",
        "openai",
    ]

    assert "secondary ok" in str(result)


# ==========================================================
# 6. Single-provider circuit open reaches caller
# ==========================================================


def test_one_provider_circuit_open_reaches_caller_as_circuit_open_error() -> None:
    mock_circuit = MagicMock()

    mock_circuit.check_permission.side_effect = (
        CircuitOpenError(
            "Circuit is OPEN."
        )
    )

    mock_budget = MagicMock()

    orchestrator = make_isolated_orchestrator(
        circuit_breaker=mock_circuit,
        budget_manager=mock_budget,
    )

    with pytest.raises(CircuitOpenError):
        orchestrator.execute(
            AITask.PLANNING,
            {
                "job_id": "123",
            },
        )

    mock_budget.reserve.assert_not_called()


# ==========================================================
# 7. Primary circuit open tries secondary
# ==========================================================


def test_primary_circuit_open_tries_secondary() -> None:
    mock_config = MagicMock()

    mock_config.provider_fallback_order = [
        "groq",
        "openai",
    ]

    mock_circuit = MagicMock()

    def check_permission(
        provider_name: str,
    ) -> CircuitPermit:
        if provider_name == "groq":
            raise CircuitOpenError(
                "Groq circuit is OPEN."
            )

        return CircuitPermit(
            provider_scope=provider_name
        )

    mock_circuit.check_permission.side_effect = (
        check_permission
    )

    def client_factory(
        provider: BaseAIProvider,
    ) -> FakeProviderClient:
        return FakeProviderClient(
            provider=provider,
            result=make_gen_result(
                provider.provider_name(),
                '{"summary": "secondary ok"}',
            ),
        )

    mock_factory = MagicMock()

    mock_factory.create_provider.side_effect = (
        lambda name,
        config=None,
        provider_kwargs=None: FakeProvider(
            name
        )
    )

    failover_executor = (
        ProviderFailoverExecutor(
            provider_factory=mock_factory,
            health_monitor=None,
            config=mock_config,
        )
    )

    orchestrator = make_isolated_orchestrator(
        failover_executor=failover_executor,
        circuit_breaker=mock_circuit,
        provider_client_factory=client_factory,
    )

    result = orchestrator.execute(
        AITask.PLANNING,
        {
            "job_id": "123",
        },
    )

    assert "secondary ok" in str(result)


# ==========================================================
# 8. Category and tenant from sanitized context
# ==========================================================


def test_category_and_tenant_from_sanitized_context_only() -> None:
    mock_sanitizer = MagicMock()
    mock_result = MagicMock()

    mock_result.sanitized_data = {
        "channel": "sms",
        "tenant_id": "sanitized_tenant_1",
    }

    mock_result.placeholder_map = MagicMock()
    mock_result.replacement_count = 0

    mock_sanitizer.sanitize.return_value = (
        mock_result
    )

    mock_sanitizer.sanitize_prompt.return_value = (
        "prompt",
        mock_result.placeholder_map,
    )

    mock_sanitizer.restore_data.return_value = (
        '{"summary": "ok"}'
    )

    mock_budget = MagicMock()

    mock_budget.config.per_request = {
        "sms": 4096
    }

    mock_budget.reserve.return_value = (
        "res_123"
    )

    orchestrator = make_isolated_orchestrator(
        sanitizer=mock_sanitizer,
        budget_manager=mock_budget,
    )

    raw_context = {
        "channel": "EMAIL_RAW",
        "tenant_id": "raw_tenant_secret",
    }

    orchestrator.execute(
        AITask.COMMUNICATION,
        raw_context,
    )

    reserve_arguments = (
        mock_budget.reserve.call_args.kwargs
    )

    assert (
        reserve_arguments["category"]
        == "sms"
    )

    assert (
        reserve_arguments["tenant_id"]
        == "sanitized_tenant_1"
    )

    assert (
        "raw_tenant_secret"
        not in str(reserve_arguments)
    )


# ==========================================================
# 9. Mismatched result cancels reservation
# ==========================================================


def test_mismatched_provider_result_cancels_reservation() -> None:
    mock_budget = MagicMock()

    mock_budget.config.per_request = {
        "general": 4096
    }

    mock_budget.reserve.return_value = (
        "res_123"
    )

    mock_circuit = MagicMock()

    permit = CircuitPermit(
        provider_scope="groq"
    )

    mock_circuit.check_permission.return_value = (
        permit
    )

    def client_factory(
        provider: BaseAIProvider,
    ) -> FakeProviderClient:
        return FakeProviderClient(
            provider=provider,
            result=make_gen_result(
                "openai"
            ),
        )

    orchestrator = make_isolated_orchestrator(
        circuit_breaker=mock_circuit,
        budget_manager=mock_budget,
        provider_client_factory=client_factory,
    )

    with pytest.raises(
        ProviderExecutionError
    ) as exc_info:
        orchestrator.execute(
            AITask.PLANNING,
            {
                "job_id": "123",
            },
        )

    assert str(exc_info.value) == (
        "AI provider execution failed."
    )

    mock_budget.cancel.assert_called_once_with(
        reservation_id="res_123",
        provider="groq",
    )

    mock_circuit.record_failure.assert_called_once()

    mock_circuit.record_success.assert_not_called()

    mock_budget.reconcile.assert_not_called()


# ==========================================================
# 10. Client-construction failure is sanitized
# ==========================================================


def test_raw_client_construction_error_absent_from_exception_chain() -> None:
    secret_key = (
        "gsk_SECRET_CLIENT_INIT_KEY"
    )

    def failing_factory(
        provider: BaseAIProvider,
    ):
        raise RuntimeError(
            f"Failure with {secret_key}"
        )

    orchestrator = make_isolated_orchestrator(
        provider_client_factory=failing_factory
    )

    with pytest.raises(
        ProviderExecutionError
    ) as exc_info:
        orchestrator.execute(
            AITask.PLANNING,
            {
                "job_id": "123",
            },
        )

    assert secret_key not in str(
        exc_info.value
    )

    assert str(exc_info.value) == (
        "AI provider execution failed."
    )


# ==========================================================
# 11. Injected client uses one provider
# ==========================================================


def test_client_injection_uses_one_provider_only() -> None:
    provider = FakeProvider("groq")

    client = FakeProviderClient(
        provider=provider,
        result=make_gen_result("groq"),
    )

    orchestrator = AIOrchestrator(
        client=client
    )

    with pytest.raises(
        ProviderConfigurationError
    ):
        (
            orchestrator
            .failover_executor
            .provider_factory
            .create_provider("openai")
        )


# ==========================================================
# 12. Exhausted error remains typed
# ==========================================================


def test_provider_failover_exhausted_error_remains_typed() -> None:
    def failing_factory(
        provider: BaseAIProvider,
    ) -> FakeProviderClient:
        return FakeProviderClient(
            provider=provider,
            error=ProviderExecutionError(
                "Rate limit.",
                status_code=429,
                is_retryable=True,
            ),
        )

    orchestrator = make_isolated_orchestrator(
        provider_client_factory=failing_factory
    )

    with pytest.raises(
        ProviderFailoverExhaustedError
    ):
        orchestrator.execute(
            AITask.PLANNING,
            {
                "job_id": "123",
            },
        )


# ==========================================================
# 13. Budget failure releases HALF_OPEN permit
# ==========================================================


def test_budget_failure_releases_half_open_probe() -> None:
    mock_circuit = MagicMock()

    permit = CircuitPermit(
        provider_scope="groq",
        is_half_open_probe=True,
        probe_token=(
            "0123456789abcdef"
            "0123456789abcdef"
        ),
    )

    mock_circuit.check_permission.return_value = (
        permit
    )

    mock_budget = MagicMock()

    mock_budget.config.per_request = {
        "general": 4096
    }

    mock_budget.reserve.side_effect = (
        BudgetExceededError(
            "Limit exceeded."
        )
    )

    provider = FakeProvider("groq")

    client = FakeProviderClient(
        provider=provider,
        result=make_gen_result("groq"),
    )

    orchestrator = make_isolated_orchestrator(
        client=client,
        circuit_breaker=mock_circuit,
        budget_manager=mock_budget,
    )

    with pytest.raises(
        ProviderExecutionError
    ) as exc_info:
        orchestrator.execute(
            AITask.PLANNING,
            {
                "job_id": "123",
            },
        )

    assert str(exc_info.value) == (
        "AI provider execution failed."
    )

    (
        mock_circuit
        .release_probe_lock
        .assert_called_once_with(permit)
    )

    mock_circuit.record_success.assert_not_called()
    mock_circuit.record_failure.assert_not_called()

    assert client.call_count == 0