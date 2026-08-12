"""
provider_factory.py

Factory responsible for dynamic provider registration, safe provider loading,
and fallback chain inspection.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Type

from app.services.ai.FieldOpsAI.config.config_loader import ConfigLoader
from app.services.ai.FieldOpsAI.providers.base_provider import (
    BaseAIProvider,
    ProviderConfigurationError,
)
from app.services.ai.FieldOpsAI.providers.groq_provider import GroqProvider

logger = logging.getLogger(__name__)


class ProviderFactory:
    """
    Thread-safe registry and factory for creating AI provider instances.
    """

    _registry: Dict[str, Type[BaseAIProvider]] = {}
    _lock = threading.RLock()

    @classmethod
    def register(
        cls,
        name: str,
        provider_class: Type[BaseAIProvider],
        replace: bool = False,
    ) -> None:
        """
        Register a provider class under a normalized provider name.
        """
        if not isinstance(name, str) or not name.strip():
            raise ProviderConfigurationError("Provider name cannot be blank.")

        if not isinstance(provider_class, type) or not issubclass(provider_class, BaseAIProvider):
            raise ProviderConfigurationError("Provider class must extend BaseAIProvider.")

        norm_name = name.strip().lower()

        with cls._lock:
            if norm_name in cls._registry:
                existing_cls = cls._registry[norm_name]
                if existing_cls is provider_class:
                    return
                if not replace:
                    raise ProviderConfigurationError(
                        f"Provider '{norm_name}' is already registered with a different class."
                    )

            cls._registry[norm_name] = provider_class

    @classmethod
    def unregister(cls, name: str) -> None:
        """
        Unregister a provider name from the registry.
        """
        if not isinstance(name, str):
            return
        norm_name = name.strip().lower()
        with cls._lock:
            cls._registry.pop(norm_name, None)

    @classmethod
    def registered_names(cls) -> List[str]:
        """
        Return a sorted list of currently registered provider names.
        """
        with cls._lock:
            return sorted(cls._registry.keys())

    @classmethod
    def get_provider(
        cls,
        name: str,
        config: Optional[ConfigLoader | Any] = None,
        provider_kwargs: Optional[Dict[str, Any]] = None,
    ) -> BaseAIProvider:
        """
        Retrieve and instantiate a provider by name.
        """
        return cls.create_provider(config=config, name=name, provider_kwargs=provider_kwargs)

    @classmethod
    def create_provider(
        cls,
        config: Optional[ConfigLoader | Any] = None,
        name: Optional[str] = None,
        provider_kwargs: Optional[Dict[str, Any]] = None,
    ) -> BaseAIProvider:
        """
        Instantiate an AI provider.
        If config is None, a fresh ConfigLoader is loaded.
        If name is supplied, it must be a non-blank string.
        If name is None, config.provider_name must be a non-blank string.
        """
        if name is not None:
            if not isinstance(name, str):
                raise ProviderConfigurationError("Provider name must be a non-blank string.")
            if not name.strip():
                raise ProviderConfigurationError("Provider name cannot be blank.")
            target_name = name.strip().lower()
            cfg = config if config is not None else ConfigLoader()
        else:
            cfg = config if config is not None else ConfigLoader()
            p_name = getattr(cfg, "provider_name", None)
            if not isinstance(p_name, str) or not p_name.strip():
                raise ProviderConfigurationError("Configuration provider_name must be a non-blank string.")
            target_name = p_name.strip().lower()

        with cls._lock:
            provider_cls = cls._registry.get(target_name)

        if provider_cls is None:
            raise ProviderConfigurationError(
                "Configured AI provider is unsupported."
            )

        try:
            if provider_kwargs is not None:
                return provider_cls(config=cfg, **provider_kwargs)
            else:
                return provider_cls(config=cfg)
        except ProviderConfigurationError:
            raise
        except Exception:
            logger.warning("AI provider initialization failed for '%s'.", target_name)
            raise ProviderConfigurationError("AI provider initialization failed.") from None

    @classmethod
    def get_healthy_providers(
        cls,
        config: Optional[ConfigLoader | Any] = None,
        provider_kwargs: Optional[Dict[str, Any]] = None,
    ) -> List[BaseAIProvider]:
        """
        Return a list of healthy, instantiated registered providers.
        Fails safely and logs warning for unhealthy or failed providers.
        """
        cfg = config if config is not None else ConfigLoader()
        healthy_providers: List[BaseAIProvider] = []

        with cls._lock:
            names = sorted(cls._registry.keys())

        for p_name in names:
            try:
                inst = cls.create_provider(config=cfg, name=p_name, provider_kwargs=provider_kwargs)
                if inst.health_check():
                    healthy_providers.append(inst)
                else:
                    logger.warning("AI provider '%s' failed health check during healthy providers check.", p_name)
            except ProviderConfigurationError:
                logger.warning("AI provider '%s' configuration failed during healthy providers check.", p_name)
            except Exception:
                logger.warning("AI provider '%s' initialization failed during healthy providers check.", p_name)

        return healthy_providers

    @classmethod
    def get_fallback_chain(
        cls,
        config: Optional[ConfigLoader | Any] = None,
        provider_kwargs: Optional[Dict[str, Any]] = None,
    ) -> List[BaseAIProvider]:
        """
        Return instantiated healthy providers in the configured fallback order.
        Preserves ProviderConfigurationError from malformed fallback configuration.
        """
        cfg = config if config is not None else ConfigLoader()

        try:
            raw_chain = cfg.provider_fallback_order
        except AttributeError:
            raw_chain = None

        if raw_chain is None:
            p_name = getattr(
                cfg,
                "provider_name",
                None,
            )

            if (
                not isinstance(p_name, str)
                or not p_name.strip()
            ):
                raise ProviderConfigurationError(
                    "Configuration provider_name must be "
                    "a non-blank string."
                )

            raw_chain = [
                p_name.strip().lower()
            ]

        if not isinstance(raw_chain, (list, tuple)):
            raise ProviderConfigurationError("provider.fallback_order must be a list of strings.")

        chain_names: List[str] = []
        for item in raw_chain:
            if not isinstance(item, str) or not item.strip():
                raise ProviderConfigurationError("provider.fallback_order elements must be non-blank strings.")
            norm = item.strip().lower()
            if norm not in chain_names:
                chain_names.append(norm)

        if not chain_names:
            raise ProviderConfigurationError("provider.fallback_order must contain at least one valid provider name.")

        chain: List[BaseAIProvider] = []

        for p_name in chain_names:
            try:
                inst = cls.create_provider(config=cfg, name=p_name, provider_kwargs=provider_kwargs)
                if inst.health_check():
                    chain.append(inst)
                else:
                    logger.warning("AI provider '%s' failed health check during fallback chain resolution.", p_name)
            except ProviderConfigurationError:
                logger.warning("AI provider '%s' configuration failed during fallback chain resolution.", p_name)
            except Exception:
                logger.warning("AI provider '%s' initialization failed during fallback chain resolution.", p_name)

        return chain

    @classmethod
    def reload_config(
        cls,
        provider_kwargs: Optional[Dict[str, Any]] = None,
    ) -> BaseAIProvider:
        """
        Load a fresh ConfigLoader and return a newly instantiated BaseAIProvider instance.
        """
        fresh_config = ConfigLoader()
        return cls.create_provider(config=fresh_config, provider_kwargs=provider_kwargs)

    @classmethod
    def reset_registry(cls) -> None:
        """
        Reset the registry to default built-in providers.
        Used primarily for test isolation.
        """
        with cls._lock:
            cls._registry = {"groq": GroqProvider}


# Register built-in default provider
ProviderFactory.register("groq", GroqProvider)