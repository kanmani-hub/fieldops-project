import pytest
import app.database
import app.redis_client

# Store original references
_orig_session_local = app.database.SessionLocal
_orig_get_redis = app.redis_client.get_redis_client

_current_request = None
_last_request_module = None

def dynamic_get_redis_client():
    module = None
    if _current_request and _current_request.module:
        module = _current_request.module
    elif _last_request_module:
        module = _last_request_module

    if module:
        for attr_name in ("fake_redis", "fake_sync_redis", "mock_redis", "fake_async_redis"):
            if hasattr(module, attr_name):
                return getattr(module, attr_name)
    return _orig_get_redis()

def dynamic_session_local(*args, **kwargs):
    module = None
    if _current_request and _current_request.module:
        module = _current_request.module
    elif _last_request_module:
        module = _last_request_module

    if module and hasattr(module, "TestingSessionLocal"):
        return getattr(module, "TestingSessionLocal")(*args, **kwargs)
    return _orig_session_local(*args, **kwargs)

# Apply global dynamic routing before importing main application
app.database.SessionLocal = dynamic_session_local
app.redis_client.get_redis_client = dynamic_get_redis_client

# Helper to prevent global import-time monkeypatch overwrites in test modules
def make_write_resistant(module_name):
    import sys
    if module_name in sys.modules:
        mod = sys.modules[module_name]
        class CustomModule(mod.__class__):
            def __setattr__(self, name, value):
                if name in ("SessionLocal", "get_redis_client"):
                    return
                super().__setattr__(name, value)
        mod.__class__ = CustomModule

make_write_resistant("app.database")
make_write_resistant("app.redis_client")

import app.main
from app.celery_app import celery_app

# Configure Celery eagerly for all tests
celery_app.conf.update(
    task_always_eager=True,
    task_eager_propagates=True
)

app.main.SessionLocal = dynamic_session_local

make_write_resistant("app.main")
import app.services.tracking_manager
make_write_resistant("app.services.tracking_manager")
try:
    import app.routes.tracking
    make_write_resistant("app.routes.tracking")
except ImportError:
    pass

@pytest.fixture(autouse=True)
def track_current_request(request):
    global _current_request, _last_request_module
    _current_request = request
    if request.module:
        _last_request_module = request.module
    yield
    _current_request = None

from unittest.mock import MagicMock, AsyncMock

class SimTimer:
    def __init__(self):
        self.now = 0.0

    def tick(self, seconds: float):
        self.now += seconds

    def time(self) -> float:
        return self.now


class FakeRedisClient:
    def __init__(self, timer: SimTimer):
        self.store: dict[str, tuple[str, float]] = {}
        self.timer = timer
        self.calls: list[str] = []
        self.fail_get = False
        self.fail_setex = False
        self.timeout_get = False
        self.fail_delete = False

    def _track(self, op: str, key: str):
        self.calls.append(f"{op}:{key}")

    def get(self, key: str) -> str | None:
        self._track("get", key)
        if self.timeout_get:
            raise TimeoutError("Simulated Redis timeout")
        if self.fail_get:
            raise ConnectionError("Simulated Redis get failure")
        if key in self.store:
            val, expires_at = self.store[key]
            if self.timer.time() >= expires_at:
                del self.store[key]
                return None
            return val
        return None

    def setex(self, key: str, time_seconds: int, value: str) -> bool:
        self._track("setex", key)
        if self.fail_setex:
            raise ConnectionError("Simulated Redis setex failure")
        self.store[key] = (value, self.timer.time() + time_seconds)
        return True

    def delete(self, key: str) -> int:
        self._track("delete", key)
        if self.fail_delete:
            raise ConnectionError("Simulated Redis delete failure")
        if key in self.store:
            del self.store[key]
            return 1
        return 0


@pytest.fixture
def sim_timer():
    return SimTimer()

@pytest.fixture
def fake_redis(sim_timer):
    return FakeRedisClient(sim_timer)

class _TrackingEmailService:
    def __init__(self):
        self.calls = []

    async def send_email(self, to_email: str, subject: str, html_content: str) -> bool:
        self.calls.append({"to": to_email, "subject": subject})
        return True

class _FakeCommIntegration:
    async def process_event(self, event, channel: str):
        pass

    async def generate(self, event, recipient_type, channel, notification_type, locale="en"):
        class FakeMessage:
            def __init__(self):
                self.body = "test body"
                self.html = "test html"
        class FakeOutput:
            def __init__(self):
                self.text = "test message"
                self.subject = "test subject"
                self.title = "test title"
                self.body = "test message"
                self.html_body = "test message"
                self.text_body = "test message"
        
        class FakeDecision:
            def __init__(self, c):
                self.channel = c
                self.message = "test message"
                self.subject = "test subject"
                self.title = "test title"
                self.output = FakeOutput()
        class FakeResult:
            def __init__(self, c):
                self.decision = FakeDecision(c)
                self.message = FakeMessage()
        return FakeResult(channel.upper().replace("-", "_"))

def _make_router(email_service, db_session):
    import app.services.notification_services as notif_mod
    return getattr(notif_mod, "NotificationRouter")(
        fcm_service=AsyncMock(return_value={"sent": 0, "failed": 0, "delivery_ids": []}),
        sms_service=AsyncMock(return_value={"sent": 0, "failed": 0, "blocked": 0, "blocked_reasons": {}}),
        email_service=email_service,
        ws_manager=MagicMock(),
        redis_client=MagicMock(),
        communication_integration=_FakeCommIntegration(),
    )


def _build_completed_event():
    from app.services.notification_services import JobStatusEvent
    import datetime
    return JobStatusEvent(
        job_id="99",
        tenant_id="tenant-test",
        from_status="IN_PROGRESS",
        to_status="COMPLETED",
        actor_id="actor-1",
        actor_role="technician",
        reason=None,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
        job_title="Pipe Fix",
        job_location="1 Test St",
        technician_id="tech-99",
        technician_name="Bob",
        customer_id="cust-1",
        customer_name="Alice",
        customer_phone="+15555550101",
        customer_email="alice@example.com",
        eta=None,
        notification_channels=[],
    )
