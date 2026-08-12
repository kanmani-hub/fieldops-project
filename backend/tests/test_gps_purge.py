import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta
import uuid
import time
from unittest.mock import patch, MagicMock
from fakeredis import FakeRedis

from app.main import app
from app.models import Job, Technician, GPSPing, TenantGPSConfiguration, GPSPurgeAuditLog
from app.database import Base, get_db
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from app.redis_client import get_redis_client
from app.celery_app import celery_app
from app.tasks import execute_daily_gps_purge_sync, execute_job_gps_purge_sync

# Force celery tasks to run synchronously in tests
celery_app.conf.update(task_always_eager=True)

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


fake_redis = FakeRedis(decode_responses=True)


def override_get_redis():
    return fake_redis


client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    monkeypatch.setattr("app.database.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.tasks.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.worker.SessionLocal", TestingSessionLocal)
    
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    
    db.query(GPSPing).delete()
    db.query(Job).delete()
    db.query(Technician).delete()
    db.query(TenantGPSConfiguration).delete()
    db.query(GPSPurgeAuditLog).delete()
    db.commit()
    
    # Reset fake redis
    fake_redis.flushall()
    
    yield db
    db.close()


@pytest.fixture(autouse=True)
def apply_overrides():
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_client] = override_get_redis
    yield
    app.dependency_overrides.clear()


def seed_data(db):
    tech = Technician(
        tech_id="tech-1",
        technician_name="John Doe",
        technician_skill="HVAC",
        technician_location="0,0",
        tenant_id="tenant-1"
    )
    db.add(tech)
    job = Job(
        id=123,
        customer_name="Alice",
        location="1,1",
        issue_description="Problem",
        priority="HIGH",
        service_type="Plumbing",
        contact_number="1234567890",
        preferred_service_date=datetime.now().date(),
        status="active",
        tenant_id="tenant-1"
    )
    db.add(job)
    db.commit()
    return tech, job


def test_daily_purge_retains_and_deletes(setup_db):
    db = setup_db
    seed_data(db)
    
    now = datetime.now(timezone.utc)
    
    # Ping 1: 31 days old (should be purged)
    ping_old = GPSPing(
        id=str(uuid.uuid4()),
        technician_id="tech-1",
        job_id="123",
        latitude=10.0,
        longitude=20.0,
        timestamp=now - timedelta(days=31),
        tenant_id="tenant-1"
    )
    # Ping 2: 29 days old (should be retained)
    ping_new = GPSPing(
        id=str(uuid.uuid4()),
        technician_id="tech-1",
        job_id="123",
        latitude=10.0,
        longitude=20.0,
        timestamp=now - timedelta(days=29),
        tenant_id="tenant-1"
    )
    db.add(ping_old)
    db.add(ping_new)
    db.commit()
    
    # Run daily purge
    execute_daily_gps_purge_sync(db)
    
    # Check retained
    pings = db.query(GPSPing).all()
    assert len(pings) == 1
    assert pings[0].id == ping_new.id
    
    # Check audit log
    audit_logs = db.query(GPSPurgeAuditLog).filter(GPSPurgeAuditLog.purge_type == "age_based").all()
    assert len(audit_logs) >= 1
    assert audit_logs[0].deleted_count == 1
    assert audit_logs[0].job_id is None
    assert audit_logs[0].tenant_id == "tenant-1"


def test_tenant_retention_override(setup_db):
    db = setup_db
    seed_data(db)
    
    # Configure custom retention config: 20 days override for tenant-1
    config = TenantGPSConfiguration(tenant_id="tenant-1", retention_days=20)
    db.add(config)
    
    now = datetime.now(timezone.utc)
    # 21 days old ping (should be purged under override of 20 days, but would be kept under default 30 days)
    ping_old = GPSPing(
        id=str(uuid.uuid4()),
        technician_id="tech-1",
        job_id="123",
        latitude=10.0,
        longitude=20.0,
        timestamp=now - timedelta(days=21),
        tenant_id="tenant-1"
    )
    db.add(ping_old)
    db.commit()
    
    execute_daily_gps_purge_sync(db)
    
    pings = db.query(GPSPing).all()
    assert len(pings) == 0


def test_event_purge_on_job_status_change(setup_db):
    db = setup_db
    tech, job = seed_data(db)
    
    ping1 = GPSPing(
        id=str(uuid.uuid4()),
        technician_id="tech-1",
        job_id=str(job.id),
        latitude=10.0,
        longitude=20.0,
        timestamp=datetime.now(timezone.utc),
        tenant_id="tenant-1"
    )
    db.add(ping1)
    db.commit()
    
    # Trigger status change to CLOSED
    job.status = "CLOSED"
    db.commit()
    
    # Wait a fraction of a second to let background threads execute
    time.sleep(0.5)
    
    # Check that GPS pings for this job were deleted
    pings = db.query(GPSPing).filter(GPSPing.job_id == str(job.id)).all()
    assert len(pings) == 0
    
    # Check that event-based purge audit log exists
    audit = db.query(GPSPurgeAuditLog).filter(GPSPurgeAuditLog.purge_type == "event_based", GPSPurgeAuditLog.job_id == str(job.id)).first()
    assert audit is not None
    assert audit.deleted_count == 1


