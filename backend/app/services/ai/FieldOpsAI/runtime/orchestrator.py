"""
orchestrator.py

Central orchestration layer for the FieldOps Commander AI.

Responsibilities
----------------
- Build the complete system prompt.
- Load the task-specific prompt.
- Sanitize PII before external AI provider calls.
- Validate that no detectable PII remains in user prompts.
- Execute the provider failover chain.
- Apply per-provider circuit breaker and token budget handling.
- Read and write sanitized provider responses through ProviderCache.
- Restore permitted placeholder values locally.
- Parse and validate structured AI responses.

Privacy rule
------------
The original context must never be passed to Groq or another
external AI provider. Only sanitized context and prompts may
cross the provider boundary.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel

from app.redis_client import get_redis_client
from app.services.ai.FieldOpsAI.config.config_loader import (
    ConfigLoader,
)
from app.services.ai.FieldOpsAI.providers.base_provider import (
    BaseAIProvider,
    ProviderConfigurationError,
    ProviderExecutionError,
)
from app.services.ai.FieldOpsAI.providers.budget import (
    BudgetExceededError,
    BudgetInfrastructureError,
    SyncTokenBudgetManager,
    TokenBudgetConfig,
)
from app.services.ai.FieldOpsAI.providers.cache import (
    CacheTTLPolicy,
    CachedProviderResponse,
    ProviderCacheConfig,
    ProviderCacheRequest,
    SyncProviderCache,
)
from app.services.ai.FieldOpsAI.providers.groq_client import (
    GroqClient,
)
from app.services.ai.FieldOpsAI.providers.provider_factory import (
    ProviderFactory,
)
from app.services.ai.FieldOpsAI.providers.provider_failover import (
    ProviderFailoverExecutor,
    ProviderFailoverExhaustedError,
)
from app.services.ai.FieldOpsAI.providers.provider_health import (
    ProviderHealthInfrastructureError,
    ProviderHealthMonitor,
)
from app.services.ai.FieldOpsAI.runtime.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerInfrastructureError,
    CircuitOpenError,
    CircuitPermit,
)
from app.services.ai.FieldOpsAI.runtime.prompt_builder import (
    PromptBuilder,
)
from app.services.ai.FieldOpsAI.runtime.response_parser import (
    ResponseParser,
)
from app.services.ai.FieldOpsAI.runtime.runtime_interface import (
    RuntimeInterface,
)
from app.services.ai.FieldOpsAI.schemas.ai_task import (
    AITask,
)
from app.services.ai.FieldOpsAI.schemas.provider import (
    GenerationResult,
    UsageStats,
)
from app.services.ai.pii_sanitizer import (
    PIISanitizer,
    PIILeakageError,
    PlaceholderMap,
    pii_sanitizer,
)


logger = logging.getLogger(__name__)


class _LegacyClientProviderAdapter(BaseAIProvider):
    """
    Provider metadata adapter for injected legacy clients.

    The adapter supplies provider and model metadata only.
    Provider execution remains the responsibility of the
    injected client.
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
        raise NotImplementedError(
            "Legacy adapter does not perform provider calls."
        )

    def provider_name(self) -> str:
        return self._name

    def model_name(self) -> str:
        return self._model

    def health_check(self) -> bool:
        return True


