"""
test_communication_configuration_cache_consistency.py

Epic 5 Story 14.7 Cache Consistency Tests
"""
import pytest
import threading
import concurrent.futures
import json
import datetime
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.models import Base, CommunicationChannelConfiguration, Technician
from app.services.ai.FieldOpsAI.services.communication_configuration_service import (
    CommunicationConfigurationService,
    _CACHE_KEY_PREFIX,
)
from app.services.ai.FieldOpsAI.schemas.communication_configuration import (
    CommunicationChannelState,
    CommunicationMessageCategory,
)
from app.services.ai.FieldOpsAI.repositories.communication_configuration_repository import (
    CommunicationConfigurationRepository,
)
from tests.conftest import SimTimer, FakeRedisClient, _TrackingEmailService, _make_router, _build_completed_event
import app.services.twilio_sms as twilio_sms_mod
from app.services.twilio_sms import send_job_assignment_sms
import app.services.notification_services as notif_mod


# --- Database Setup for the Module ---
engine = None
TestingSessionLocal = None

@pytest.fixture(scope="module", autouse=True)
def setup_module_db(tmp_path_factory):
    global engine, TestingSessionLocal
    db_path = tmp_path_factory.mktemp("db") / "cachetest.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield
    engine.dispose()




@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    with TestingSessionLocal() as session:
        session.query(CommunicationChannelConfiguration).delete()
        config_sms = CommunicationChannelConfiguration(
            id="1",
            tenant_id="default",
            channel="SMS",
            state="ENABLED",
            revision=1,
            updated_by="system",
            created_at=datetime.datetime.now(datetime.timezone.utc),
            updated_at=datetime.datetime.now(datetime.timezone.utc),
        )
        config_email = CommunicationChannelConfiguration(
            id="2",
            tenant_id="default",
            channel="EMAIL",
            state="ENABLED",
            revision=1,
            updated_by="system",
            created_at=datetime.datetime.now(datetime.timezone.utc),
            updated_at=datetime.datetime.now(datetime.timezone.utc),
        )
        session.add(config_sms)
        session.add(config_email)
        session.commit()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Thread-Safe Threading Primitives ---
class ThreadSafeSimTimer(SimTimer):
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        
    def tick(self, seconds: float):
        with self._lock:
            super().tick(seconds)
            
    def time(self) -> float:
        with self._lock:
            return super().time()


class ThreadSafeFakeRedisClient(FakeRedisClient):
    def __init__(self, timer: ThreadSafeSimTimer):
        super().__init__(timer)
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            return super().get(key)

    def setex(self, key: str, time_seconds: int, value: str) -> bool:
        with self._lock:
            return super().setex(key, time_seconds, value)

    def delete(self, key: str) -> int:
        with self._lock:
            return super().delete(key)

    def _track(self, op: str, key: str):
        # FakeRedisClient calls _track inside get/setex/delete, which are already locked.
        # So we don't need additional locking here, but we'll lock just in case.
        super()._track(op, key)


@pytest.fixture
def sim_timer():
    return ThreadSafeSimTimer()


@pytest.fixture
def fake_redis(sim_timer):
    return ThreadSafeFakeRedisClient(sim_timer)


def _set_channel_state(session: Session, channel: str, state: str, revision: int = None):
    try:
        config = session.query(CommunicationChannelConfiguration).filter_by(channel=channel).first()
        import uuid
        if not config:
            config = CommunicationChannelConfiguration(
                id=str(uuid.uuid4()),
                tenant_id="default", 
                channel=channel,
                state=state,
                revision=revision or 1,
                updated_by="system",
                updated_at=datetime.datetime.now(datetime.timezone.utc),
                created_at=datetime.datetime.now(datetime.timezone.utc)
            )
            session.add(config)
        else:
            config.state = state
            if revision is not None:
                config.revision = revision
        session.commit()
        session.refresh(config)
        return config
    finally:
        pass



# --- 1. Exact TTL ---
def test_1_sms_ttl_is_exactly_60(db_session, fake_redis, sim_timer):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    val, expiry = fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"]
    assert expiry - sim_timer.time() == 60

def test_2_email_ttl_is_exactly_60(db_session, fake_redis, sim_timer):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("EMAIL")
    
    val, expiry = fake_redis.store[f"{_CACHE_KEY_PREFIX}:email"]
    assert expiry - sim_timer.time() == 60

