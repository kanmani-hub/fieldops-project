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


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    
    db.query(DispatcherNotification).delete()
    db.query(AuditEvent).delete()
    db.query(Job).delete()
    db.query(Technician).delete()
    db.commit()
    
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

def test_reassign_succeeds_with_eligible_new_tech(setup_db):
    db = setup_db
    
    old_tech = Technician(
        tech_id="tech-1", technician_name="Old Tech", technician_skill="Plumbing",
        technician_location="0,0", technician_status="BUSY", current_jobs=1
    )
    new_tech = Technician(
        tech_id="tech-2", technician_name="New Tech", technician_skill="Plumbing",
        technician_location="0,0", technician_status="AVAILABLE", current_jobs=0
    )
    db.add(old_tech)
    db.add(new_tech)
    db.commit()
    db.refresh(old_tech)
    db.refresh(new_tech)
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="Leak",
        priority="HIGH", service_type="Plumbing", required_skill="Plumbing", contact_number="1234567890",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        assigned_technician_id=old_tech.technician_id
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    response = client.post(
        f"/jobs/{job.id}/reassign",
        headers={"Authorization": "Bearer tech-1", "X-Tenant-ID": "tenant-1"},
        json={"new_tech_id": "tech-2", "reason": "Better equipped"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ASSIGNED"
    assert data["previous_technician"]["tech_id"] == "tech-1"
    assert data["new_technician"]["tech_id"] == "tech-2"
    
    db.refresh(old_tech)
    db.refresh(new_tech)
    db.refresh(job)
    
    assert job.assigned_technician_id == new_tech.technician_id
    assert old_tech.current_jobs == 0
    assert new_tech.current_jobs == 1
    
    assert mock_redis.exists(f"job:timer:{job.id}")
    
    audit = db.query(AuditEvent).first()
    assert audit.event_type == "JOB_REASSIGNED"
    assert audit.reason == "Better equipped"
    assert audit.tech_id == "tech-2"


def test_reassign_400_ineligible_new_tech_missing_skills(setup_db):
    db = setup_db
    old_tech = Technician(
        tech_id="tech-1", technician_name="Old Tech", technician_skill="HVAC",
        technician_location="0,0", technician_status="BUSY", current_jobs=1
    )
    new_tech = Technician(
        tech_id="tech-2", technician_name="New Tech", technician_skill="Plumbing",
        technician_location="0,0", technician_status="AVAILABLE", current_jobs=0
    )
    db.add(old_tech)
    db.add(new_tech)
    db.commit()
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="AC broken",
        priority="HIGH", service_type="HVAC", required_skill="HVAC", contact_number="1234567890",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        assigned_technician_id=1
    )
    db.add(job)
    db.commit()
    
    response = client.post(
        f"/jobs/{job.id}/reassign",
        headers={"Authorization": "Bearer tech-1", "X-Tenant-ID": "tenant-1"},
        json={"new_tech_id": "tech-2", "reason": "Better equipped"}
    )
    
    assert response.status_code == 400
    assert response.json()["detail"] == "New technician missing required skills"


def test_reassign_400_new_tech_max_workload(setup_db):
    db = setup_db
    old_tech = Technician(
        tech_id="tech-1", technician_name="Old Tech", technician_skill="HVAC",
        technician_location="0,0", technician_status="BUSY", current_jobs=1
    )
    new_tech = Technician(
        tech_id="tech-2", technician_name="New Tech", technician_skill="HVAC",
        technician_location="0,0", technician_status="AVAILABLE", current_jobs=3
    )
    db.add(old_tech)
    db.add(new_tech)
    db.commit()
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="AC broken",
        priority="HIGH", service_type="HVAC", required_skill="HVAC", contact_number="1234567890",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        assigned_technician_id=1
    )
    db.add(job)
    db.commit()
    
    response = client.post(
        f"/jobs/{job.id}/reassign",
        headers={"Authorization": "Bearer tech-1", "X-Tenant-ID": "tenant-1"},
        json={"new_tech_id": "tech-2", "reason": "Better equipped"}
    )
    
    assert response.status_code == 400
    assert response.json()["detail"] == "New technician at maximum workload capacity"


def test_reassign_400_offline_new_tech(setup_db):
    db = setup_db
    old_tech = Technician(
        tech_id="tech-1", technician_name="Old Tech", technician_skill="HVAC",
        technician_location="0,0", technician_status="BUSY", current_jobs=1
    )
    new_tech = Technician(
        tech_id="tech-2", technician_name="New Tech", technician_skill="HVAC",
        technician_location="0,0", technician_status="OFFLINE", current_jobs=0
    )
    db.add(old_tech)
    db.add(new_tech)
    db.commit()
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="AC broken",
        priority="HIGH", service_type="HVAC", required_skill="HVAC", contact_number="1234567890",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        assigned_technician_id=1
    )
    db.add(job)
    db.commit()
    
    response = client.post(
        f"/jobs/{job.id}/reassign",
        headers={"Authorization": "Bearer tech-1", "X-Tenant-ID": "tenant-1"},
        json={"new_tech_id": "tech-2", "reason": "Better equipped"}
    )
    
    assert response.status_code == 400
    assert response.json()["detail"] == "New technician is OFFLINE"

def test_reassign_404_job_not_found(setup_db):
    response = client.post(
        "/jobs/999/reassign",
        headers={"Authorization": "Bearer tech-1", "X-Tenant-ID": "tenant-1"},
        json={"new_tech_id": "tech-2", "reason": "Better equipped"}
    )
    assert response.status_code == 404

def test_reassign_403_current_technician_not_assigned(setup_db):
    db = setup_db
    old_tech = Technician(
        tech_id="tech-1", technician_name="Old Tech", technician_skill="HVAC",
        technician_location="0,0", technician_status="BUSY", current_jobs=1
    )
    new_tech = Technician(
        tech_id="tech-2", technician_name="New Tech", technician_skill="HVAC",
        technician_location="0,0", technician_status="AVAILABLE", current_jobs=0
    )
    db.add(old_tech)
    db.add(new_tech)
    db.commit()
    
    job = Job(
        customer_name="Alice", location="1,1", issue_description="AC broken",
        priority="HIGH", service_type="HVAC", required_skill="HVAC", contact_number="1234567890",
        preferred_service_date=datetime.now().date(), status="ASSIGNED",
        assigned_technician_id=1
    )
    db.add(job)
    db.commit()
    
    response = client.post(
        f"/jobs/{job.id}/reassign",
        headers={"Authorization": "Bearer wrong-tech", "X-Tenant-ID": "tenant-1"},
        json={"new_tech_id": "tech-2", "reason": "Better equipped"}
    )
    assert response.status_code == 403
