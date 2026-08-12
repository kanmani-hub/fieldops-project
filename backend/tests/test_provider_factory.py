"""
test_provider_factory.py

Unit test suite for ProviderFactory, dynamic registry, fallback chain, hot config reload, and GroqClient integration.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence
from unittest.mock import MagicMock
import pytest

from app.services.ai.FieldOpsAI.config.config_loader import ConfigLoader
from app.services.ai.FieldOpsAI.providers.base_provider import (
    BaseAIProvider,
    ProviderConfigurationError,
)
from app.services.ai.FieldOpsAI.providers.groq_client import GroqClient
from app.services.ai.FieldOpsAI.providers.groq_provider import GroqProvider
from app.services.ai.FieldOpsAI.providers.provider_factory import ProviderFactory


class FakeProviderA(BaseAIProvider):
    def __init__(self, config: Optional[Any] = None, custom_option: str = "default") -> None:
        self.config = config
        self.custom_option = custom_option

    def generate_completion(self, messages: Sequence[Dict[str, Any]], temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str:
        return "Response A"

    def provider_name(self) -> str:
        return "FakeA"

    def model_name(self) -> str:
        return "model-a"

    def health_check(self) -> bool:
        return True
class HealthyProviderB(BaseAIProvider):
    """
    Second healthy fake provider used to verify fallback order.
    """

    def __init__(
        self,
        config: Optional[Any] = None,
    ) -> None:
        self.config = config

    def generate_completion(
        self,
        messages: Sequence[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        return "Response B"

    def provider_name(self) -> str:
        return "HealthyB"

    def model_name(self) -> str:
        return "model-b"

    def health_check(self) -> bool:
        return True

class FakeProviderB(BaseAIProvider):
    def __init__(self, config: Optional[Any] = None, should_fail: bool = False) -> None:
        if should_fail:
            raise RuntimeError("CRITICAL_INTERNAL_DB_SECRET_FAILURE_123")
        self.config = config

    def generate_completion(self, messages: Sequence[Dict[str, Any]], temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str:
        return "Response B"

    def provider_name(self) -> str:
        return "FakeB"

    def model_name(self) -> str:
        return "model-b"

    def health_check(self) -> bool:
        return False


class UnhealthyProvider(BaseAIProvider):
    def __init__(self, config: Optional[Any] = None) -> None:
        self.config = config

    def generate_completion(self, messages: Sequence[Dict[str, Any]], temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str:
        return "Unhealthy"

    def provider_name(self) -> str:
        return "Unhealthy"

    def model_name(self) -> str:
        return "unhealthy-model"

    def health_check(self) -> bool:
        raise RuntimeError("Health check crashed with secret detail 999!")


@pytest.fixture(autouse=True)
def reset_factory_registry():
    ProviderFactory.reset_registry()
    yield
    ProviderFactory.reset_registry()


# 1. Registered Names is Sorted
def test_registered_names_is_sorted() -> None:
    ProviderFactory.register("zeta", FakeProviderA)
    ProviderFactory.register("alpha", FakeProviderB)
    names = ProviderFactory.registered_names()
    assert names == sorted(names)
    assert names == ["alpha", "groq", "zeta"]


# 2. Explicit Blank Name Rejected
def test_explicit_blank_name_rejected() -> None:
    with pytest.raises(ProviderConfigurationError, match="blank"):
        ProviderFactory.create_provider(name="   ")


# 3. Explicit Non-String Name Rejected
def test_explicit_non_string_name_rejected() -> None:
    with pytest.raises(ProviderConfigurationError, match="non-blank string"):
        ProviderFactory.create_provider(name=123)  # type: ignore


# 4. Missing Config Provider Name Rejected
def test_missing_config_provider_name_rejected() -> None:
    class MissingProviderNameConfig:
        pass

    with pytest.raises(
        ProviderConfigurationError,
        match="non-blank string",
    ):
        ProviderFactory.create_provider(
            config=MissingProviderNameConfig(),
        )

# 5. Blank Config Provider Name Rejected
def test_blank_config_provider_name_rejected() -> None:
    class BlankProviderNameConfig:
        provider_name = "   "

    with pytest.raises(
        ProviderConfigurationError,
        match="non-blank string",
    ):
        ProviderFactory.create_provider(
            config=BlankProviderNameConfig(),
        )
def test_groq_registered_by_default() -> None:
    assert "groq" in (
        ProviderFactory.registered_names()
    )


def test_registration_normalizes_name() -> None:
    ProviderFactory.register(
        "  FAKE_A  ",
        FakeProviderA,
    )

    assert "fake_a" in (
        ProviderFactory.registered_names()
    )


def test_blank_registration_rejected() -> None:
    with pytest.raises(
        ProviderConfigurationError,
        match="blank",
    ):
        ProviderFactory.register(
            "   ",
            FakeProviderA,
        )


def test_non_provider_class_rejected() -> None:
    class NotAProvider:
        pass

    with pytest.raises(
        ProviderConfigurationError,
        match="BaseAIProvider",
    ):
        ProviderFactory.register(
            "invalid",
            NotAProvider,  # type: ignore[arg-type]
        )


def test_identical_registration_is_idempotent() -> None:
    ProviderFactory.register(
        "fake_a",
        FakeProviderA,
    )
    ProviderFactory.register(
        "fake_a",
        FakeProviderA,
    )

    assert (
        ProviderFactory.registered_names()
        .count("fake_a")
        == 1
    )


def test_conflicting_registration_rejected() -> None:
    ProviderFactory.register(
        "fake_provider",
        FakeProviderA,
    )

    with pytest.raises(
        ProviderConfigurationError,
        match="already registered",
    ):
        ProviderFactory.register(
            "fake_provider",
            HealthyProviderB,
        )


def test_replace_registration() -> None:
    ProviderFactory.register(
        "fake_provider",
        FakeProviderA,
    )

    ProviderFactory.register(
        "fake_provider",
        HealthyProviderB,
        replace=True,
    )

    provider = ProviderFactory.create_provider(
        name="fake_provider",
    )

    assert isinstance(
        provider,
        HealthyProviderB,
    )


def test_unregister_removes_provider() -> None:
    ProviderFactory.register(
        "fake_a",
        FakeProviderA,
    )

    ProviderFactory.unregister(
        "fake_a"
    )

    assert "fake_a" not in (
        ProviderFactory.registered_names()
    )

# 6. Malformed Fallback Order Raises ProviderConfigurationError
def test_malformed_fallback_order_raises_config_error() -> None:
    mock_config = MagicMock()
    mock_config.provider_fallback_order = 12345  # Not a list
    with pytest.raises(ProviderConfigurationError, match="list of strings"):
        ProviderFactory.get_fallback_chain(config=mock_config)

    mock_config.provider_fallback_order = ["valid", ""]  # Blank element
    with pytest.raises(ProviderConfigurationError, match="non-blank strings"):
        ProviderFactory.get_fallback_chain(config=mock_config)


# 7. Fallback Property Missing Defaults to Primary Provider
def test_fallback_property_missing_defaults_to_primary() -> None:
    ProviderFactory.register(
        "fake_a",
        FakeProviderA,
    )

    class MissingFallbackConfig:
        provider_name = "fake_a"

    chain = ProviderFactory.get_fallback_chain(
        config=MissingFallbackConfig(),
    )

    assert len(chain) == 1
    assert isinstance(
        chain[0],
        FakeProviderA,
    )

# 8. Fallback Property None Defaults to Primary Provider
def test_fallback_property_none_defaults_to_primary() -> None:
    ProviderFactory.register(
        "fake_a",
        FakeProviderA,
    )

    class NoneFallbackConfig:
        provider_name = "fake_a"
        provider_fallback_order = None

    chain = ProviderFactory.get_fallback_chain(
        config=NoneFallbackConfig(),
    )

    assert len(chain) == 1
    assert isinstance(
        chain[0],
        FakeProviderA,
    )

# 9. Fallback Order Preserves Configured Ordering
def test_fallback_order_preserves_configured_ordering() -> None:
    ProviderFactory.register(
        "prov_a",
        FakeProviderA,
    )
    ProviderFactory.register(
        "prov_b",
        HealthyProviderB,
    )

    class OrderedFallbackConfig:
        provider_name = "prov_a"
        provider_fallback_order = [
            "prov_b",
            "prov_a",
        ]

    chain = ProviderFactory.get_fallback_chain(
        config=OrderedFallbackConfig(),
    )

    provider_names = [
        provider.provider_name()
        for provider in chain
    ]

    assert provider_names == [
        "HealthyB",
        "FakeA",
    ]

# 10. Fallback Duplicates Are Removed
def test_fallback_duplicates_are_removed() -> None:
    ProviderFactory.register("prov_a", FakeProviderA)
    mock_config = MagicMock()
    mock_config.provider_fallback_order = ["prov_a", "prov_a", "PROV_A"]

    chain = ProviderFactory.get_fallback_chain(config=mock_config)
    assert len(chain) == 1


# 11, 12, 13. reload_config Returns a New BaseAIProvider Using Fresh ConfigLoader
def test_reload_config_uses_fresh_config_and_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ProviderFactory.register(
        "fake_a",
        FakeProviderA,
    )

    created_configs = []

    class ReloadConfig:
        provider_name = "fake_a"

    def fake_config_loader():
        config = ReloadConfig()
        created_configs.append(config)
        return config

    monkeypatch.setattr(
        "app.services.ai.FieldOpsAI.providers."
        "provider_factory.ConfigLoader",
        fake_config_loader,
    )

    first_provider = (
        ProviderFactory.reload_config()
    )
    second_provider = (
        ProviderFactory.reload_config()
    )

    assert len(created_configs) == 2

    assert isinstance(
        first_provider,
        BaseAIProvider,
    )
    assert isinstance(
        second_provider,
        BaseAIProvider,
    )

    assert isinstance(
        first_provider,
        FakeProviderA,
    )
    assert isinstance(
        second_provider,
        FakeProviderA,
    )

    assert first_provider is not second_provider

    assert (
        first_provider.config
        is created_configs[0]
    )
    assert (
        second_provider.config
        is created_configs[1]
    )

    assert (
        first_provider.config
        is not second_provider.config
    )

    assert not isinstance(
        first_provider,
        ConfigLoader,
    )

# 14 & 15. Health-Check Exception Skipped & Safely Logged Without Raw Error Details
def test_health_check_exception_skipped_and_safely_logged(caplog: pytest.LogCaptureFixture) -> None:
    pf_logger = logging.getLogger("app.services.ai.FieldOpsAI.providers.provider_factory")
    pf_logger.setLevel(logging.WARNING)
    pf_logger.disabled = False
    pf_logger.propagate = True
    caplog.set_level(logging.WARNING)

    ProviderFactory.register("unhealthy", UnhealthyProvider)

    mock_config = MagicMock()
    mock_config.provider_fallback_order = ["unhealthy"]

    chain = ProviderFactory.get_fallback_chain(config=mock_config)
    assert len(chain) == 0

    assert "unhealthy" in caplog.text.lower()
    assert "Health check crashed with secret detail 999!" not in caplog.text


# 16. Existing GroqClient Compatibility Intact
def test_groq_client_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key_12345")
    client = GroqClient()
    assert isinstance(client.provider, BaseAIProvider)
    assert isinstance(client.provider, GroqProvider)

def test_get_provider_creates_registered_provider() -> None:
    ProviderFactory.register(
        "fake_a",
        FakeProviderA,
    )

    provider = ProviderFactory.get_provider(
        "fake_a",
    )

    assert isinstance(
        provider,
        FakeProviderA,
    )


def test_unknown_provider_uses_safe_error() -> None:
    with pytest.raises(
        ProviderConfigurationError,
        match="Configured AI provider is unsupported",
    ) as exc_info:
        ProviderFactory.create_provider(
            name="unknown-provider",
        )

    assert (
        "unknown-provider"
        not in str(exc_info.value)
    )


def test_create_provider_uses_config_name() -> None:
    ProviderFactory.register(
        "fake_a",
        FakeProviderA,
    )

    class FakeConfig:
        provider_name = "fake_a"

    provider = ProviderFactory.create_provider(
        config=FakeConfig(),
    )

    assert isinstance(
        provider,
        FakeProviderA,
    )


def test_explicit_name_overrides_config_name() -> None:
    ProviderFactory.register(
        "fake_a",
        FakeProviderA,
    )

    class FakeConfig:
        provider_name = "groq"

    provider = ProviderFactory.create_provider(
        config=FakeConfig(),
        name="fake_a",
    )

    assert isinstance(
        provider,
        FakeProviderA,
    )


def test_provider_constructor_kwargs_passed() -> None:
    ProviderFactory.register(
        "fake_a",
        FakeProviderA,
    )

    provider = ProviderFactory.create_provider(
        name="fake_a",
        provider_kwargs={
            "custom_option": "custom-value",
        },
    )

    assert isinstance(
        provider,
        FakeProviderA,
    )
    assert (
        provider.custom_option
        == "custom-value"
    )

def test_healthy_providers_filters_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)

    ProviderFactory.unregister("groq")

    ProviderFactory.register(
        "healthy_a",
        FakeProviderA,
    )
    ProviderFactory.register(
        "unhealthy_b",
        FakeProviderB,
    )
    ProviderFactory.register(
        "exception_c",
        UnhealthyProvider,
    )

    class FakeConfig:
        provider_name = "healthy_a"

    providers = (
        ProviderFactory.get_healthy_providers(
            config=FakeConfig(),
        )
    )

    names = [
        provider.provider_name()
        for provider in providers
    ]

    assert names == ["FakeA"]

    assert (
        "Health check crashed with secret detail 999!"
        not in caplog.text
    )

def test_constructor_error_is_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)

    ProviderFactory.register(
        "failing_b",
        FakeProviderB,
    )

    with pytest.raises(
        ProviderConfigurationError,
        match="AI provider initialization failed",
    ) as exc_info:
        ProviderFactory.create_provider(
            name="failing_b",
            provider_kwargs={
                "should_fail": True,
            },
        )

    secret = (
        "CRITICAL_INTERNAL_DB_SECRET_FAILURE_123"
    )

    assert secret not in str(
        exc_info.value
    )
    assert secret not in caplog.text

    assert str(exc_info.value) == (
        "AI provider initialization failed."
    )

def test_unknown_fallback_provider_is_skipped() -> None:
    ProviderFactory.register(
        "fake_a",
        FakeProviderA,
    )

    class FallbackConfig:
        provider_name = "fake_a"
        provider_fallback_order = [
            "unknown-provider",
            "fake_a",
        ]

    chain = ProviderFactory.get_fallback_chain(
        config=FallbackConfig(),
    )

    assert [
        provider.provider_name()
        for provider in chain
    ] == ["FakeA"]


def test_unhealthy_fallback_provider_is_skipped() -> None:
    ProviderFactory.register(
        "fake_a",
        FakeProviderA,
    )
    ProviderFactory.register(
        "fake_b",
        FakeProviderB,
    )

    class FallbackConfig:
        provider_name = "fake_a"
        provider_fallback_order = [
            "fake_b",
            "fake_a",
        ]

    chain = ProviderFactory.get_fallback_chain(
        config=FallbackConfig(),
    )

    assert [
        provider.provider_name()
        for provider in chain
    ] == ["FakeA"]
