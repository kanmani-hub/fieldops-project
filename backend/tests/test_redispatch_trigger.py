import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from fakeredis import FakeRedis
from freezegun import freeze_time
from contextlib import contextmanager

from app.main import app
from app.models import Job, Technician, AuditEvent, DispatcherNotification
from app.database import Base, get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.redis_client import get_redis_client
from app.services.re_dispatch_trigger import ReDispatchTriggerService
from app.services.re_dispatch_queue import ReDispatchQueueService
from app.services.timer_service import TimerService

# Setup test DB
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_redispatch.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

client = TestClient(app)

fake_redis = FakeRedis(decode_responses=True)

def override_get_redis():
    return fake_redis

@pytest.fixture(autouse=True)
def apply_overrides():
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_client] = override_get_redis
    yield
    app.dependency_overrides.clear()

@contextmanager
def dummy_job_lock(*args, **kwargs):
    yield "dummy_lock"

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    
    # Cleanup
    db.query(DispatcherNotification).delete()
    db.query(AuditEvent).delete()
    db.query(Job).delete()
    db.query(Technician).delete()
    db.commit()
    
    # Reset fake redis
    fake_redis.flushall()
    
    yield db
    Base.metadata.drop_all(bind=engine)

@patch("app.routes.jobs.with_job_lock", side_effect=dummy_job_lock)
def test_rejection_triggers_redispatch(mock_lock, setup_db):
    db = setup_db
    tech = Technician(
        tech_id="tech-123", technician_name="John", technician_status="BUSY", current_jobs=1,
        technician_skill="Plumbing", technician_location="0,0"
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="P4", service_type="Plumbing", contact_number="123",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        assigned_technician_id=tech.technician_id
    )
    db.add(job)
    db.commit()
    
    response = client.post(
        f"/jobs/{job.id}/reject",
        headers={"Authorization": "Bearer tech-123", "X-Tenant-ID": "tenant-1"},
        json={"reason": "Customer too far"}
    )
    
    assert response.status_code == 200, response.text
    
    db.refresh(job)
    assert job.status == "QUEUED"
    
    # Check redis queue
    queue_key = f"dispatch:queue:tenant-1"
    rank = fake_redis.zrank(queue_key, str(job.id))
    assert rank is not None

def test_timeout_triggers_redispatch(setup_db):
    db = setup_db
    tech = Technician(
        tech_id="tech-123", technician_name="John", technician_status="AVAILABLE",
        technician_skill="Plumbing", technician_location="0,0"
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="P4", service_type="Plumbing", contact_number="123",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        assigned_technician_id=tech.technician_id,
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=15)
    )
    db.add(job)
    db.commit()
    
    # Simulate missing timer
    trigger = ReDispatchTriggerService.detect_trigger(job, tech, timer_exists=False, timer_ttl=0)
    assert trigger is not None
    assert trigger["type"] == "trigger"
    assert trigger["reason"] == "timeout"
    
    # Simulate valid timer
    trigger2 = ReDispatchTriggerService.detect_trigger(job, tech, timer_exists=True, timer_ttl=300)
    assert trigger2 is None

def test_offline_triggers_redispatch(setup_db):
    db = setup_db
    tech = Technician(
        tech_id="tech-123", technician_name="John", technician_status="OFFLINE",
        technician_skill="Plumbing", technician_location="0,0"
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="P1", service_type="Plumbing", contact_number="123",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        assigned_technician_id=tech.technician_id
    )
    db.add(job)
    db.commit()
    
    # Offline trigger
    trigger = ReDispatchTriggerService.detect_trigger(job, tech, timer_exists=True, timer_ttl=300)
    assert trigger is not None
    assert trigger["type"] == "trigger"
    assert trigger["reason"] == "tech_offline"

def test_status_assigned_to_queued(setup_db):
    db = setup_db
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="P4", service_type="Plumbing", contact_number="123",
        preferred_service_date=datetime.now().date(), status="ASSIGNED"
    )
    db.add(job)
    db.commit()
    
    ReDispatchQueueService.enqueue_failed_job(db, fake_redis, job, "tenant-1", "Test")
    
    db.refresh(job)
    assert job.status == "QUEUED"

def test_priority_bump_applied(setup_db):
    db = setup_db
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="P4", service_type="Plumbing", contact_number="123",
        preferred_service_date=datetime.now().date(), status="ASSIGNED"
    )
    db.add(job)
    db.commit()
    
    ReDispatchQueueService.enqueue_failed_job(db, fake_redis, job, "tenant-1", "Test failure")
    
    db.refresh(job)
    assert job.status == "QUEUED"
    assert job.priority == "P3"
    assert job.previous_priority == "P4"
    assert job.bumped_at is not None

def test_attempt_count_incremented(setup_db):
    db = setup_db
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="P4", service_type="Plumbing", contact_number="123",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        attempt_count=2
    )
    db.add(job)
    db.commit()
    
    ReDispatchQueueService.enqueue_failed_job(db, fake_redis, job, "tenant-1", "Failure")
    
    db.refresh(job)
    assert job.attempt_count == 3

def test_queue_position_by_priority(setup_db):
    db = setup_db
    
    # Create jobs with different priorities but identical timestamps
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    
    jobs = [
        Job(priority="P3", status="ASSIGNED", created_at=base_time, customer_name="N", location="L", issue_description="I", service_type="T", contact_number="1", preferred_service_date=base_time.date()),
        Job(priority="P1", status="ASSIGNED", created_at=base_time, customer_name="N", location="L", issue_description="I", service_type="T", contact_number="1", preferred_service_date=base_time.date()),
        Job(priority="P2", status="ASSIGNED", created_at=base_time, customer_name="N", location="L", issue_description="I", service_type="T", contact_number="1", preferred_service_date=base_time.date())
    ]
    db.add_all(jobs)
    db.commit()
    
    for job in jobs:
        ReDispatchQueueService.enqueue_failed_job(db, fake_redis, job, "tenant-1", "Reason")
        
    queue_key = f"dispatch:queue:tenant-1"
    
    # zrevrange gets highest score first
    queued_ids = fake_redis.zrevrange(queue_key, 0, -1)
    
    db.refresh(jobs[0]) 
    db.refresh(jobs[1]) 
    db.refresh(jobs[2]) 
    
    assert str(jobs[1].id) == queued_ids[0]

def test_no_trigger_for_accepted(setup_db):
    db = setup_db
    tech = Technician(
        tech_id="tech-123", technician_name="John", technician_status="EN_ROUTE",
        technician_skill="Plumbing", technician_location="0,0"
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="P4", service_type="Plumbing", contact_number="123",
        preferred_service_date=datetime.now().date(), status="EN_ROUTE",
        assigned_technician_id=tech.technician_id
    )
    db.add(job)
    db.commit()
    
    trigger = ReDispatchTriggerService.detect_trigger(job, tech, timer_exists=False, timer_ttl=0)
    assert trigger is None
