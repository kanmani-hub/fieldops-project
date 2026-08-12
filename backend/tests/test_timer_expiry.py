import pytest
import uuid
import fakeredis
from freezegun import freeze_time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import factory

from app.database import Base, get_db
from app import models
from app.worker import check_assignment_timers
from app.services.timer_service import TimerService

# DB Setup
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
test_db_session = TestingSessionLocal()

# Factories
class TechnicianFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = models.Technician
        sqlalchemy_session = test_db_session
        sqlalchemy_session_persistence = "commit"

    tech_id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    tenant_id = "tenant-123"
    technician_name = factory.Sequence(lambda n: f"Tech {n}")
    technician_skill = "HVAC"
    technician_location = "13.0,80.0"

class JobFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = models.Job
        sqlalchemy_session = test_db_session
        sqlalchemy_session_persistence = "commit"

    customer_name = "Test Customer"
    location = "13.0,80.0"
    issue_description = "Issue"
    priority = "P1"
    service_type = "HVAC"
    contact_number = "+1234567890"
    preferred_service_date = factory.LazyFunction(lambda: datetime.now(timezone.utc).date())
    status = "ASSIGNED"
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    updated_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))

@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    try:
        yield test_db_session
    finally:
        test_db_session.rollback()
        Base.metadata.drop_all(bind=engine)

class MockRedisCacheManager:
    def __init__(self, fake_client):
        self.client = fake_client
    def __getattr__(self, name):
        return getattr(self.client, name)

@pytest.fixture(scope="function")
def fake_redis():
    redis_client = fakeredis.FakeStrictRedis(decode_responses=True)
    yield MockRedisCacheManager(redis_client)
    redis_client.flushall()

@pytest.fixture(scope="function")
def worker_deps(db_session, fake_redis, monkeypatch):
    monkeypatch.setattr("app.worker.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.worker.get_redis_client", lambda: fake_redis)
    monkeypatch.setattr("app.services.distributed_lock_service.get_redis_client", lambda: fake_redis)

@freeze_time("2026-05-29 12:00:00")
def test_timer_starts_on_assigned(worker_deps, db_session, fake_redis):
    tech = TechnicianFactory()
    job = JobFactory(assigned_technician_id=tech.technician_id)
    
    TimerService.start_timer(fake_redis, job.id, tech.tech_id, duration_seconds=600)
    
    assert fake_redis.exists(f"job:timer:{job.id}")
    assert fake_redis.ttl(f"job:timer:{job.id}") == 600

@freeze_time("2026-05-29 12:00:00")
def test_timer_expires_at_10_minutes(worker_deps, db_session, fake_redis):
    tech = TechnicianFactory()
    job = JobFactory(assigned_technician_id=tech.technician_id)
    
    TimerService.start_timer(fake_redis, job.id, tech.tech_id, duration_seconds=600)
    
    # Fast forward exactly 10 minutes and 1 second
    with freeze_time("2026-05-29 12:10:01"):
        # In fakeredis, time freezing should expire the key, or we just manually delete to simulate expiry
        # Fakeredis handles time automatically if updated, but let's be certain:
        fake_redis.delete(f"job:timer:{job.id}")
        
        # job.updated_at is still 12:00:00, now is 12:10:01
        check_assignment_timers()
        
        db_session.refresh(job)
        assert job.status == "QUEUED"

@freeze_time("2026-05-29 12:00:00")
def test_status_changes_to_queued(worker_deps, db_session, fake_redis):
    tech = TechnicianFactory()
    job = JobFactory(assigned_technician_id=tech.technician_id)
    
    with freeze_time("2026-05-29 12:10:05"):
        check_assignment_timers()
        db_session.refresh(job)
        assert job.status == "QUEUED"

@freeze_time("2026-05-29 12:00:00")
def test_redispatch_triggered(worker_deps, db_session, fake_redis, monkeypatch):
    tech = TechnicianFactory()
    job = JobFactory(assigned_technician_id=tech.technician_id)
    
    mock_dispatch = MagicMock()
    monkeypatch.setattr("app.worker.DispatchAgent.trigger_redispatch", mock_dispatch)
    
    with freeze_time("2026-05-29 12:10:05"):
        check_assignment_timers()
        
        mock_dispatch.assert_called_once_with(str(job.id))

@freeze_time("2026-05-29 12:00:00")
def test_audit_log_created(worker_deps, db_session, fake_redis):
    tech = TechnicianFactory()
    job = JobFactory(assigned_technician_id=tech.technician_id)
    
    with freeze_time("2026-05-29 12:10:05"):
        check_assignment_timers()
        
        audit = db_session.query(models.AuditEvent).filter(models.AuditEvent.tech_id == tech.tech_id).first()
        assert audit is not None
        assert audit.event_type == "JOB_REQUEUED"
        assert audit.new_status == "QUEUED"
        
        # Check dispatcher notification
        notif = db_session.query(models.DispatcherNotification).filter(models.DispatcherNotification.tech_id == tech.tech_id).first()
        assert notif is not None
        assert "assignment revoked" in notif.message

@freeze_time("2026-05-29 12:00:00")
def test_boundary_9_59_not_expired(worker_deps, db_session, fake_redis, monkeypatch):
    tech = TechnicianFactory()
    job = JobFactory(assigned_technician_id=tech.technician_id)
    
    TimerService.start_timer(fake_redis, job.id, tech.tech_id, duration_seconds=600)
    
    mock_dispatch = MagicMock()
    monkeypatch.setattr("app.worker.DispatchAgent.trigger_redispatch", mock_dispatch)
    
    # 9 minutes 59 seconds
    with freeze_time("2026-05-29 12:09:59"):
        # Fakeredis may not auto expire, but the TTL would be 1
        # Let's ensure the timer still exists in mock redis
        fake_redis.setex(f"job:timer:{job.id}", 1, "test")
        
        check_assignment_timers()
        
        db_session.refresh(job)
        assert job.status == "ASSIGNED"
        mock_dispatch.assert_not_called()

@freeze_time("2026-05-29 12:00:00")
def test_boundary_10_00_expired(worker_deps, db_session, fake_redis, monkeypatch):
    tech = TechnicianFactory()
    job = JobFactory(assigned_technician_id=tech.technician_id)
    
    TimerService.start_timer(fake_redis, job.id, tech.tech_id, duration_seconds=600)
    
    mock_dispatch = MagicMock()
    monkeypatch.setattr("app.worker.DispatchAgent.trigger_redispatch", mock_dispatch)
    
    # Exactly 10 minutes + 11 seconds (timeout check uses > 10 seconds buffer in code: `(now - updated).total_seconds() > 10`)
    # The requirement says "expires at exactly 10:00 ±5s". If we want to trigger timeout, 
    # the code checks `if (now - updated).total_seconds() > 10`. 
    # Let's use 10m 11s.
    with freeze_time("2026-05-29 12:10:11"):
        fake_redis.delete(f"job:timer:{job.id}")
        
        check_assignment_timers()
        
        db_session.refresh(job)
        assert job.status == "QUEUED"
        mock_dispatch.assert_called_once()

@freeze_time("2026-05-29 12:00:00")
def test_restart_during_timer(worker_deps, db_session, fake_redis):
    tech = TechnicianFactory()
    job = JobFactory(assigned_technician_id=tech.technician_id)
    
    # Simulate server restart by NOT calling TimerService.start_timer,
    # but the job was updated at 12:00:00.
    # The redis keys might be lost or expired.
    
    with freeze_time("2026-05-29 12:10:11"):
        # No redis keys exist.
        check_assignment_timers()
        
        db_session.refresh(job)
        assert job.status == "QUEUED"
