import pytest
from pydantic import ValidationError
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.config.agent_config_manager import (
    AgentConfigManager
)
from app.services.ai.FieldOpsAI.config.config_loader import ConfigLoader

# 1. Valid config accepted.
def test_valid_config_accepted() -> None:
    """AgentConfig accepts a valid configuration matching schemas."""
    config = AgentConfig(
        agent_type=AITask.PLANNING,
        tenant_id="tenant-001",
        agent_version="1.0",
        timeout_seconds=30.0,
        max_retries=2,
        enabled=True
    )
    assert config.agent_type == AITask.PLANNING
    assert config.tenant_id == "tenant-001"
    assert config.agent_version == "1.0"
    assert config.timeout_seconds == 30.0
    assert config.max_retries == 2
    assert config.enabled is True

# 2. Blank tenant rejected.
def test_blank_tenant_rejected() -> None:
    """Tenant ID must not be blank or empty."""
    with pytest.raises(ValidationError):
        AgentConfig(
            agent_type=AITask.PLANNING,
            tenant_id="",
            agent_version="1.0",
            timeout_seconds=30.0,
            max_retries=2,
            enabled=True
        )

    with pytest.raises(ValidationError):
        AgentConfig(
            agent_type=AITask.PLANNING,
            tenant_id="   ",
            agent_version="1.0",
            timeout_seconds=30.0,
            max_retries=2,
            enabled=True
        )

# 3. Invalid timeout rejected.
def test_invalid_timeout_rejected() -> None:
    """Timeout must be positive and <= 300."""
    with pytest.raises(ValidationError):
        AgentConfig(
            agent_type=AITask.PLANNING,
            tenant_id="tenant-001",
            timeout_seconds=0.0
        )
    with pytest.raises(ValidationError):
        AgentConfig(
            agent_type=AITask.PLANNING,
            tenant_id="tenant-001",
            timeout_seconds=-5.0
        )
    with pytest.raises(ValidationError):
        AgentConfig(
            agent_type=AITask.PLANNING,
            tenant_id="tenant-001",
            timeout_seconds=301.0
        )

# 4. Invalid retry count rejected.
def test_invalid_retry_count_rejected() -> None:
    """Max retries must be between 0 and 10."""
    with pytest.raises(ValidationError):
        AgentConfig(
            agent_type=AITask.PLANNING,
            tenant_id="tenant-001",
            max_retries=-1
        )
    with pytest.raises(ValidationError):
        AgentConfig(
            agent_type=AITask.PLANNING,
            tenant_id="tenant-001",
            max_retries=11
        )

# 5. Invalid enabled value or extra field rejected.
def test_extra_field_rejected() -> None:
    """Extra fields are strictly forbidden, and enabled must be a strict boolean."""
    # Extra field
    with pytest.raises(ValidationError):
        AgentConfig(
            agent_type=AITask.PLANNING,
            tenant_id="tenant-001",
            extra_field="unsupported"  # type: ignore
        )
    # Enabled not a strict boolean (e.g. passing a string 'yes')
    with pytest.raises(ValidationError):
        AgentConfig(
            agent_type=AITask.PLANNING,
            tenant_id="tenant-001",
            enabled="yes"  # type: ignore
        )

# 6. Default configuration resolves correctly.
def test_default_config_resolves_correctly() -> None:
    """AgentConfigManager resolves default values from ai.yaml if no overrides given."""
    loader = ConfigLoader()
    manager = AgentConfigManager(config_loader=loader)
    
    config = manager.resolve(agent_type=AITask.PLANNING, tenant_id="tenant-001")
    assert config.timeout_seconds == 30.0
    assert config.max_retries == 2
    assert config.enabled is True

# 7. Tenant override is applied.
def test_tenant_override_applied() -> None:
    """AgentConfigManager applies runtime overrides successfully."""
    loader = ConfigLoader()
    manager = AgentConfigManager(config_loader=loader)
    
    overrides = {
        "timeout_seconds": 45.0,
        "max_retries": 5
    }
    
    config = manager.resolve(
        agent_type=AITask.PLANNING,
        tenant_id="tenant-001",
        overrides=overrides
    )
    assert config.timeout_seconds == 45.0
    assert config.max_retries == 5
    # Default persists
    assert config.enabled is True

# 8. Override isolation prevents one tenant from changing another tenant.
def test_override_isolation() -> None:
    """Config manager resolution is stateless and overrides for one tenant don't affect subsequent resolutions."""
    loader = ConfigLoader()
    manager = AgentConfigManager(config_loader=loader)
    
    # Resolve first tenant with overrides
    config1 = manager.resolve(
        agent_type=AITask.PLANNING,
        tenant_id="tenant-001",
        overrides={"timeout_seconds": 50.0}
    )
    
    # Resolve second tenant without overrides
    config2 = manager.resolve(
        agent_type=AITask.PLANNING,
        tenant_id="tenant-002"
    )
    
    assert config1.timeout_seconds == 50.0
    assert config2.timeout_seconds == 30.0  # Resolves defaults independently
