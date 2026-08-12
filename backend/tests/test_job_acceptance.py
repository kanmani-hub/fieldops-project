import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone
import json

from app.main import app
from app.models import Job, Technician, AuditEvent
from app.database import Base, get_db
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

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

from app.redis_client import get_redis_client

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    
    # Cleanup
    db.query(AuditEvent).delete()
    db.query(Job).delete()
    db.query(Technician).delete()
    db.commit()
    
    # Reset mock redis
    mock_redis.data = {}
    
    yield db
    db.close()


@pytest.fixture(autouse=True)
def apply_overrides():
    app.dependency_overrides[get_db] = override_get_db
    if "override_get_redis" in globals():
        app.dependency_overrides[get_redis_client] = override_get_redis
    yield
    app.dependency_overrides.clear()

def test_accept_succeeds_for_valid_assigned_job(setup_db):
    db = setup_db
    
    tech = Technician(
        tech_id="tech-123",
        technician_name="John Doe",
        technician_skill="Plumbing",
        technician_location="0,0",
        technician_status="AVAILABLE",
        current_jobs=0
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    
    job = Job(
        customer_name="Alice",
        location="1,1",
        issue_description="Leak",
        priority="HIGH",
        service_type="Plumbing",
        contact_number="1234567890",
        preferred_service_date=datetime.now().date(),
        status="ASSIGNED",
        assigned_technician_id=tech.technician_id
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # Set timer key in redis
    mock_redis.set(f"job:timer:{job.id}", "1")
    
    response = client.post(
        f"/jobs/{job.id}/accept",
        headers={
            "Authorization": "Bearer tech-123",
            "X-Tenant-ID": "tenant-1"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "EN_ROUTE"
    assert data["previous_status"] == "ASSIGNED"
    assert data["technician"]["tech_id"] == "tech-123"
    assert data["technician"]["status"] == "EN_ROUTE"
    assert data["tracking_enabled"] is True
    
    # Verify DB state
    db.refresh(job)
    db.refresh(tech)
    assert job.status == "EN_ROUTE"
    assert tech.technician_status == "EN_ROUTE"
    assert tech.current_jobs == 1
    
    # Verify timer key deleted
    assert mock_redis.exists(f"job:timer:{job.id}") == False
    
    # Verify audit event
    audit = db.query(AuditEvent).filter(AuditEvent.tech_id == "tech-123").first()
    assert audit is not None
    assert audit.event_type == "JOB_ACCEPTED"

def test_accept_404_job_not_found(setup_db):
    response = client.post(
        "/jobs/999/accept",
        headers={
            "Authorization": "Bearer tech-123",
            "X-Tenant-ID": "tenant-1"
        }
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"

def test_accept_400_not_assigned_status(setup_db):
    db = setup_db
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="HIGH", service_type="Plumbing", contact_number="1234567890",
        preferred_service_date=datetime.now().date(), status="QUEUED"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    response = client.post(
        f"/jobs/{job.id}/accept",
        headers={
            "Authorization": "Bearer tech-123",
            "X-Tenant-ID": "tenant-1"
        }
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Job is not in ASSIGNED status"

def test_accept_403_wrong_technician(setup_db):
    db = setup_db
    tech = Technician(
        tech_id="tech-123", technician_name="John", technician_skill="Plumbing",
        technician_location="0,0", technician_status="AVAILABLE", current_jobs=0
    )
    db.add(tech)
    db.commit()
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="HIGH", service_type="Plumbing", contact_number="1234567890",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        assigned_technician_id=1
    )
    db.add(job)
    db.commit()
    
    response = client.post(
        f"/jobs/{job.id}/accept",
        headers={
            "Authorization": "Bearer wrong-tech",
            "X-Tenant-ID": "tenant-1"
        }
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Technician not assigned to this job"

def test_accept_423_expired_window(setup_db):
    db = setup_db
    tech = Technician(
        tech_id="tech-123", technician_name="John", technician_skill="Plumbing",
        technician_location="0,0", technician_status="AVAILABLE", current_jobs=0
    )
    db.add(tech)
    db.commit()
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="HIGH", service_type="Plumbing", contact_number="1234567890",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        assigned_technician_id=1
    )
    db.add(job)
    db.commit()
    
    # Do NOT set timer key in redis to simulate expiration
    response = client.post(
        f"/jobs/{job.id}/accept",
        headers={
            "Authorization": "Bearer tech-123",
            "X-Tenant-ID": "tenant-1"
        }
    )
    assert response.status_code == 423
    assert response.json()["detail"] == "Acceptance window expired"

def test_accept_409_concurrent_modification(setup_db):
    db = setup_db
    tech = Technician(
        tech_id="tech-123", technician_name="John", technician_skill="Plumbing",
        technician_location="0,0", technician_status="AVAILABLE", current_jobs=0
    )
    db.add(tech)
    db.commit()
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="HIGH", service_type="Plumbing", contact_number="1234567890",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        assigned_technician_id=1
    )
    db.add(job)
    db.commit()
    
    mock_redis.set(f"job:timer:{job.id}", "1")
    
    # Simulate concurrent lock
    mock_redis.set(f"lock:job_accept:{job.id}", "locked", nx=True)
    
    response = client.post(
        f"/jobs/{job.id}/accept",
        headers={
            "Authorization": "Bearer tech-123",
            "X-Tenant-ID": "tenant-1"
        }
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Concurrent modification"
