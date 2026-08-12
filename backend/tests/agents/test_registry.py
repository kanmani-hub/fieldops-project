import pytest
from unittest.mock import MagicMock
from uuid import UUID, uuid4
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.agent_registration import AgentRegistration
from app.services.ai.FieldOpsAI.runtime.agent_registry import (
    AgentRegistry,
    AgentAlreadyRegisteredError,
    AgentNotRegisteredError,
    AgentRegistrationDisabledError,
    AgentRegistryError
)
from app.services.ai.FieldOpsAI.agents.base import BaseAgent, AgentState
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from typing import Any

class MockConfigManager:
    """Mock config manager that resolves standard configs."""
    def __init__(self, is_enabled: bool = True) -> None:
        self.is_enabled = is_enabled

    def resolve(self, *, agent_type: AITask, tenant_id: str, overrides: dict | None = None) -> AgentConfig:
        return AgentConfig(
            agent_type=agent_type,
            tenant_id=tenant_id,
            agent_version="1.0",
            timeout_seconds=30.0,
            max_retries=2,
            enabled=self.is_enabled
        )

# 1. Register adds a definition.
def test_register_adds_definition(agent_registry: AgentRegistry, successful_agent_class: type[BaseAgent]) -> None:
    """Registering a valid AgentRegistration adds it to the registry."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    assert not agent_registry.contains(AITask.PLANNING)
    
    agent_registry.register(registration=reg)
    assert agent_registry.contains(AITask.PLANNING)

# 2. contains() becomes true after registration.
def test_contains_becomes_true(agent_registry: AgentRegistry, successful_agent_class: type[BaseAgent]) -> None:
    """contains() returns True for registered types and False otherwise."""
    reg = AgentRegistration(agent_type=AITask.DISPATCH, agent_class=successful_agent_class, version="1.0")
    assert not agent_registry.contains(AITask.DISPATCH)
    agent_registry.register(registration=reg)
    assert agent_registry.contains(AITask.DISPATCH)

# 3. list_registrations() exposes the registration.
def test_list_registrations(agent_registry: AgentRegistry, successful_agent_class: type[BaseAgent]) -> None:
    """list_registrations() lists all registered definitions."""
    reg1 = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    reg2 = AgentRegistration(agent_type=AITask.DISPATCH, agent_class=successful_agent_class, version="1.0")
    
    agent_registry.register(registration=reg1)
    agent_registry.register(registration=reg2)
    
    regs = agent_registry.list_registrations()
    assert len(regs) == 2
    assert regs[0].agent_type == AITask.PLANNING
    assert regs[1].agent_type == AITask.DISPATCH

# 4. get() returns the correct registration.
def test_get_returns_registration(agent_registry: AgentRegistry, successful_agent_class: type[BaseAgent]) -> None:
    """get() retrieves the exact registered AgentRegistration metadata."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="2.0")
    agent_registry.register(registration=reg)
    
    retrieved = agent_registry.get(AITask.PLANNING)
    assert retrieved.version == "2.0"
    assert retrieved.agent_class == successful_agent_class

# 5. Duplicate registration is rejected according to current rules.
def test_duplicate_registration_rejected(agent_registry: AgentRegistry, successful_agent_class: type[BaseAgent]) -> None:
    """Registering an already registered agent type without replace=True raises AgentAlreadyRegisteredError."""
    reg1 = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    reg2 = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    
    agent_registry.register(registration=reg1)
    with pytest.raises(AgentAlreadyRegisteredError):
        agent_registry.register(registration=reg2)
        
    # With replace=True it is accepted
    agent_registry.register(registration=reg2, replace=True)

# 6. unregister removes the registration.
def test_unregister_removes_registration(agent_registry: AgentRegistry, successful_agent_class: type[BaseAgent]) -> None:
    """unregister removes registration definition and returns True."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    agent_registry.register(registration=reg)
    
    removed = agent_registry.unregister(AITask.PLANNING)
    assert removed is True
    assert not agent_registry.contains(AITask.PLANNING)

# 7. unregister missing registration returns the documented value.
def test_unregister_missing_returns_false(agent_registry: AgentRegistry) -> None:
    """unregister returns False if the agent type was not registered."""
    removed = agent_registry.unregister(AITask.PLANNING)
    assert removed is False

# 8. Disabled registration cannot create an agent.
def test_disabled_registration_cannot_create(agent_registry: AgentRegistry, successful_agent_class: type[BaseAgent]) -> None:
    """A disabled registration raises AgentRegistrationDisabledError on creation."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0", enabled=False)
    agent_registry.register(registration=reg)
    
    config_manager = MockConfigManager(is_enabled=False)
    with pytest.raises(AgentRegistrationDisabledError):
        agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_manager)

# 9. create returns a fresh, uninitialized BaseAgent.
def test_create_returns_fresh_agent(agent_registry: AgentRegistry, successful_agent_class: type[BaseAgent]) -> None:
    """create() instantiates the correct agent class which is fresh and uninitialized."""
    reg = AgentRegistration(agent_type=AITask.PLANNING, agent_class=successful_agent_class, version="1.0")
    agent_registry.register(registration=reg)
    
    config_manager = MockConfigManager()
    agent = agent_registry.create(agent_type=AITask.PLANNING, tenant_id="tenant-001", config_manager=config_manager)
    
    assert isinstance(agent, BaseAgent)
    assert isinstance(agent.agent_id, UUID)
    assert agent.tenant_id == "tenant-001"
    assert agent.state == AgentState.IDLE
    assert agent.is_setup is False

# 10. Factory result with wrong tenant or agent type is rejected.
def test_factory_validation_checks(
    successful_agent_class: type[BaseAgent],
) -> None:
    """
    Registry rejects factory results with an incorrect tenant
    or incorrect agent type.
    """

    config_manager = MockConfigManager()

    # Wrong tenant
    tenant_registry = AgentRegistry()

    registration = AgentRegistration(
        agent_type=AITask.PLANNING,
        agent_class=successful_agent_class,
        version="1.0",
    )

    def bad_tenant_factory(
        config: AgentConfig,
        orchestrator: Any = None,
    ) -> BaseAgent:
        bad_config = AgentConfig(
            agent_type=AITask.PLANNING,
            tenant_id="wrong-tenant",
            agent_version=config.agent_version,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
            enabled=config.enabled,
        )
        return successful_agent_class(bad_config)

    tenant_registry.register(
        registration=registration,
        factory=bad_tenant_factory,
    )

    with pytest.raises(AgentRegistryError):
        tenant_registry.create(
            agent_type=AITask.PLANNING,
            tenant_id="tenant-001",
            config_manager=config_manager,
        )

    # Wrong agent type
    type_registry = AgentRegistry()

    def bad_type_factory(
        config: AgentConfig,
        orchestrator: Any = None,
    ) -> BaseAgent:
        bad_config = AgentConfig(
            agent_type=AITask.DISPATCH,
            tenant_id=config.tenant_id,
            agent_version=config.agent_version,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
            enabled=config.enabled,
        )
        return successful_agent_class(bad_config)

    type_registry.register(
        registration=registration,
        factory=bad_type_factory,
    )

    with pytest.raises(AgentRegistryError):
        type_registry.create(
            agent_type=AITask.PLANNING,
            tenant_id="tenant-001",
            config_manager=config_manager,
        )