def test_3_entry_valid_immediately_before_expiry(db_session, fake_redis, sim_timer):
    _set_channel_state(db_session, "SMS", "DISABLED", 1)
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    _set_channel_state(db_session, "SMS", "ENABLED", 2)
    sim_timer.tick(59.999)
    
    res = service.get_channel_configuration("SMS")
    assert res.state == CommunicationChannelState.DISABLED
    assert res.revision == 1

def test_4_entry_absent_at_expiry(db_session, fake_redis, sim_timer):
    _set_channel_state(db_session, "SMS", "DISABLED", 1)
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    _set_channel_state(db_session, "SMS", "ENABLED", 2)
    sim_timer.tick(60.000)
    
    res = service.get_channel_configuration("SMS")
    assert res.state == CommunicationChannelState.ENABLED
    assert res.revision == 2

def test_5_cache_hit_does_not_refresh_ttl(db_session, fake_redis, sim_timer):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    original_expiry = fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"][1]
    sim_timer.tick(30)
    
    service.get_channel_configuration("SMS")
    current_expiry = fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"][1]
    assert current_expiry == original_expiry

def test_6_repopulation_creates_new_60_second_expiry(db_session, fake_redis, sim_timer):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    sim_timer.tick(61)
    service.get_channel_configuration("SMS")
    
    val, expiry = fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"]
    assert expiry - sim_timer.time() == 60

# --- 2. Bounded staleness ---
def test_7_sms_stale_value_expires_within_60_seconds(db_session, fake_redis, sim_timer):
    _set_channel_state(db_session, "SMS", "ENABLED", 1)
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    _set_channel_state(db_session, "SMS", "DISABLED", 2)
    sim_timer.tick(59)
    assert service.get_channel_configuration("SMS").revision == 1
    
    sim_timer.tick(1)
    assert service.get_channel_configuration("SMS").revision == 2

def test_8_email_stale_value_expires_within_60_seconds(db_session, fake_redis, sim_timer):
    _set_channel_state(db_session, "EMAIL", "ENABLED", 1)
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("EMAIL")
    
    _set_channel_state(db_session, "EMAIL", "DISABLED", 2)
    sim_timer.tick(59)
    assert service.get_channel_configuration("EMAIL").revision == 1
    
    sim_timer.tick(1)
    assert service.get_channel_configuration("EMAIL").revision == 2

def test_9_new_db_revision_appears_after_expiry(db_session, fake_redis, sim_timer):
    _set_channel_state(db_session, "SMS", "ENABLED", 1)
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    _set_channel_state(db_session, "SMS", "DISABLED", 2)
    sim_timer.tick(60.1)
    
    res = service.get_channel_configuration("SMS")
    assert res.revision == 2

def test_10_expired_value_is_not_returned(db_session, fake_redis, sim_timer):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    _set_channel_state(db_session, "SMS", "DISABLED", 2)
    sim_timer.tick(65)
    
    res = service.get_channel_configuration("SMS")
    assert res.state == CommunicationChannelState.DISABLED

def test_11_missing_row_compatibility_default_is_not_cached(db_session, fake_redis, sim_timer):
    db_session.query(CommunicationChannelConfiguration).delete()
    db_session.commit()
    
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    assert f"{_CACHE_KEY_PREFIX}:sms" not in fake_redis.store

def test_12_database_exception_is_not_cached(fake_redis, monkeypatch):
    repo = CommunicationConfigurationRepository(MagicMock())
    repo.get_by_channel = MagicMock(side_effect=Exception("DB Down"))
    service = CommunicationConfigurationService(repo, MagicMock(), redis_client=fake_redis)
    
    with pytest.raises(Exception):
        service.get_channel_configuration("SMS")
    
    assert f"{_CACHE_KEY_PREFIX}:sms" not in fake_redis.store

# --- 3. Immediate invalidation ---
def test_13_sms_update_invalidates_only_sms(db_session, fake_redis):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    service.get_channel_configuration("EMAIL")
    
    service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
    
    assert f"{_CACHE_KEY_PREFIX}:sms" not in fake_redis.store
    assert f"{_CACHE_KEY_PREFIX}:email" in fake_redis.store

