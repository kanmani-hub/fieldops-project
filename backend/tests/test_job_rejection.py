import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from app.main import app
from app.models import Job, Technician, AuditEvent, DispatcherNotification
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

from fakeredis import FakeRedis

mock_redis = FakeRedis(decode_responses=True)

def override_get_redis():
    return mock_redis


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
    
    # Reset mock redis
    mock_redis.flushall()
    
    yield db
    db.close()


@pytest.fixture(autouse=True)
def apply_overrides():
    app.dependency_overrides[get_db] = override_get_db
    if "override_get_redis" in globals():
        app.dependency_overrides[get_redis_client] = override_get_redis
    yield
    app.dependency_overrides.clear()

def test_reject_succeeds_with_valid_reason(setup_db):
    db = setup_db
    tech = Technician(
        tech_id="tech-123", technician_name="John Doe", technician_skill="Plumbing",
        technician_location="0,0", technician_status="BUSY", current_jobs=1
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="HIGH", service_type="Plumbing", contact_number="1234567890",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        assigned_technician_id=tech.technician_id
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    reason_text = "Customer is way too far away from me"
    response = client.post(
        f"/jobs/{job.id}/reject",
        headers={"Authorization": "Bearer tech-123", "X-Tenant-ID": "tenant-1"},
        json={"reason": reason_text}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "QUEUED"
    assert data["rejection"]["reason"] == reason_text
    assert data["cooldown"]["duration_seconds"] == 120
    assert data["re_dispatch"]["triggered"] is True
    
    # Verify DB state
    db.refresh(job)
    db.refresh(tech)
    assert job.status == "QUEUED"
    assert tech.current_jobs == 0
    assert tech.technician_status == "AVAILABLE"
    
    # Verify Redis cooldown
    assert mock_redis.exists(f"job:cooldown:{job.id}:tech-123")
    
    # Verify Audit Event
    audit = db.query(AuditEvent).filter(AuditEvent.tech_id == "tech-123", AuditEvent.event_type == "JOB_REJECTED").first()
    assert audit is not None
    assert audit.reason == reason_text
    
    # Verify Notification
    notif = db.query(DispatcherNotification).filter(DispatcherNotification.tech_id == "tech-123").first()
    assert reason_text in notif.message

def test_reject_400_reason_too_short(setup_db):
    response = client.post(
        "/jobs/1/reject",
        headers={"Authorization": "Bearer tech-123", "X-Tenant-ID": "tenant-1"},
        json={"reason": "short"}
    )
    assert response.status_code == 400 # Pydantic validation error

def test_reject_404_job_not_found(setup_db):
    response = client.post(
        "/jobs/999/reject",
        headers={"Authorization": "Bearer tech-123", "X-Tenant-ID": "tenant-1"},
        json={"reason": "Customer is way too far away from me"}
    )
    assert response.status_code == 404

def test_reject_403_wrong_technician(setup_db):
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
        f"/jobs/{job.id}/reject",
        headers={"Authorization": "Bearer wrong-tech", "X-Tenant-ID": "tenant-1"},
        json={"reason": "Customer is way too far away from me"}
    )
    assert response.status_code == 403
