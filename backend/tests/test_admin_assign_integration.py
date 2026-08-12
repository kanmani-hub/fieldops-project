import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta
import json
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from app.main import app
from app.models import Job, Technician, AuditEvent, SLAEscalation, AssignmentOverride, OverrideAuditEvent
from app.database import Base, get_db
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from app.redis_client import get_redis_client

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

class MockRedis:
    def __init__(self):
        self.data = {}
        
    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True
        
    def setex(self, key, time, value):
        self.data[key] = value
        return True
        
    def get(self, key):
        return self.data.get(key)
        
    def exists(self, key):
        return key in self.data
        
    def delete(self, key):
        if key in self.data:
            del self.data[key]
            return 1
        return 0

mock_redis = MockRedis()

def override_get_redis():
    return mock_redis

@contextmanager
def mock_job_lock(job_id):
    yield "mock_lock"

@pytest.fixture(autouse=True)
def patch_job_lock():
    with patch("app.routes.jobs.with_job_lock", side_effect=mock_job_lock):
        yield

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    
    db.query(AuditEvent).delete()
    db.query(Job).delete()
    db.query(Technician).delete()
    db.query(SLAEscalation).delete()
    db.query(AssignmentOverride).delete()
    db.query(OverrideAuditEvent).delete()
    db.commit()
    
    mock_redis.data = {}
    
    yield db
    db.close()

@pytest.fixture(autouse=True)
def apply_overrides():
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_client] = override_get_redis
    yield
    app.dependency_overrides.clear()

def test_technicians_metrics_routing(setup_db):
    # Verify that requesting metrics does not match /technicians/{id} routing (FastAPI routing fix validation)
    mock_redis.set("metrics:offline_events:" + datetime.now(timezone.utc).strftime("%Y-%m-%d-%H"), "5")
    
    response = client.get(
        "/technicians/metrics",
        headers={"Authorization": "Bearer admin", "X-Tenant-ID": "tenant-1"}
    )
    
    assert response.status_code == 200
    assert response.json()["offline_events_current_hour"] == 5

def test_jobs_assign_skill_and_workload_validation(setup_db):
    db = setup_db
    
    # 1. Create a tech with skill Plumbing and workload 3/3
    tech = Technician(
        tech_id="tech-xyz",
        technician_name="John Plumber",
        technician_skill="Plumbing",
        technician_location="0,0",
        technician_status="AVAILABLE",
        current_jobs=3,
        max_jobs=3
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    
    # 2. Create a job requiring HVAC
    job = Job(
        customer_name="Alice",
        location="1,1",
        issue_description="AC leak",
        priority="HIGH",
        service_type="HVAC",
        required_skill="HVAC",
        contact_number="1234567890",
        preferred_service_date=datetime.now(timezone.utc).date(),
        status="QUEUED"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # Check 1: Should fail skill verification if skip_skill_check is False
    resp1 = client.post(
        f"/jobs/{job.id}/assign",
        headers={"Authorization": "Bearer admin", "X-Tenant-ID": "tenant-1"},
        json={
            "tech_id": "tech-xyz",
            "justification": "This is a dummy justification with at least 20 chars.",
            "skip_skill_check": False,
            "skip_workload_check": True
        }
    )
    assert resp1.status_code == 400
    assert "missing required skills" in resp1.json()["detail"]
    
    # Check 2: Should fail workload check if skip_workload_check is False
    resp2 = client.post(
        f"/jobs/{job.id}/assign",
        headers={"Authorization": "Bearer admin", "X-Tenant-ID": "tenant-1"},
        json={
            "tech_id": "tech-xyz",
            "justification": "This is a dummy justification with at least 20 chars.",
            "skip_skill_check": True,
            "skip_workload_check": False
        }
    )
    assert resp2.status_code == 400
    assert "maximum workload capacity" in resp2.json()["detail"]

    # Check 3: Should succeed if both checks are bypassed or skipped
    resp3 = client.post(
        f"/jobs/{job.id}/assign",
        headers={"Authorization": "Bearer admin", "X-Tenant-ID": "tenant-1"},
        json={
            "tech_id": "tech-xyz",
            "justification": "This is a dummy justification with at least 20 chars.",
            "skip_skill_check": True,
            "skip_workload_check": True
        }
    )
    assert resp3.status_code == 200
    assert resp3.json()["status"] == "ASSIGNED"
    
    # Verify Redis timer is started
    assert mock_redis.exists(f"job:timer:{job.id}") == True

def test_escalation_force_assign_timer(setup_db):
    db = setup_db
    
    tech = Technician(


        tech_id="tech-abc",
        technician_name="Bob Mechanic",
        technician_skill="Plumbing",
        technician_location="0,0",
        technician_status="AVAILABLE",
        current_jobs=0
    )
    db.add(tech)
    
    job = Job(
        customer_name="Charlie",
        location="1,1",
        issue_description="Leak",
        priority="P1",
        service_type="Plumbing",
        contact_number="123",
        preferred_service_date=datetime.now(timezone.utc).date(),
        status="ESCALATED"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    esc = SLAEscalation(
        job_id=job.id,
        status="ESCALATED",
        manager_notified_at=datetime.now(timezone.utc)
    )
    db.add(esc)
    db.commit()
    
    response = client.post(
        f"/escalations/{job.id}/force-assign",
        headers={"Authorization": "Bearer admin", "X-Tenant-ID": "tenant-1"},
        json={
            "tech_id": "tech-abc",
            "reason": "Expert tech needed immediately"
        }
    )
    
    assert response.status_code == 200
    assert response.json()["message"] == "Job force-assigned successfully"
    
    db.refresh(job)
    db.refresh(esc)
    assert job.status == "ASSIGNED"
    assert job.assigned_technician_id == tech.technician_id
    assert esc.manager_responded_at is not None
    assert esc.action_taken == "Force Assigned to tech-abc"
    
    # Verify Redis timer is started
    assert mock_redis.exists(f"job:timer:{job.id}") == True