def test_14_email_update_invalidates_only_email(db_session, fake_redis):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    service.get_channel_configuration("EMAIL")
    
    service.update_channel_state("EMAIL", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
    
    assert f"{_CACHE_KEY_PREFIX}:email" not in fake_redis.store
    assert f"{_CACHE_KEY_PREFIX}:sms" in fake_redis.store

def test_15_failed_commit_invalidates_neither(db_session, fake_redis, monkeypatch):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    service.get_channel_configuration("EMAIL")
    
    def fail_commit(*args):
        raise Exception("DB Error")
    monkeypatch.setattr(db_session, "commit", fail_commit)
    
    with pytest.raises(Exception):
        service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
        
    assert f"{_CACHE_KEY_PREFIX}:sms" in fake_redis.store
    assert f"{_CACHE_KEY_PREFIX}:email" in fake_redis.store

def test_16_no_op_invalidates_neither(db_session, fake_redis):
    _set_channel_state(db_session, "SMS", "ENABLED", 1)
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    service.get_channel_configuration("EMAIL")
    
    service.update_channel_state("SMS", CommunicationChannelState.ENABLED, "user1", "tenant1", "Valid reason")
    
    assert f"{_CACHE_KEY_PREFIX}:sms" in fake_redis.store
    assert f"{_CACHE_KEY_PREFIX}:email" in fake_redis.store

def test_17_next_reader_gets_latest_revision(db_session, fake_redis):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
    
    res = service.get_channel_configuration("SMS")
    assert res.revision == 2
    assert res.state == CommunicationChannelState.DISABLED

def test_18_next_provider_policy_gets_latest_state(db_session, fake_redis):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
    
    decision = service.evaluate_delivery("SMS")
    assert decision.allowed is False
    assert decision.state == CommunicationChannelState.DISABLED

# --- 4. Degraded invalidation ---
def test_19_delete_failure_triggers_setex(db_session, fake_redis):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    fake_redis.fail_delete = True
    fake_redis.calls.clear()
    
    service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
    assert any(c.startswith(f"setex:{_CACHE_KEY_PREFIX}:sms") for c in fake_redis.calls)

def test_20_setex_replacement_uses_ttl_60(db_session, fake_redis, sim_timer):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    fake_redis.fail_delete = True
    service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
    
    val, expiry = fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"]
    assert expiry - sim_timer.time() == 60

def test_21_delete_and_setex_failure_does_not_extend_old_ttl(db_session, fake_redis, sim_timer, caplog):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    original_expiry = fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"][1]
    
    sim_timer.tick(30)
    fake_redis.fail_delete = True
    fake_redis.fail_setex = True
    
    service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
    
    current_expiry = fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"][1]
    assert current_expiry == original_expiry

def test_22_old_value_disappears_at_original_expiry(db_session, fake_redis, sim_timer):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    sim_timer.tick(30)
    fake_redis.fail_delete = True
    fake_redis.fail_setex = True
    service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
    
    # Old cache is still there before expiry
    res = service.get_channel_configuration("SMS")
    assert res.state == CommunicationChannelState.ENABLED
    
    sim_timer.tick(30)
    # Original expiry reached
    res2 = service.get_channel_configuration("SMS")
    assert res2.state == CommunicationChannelState.DISABLED

def test_23_latest_db_value_appears_after_expiry(db_session, fake_redis, sim_timer):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("SMS")
    
    fake_redis.fail_delete = True
    fake_redis.fail_setex = True
    service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
    
    sim_timer.tick(61)
    res = service.get_channel_configuration("SMS")
    assert res.state == CommunicationChannelState.DISABLED

def test_24_sanitized_degraded_sync_log_exists(
    db_session,
    fake_redis,
    monkeypatch,
):
    from unittest.mock import MagicMock

    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(
        repo,
        db_session,
        redis_client=fake_redis,
    )

    service.get_channel_configuration("SMS")

    fake_redis.fail_delete = True
    fake_redis.fail_setex = True

    warning_mock = MagicMock()

    monkeypatch.setattr(
        "app.services.ai.FieldOpsAI.services."
        "communication_configuration_service.logger.warning",
        warning_mock,
    )

    service.update_channel_state(
        "SMS",
        CommunicationChannelState.DISABLED,
        "user1",
        "tenant1",
        "Valid reason",
    )

    assert warning_mock.called

    rendered_messages = []

    for call in warning_mock.call_args_list:
        args = call.args

        if not args:
            continue

        message = str(args[0])

        # Support normal logging placeholders:
        # logger.warning("channel=%s", channel)
        if len(args) > 1:
            try:
                message = message % args[1:]
            except (TypeError, ValueError):
                pass

        rendered_messages.append(message)

    assert (
        "cache_sync_degraded "
        "operation=delete "
        "channel=SMS "
        "result=failure"
        in rendered_messages
    )

    combined_logs = " ".join(rendered_messages)

    assert "Simulated Redis delete failure" not in combined_logs
    assert "Simulated Redis setex failure" not in combined_logs
    assert "ConnectionError" not in combined_logs
# --- 5. Concurrent readers ---
def test_25_readers_after_sms_invalidation_all_get_latest_revision(fake_redis):
    with TestingSessionLocal() as db:
        _set_channel_state(db, "SMS", "ENABLED", 1)
    
    # Writer
    with TestingSessionLocal() as db:
        repo = CommunicationConfigurationRepository(db)
        service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
        service.get_channel_configuration("SMS")
        service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
        
    def reader_task():
        with TestingSessionLocal() as db:
            repo = CommunicationConfigurationRepository(db)
            service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
            return service.get_channel_configuration("SMS")
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(reader_task) for _ in range(50)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
        
    for res in results:
        assert res.revision == 2
        assert res.state == CommunicationChannelState.DISABLED

def test_26_readers_after_email_invalidation_all_get_latest_revision(fake_redis):
    with TestingSessionLocal() as db:
        _set_channel_state(db, "EMAIL", "ENABLED", 1)
    
    with TestingSessionLocal() as db:
        repo = CommunicationConfigurationRepository(db)
        service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
        service.get_channel_configuration("EMAIL")
        service.update_channel_state("EMAIL", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
        
    def reader_task():
        with TestingSessionLocal() as db:
            repo = CommunicationConfigurationRepository(db)
            service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
            return service.get_channel_configuration("EMAIL")
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(reader_task) for _ in range(50)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
        
    for res in results:
        assert res.revision == 2
        assert res.state == CommunicationChannelState.DISABLED

def test_27_to_32_readers_during_update_controlled_phases(fake_redis, monkeypatch):
    import threading
    from app.services.ai.FieldOpsAI.schemas.communication_configuration import CommunicationConfigurationCachePayload

    with TestingSessionLocal() as db:
        _set_channel_state(db, "SMS", "ENABLED", 1)

    # 1. Old value is cached.
    with TestingSessionLocal() as db:
        repo = CommunicationConfigurationRepository(db)
        service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
        service.get_channel_configuration("SMS")

    pause_before_delete = threading.Event()
    delete_completed = threading.Event()
    reader_observed_n = threading.Event()

    original_cache_delete = CommunicationConfigurationService._cache_delete
    def patched_cache_delete(self, key):
        # 3. Writer pauses immediately before cache deletion.
        pause_before_delete.set()
        reader_observed_n.wait(timeout=5)
        res = original_cache_delete(self, key)
        # 5. Allow deletion to complete.
        delete_completed.set()
        return res
    
    monkeypatch.setattr(CommunicationConfigurationService, "_cache_delete", patched_cache_delete)

    writer_result = []
    def writer_task():
        try:
            with TestingSessionLocal() as db:
                repo = CommunicationConfigurationRepository(db)
                service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
                # 2. Writer commits revision N+1.
                service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
        except Exception as e:
            writer_result.append(e)

    reader_n_res = []
    def reader_n_task():
        pause_before_delete.wait(timeout=5)
        try:
            with TestingSessionLocal() as db:
                repo = CommunicationConfigurationRepository(db)
                service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
                # 4. A controlled reader may observe revision N.
                res = service.get_channel_configuration("SMS")
                reader_n_res.append(res)
        finally:
            reader_observed_n.set()

    reader_n1_results = []
    def reader_n1_task():
        delete_completed.wait(timeout=5)
        try:
            with TestingSessionLocal() as db:
                repo = CommunicationConfigurationRepository(db)
                service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
                # 6. All later readers must observe revision N+1.
                res = service.get_channel_configuration("SMS")
                reader_n1_results.append(res)
        except Exception as e:
            reader_n1_results.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        f_writer = executor.submit(writer_task)
        f_reader_n = executor.submit(reader_n_task)
        f_readers_n1 = [executor.submit(reader_n1_task) for _ in range(10)]
        
        # All worker futures are resolved with timeout=5
        f_writer.result(timeout=5)
        f_reader_n.result(timeout=5)
        for f in f_readers_n1:
            f.result(timeout=5)
            
    # Exceptions surfaced if any
    assert not writer_result
    
    assert reader_n_res[0].revision == 1
    assert reader_n_res[0].state == CommunicationChannelState.ENABLED
    
    for res in reader_n1_results:
        assert not isinstance(res, Exception)
        assert res.revision == 2
        assert res.state == CommunicationChannelState.DISABLED
        
    # Check cache payload validates
    val, exp = fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"]
    payload = CommunicationConfigurationCachePayload.model_validate_json(val)
    assert payload.revision == 2
    assert payload.state == "DISABLED"


# --- 6. Concurrent misses ---
def test_33_concurrent_sms_misses_return_one_consistent_revision(fake_redis):
    with TestingSessionLocal() as db:
        _set_channel_state(db, "SMS", "ENABLED", 1)
    
    def reader_task():
        with TestingSessionLocal() as db:
            repo = CommunicationConfigurationRepository(db)
            service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
            return service.get_channel_configuration("SMS")
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(reader_task) for _ in range(50)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
        
    for res in results:
        assert res.revision == 1

def test_34_concurrent_email_misses_return_one_consistent_revision(fake_redis):
    with TestingSessionLocal() as db:
        _set_channel_state(db, "EMAIL", "ENABLED", 1)
    
    def reader_task():
        with TestingSessionLocal() as db:
            repo = CommunicationConfigurationRepository(db)
            service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
            return service.get_channel_configuration("EMAIL")
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(reader_task) for _ in range(50)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
        
    for res in results:
        assert res.revision == 1

def test_35_repeated_setex_values_are_identical(fake_redis, monkeypatch):
    from app.services.ai.FieldOpsAI.schemas.communication_configuration import CommunicationConfigurationCachePayload
    
    with TestingSessionLocal() as db:
        _set_channel_state(db, "SMS", "ENABLED", 1)
    
    setex_records = []
    original_setex = fake_redis.setex
    def tracking_setex(key: str, time_seconds: int, value: str):
        setex_records.append((key, time_seconds, value))
        return original_setex(key, time_seconds, value)
    monkeypatch.setattr(fake_redis, "setex", tracking_setex)

    def reader_task():
        with TestingSessionLocal() as db:
            repo = CommunicationConfigurationRepository(db)
            service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
            return service.get_channel_configuration("SMS")
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(reader_task) for _ in range(50)]
        for f in concurrent.futures.as_completed(futures):
            f.result(timeout=5)
        
    assert len(setex_records) > 0
    
    first_payload_str = setex_records[0][2]
    
    for key, ttl, payload_str in setex_records:
        assert key == f"{_CACHE_KEY_PREFIX}:sms"
        assert ttl == 60
        assert payload_str == first_payload_str
        
        payload = CommunicationConfigurationCachePayload.model_validate_json(payload_str)
        assert payload.channel == "SMS"
        assert payload.state == "ENABLED"
        assert payload.revision == 1

def test_36_no_cross_channel_payload_is_written(fake_redis):
    def reader_task_sms():
        with TestingSessionLocal() as db:
            repo = CommunicationConfigurationRepository(db)
            service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
            return service.get_channel_configuration("SMS")
            
    def reader_task_email():
        with TestingSessionLocal() as db:
            repo = CommunicationConfigurationRepository(db)
            service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
            return service.get_channel_configuration("EMAIL")
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        f1 = [executor.submit(reader_task_sms) for _ in range(25)]
        f2 = [executor.submit(reader_task_email) for _ in range(25)]
        concurrent.futures.wait(f1 + f2)
        
    val_sms = json.loads(fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"][0])
    val_email = json.loads(fake_redis.store[f"{_CACHE_KEY_PREFIX}:email"][0])
    assert val_sms["channel"] == "SMS"
    assert val_email["channel"] == "EMAIL"

def test_37_no_correctness_dependency_on_exactly_one_db_query(fake_redis, monkeypatch):
    import threading
    from app.services.ai.FieldOpsAI.schemas.communication_configuration import CommunicationConfigurationCachePayload

    with TestingSessionLocal() as db:
        _set_channel_state(db, "SMS", "ENABLED", 1)

    read_count = 0
    read_lock = threading.Lock()

    original_get_by_channel = CommunicationConfigurationRepository.get_by_channel

    def counting_get_by_channel(self, channel: str):
        nonlocal read_count
        with read_lock:
            read_count += 1
        # Add a tiny sleep to increase chance of concurrent DB reads before first SETEX
        import time
        time.sleep(0.01)
        return original_get_by_channel(self, channel)

    monkeypatch.setattr(CommunicationConfigurationRepository, "get_by_channel", counting_get_by_channel)

    def reader_task():
        with TestingSessionLocal() as db:
            repo = CommunicationConfigurationRepository(db)
            service = CommunicationConfigurationService(repo, db, redis_client=fake_redis)
            return service.get_channel_configuration("SMS")

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(reader_task) for _ in range(50)]
        results = [f.result(timeout=5) for f in concurrent.futures.as_completed(futures)]

    # DB reads > 0, potentially > 1 without single-flight locking, which is fine
    assert read_count >= 1

    # all readers receive the same committed state and revision
    for res in results:
        assert res.revision == 1
        assert res.state == CommunicationChannelState.ENABLED

    # all written cache payloads are valid and identical
    setex_calls = [c for c in fake_redis.calls if c.startswith(f"setex:{_CACHE_KEY_PREFIX}:sms")]
    assert len(setex_calls) >= 1
    
    val, ttl = fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"]
    payload = CommunicationConfigurationCachePayload.model_validate_json(val)
    assert payload.revision == 1
    assert payload.state == "ENABLED"

# --- 7. Corruption and failures ---
def test_38_partial_json_falls_back_to_db(db_session, fake_redis):
    _set_channel_state(db_session, "SMS", "DISABLED", 1)
    fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"] = ("{invalid", 9999)
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    
    res = service.get_channel_configuration("SMS")
    assert res.state == CommunicationChannelState.DISABLED

def test_39_wrong_channel_payload_falls_back_to_db(db_session, fake_redis):
    _set_channel_state(db_session, "SMS", "DISABLED", 1)
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = json.dumps({"schema_version": 1, "channel": "EMAIL", "state": "ENABLED", "revision": 1, "updated_at": now.isoformat(), "updated_by": "A"})
    fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"] = (payload, 9999)
    
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    
    res = service.get_channel_configuration("SMS")
    assert res.state == CommunicationChannelState.DISABLED

def test_40_invalid_state_falls_back_to_db(db_session, fake_redis):
    _set_channel_state(db_session, "SMS", "DISABLED", 1)
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = json.dumps({"schema_version": 1, "channel": "SMS", "state": "UNKNOWN", "revision": 1, "updated_at": now.isoformat(), "updated_by": "A"})
    fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"] = (payload, 9999)
    
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    
    res = service.get_channel_configuration("SMS")
    assert res.state == CommunicationChannelState.DISABLED

def test_41_corrupt_cache_plus_db_outage_fails_closed(db_session, fake_redis, monkeypatch):
    import asyncio
    fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"] = ("{invalid", 9999)
    repo = CommunicationConfigurationRepository(db_session)
    repo.get_by_channel = MagicMock(side_effect=Exception("DB Down"))
    
    mock_twilio = MagicMock()
    monkeypatch.setattr(twilio_sms_mod, "twilio_client", mock_twilio)
    monkeypatch.setattr(twilio_sms_mod, "check_rate_limit", lambda redis_client, tech_id: True)
    
    def mock_service(*args, **kwargs):
        return CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    monkeypatch.setattr(notif_mod, "CommunicationConfigurationService", mock_service)
    # also patch the one used inside twilio_sms itself
    monkeypatch.setattr(twilio_sms_mod, "CommunicationConfigurationService", mock_service)
    
    from app.models import Technician
    tech = Technician(technician_id=10, tech_id="tech1", technician_name="T", technician_skill="S", technician_location="L", phone_number="+1234567890", sms_opt_out=0)
    db_session.add(tech)
    db_session.commit()
    
    res = asyncio.run(send_job_assignment_sms(db_session, "job1", "Title", "Loc", "P", ["tech1"]))
    
    assert res["blocked"] == 1
    assert mock_twilio.messages.create.call_count == 0
    assert "CONFIGURATION_UNAVAILABLE" in str(res.get("blocked_reasons", {}))

def test_42_redis_get_outage_falls_back_to_db(db_session, fake_redis):
    _set_channel_state(db_session, "SMS", "DISABLED", 1)
    fake_redis.fail_get = True
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    
    res = service.get_channel_configuration("SMS")
    assert res.state == CommunicationChannelState.DISABLED

def test_43_redis_outage_plus_db_outage_fails_closed(db_session, fake_redis, monkeypatch):
    import asyncio
    fake_redis.fail_get = True
    repo = CommunicationConfigurationRepository(db_session)
    repo.get_by_channel = MagicMock(side_effect=Exception("DB Down"))
    
    mock_twilio = MagicMock()
    monkeypatch.setattr(twilio_sms_mod, "twilio_client", mock_twilio)
    monkeypatch.setattr(twilio_sms_mod, "check_rate_limit", lambda redis_client, tech_id: True)
    
    def mock_service(*args, **kwargs):
        return CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    monkeypatch.setattr(notif_mod, "CommunicationConfigurationService", mock_service)
    monkeypatch.setattr(twilio_sms_mod, "CommunicationConfigurationService", mock_service)
    
    from app.models import Technician
    tech = Technician(technician_id=11, tech_id="tech1", technician_name="T", technician_skill="S", technician_location="L", phone_number="+1234567890", sms_opt_out=0)
    db_session.add(tech)
    db_session.commit()
    
    res = asyncio.run(send_job_assignment_sms(db_session, "job1", "Title", "Loc", "P", ["tech1"]))
    
    assert res["blocked"] == 1
    assert mock_twilio.messages.create.call_count == 0
    assert "CONFIGURATION_UNAVAILABLE" in str(res.get("blocked_reasons", {}))

def test_44_no_raw_corrupt_value_appears_in_logs(db_session, fake_redis, caplog):
    fake_redis.store[f"{_CACHE_KEY_PREFIX}:sms"] = ("BAD_PAYLOAD_CONTENT", 9999)
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    
    service.get_channel_configuration("SMS")
    assert "BAD_PAYLOAD_CONTENT" not in caplog.text

def test_45_no_raw_exception_appears_in_logs(db_session, fake_redis, monkeypatch, caplog):
    fake_redis.fail_get = True
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    
    service.get_channel_configuration("SMS")
    assert "Simulated Redis get failure" not in caplog.text

# --- 8. Delivery integration ---
def test_46_47_sms_becomes_blocked_after_stale_enabled_expires(db_session, fake_redis, sim_timer, monkeypatch):
    import asyncio
    
    # Add technician
    tech = Technician(technician_id=1, tech_id="tech1", technician_name="T", technician_skill="S", technician_location="L", phone_number="+1234567890", sms_opt_out=0)
    db_session.add(tech)
    db_session.commit()
    
    mock_twilio = MagicMock()
    mock_twilio.messages.create.return_value.sid = "123"
    monkeypatch.setattr(twilio_sms_mod, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(twilio_sms_mod, "twilio_client", mock_twilio)
    monkeypatch.setattr(twilio_sms_mod, "check_rate_limit", lambda redis_client, tech_id: True)

    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    
    _set_channel_state(db_session, "SMS", "ENABLED", 1)
    service.get_channel_configuration("SMS")
    
    _set_channel_state(db_session, "SMS", "DISABLED", 2)
    
    # Still enabled from cache
    res1 = asyncio.run(send_job_assignment_sms(db_session, "job1", "Title", "Loc", "P", ["tech1"]))
    assert res1["sent"] == 1
    assert mock_twilio.messages.create.call_count == 1
    
    # Expiry
    sim_timer.tick(61)
    
    # Now disabled
    res2 = asyncio.run(send_job_assignment_sms(db_session, "job1", "Title", "Loc", "P", ["tech1"]))
    assert res2["blocked"] == 1
    assert mock_twilio.messages.create.call_count == 1  # No second call made
    
def test_48_49_email_becomes_allowed_after_stale_disabled_expires(db_session, fake_redis, sim_timer, monkeypatch):
    import asyncio
    
    _set_channel_state(db_session, "EMAIL", "DISABLED", 1)
    
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    service.get_channel_configuration("EMAIL")
    
    _set_channel_state(db_session, "EMAIL", "ENABLED", 2)
    
    email_svc = _TrackingEmailService()
    router = _make_router(email_svc, db_session)
    monkeypatch.setattr(notif_mod, "SessionLocal", TestingSessionLocal)
    
    # Monkey patch the internal service creation in _send_email to use fake_redis
    def mock_service(repo, db, **kwargs):
        return CommunicationConfigurationService(repo, db, redis_client=fake_redis)
    monkeypatch.setattr(notif_mod, "CommunicationConfigurationService", mock_service)
    
    asyncio.run(router.route(_build_completed_event()))
    assert len(email_svc.calls) == 0
    
    sim_timer.tick(61)
    
    asyncio.run(router.route(_build_completed_event()))
    assert len(email_svc.calls) == 1

def test_50_immediate_invalidation_path_requires_no_clock_advance(db_session, fake_redis):
    repo = CommunicationConfigurationRepository(db_session)
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis)
    _set_channel_state(db_session, "SMS", "ENABLED", 1)
    service.get_channel_configuration("SMS")
    
    service.update_channel_state("SMS", CommunicationChannelState.DISABLED, "user1", "tenant1", "Valid reason")
    
    res = service.get_channel_configuration("SMS")
    assert res.state == CommunicationChannelState.DISABLED

def test_51_environment_override_takes_effect_without_cache_expiry(db_session, fake_redis):
    repo = CommunicationConfigurationRepository(db_session)
    env = {}
    service = CommunicationConfigurationService(repo, db_session, redis_client=fake_redis, environment=env)
    
    # Persistent cached state = ENABLED
    _set_channel_state(db_session, "SMS", "ENABLED", 1)
    
    # Override initially absent, decision allowed
    decision1 = service.evaluate_delivery("SMS")
    assert decision1.allowed is True
    
    # Set override to DISABLED
    env["FIELDOPS_SMS_EMERGENCY_OVERRIDE"] = "DISABLED"
    
    # Next decision blocked, no clock advancement, no cache deletion, no service reconstruction
    decision2 = service.evaluate_delivery("SMS")
    assert decision2.allowed is False
    assert decision2.policy_source == "environment"
    
    # Remove override
    del env["FIELDOPS_SMS_EMERGENCY_OVERRIDE"]
    
    # Next decision allowed
    decision3 = service.evaluate_delivery("SMS")
    assert decision3.allowed is True
    
    # Assert the override value never appears in Redis
    assert "DISABLED" not in fake_redis.store.get(f"{_CACHE_KEY_PREFIX}:sms", ("", 0))[0]

def test_52_customer_preference_block_remains_effective(db_session, fake_redis):
    from app.services.ai.FieldOpsAI.services.communication_delivery_policy_service import CommunicationDeliveryPolicyService
    from app.services.ai.FieldOpsAI.schemas.communication_configuration import CommunicationMessageCategory
    from app.services.ai.FieldOpsAI.schemas.communication import CommunicationRecipient
    import uuid

    _set_channel_state(db_session, "SMS", "ENABLED", 1)
    
    tenant1 = f"tenant-{uuid.uuid4()}"
    tenant2 = f"tenant-{uuid.uuid4()}"
    cust1 = f"cust-{uuid.uuid4()}"
    
    config_repo = CommunicationConfigurationRepository(db_session)
    config_service = CommunicationConfigurationService(config_repo, db_session, redis_client=fake_redis)
    
    # Mock CustomerPreferenceService
    class MockPrefService:
        def evaluate_channel(self, tenant_id, customer_id, channel, category=None):
            from app.services.ai.FieldOpsAI.schemas.customer_profile import CustomerPreferenceDecision
            if tenant_id == tenant1 and customer_id == cust1 and channel == "SMS":
                return CustomerPreferenceDecision(allowed=False, channel=channel, reason_code="customer_opt_out", source="PROFILE", revision=1)
            return CustomerPreferenceDecision(allowed=True, channel=channel, reason_code="default", source="PROFILE", revision=1)

    pref_service = MockPrefService()
    policy_service = CommunicationDeliveryPolicyService(config_service, pref_service)

    decision = policy_service.evaluate(
        channel="SMS", 
        category=CommunicationMessageCategory.STANDARD,
        recipient_type=CommunicationRecipient.CUSTOMER,
        tenant_id=tenant1, 
        customer_id=cust1
    )
    
    assert decision.allowed is False
    assert decision.final_reason_code.upper() == "CUSTOMER_OPT_OUT"
    assert getattr(decision, "preference_source", None) == "PROFILE"

    # Global cache remains unchanged (only global configuration is cached)
    keys = list(fake_redis.store.keys())
    assert len(keys) == 1
    assert keys[0] == f"{_CACHE_KEY_PREFIX}:sms"
    
    # Same customer ID in another tenant does not affect decision (they opted IN for tenant-2)
    decision_other = policy_service.evaluate(
        channel="SMS", 
        category=CommunicationMessageCategory.STANDARD,
        recipient_type=CommunicationRecipient.CUSTOMER,
        tenant_id=tenant2, 
        customer_id=cust1
    )
    assert decision_other.allowed is True

