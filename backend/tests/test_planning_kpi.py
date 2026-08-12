import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta

from app.main import app
from app.models import Job, Technician
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
    db.commit()
    
    # Seed mock technician
    tech1 = Technician(
        technician_id=1,
        tech_id="tech-1",
        technician_name="Alice Smith",
        technician_skill="HVAC Repair",
        technician_location="North Zone",
        technician_status="AVAILABLE",
        current_jobs=1,
        max_jobs=5
    )
    tech2 = Technician(
        technician_id=2,
        tech_id="tech-2",
        technician_name="Bob Jones",
        technician_skill="Plumbing Service",
        technician_location="South Zone",
        technician_status="OFFLINE",
        current_jobs=0,
        max_jobs=3
    )
    db.add_all([tech1, tech2])
    db.commit()

    now = datetime.now(timezone.utc)
    jobs = [
        # Job 101: Dispatched (assigned_technician_id IS NOT NULL)
        Job(
            id=101,
            customer_name="John Doe",
            status="in progress",
            priority="CRITICAL",
            service_type="HVAC Repair",
            location="North Zone",
            issue_description="AC not cooling",
            contact_number="9876543201",
            preferred_service_date=now.date(),
            assigned_technician_id=1,
            attempt_count=0,
            created_at=now
        ),
        # Job 102: Pending (unassigned, active status)
        Job(
            id=102,
            customer_name="Jane Smith",
            status="active",
            priority="HIGH",
            service_type="Electrical Service",
            location="South Zone",
            issue_description="Fuse blown",
            contact_number="9876543202",
            preferred_service_date=now.date(),
            assigned_technician_id=None,
            attempt_count=0,
            created_at=now
        ),
        # Job 103: Expired & Pending (unassigned, sla_deadline in past, NOT completed/cancelled)
        Job(
            id=103,
            customer_name="Bob Johnson",
            status="queued",
            priority="MEDIUM",
            service_type="Plumbing Service",
            location="East Zone",
            issue_description="Leak in pipe",
            contact_number="9876543203",
            preferred_service_date=now.date(),
            assigned_technician_id=None,
            sla_deadline=now - timedelta(hours=2),
            attempt_count=0,
            created_at=now
        ),
        # Job 104: Re-dispatched (attempt_count=2 > 1, unassigned)
        Job(
            id=104,
            customer_name="Dave Adams",
            status="queued",
            priority="LOW",
            service_type="Network Support",
            location="West Zone",
            issue_description="WiFi offline",
            contact_number="9876543204",
            preferred_service_date=now.date(),
            assigned_technician_id=None,
            attempt_count=2,
            created_at=now
        ),
        # Job 105: Completed & unassigned - must NOT count as Pending
        Job(
            id=105,
            customer_name="Sara Lee",
            status="completed",
            priority="LOW",
            service_type="HVAC Repair",
            location="Central Zone",
            issue_description="Done",
            contact_number="9876543205",
            preferred_service_date=now.date(),
            assigned_technician_id=None,
            attempt_count=0,
            created_at=now
        ),
        # Job 106: attempt_count=1 - must NOT count as Re-dispatched (threshold is > 1)
        Job(
            id=106,
            customer_name="Tom Clark",
            status="queued",
            priority="HIGH",
            service_type="Electrical Service",
            location="North Zone",
            issue_description="Breaker tripped",
            contact_number="9876543206",
            preferred_service_date=now.date(),
            assigned_technician_id=None,
            attempt_count=1,
            created_at=now
        ),
    ]
    for j in jobs:
        db.add(j)
    db.commit()
    
    yield db
    db.close()

@pytest.fixture(autouse=True)
def apply_overrides():
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()

def test_get_planning_kpi():
    response = client.get("/planning/kpi")
    assert response.status_code == 200
    data = response.json()
    
    # ── Dispatched: Job 101 has assigned_technician_id=1 → Count: 1
    assert data["jobs_dispatched"] == 1
    
    # ── Pending: unassigned AND not completed/cancelled
    # Job 102 (active, unassigned), Job 103 (queued, unassigned), Job 104 (queued, unassigned), Job 106 (queued, unassigned)
    # Job 105 (completed, unassigned) must be EXCLUDED
    assert data["jobs_pending"] == 4
    
    # ── Expired: unassigned AND sla_deadline in past AND not completed/cancelled
    # Only Job 103 has sla_deadline in past and is unassigned
    assert data["jobs_expired"] == 1
    
    # ── Re-dispatched: attempt_count > 1
    # Job 104 (attempt_count=2) → Count: 1
    # Job 106 (attempt_count=1) must NOT be counted
    assert data["jobs_redispatched"] == 1
    
    # ── Technicians
    assert data["technicians"]["total"] == 2
    assert data["technicians"]["available"] == 1  # Alice Smith AVAILABLE
    assert data["technicians"]["busy"] == 0
    assert data["technicians"]["offline"] == 1    # Bob Jones OFFLINE
    
    # utilization_pct: Alice (current=1, max=5) → 1/5 = 20.0% (Bob offline, excluded)
    assert data["technicians"]["utilization_pct"] == 20.0
    
    # ── Shape checks
    assert "trends" in data
    assert "dispatched" in data["trends"]
    assert "pending" in data["trends"]
    assert "expired" in data["trends"]
    assert "redispatched" in data["trends"]
    assert "sparklines" in data
    assert len(data["sparklines"]["dispatched"]) == 7


def test_completed_job_excluded_from_pending():
    """Ensure a completed unassigned job is not counted as pending."""
    response = client.get("/planning/kpi")
    data = response.json()
    # Job 105 is completed+unassigned; it must not be in pending
    # Without fix this would be 5; corrected rule gives 4
    assert data["jobs_pending"] == 4


def test_redispatch_threshold_is_greater_than_one():
    """Ensure attempt_count=1 does NOT count as re-dispatched."""
    response = client.get("/planning/kpi")
    data = response.json()
    # Job 104 (attempt_count=2) → 1 re-dispatched
    # Job 106 (attempt_count=1) must NOT count → still 1
    assert data["jobs_redispatched"] == 1
