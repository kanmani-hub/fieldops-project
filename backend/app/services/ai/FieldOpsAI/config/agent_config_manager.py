"""
agent_config_manager.py

Story 1.4: Dynamic configuration manager for FieldOps AI agents.
Resolves validated AgentConfig objects using default YAML settings,
agent-specific configurations, and runtime overrides.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError
import structlog

from app.services.ai.FieldOpsAI.config.config_loader import ConfigLoader
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

logger = structlog.get_logger("fieldops.ai.agent_config_manager")


class AgentConfigurationError(Exception):
    """
    Base exception for all agent configuration errors.
    """
    pass


class AgentConfigurationNotFoundError(AgentConfigurationError):
    """
    Raised when agent configuration cannot be resolved.
    """
    pass


class AgentConfigurationOverrideError(AgentConfigurationError):
    """
    Raised when runtime overrides are invalid or unsupported.
    """
    pass


class AgentConfigManager:
    """
    Production-quality agent configuration resolver.
    """

    def __init__(self, config_loader: ConfigLoader | None = None) -> None:
        """
        Initialize the configuration manager with a ConfigLoader.
        """
        if config_loader is None:
            try:
                self._config_loader = ConfigLoader()
            except (FileNotFoundError, ValueError) as exc:
                raise AgentConfigurationNotFoundError(
                    "Configuration could not be loaded by ConfigLoader."
                ) from exc
        else:
            self._config_loader = config_loader

    def resolve(
        self,
        *,
        agent_type: AITask,
        tenant_id: str,
        overrides: Mapping[str, object] | None = None,
    ) -> AgentConfig:
        """
        Resolve a validated AgentConfig for a specific tenant and agent type.
        """
        # Validate agent_type identity
        if not isinstance(agent_type, AITask):
            raise AgentConfigurationError("agent_type must be an AITask member.")

        # Validate tenant_id identity
        if not isinstance(tenant_id, str):
            raise AgentConfigurationError("tenant_id must be a string.")
        if not tenant_id.strip():
            raise AgentConfigurationError("tenant_id must not be blank.")

        # Validate overrides Mapping
        if overrides is not None:
            if not isinstance(overrides, Mapping):
                raise AgentConfigurationError("overrides must be a mapping.")

            # Reject identity field override attempts
            if "agent_type" in overrides or "tenant_id" in overrides:
                raise AgentConfigurationOverrideError(
                    "Overrides cannot modify identity fields: 'agent_type' or 'tenant_id'."
                )

            # Reject unknown/unsupported keys
            allowed_keys = {"agent_version", "timeout_seconds", "max_retries", "enabled"}
            for key in overrides:
                if key not in allowed_keys:
                    raise AgentConfigurationOverrideError(
                        f"Unsupported or unknown override key: {key}"
                    )

        # Retrieve agents section from YAML configuration snapshot
        try:
            config_dict = self._config_loader.get_config_snapshot()
        except Exception as exc:
            raise AgentConfigurationNotFoundError(
                "Unable to retrieve configuration snapshot from loader."
            ) from exc

        if not isinstance(config_dict, dict):
            raise AgentConfigurationNotFoundError(
                "Unable to retrieve underlying YAML configuration snapshot."
            )

        agents_section = config_dict.get("agents")
        if agents_section is not None:
            if not isinstance(agents_section, Mapping):
                raise AgentConfigurationError(
                    "The 'agents' section in YAML configuration must be a mapping."
                )
        else:
            agents_section = {}

        # Retrieve defaults section
        defaults = agents_section.get("defaults")
        if defaults is not None:
            if not isinstance(defaults, Mapping):
                raise AgentConfigurationError(
                    "The 'defaults' agent section in YAML must be a mapping."
                )
        else:
            defaults = {}

        # Retrieve agent-specific section
        agent_key = agent_type.value
        agent_specific = agents_section.get(agent_key)
        if agent_specific is not None:
            if not isinstance(agent_specific, Mapping):
                raise AgentConfigurationError(
                    f"The agent-specific YAML section for '{agent_key}' must be a mapping."
                )
        else:
            agent_specific = {}

        # Merge config in priority order (lowest to highest):
        # 1. Defaults from AgentConfig fields (handled implicitly by Pydantic)
        # 2. Defaults from YAML (L3)
        # 3. Agent-specific configuration from YAML (L2)
        # 4. Runtime overrides (L1)
        merged_config: dict[str, Any] = {}

        for k, v in defaults.items():
            merged_config[k] = v

        for k, v in agent_specific.items():
            merged_config[k] = v

        if overrides:
            for k, v in overrides.items():
                merged_config[k] = v

        # Enforce exact agent_type and tenant_id
        merged_config["agent_type"] = agent_type
        merged_config["tenant_id"] = tenant_id

        # Construct AgentConfig (raises validation errors)
        try:
            config = AgentConfig(**merged_config)
        except ValidationError as exc:
            raise AgentConfigurationError(
                "Failed to validate final resolved AgentConfig."
            ) from exc

        logger.info(
            "resolved_agent_config",
            agent_type=agent_type.value,
            tenant_id=tenant_id,
            overrides_applied=bool(overrides),
        )

        return config
