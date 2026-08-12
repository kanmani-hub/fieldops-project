import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta

from app.main import app
from app.models import Job, SLAEscalation, AuditEvent
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

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    db.query(Job).delete()
    db.query(SLAEscalation).delete()
    db.query(AuditEvent).delete()
    db.commit()
    yield db
    db.close()

@pytest.fixture(autouse=True)
def apply_overrides():
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()

def test_create_queued_job():
    future_date = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    payload = {
        "customer_name": "Test Customer Queued",
        "location": "Test Location",
        "issue_description": "Test Issue",
        "priority": "HIGH",
        "service_type": "HVAC Repair",
        "contact_number": "9876543210",
        "preferred_service_date": "2026-06-15",
        "status": "QUEUED",
        "tenant_id": "tenant-test",
        "sla_deadline": future_date
    }
    
    response = client.post("/jobs/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "QUEUED"
    
    # Verify in DB
    db = TestingSessionLocal()
    job = db.query(Job).filter(Job.id == data["id"]).first()
    assert job is not None
    assert job.status == "QUEUED"
    assert job.tenant_id == "tenant-test"
    assert job.sla_deadline is not None

def test_create_escalated_job():
    payload = {
        "customer_name": "Test Customer Escalated",
        "location": "Test Location",
        "issue_description": "Test Issue",
        "priority": "P1",
        "service_type": "Plumbing Service",
        "contact_number": "9876543210",
        "preferred_service_date": "2026-06-15",
        "status": "ESCALATED",
        "tenant_id": "tenant-1"
    }
    
    response = client.post("/jobs/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "ESCALATED"
    
    # Verify SLAEscalation & AuditEvent records are automatically created
    db = TestingSessionLocal()
    job = db.query(Job).filter(Job.id == data["id"]).first()
    assert job is not None
    
    escalation = db.query(SLAEscalation).filter(SLAEscalation.job_id == job.id).first()
    assert escalation is not None
    assert escalation.status == "ESCALATED"
    assert escalation.manager_responded_at is None
    
    audit = db.query(AuditEvent).filter(AuditEvent.event_type == "SLA_ESCALATION").first()
    assert audit is not None
    assert audit.new_status == "ESCALATED"
    assert audit.tenant_id == "tenant-1"


def test_create_job_with_attempt_count():
    payload = {
        "customer_name": "Test Attempt Count",
        "location": "Test Location",
        "issue_description": "Test Issue",
        "priority": "LOW",
        "service_type": "Electrical Service",
        "contact_number": "9876543210",
        "preferred_service_date": "2026-06-15",
        "status": "QUEUED",
        "tenant_id": "tenant-test",
        "attempt_count": 3
    }
    
    response = client.post("/jobs/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["attempt_count"] == 3
    
    # Verify update preserves/modifies it
    update_payload = payload.copy()
    update_payload["attempt_count"] = 5
    response = client.put(f"/jobs/{data['id']}", json=update_payload)
    assert response.status_code == 200
    data_up = response.json()
    assert data_up["attempt_count"] == 5
