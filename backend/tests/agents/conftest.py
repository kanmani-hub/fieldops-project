import asyncio
from datetime import datetime, timezone
import pytest
from typing import Any, Generator
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.services.ai.FieldOpsAI.agents.base import BaseAgent
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.runtime.agent_pool import AgentPool
from app.services.ai.FieldOpsAI.runtime.agent_registry import AgentRegistry
from app.services.ai.FieldOpsAI.runtime.agent_bus import AgentBus
from app.services.ai.FieldOpsAI.runtime.agent_health_monitor import AgentHealthMonitor
from app.services.ai.FieldOpsAI.repositories.agent_state_repository import AgentStateRepository
from app.services.ai.FieldOpsAI.runtime.agent_state_manager import AgentStateManager
from app.services.ai.FieldOpsAI.schemas.agent_messages import AgentAddress, MessageEnvelope

class ControllableClock:
    """
    A clock class that allows advancing and retrieving timezone-aware time.
    """
    def __init__(self, start_time: datetime | None = None) -> None:
        self.current_time = start_time or datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current_time

    def advance(self, seconds: float) -> None:
        from datetime import timedelta
        self.current_time += timedelta(seconds=seconds)

@pytest.fixture
def anyio_backend() -> str:
    """
    Force AnyIO tests to use Python's asyncio backend.
    """
    return "asyncio"

@pytest.fixture
def clock() -> ControllableClock:
    """
    Controllable timezone-aware clock.
    """
    return ControllableClock()

@pytest.fixture
def db_engine() -> Generator[Any, None, None]:
    """
    In-memory SQLite database engine.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()

@pytest.fixture
def db_session(db_engine) -> Generator[Session, None, None]:
    """
    In-memory SQLite database session.
    """
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    yield session
    session.close()

@pytest.fixture
def state_repository(db_session) -> AgentStateRepository:
    """
    AgentStateRepository pointing to in-memory database.
    """
    return AgentStateRepository(db_session)

@pytest.fixture
def state_manager(state_repository) -> AgentStateManager:
    """
    AgentStateManager using the mock repository.
    """
    return AgentStateManager(state_repository)

@pytest.fixture
def valid_config() -> AgentConfig:
    """
    Valid AgentConfig fixture for tenant-001.
    """
    return AgentConfig(
        agent_type=AITask.PLANNING,
        tenant_id="tenant-001",
        agent_version="1.0",
        timeout_seconds=30.0,
        max_retries=2,
        enabled=True
    )

@pytest.fixture
def tenant_two_config() -> AgentConfig:
    """
    AgentConfig fixture for a second tenant (tenant-002).
    """
    return AgentConfig(
        agent_type=AITask.PLANNING,
        tenant_id="tenant-002",
        agent_version="1.0",
        timeout_seconds=30.0,
        max_retries=2,
        enabled=True
    )

class SuccessfulAgent(BaseAgent[dict[str, Any]]):
    """
    Mock agent that returns its supplied context or success dict.
    """
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "success",
            "tenant_id": self.tenant_id,
            "agent_id": str(self.agent_id),
            "payload": context.get("payload")
        }

class FailingAgent(BaseAgent[dict[str, Any]]):
    """
    Mock agent that raises a RuntimeError during execute.
    """
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("Simulated agent execution failure")

class SlowAgent(BaseAgent[dict[str, Any]]):
    """
    Mock agent that sleeps for a configurable duration.
    """
    def __init__(self, config: AgentConfig, sleep_seconds: float = 0.5) -> None:
        super().__init__(config)
        self.sleep_seconds = sleep_seconds

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(self.sleep_seconds)
        return {"status": "success"}

class CancellableAgent(BaseAgent[dict[str, Any]]):
    """
    Mock agent that sleeps indefinitely until cancelled.
    """
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        try:
            await asyncio.sleep(100.0)
        except asyncio.CancelledError:
            raise
        return {"status": "success"}

@pytest.fixture
def successful_agent_class() -> type[BaseAgent]:
    return SuccessfulAgent

@pytest.fixture
def failing_agent_class() -> type[BaseAgent]:
    return FailingAgent

@pytest.fixture
def slow_agent_class() -> type[BaseAgent]:
    return SlowAgent

@pytest.fixture
def cancellable_agent_class() -> type[BaseAgent]:
    return CancellableAgent

@pytest.fixture
def agent_pool() -> AgentPool:
    """
    Fresh AgentPool instance.
    """
    return AgentPool()

@pytest.fixture
def agent_registry() -> AgentRegistry:
    """
    Fresh AgentRegistry instance.
    """
    return AgentRegistry()

@pytest.fixture
def agent_bus() -> AgentBus:
    """
    Fresh AgentBus instance with default handler timeout of 2 seconds.
    """
    return AgentBus(handler_timeout_seconds=2.0)

@pytest.fixture
def health_monitor(clock) -> AgentHealthMonitor:
    """
    AgentHealthMonitor with controllable clock.
    """
    return AgentHealthMonitor(
        degraded_after_seconds=30.0,
        unhealthy_after_seconds=120.0,
        clock=clock
    )

@pytest.fixture
def sender_address() -> AgentAddress:
    """
    Safe sender AgentAddress.
    """
    return AgentAddress(
        tenant_id="tenant-001",
        agent_type=AITask.PLANNING,
        agent_id=str(uuid4())
    )

@pytest.fixture
def recipient_address() -> AgentAddress:
    """
    Safe recipient AgentAddress.
    """
    return AgentAddress(
        tenant_id="tenant-001",
        agent_type=AITask.DISPATCH,
        agent_id=str(uuid4())
    )

@pytest.fixture
def noop_handler():
    """
    No-op message handler.
    """
    received = []
    async def _handler(envelope: MessageEnvelope) -> None:
        received.append(envelope)
    _handler.received = received  # type: ignore
    return _handler

@pytest.fixture
def failing_handler():
    """
    Message handler that raises RuntimeError.
    """
    async def _handler(envelope: MessageEnvelope) -> None:
        raise RuntimeError("Simulated handler failure")
    return _handler

@pytest.fixture
def slow_handler():
    """
    Message handler that runs slowly.
    """
    async def _handler(envelope: MessageEnvelope) -> None:
        await asyncio.sleep(5.0)
    return _handler

@pytest.fixture
def correlation_id() -> str:
    """
    Deterministic correlation ID.
    """
    return "corr-123456"
