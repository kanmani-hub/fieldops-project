import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta
from app.main import app
from app.models import Job, Technician, GPSPing
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
    db.query(Technician).delete()
    db.query(GPSPing).delete()
    db.commit()
    yield db
    db.close()

@pytest.fixture(autouse=True)
def apply_overrides():
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()

def test_share_job_and_track_flow():
    # 1. Seed tech and job
    db = TestingSessionLocal()
    tech = Technician(
        technician_id=1,
        tech_id="tech-uuid-1",
        technician_name="John Doe",
        technician_skill="HVAC",
        technician_location="13.0827,80.2707",
        technician_status="Busy",
        tenant_id="tenant-1",
        current_jobs=1
    )
    db.add(tech)
    db.commit()

    job = Job(
        id=123,
        customer_name="Alice Smith",
        location="13.0827,80.2707",
        issue_description="AC check",
        priority="HIGH",
        service_type="HVAC Repair",
        contact_number="9876543210",
        preferred_service_date=datetime.now(timezone.utc).date(),
        status="EN_ROUTE",
        assigned_technician_id=1,
        tenant_id="tenant-1"
    )
    db.add(job)
    db.commit()

    # 2. Call share link endpoint (requires auth)
    headers = {
        "Authorization": "Bearer dev-dispatcher-token",
        "X-Tenant-ID": "tenant-1"
    }
    response = client.post("/api/v1/jobs/123/share", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert "expires_at" in data
    assert "share_url" in data
    
    token = data["token"]

    # 3. Call public track endpoint (unauthenticated)
    response_track = client.get(f"/api/v1/track/{token}")
    assert response_track.status_code == 200
    track_data = response_track.json()
    assert track_data["expired"] is False
    assert track_data["job"]["customer_name"] == "Alice Smith"
    assert track_data["job"]["status"] == "EN_ROUTE"
    assert track_data["technician"]["name"] == "John" # First name check

def test_expired_token():
    db = TestingSessionLocal()
    job = Job(
        id=456,
        customer_name="Bob Brown",
        location="13.0827,80.2707",
        issue_description="Pipe Leak",
        priority="HIGH",
        service_type="Plumbing",
        contact_number="9876543210",
        preferred_service_date=datetime.now(timezone.utc).date(),
        status="ASSIGNED",
        tenant_id="tenant-1",
        share_token="expired-token-123",
        share_token_expires_at=datetime.now(timezone.utc) - timedelta(hours=1) # Expired
    )
    db.add(job)
    db.commit()

    # Call track endpoint
    response = client.get("/api/v1/track/expired-token-123")
    assert response.status_code == 200
    data = response.json()
    assert data["expired"] is True
    assert data["message"] == "This tracking link has expired"

def test_nonexistent_token():
    response = client.get("/api/v1/track/invalid-token-xyz")
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