class AIOrchestrator(RuntimeInterface):
    """
    Coordinates prompt construction, privacy, provider failover,
    circuit protection, budget accounting, caching, placeholder
    restoration, and structured response validation.
    """

    def __init__(
        self,
        *,
        failover_executor: ProviderFailoverExecutor | None = None,
        provider_health_monitor: ProviderHealthMonitor | None = None,
        provider_client_factory: (
            Callable[[BaseAIProvider], Any] | None
        ) = None,
        client: Any | None = None,
        sanitizer: PIISanitizer | None = None,
        prompt_builder: PromptBuilder | None = None,
        response_parser: ResponseParser | None = None,
        budget_manager: SyncTokenBudgetManager | None = None,
        provider_cache: SyncProviderCache | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        """
        Initialize the AI orchestrator.

        Dependencies may be injected by tests or other runtime
        integrations. Production dependencies are created only
        when an injected dependency is not supplied.
        """

        self.prompt_builder = (
            prompt_builder
            if prompt_builder is not None
            else PromptBuilder()
        )

        self.pii_sanitizer = (
            sanitizer
            if sanitizer is not None
            else pii_sanitizer
        )

        self.response_parser = (
            response_parser
            if response_parser is not None
            else ResponseParser()
        )

        self.client = client

        base_config_loader = ConfigLoader()

        # --------------------------------------------------
        # Shared synchronous Redis client
        # --------------------------------------------------

        redis_client: Any | None = None

        if circuit_breaker is not None:
            try:
                redis_client = vars(
                    circuit_breaker
                ).get("redis")
            except TypeError:
                redis_client = None

        if (
            redis_client is None
            and budget_manager is not None
        ):
            try:
                redis_client = vars(
                    budget_manager
                ).get("redis")
            except TypeError:
                redis_client = None

        if redis_client is None:
            redis_client = get_redis_client()

        # --------------------------------------------------
        # Provider health monitor
        # --------------------------------------------------

        if provider_health_monitor is not None:
            self.provider_health_monitor = (
                provider_health_monitor
            )
        else:
            self.provider_health_monitor = (
                ProviderHealthMonitor(
                    redis_client,
                    config=(
                        base_config_loader
                        .provider_health
                    ),
                )
            )

        # --------------------------------------------------
        # Injected-client provider factory
        # --------------------------------------------------

        if client is not None:
            raw_provider = getattr(
                client,
                "provider",
                None,
            )

            candidate_name: str | None = None
            candidate_model: str | None = None

            if raw_provider is not None:
                try:
                    raw_name = (
                        raw_provider.provider_name()
                    )

                    raw_model = (
                        raw_provider.model_name()
                    )

                    if (
                        isinstance(raw_name, str)
                        and raw_name.strip()
                        and isinstance(raw_model, str)
                        and raw_model.strip()
                    ):
                        candidate_name = (
                            raw_name.strip().lower()
                        )

                        candidate_model = (
                            raw_model.strip()
                        )

                except Exception:
                    candidate_name = None
                    candidate_model = None

            if (
                candidate_name is not None
                and candidate_model is not None
            ):
                injected_provider = raw_provider
                injected_provider_name = candidate_name

            else:
                injected_provider_name = "groq"

                injected_provider = (
                    _LegacyClientProviderAdapter(
                        name=injected_provider_name,
                        model=(
                            base_config_loader
                            .model_name
                        ),
                    )
                )

            class SingleClientProviderFactory:
                """
                Provider factory used for one injected client.
                """

                @classmethod
                def create_provider(
                    cls,
                    name: str,
                    config=None,
                    provider_kwargs=None,
                ) -> BaseAIProvider:
                    normalized_name = (
                        name.strip().lower()
                    )

                    if (
                        normalized_name
                        != injected_provider_name
                    ):
                        raise ProviderConfigurationError(
                            "Configured AI provider "
                            "is unsupported."
                        )

                    return injected_provider

                @classmethod
                def registered_names(
                    cls,
                ) -> list[str]:
                    return [
                        injected_provider_name
                    ]

            class InjectedSingleConfigLoader:
                """
                Config wrapper that limits an injected client
                to one provider.
                """

                def __getattr__(
                    self,
                    name: str,
                ) -> Any:
                    return getattr(
                        base_config_loader,
                        name,
                    )

                @property
                def provider_fallback_order(
                    self,
                ) -> list[str]:
                    return [
                        injected_provider_name
                    ]

            factory_to_use = (
                SingleClientProviderFactory
            )

            config_loader: Any = (
                InjectedSingleConfigLoader()
            )

            # Do not perform production provider-health Redis
            # lookups for an injected single client.
            executor_health_monitor = None

        else:
            factory_to_use = ProviderFactory
            config_loader = base_config_loader

            executor_health_monitor = (
                self.provider_health_monitor
            )

        # --------------------------------------------------
        # Failover executor
        # --------------------------------------------------

        if failover_executor is not None:
            self.failover_executor = (
                failover_executor
            )
        else:
            self.failover_executor = (
                ProviderFailoverExecutor(
                    provider_factory=(
                        factory_to_use
                    ),
                    health_monitor=(
                        executor_health_monitor
                    ),
                    config=config_loader,
                )
            )

        # --------------------------------------------------
        # Token budget
        # --------------------------------------------------

        if budget_manager is not None:
            self.token_budget_manager = (
                budget_manager
            )
        else:
            budget_config = (
                TokenBudgetConfig.from_mapping(
                    config_loader.provider_budget
                )
            )

            self.token_budget_manager = (
                SyncTokenBudgetManager(
                    redis_client,
                    budget_config,
                )
            )

        # --------------------------------------------------
        # Provider cache
        # --------------------------------------------------

        if provider_cache is not None:
            self.provider_cache = provider_cache
        else:
            raw_cache_config = getattr(
                config_loader,
                "provider_cache",
                {},
            )

            if isinstance(
                raw_cache_config,
                dict,
            ):
                cache_config = (
                    ProviderCacheConfig(
                        **raw_cache_config
                    )
                )
            else:
                cache_config = (
                    ProviderCacheConfig()
                )

            self.provider_cache = (
                SyncProviderCache(
                    redis_client,
                    cache_config,
                )
            )

        # --------------------------------------------------
        # Circuit breaker
        # --------------------------------------------------

        if circuit_breaker is not None:
            self.circuit_breaker = (
                circuit_breaker
            )
        else:
            circuit_config = (
                CircuitBreakerConfig.from_mapping(
                    config_loader
                    .provider_circuit_breaker
                )
            )

            self.circuit_breaker = (
                CircuitBreaker(
                    redis_client,
                    circuit_config,
                )
            )

        # --------------------------------------------------
        # Provider-client construction
        # --------------------------------------------------

        if provider_client_factory is not None:
            self.provider_client_factory = (
                provider_client_factory
            )

        elif self.client is not None:
            self.provider_client_factory = (
                lambda provider: self.client
            )

        else:
            self.provider_client_factory = (
                lambda provider: GroqClient(
                    provider=provider
                )
            )

    def _load_task_prompt(
        self,
        task: AITask,
    ) -> str:
        """
        Load a task prompt from the prompt registry.
        (Kept as a compatibility wrapper).
        """
        return self.prompt_builder.get_task_prompt(task)

    def execute(
        self,
        task: AITask,
        context: Dict[str, Any],
        response_schema: (
            Optional[Type[BaseModel]]
        ) = None,
    ) -> str | BaseModel:
        """
        Execute one AI task using only sanitized provider-bound
        data.
        """

        placeholder_map: (
            PlaceholderMap | None
        ) = None

        try:
            logger.info(
                "Starting AI task '%s'.",
                task.value,
            )

            # ----------------------------------------------
            # 1. Build prompts once
            # ----------------------------------------------

            system_prompt = (
                self.prompt_builder.build()
                + "\n\n"
                + self._load_task_prompt(task)
            )

            # ----------------------------------------------
            # 2. Sanitize context once
            # ----------------------------------------------

            sanitization_result = (
                self.pii_sanitizer.sanitize(
                    context
                )
            )

            sanitized_context = (
                sanitization_result
                .sanitized_data
            )

            placeholder_map = (
                sanitization_result
                .placeholder_map
            )

            if not isinstance(
                sanitized_context,
                dict,
            ):
                raise TypeError(
                    "Sanitized AI context must "
                    "be a dictionary."
                )

            logger.info(
                "Structured context sanitized "
                "for task '%s'. Replacement "
                "count: %s.",
                task.value,
                sanitization_result
                .replacement_count,
            )

            # ----------------------------------------------
            # 3. Build user prompt from sanitized context
            # ----------------------------------------------

            user_prompt = (
                self._build_user_prompt(
                    task=task,
                    context=sanitized_context,
                )
            )

            (
                sanitized_user_prompt,
                placeholder_map,
            ) = (
                self.pii_sanitizer
                .sanitize_prompt(
                    prompt=user_prompt,
                    placeholder_map=(
                        placeholder_map
                    ),
                )
            )

            messages: List[
                Dict[str, str]
            ] = [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": (
                        sanitized_user_prompt
                    ),
                },
            ]

            # ----------------------------------------------
            # 4. Request metadata
            # ----------------------------------------------

            estimated_input_tokens = (
                len(system_prompt.split())
                + len(
                    sanitized_user_prompt
                    .split()
                )
            )

            category = "general"

            if task == AITask.SENTIMENT:
                category = "sentiment"

            elif task == AITask.COMMUNICATION:
                channel = (
                    sanitized_context.get(
                        "channel"
                    )
                )

                if isinstance(channel, str):
                    normalized_channel = (
                        channel.lower()
                    )

                    if normalized_channel in (
                        "sms",
                        "email",
                        "push",
                    ):
                        category = (
                            normalized_channel
                        )

                    elif normalized_channel in (
                        "in_app",
                        "portal",
                    ):
                        category = "portal"

            tenant_id = (
                sanitized_context.get(
                    "tenant_id"
                )
            )

            # ----------------------------------------------
            # 5. Per-provider execution
            # ----------------------------------------------

            def attempt_runner(
                provider_name: str,
                provider: BaseAIProvider,
            ) -> GenerationResult:
                normalized_provider = (
                    provider_name
                    .strip()
                    .lower()
                )

                # ------------------------------------------
                # Provider metadata
                # ------------------------------------------

                try:
                    model_name = (
                        provider.model_name()
                    )

                    if (
                        not isinstance(
                            model_name,
                            str,
                        )
                        or not model_name.strip()
                    ):
                        raise ValueError(
                            "Invalid model name."
                        )

                    model_name = (
                        model_name.strip()
                    )

                except Exception:
                    raise ProviderExecutionError(
                        "Invalid provider metadata.",
                        is_retryable=False,
                    ) from None

                max_output_tokens = (
                    self.token_budget_manager
                    .config
                    .per_request
                    .get(
                        category,
                        4096,
                    )
                )

                # ------------------------------------------
                # Cache lookup before circuit and budget
                # ------------------------------------------

                cache_request = (
                    ProviderCacheRequest(
                        tenant_id=tenant_id,
                        provider=(
                            normalized_provider
                        ),
                        model=model_name,
                        sanitized_messages=(
                            messages
                        ),
                        temperature=0.7,
                        max_tokens=(
                            max_output_tokens
                        ),
                        ttl_policy=(
                            CacheTTLPolicy.STATIC
                        ),
                        explicit_safety_verification=True,
                    )
                )

                cached_response = (
                    self.provider_cache.get(
                        cache_request
                    )
                )

                if cached_response is not None:
                    return GenerationResult(
                        text=(
                            cached_response.text
                        ),
                        provider_name=(
                            normalized_provider
                        ),
                        model_name=model_name,
                        usage=UsageStats(
                            prompt_tokens=0,
                            completion_tokens=0,
                            total_tokens=0,
                            request_count=0,
                            latency_ms=0.0,
                            cost_usd=0.0,
                        ),
                    )

                permit: CircuitPermit | None = None

                reservation_id: (
                    str | None
                ) = None

                generation_result: (
                    GenerationResult | None
                ) = None

                provider_error: (
                    BaseException | None
                ) = None

                cleanup_error: (
                    ProviderExecutionError | None
                ) = None

                client_construction_failed = (
                    False
                )

                # ------------------------------------------
                # Provider attempt
                # ------------------------------------------

                try:
                    permit = (
                        self.circuit_breaker
                        .check_permission(
                            normalized_provider
                        )
                    )

                    try:
                        reservation_id = (
                            self
                            .token_budget_manager
                            .reserve(
                                estimated_input_tokens=(
                                    estimated_input_tokens
                                ),
                                max_output_tokens=(
                                    max_output_tokens
                                ),
                                category=category,
                                provider=(
                                    normalized_provider
                                ),
                                model=model_name,
                                tenant_id=tenant_id,
                            )
                        )

                    except BudgetExceededError:
                        logger.warning(
                            "Token budget exceeded "
                            "for provider '%s'.",
                            normalized_provider,
                        )

                        raise ProviderExecutionError(
                            "Daily AI token budget "
                            "exceeded.",
                            status_code=None,
                            is_retryable=False,
                        ) from None

                    except BudgetInfrastructureError:
                        logger.warning(
                            "Token budget "
                            "infrastructure error "
                            "for provider '%s'.",
                            normalized_provider,
                        )

                        raise ProviderExecutionError(
                            "AI budget infrastructure "
                            "failure.",
                            status_code=None,
                            is_retryable=False,
                        ) from None

                    try:
                        provider_client = (
                            self
                            .provider_client_factory(
                                provider
                            )
                        )

                    except Exception:
                        client_construction_failed = (
                            True
                        )

                        raise ProviderExecutionError(
                            "Provider client "
                            "construction failed.",
                            is_retryable=False,
                        ) from None

                    generate_result_method = (
                        getattr(
                            provider_client,
                            "generate_result",
                            None,
                        )
                    )

                    if callable(
                        generate_result_method
                    ):
                        raw_result = (
                            generate_result_method(
                                task=task,
                                messages=messages,
                                context=(
                                    sanitized_context
                                ),
                            )
                        )

                    else:
                        generate_method = getattr(
                            provider_client,
                            "generate",
                            None,
                        )

                        if not callable(
                            generate_method
                        ):
                            raise ProviderExecutionError(
                                "Provider client has "
                                "no supported generation "
                                "method.",
                                is_retryable=False,
                            )

                        raw_text = generate_method(
                            task=task,
                            messages=messages,
                            context=(
                                sanitized_context
                            ),
                        )

                        if isinstance(
                            raw_text,
                            GenerationResult,
                        ):
                            raw_result = raw_text

                        elif isinstance(
                            raw_text,
                            str,
                        ):
                            actual_output_tokens = (
                                len(
                                    raw_text.split()
                                )
                            )

                            usage = UsageStats(
                                prompt_tokens=(
                                    estimated_input_tokens
                                ),
                                completion_tokens=(
                                    actual_output_tokens
                                ),
                                total_tokens=(
                                    estimated_input_tokens
                                    + actual_output_tokens
                                ),
                                request_count=1,
                                latency_ms=0.0,
                                cost_usd=0.0,
                            )

                            raw_result = (
                                GenerationResult(
                                    text=raw_text,
                                    provider_name=(
                                        normalized_provider
                                    ),
                                    model_name=(
                                        model_name
                                    ),
                                    usage=usage,
                                )
                            )

                        else:
                            raise ProviderExecutionError(
                                "Provider output must "
                                "be a string or "
                                "GenerationResult.",
                                is_retryable=False,
                            )

                    # --------------------------------------
                    # Validate provider response
                    # --------------------------------------

                    if not isinstance(
                        raw_result,
                        GenerationResult,
                    ):
                        raise ProviderExecutionError(
                            "Invalid provider response "
                            "metadata.",
                            is_retryable=False,
                        )

                    if (
                        not isinstance(
                            raw_result.text,
                            str,
                        )
                        or not raw_result.text.strip()
                    ):
                        raise ProviderExecutionError(
                            "Invalid provider response "
                            "metadata.",
                            is_retryable=False,
                        )

                    if (
                        not isinstance(
                            raw_result.provider_name,
                            str,
                        )
                        or (
                            raw_result
                            .provider_name
                            .strip()
                            .lower()
                            != normalized_provider
                        )
                    ):
                        raise ProviderExecutionError(
                            "Invalid provider response "
                            "metadata.",
                            is_retryable=False,
                        )

                    if (
                        not isinstance(
                            raw_result.model_name,
                            str,
                        )
                        or not (
                            raw_result
                            .model_name
                            .strip()
                        )
                    ):
                        raise ProviderExecutionError(
                            "Invalid provider response "
                            "metadata.",
                            is_retryable=False,
                        )

                    if (
                        raw_result
                        .model_name
                        .strip()
                        != model_name
                    ):
                        raise ProviderExecutionError(
                            "Invalid provider response "
                            "metadata.",
                            is_retryable=False,
                        )

                    if not isinstance(
                        raw_result.usage,
                        UsageStats,
                    ):
                        raise ProviderExecutionError(
                            "Invalid provider response "
                            "metadata.",
                            is_retryable=False,
                        )

                    generation_result = (
                        raw_result
                    )

                except (
                    ProviderExecutionError,
                    ProviderHealthInfrastructureError,
                    CircuitBreakerInfrastructureError,
                    CircuitOpenError,
                ) as error:
                    provider_error = error

                except Exception:
                    provider_error = (
                        ProviderExecutionError(
                            "AI provider execution "
                            "failed.",
                            is_retryable=False,
                        )
                    )

                # ------------------------------------------
                # Exactly-once reservation cleanup
                # ------------------------------------------

                if reservation_id is not None:
                    if generation_result is not None:
                        try:
                            (
                                self
                                .token_budget_manager
                                .reconcile(
                                    reservation_id=(
                                        reservation_id
                                    ),
                                    actual_input_tokens=(
                                        generation_result
                                        .usage
                                        .prompt_tokens
                                    ),
                                    actual_output_tokens=(
                                        generation_result
                                        .usage
                                        .completion_tokens
                                    ),
                                    provider=(
                                        normalized_provider
                                    ),
                                )
                            )

                        except BudgetExceededError:
                            cleanup_error = (
                                ProviderExecutionError(
                                    "AI token budget "
                                    "overrun detected.",
                                    is_retryable=False,
                                )
                            )

                        except Exception:
                            cleanup_error = (
                                ProviderExecutionError(
                                    "AI budget "
                                    "infrastructure "
                                    "failure.",
                                    is_retryable=False,
                                )
                            )

                    else:
                        try:
                            (
                                self
                                .token_budget_manager
                                .cancel(
                                    reservation_id=(
                                        reservation_id
                                    ),
                                    provider=(
                                        normalized_provider
                                    ),
                                )
                            )

                        except Exception:
                            cleanup_error = (
                                ProviderExecutionError(
                                    "AI budget "
                                    "infrastructure "
                                    "failure.",
                                    is_retryable=False,
                                )
                            )

                # ------------------------------------------
                # Exactly-once permit cleanup
                # ------------------------------------------

                if permit is not None:
                    try:
                        if (
                            generation_result
                            is not None
                        ):
                            (
                                self
                                .circuit_breaker
                                .record_success(
                                    permit
                                )
                            )

                        elif (
                            reservation_id is None
                            or client_construction_failed
                        ):
                            # No provider call took place.
                            # For HALF_OPEN permits this
                            # releases the probe lock.
                            (
                                self
                                .circuit_breaker
                                .release_probe_lock(
                                    permit
                                )
                            )

                        else:
                            failure_for_circuit = (
                                provider_error
                                if provider_error
                                is not None
                                else ProviderExecutionError(
                                    "AI provider execution "
                                    "failed.",
                                    is_retryable=False,
                                )
                            )

                            (
                                self
                                .circuit_breaker
                                .record_failure(
                                    permit,
                                    failure_for_circuit,
                                )
                            )

                    except Exception:
                        if cleanup_error is None:
                            cleanup_error = (
                                ProviderExecutionError(
                                    "Circuit breaker "
                                    "infrastructure "
                                    "failure.",
                                    is_retryable=False,
                                )
                            )

                # Cleanup failures must not be swallowed.
                if cleanup_error is not None:
                    raise cleanup_error from None

                # Re-raise the provider failure after cleanup.
                if provider_error is not None:
                    raise provider_error from None

                if generation_result is None:
                    raise ProviderExecutionError(
                        "AI provider execution failed.",
                        is_retryable=False,
                    ) from None

                # ------------------------------------------
                # Cache only successful finalized responses
                # ------------------------------------------

                self.provider_cache.set(
                    cache_request,
                    CachedProviderResponse(
                        text=(
                            generation_result.text
                        ),
                        usage=(
                            generation_result.usage
                        ),
                    ),
                )

                return generation_result

            # ----------------------------------------------
            # 6. Provider failover
            # ----------------------------------------------

            try:
                failover_result = (
                    self.failover_executor
                    .execute(
                        attempt_runner
                    )
                )

            except (
                PIILeakageError,
                CircuitOpenError,
                ProviderFailoverExhaustedError,
                ProviderExecutionError,
                CircuitBreakerInfrastructureError,
                ProviderHealthInfrastructureError,
            ):
                raise

            except Exception:
                logger.warning(
                    "AI orchestration failed "
                    "for task '%s'.",
                    task.value,
                )

                raise RuntimeError(
                    "AI orchestration failed "
                    f"for task '{task.value}'."
                ) from None

            raw_response = (
                failover_result
                .generation_result
                .text
            )

            # ----------------------------------------------
            # 7. Restore placeholders locally
            # ----------------------------------------------

            restored_response = (
                self.pii_sanitizer
                .restore_data(
                    data=raw_response,
                    placeholder_map=(
                        placeholder_map
                    ),
                    clear_mapping=False,
                )
            )

            if not isinstance(
                restored_response,
                str,
            ):
                raise TypeError(
                    "Restored AI response must "
                    "be a string."
                )

            # ----------------------------------------------
            # 8. Parse optional response schema
            # ----------------------------------------------

            if response_schema is not None:
                logger.info(
                    "Validating AI response "
                    "using schema '%s'.",
                    response_schema.__name__,
                )

                return (
                    self.response_parser.parse(
                        restored_response,
                        response_schema,
                    )
                )

            return restored_response

        finally:
            if placeholder_map is not None:
                placeholder_map.clear()

    @staticmethod
    def _build_user_prompt(
        task: AITask,
        context: Dict[str, Any],
    ) -> str:
        """
        Build a task prompt using sanitized context only.
        """

        context_text = json.dumps(
            context,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

        return (
            f"TASK:\n"
            f"{task.value}\n\n"
            f"CONTEXT:\n"
            f"{context_text}\n\n"
            "IMPORTANT INSTRUCTIONS\n"
            "----------------------\n"
            "1. Return ONLY a valid JSON object.\n"
            "2. Do NOT use markdown.\n"
            "3. Do NOT explain your answer.\n"
            "4. Do NOT include headings.\n"
            "5. Do NOT include bullet points.\n"
            "6. Do NOT wrap the JSON inside ```json.\n"
            "7. Follow the schema defined in the system "
            "prompt exactly.\n"
            "8. Use ONLY the information provided in "
            "CONTEXT.\n"
            "9. Never invent facts, IDs, names, dates, "
            "or values.\n"
            "10. If required information is missing, "
            "respond according to the task schema.\n\n"
            "Return ONLY the JSON object."
        )

    def runtime_name(self) -> str:
        """
        Return the provider-neutral runtime name.
        """

        return "FieldOps AI Runtime"

    def health_check(self) -> bool:
        """
        Verify basic runtime initialization.
        """

        try:
            return (
                self.pii_sanitizer is not None
                and self.failover_executor
                is not None
            )

        except Exception:
            return False


ai_orchestrator = AIOrchestrator()