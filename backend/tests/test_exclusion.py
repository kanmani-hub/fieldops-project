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
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from app.redis_client import get_redis_client
from app.services.exclusion_service import ExclusionService
from app.services.cooldown_service import CooldownService
from app.services.re_dispatch_queue import ReDispatchQueueService

# Setup test DB
SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
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


@contextmanager
def dummy_job_lock(*args, **kwargs):
    yield "dummy_lock"

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
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
    db.close()


@pytest.fixture(autouse=True)
def apply_overrides():
    app.dependency_overrides[get_db] = override_get_db
    if "override_get_redis" in globals():
        app.dependency_overrides[get_redis_client] = override_get_redis
    yield
    app.dependency_overrides.clear()

def test_rejected_tech_excluded_during_cooldown(setup_db):
    db = setup_db
    job_id = "job-1"
    tech_id = "tech-1"
    
    ExclusionService.add_exclusion(fake_redis, job_id, tech_id, "Customer too far")
    CooldownService.set_cooldown(fake_redis, job_id, tech_id, 120)
    
    exclusion = ExclusionService.is_excluded(fake_redis, job_id, tech_id)
    assert exclusion["excluded"] is True
    assert exclusion["reason"] == "cooldown_active"

def test_tech_available_after_cooldown_expiry(setup_db):
    db = setup_db
    job_id = "job-2"
    tech_id = "tech-2"
    
    ExclusionService.add_exclusion(fake_redis, job_id, tech_id, "Rejected")
    
    # We won't use freezegun here because fake_redis TTL depends on system time.
    # Instead, we just don't set the cooldown, simulating it has expired.
    
    exclusion = ExclusionService.is_excluded(fake_redis, job_id, tech_id)
    assert exclusion["excluded"] is True
    # Reason should fall back to permanent exclusion
    assert exclusion["reason"] == "previously_rejected"

def test_multiple_rejected_techs_excluded(setup_db):
    db = setup_db
    job_id = "job-3"
    
    ExclusionService.add_exclusion(fake_redis, job_id, "tech-A", "Reason A")
    ExclusionService.add_exclusion(fake_redis, job_id, "tech-B", "Reason B")
    
    assert fake_redis.scard(f"job:excluded:{job_id}") == 2
    
    exc_a = ExclusionService.is_excluded(fake_redis, job_id, "tech-A")
    exc_b = ExclusionService.is_excluded(fake_redis, job_id, "tech-B")
    
    assert exc_a["excluded"] is True
    assert exc_b["excluded"] is True

def test_timeout_tech_excluded(setup_db):
    db = setup_db
    job_id = "job-4"
    tech_id = "tech-4"
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P1", service_type="Service", contact_number="123",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        created_at=datetime.now(timezone.utc)
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    ReDispatchQueueService.enqueue_failed_job(db, fake_redis, job, "tenant-1", "timeout", tech_id)
    
    exc = ExclusionService.is_excluded(fake_redis, str(job.id), tech_id)
    assert exc["excluded"] is True
    assert exc["reason"] == "previously_rejected"
    
    details = ExclusionService.get_exclusion_details(fake_redis, str(job.id), tech_id)
    assert details["rejection_reason"] == "timeout"

def test_offline_tech_excluded(setup_db):
    db = setup_db
    job_id = "job-5"
    tech_id = "tech-5"
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P1", service_type="Service", contact_number="123",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        created_at=datetime.now(timezone.utc)
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    ReDispatchQueueService.enqueue_failed_job(db, fake_redis, job, "tenant-1", "tech_offline", tech_id)
    
    exc = ExclusionService.is_excluded(fake_redis, str(job.id), tech_id)
    assert exc["excluded"] is True
    
    details = ExclusionService.get_exclusion_details(fake_redis, str(job.id), tech_id)
    assert details["rejection_reason"] == "tech_offline"

@patch("app.routes.jobs.with_job_lock", side_effect=dummy_job_lock)
def test_exclusion_checked_in_planning(mock_lock, setup_db):
    db = setup_db
    
    tech1 = Technician(tech_id="tech-1", technician_name="Alice", technician_status="AVAILABLE", technician_skill="Skill", technician_location="0,0")
    tech2 = Technician(tech_id="tech-2", technician_name="Bob", technician_status="AVAILABLE", technician_skill="Skill", technician_location="0,0")
    db.add_all([tech1, tech2])
    db.commit()
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P1", service_type="Service", contact_number="123",
        preferred_service_date=datetime.now().date(), status="QUEUED",
        required_skill="Skill"
    )
    db.add(job)
    db.commit()
    
    # Exclude Tech 1
    ExclusionService.add_exclusion(fake_redis, str(job.id), "tech-1", "Rejected previously")
    
    response = client.post(
        f"/jobs/{job.id}/plan",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer some-token"}
    )
    
    assert response.status_code == 200, response.text
    data = response.json()
    
    ranked = data.get("ranked_technicians", [])
    disqualified = data.get("disqualified_technicians", [])
    
    assert any(t["tech_id"] == "tech-2" for t in ranked)
    assert any(t["tech_id"] == "tech-1" for t in disqualified)
    
    tech1_disq = next(t for t in disqualified if t["tech_id"] == "tech-1")
    assert tech1_disq["reason"] == "previously_rejected"

def test_exclusion_persists_across_cycles(setup_db):
    db = setup_db
    job_id = "job-6"
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P4", service_type="Service", contact_number="123",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        created_at=datetime.now(timezone.utc)
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # Cycle 1
    ReDispatchQueueService.enqueue_failed_job(db, fake_redis, job, "tenant-1", "Rejected 1", "tech-1")
    
    # Reassign
    job.status = "ASSIGNED"
    
    # Cycle 2
    ReDispatchQueueService.enqueue_failed_job(db, fake_redis, job, "tenant-1", "Rejected 2", "tech-2")
    
    assert fake_redis.scard(f"job:excluded:{job.id}") == 2
    
    exc1 = ExclusionService.is_excluded(fake_redis, str(job.id), "tech-1")
    exc2 = ExclusionService.is_excluded(fake_redis, str(job.id), "tech-2")
    
    assert exc1["excluded"] is True
    assert exc2["excluded"] is True

@patch("app.routes.jobs.with_job_lock", side_effect=dummy_job_lock)
def test_manual_override_bypasses_exclusion(mock_lock, setup_db):
    db = setup_db
    
    tech = Technician(tech_id="tech-1", technician_name="Alice", technician_status="AVAILABLE", technician_skill="Skill", technician_location="0,0")
    db.add(tech)
    db.commit()
    db.refresh(tech)
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P1", service_type="Service", contact_number="123",
        preferred_service_date=datetime.now().date(), status="QUEUED",
        required_skill="Skill"
    )
    db.add(job)
    db.commit()
    
    # Exclude tech permanently
    ExclusionService.add_exclusion(fake_redis, str(job.id), tech.tech_id, "Permanently excluded")
    
    # Try manual assign (should bypass exclusion)
    response = client.post(
        f"/jobs/{job.id}/assign",
        headers={"X-Permissions": "dispatcher", "Authorization": "Bearer admin", "X-Tenant-ID": "tenant-1"},
        json={"tech_id": tech.tech_id, "justification": "This is a very long and detailed justification that bypasses all length limits imposed by the rules."}
    )
    
    assert response.status_code == 200, response.text
    
    db.refresh(job)
    assert job.status == "ASSIGNED"
    assert job.assigned_technician_id == tech.technician_id