def test_event_purge_on_job_cancelled(setup_db):
    db = setup_db
    tech, job = seed_data(db)
    
    ping1 = GPSPing(
        id=str(uuid.uuid4()),
        technician_id="tech-1",
        job_id=str(job.id),
        latitude=10.0,
        longitude=20.0,
        timestamp=datetime.now(timezone.utc),
        tenant_id="tenant-1"
    )
    db.add(ping1)
    db.commit()
    
    # Trigger status change to CANCELLED
    job.status = "CANCELLED"
    db.commit()
    time.sleep(0.5)
    
    pings = db.query(GPSPing).filter(GPSPing.job_id == str(job.id)).all()
    assert len(pings) == 0


def test_event_purge_does_not_trigger_on_other_status(setup_db):
    db = setup_db
    tech, job = seed_data(db)
    
    ping1 = GPSPing(
        id=str(uuid.uuid4()),
        technician_id="tech-1",
        job_id=str(job.id),
        latitude=10.0,
        longitude=20.0,
        timestamp=datetime.now(timezone.utc),
        tenant_id="tenant-1"
    )
    db.add(ping1)
    db.commit()
    
    # Trigger status change to IN_PROGRESS
    job.status = "IN_PROGRESS"
    db.commit()
    time.sleep(0.1)
    
    pings = db.query(GPSPing).filter(GPSPing.job_id == str(job.id)).all()
    assert len(pings) == 1  # Retained!


def test_admin_purge_endpoint(setup_db):
    db = setup_db
    tech, job = seed_data(db)
    
    ping1 = GPSPing(
        id=str(uuid.uuid4()),
        technician_id="tech-1",
        job_id=str(job.id),
        latitude=10.0,
        longitude=20.0,
        timestamp=datetime.now(timezone.utc),
        tenant_id="tenant-1"
    )
    db.add(ping1)
    db.commit()
    
    # Test Dry Run
    response = client.post(
        f"/api/v1/admin/gps/purge/{job.id}",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer admin-token"},
        json={"dry_run": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dry_run_preview"
    assert data["deleted_count"] == 1
    
    # Verify not deleted yet
    assert db.query(GPSPing).count() == 1
    
    # Test Actual Purge
    response = client.post(
        f"/api/v1/admin/gps/purge/{job.id}",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer admin-token"},
        json={"dry_run": False}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "purged"
    assert data["deleted_count"] == 1
    
    # Verify deleted
    assert db.query(GPSPing).count() == 0


def test_admin_endpoints_permission(setup_db):
    response = client.post(
        "/api/v1/admin/gps/purge/123",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer tech-token"},
        json={"dry_run": True}
    )
    assert response.status_code == 403


def test_stats_and_config_endpoints(setup_db):
    db = setup_db
    
    # Configure retention period
    response = client.post(
        "/api/v1/admin/gps/config",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer admin-token"},
        json={"retention_days": 45}
    )
    assert response.status_code == 200
    assert response.json()["retention_days"] == 45
    
    # Check DB update
    config = db.query(TenantGPSConfiguration).filter(TenantGPSConfiguration.tenant_id == "tenant-1").first()
    assert config is not None
    assert config.retention_days == 45
    
    # Check invalid config days (validation test ge=1, le=90)
    response_invalid = client.post(
        "/api/v1/admin/gps/config",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer admin-token"},
        json={"retention_days": 100}
    )
    assert response_invalid.status_code == 400
    
    # Get stats
    response_stats = client.get(
        "/api/v1/admin/gps/purge-stats",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer admin-token"}
    )
    assert response_stats.status_code == 200
    assert "total_purged_30d" in response_stats.json()
    assert "next_scheduled" in response_stats.json()


def test_purge_zero_matching_records(setup_db):
    db = setup_db
    # Running daily purge when database is empty: should not throw error and return successfully
    total = execute_daily_gps_purge_sync(db)
    assert total == 0


def test_purge_audit_search_endpoint(setup_db):
    db = setup_db
    seed_data(db)
    
    # Seed audit logs manually
    audit1 = GPSPurgeAuditLog(
        id=str(uuid.uuid4()),
        tenant_id="tenant-1",
        job_id="123",
        purge_type="manual",
        deleted_count=10,
        correlation_id="corr-1"
    )
    db.add(audit1)
    db.commit()
    
    response = client.get(
        "/api/v1/admin/gps/purge-audit",
        headers={"X-Tenant-ID": "tenant-1", "Authorization": "Bearer admin-token"},
        params={"job_id": "123"}
    )
    assert response.status_code == 200
    logs = response.json()
    assert len(logs) == 1
    assert logs[0]["job_id"] == "123"
    assert logs[0]["deleted_count"] == 10
