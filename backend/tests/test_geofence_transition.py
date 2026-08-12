import pytest
import json
from datetime import datetime, date, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch, MagicMock

# Setup test DB
SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Patch app.database.SessionLocal globally before importing components
import app.database
app.database.SessionLocal = TestingSessionLocal

import fakeredis
fake_redis = fakeredis.FakeRedis(decode_responses=True)
import app.redis_client
app.redis_client.get_redis_client = lambda: fake_redis

from app.database import Base
from app.models import Job, GPSPing, AuditEvent
from app.services.geofence_monitor import GeofenceMonitor, calculate_haversine_distance
from app.tasks import auto_transition_on_geofence

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    fake_redis.flushall()
    # Mock update_eta_task to prevent API calls in test
    with patch("app.tasks.update_eta_task.delay") as mock_eta:
        yield
    fake_redis.flushall()

def test_distance_calculation():
    # Test distance between two known lat/lng points
    # Point A: New York (40.7128, -74.0060)
    # Point B: Brooklyn (40.6782, -73.9442)
    # Distance is approx 6.55 km (6550 meters)
    dist = calculate_haversine_distance(40.7128, -74.0060, 40.6782, -73.9442)
    assert abs(dist - 6550) < 100 # accurate within 1.5%

def test_geofence_consecutive_pings_inside_triggers_transition(setup_db):
    db = TestingSessionLocal()
    
    # Create job at 12.9716, 77.5946 (Bangalore City Center) with default 100m geofence
    job = Job(
        id=201,
        customer_name="Alice Geofence",
        location="12.9716, 77.5946",
        issue_description="Leak",
        priority="P2",
        service_type="Plumbing",
        contact_number="+15555555555",
        preferred_service_date=date.today(),
        status="EN_ROUTE",
        assigned_technician_id=88,
        site_latitude=12.9716,
        site_longitude=77.5946,
        geofence_radius=100.0,
        gps_active=True,
        tenant_id="tenant-1"
    )
    db.add(job)
    db.commit()
    
    monitor = GeofenceMonitor()
    
    # Send 1st ping inside geofence (very close to site)
    ping1 = GPSPing(
        id="ping-1",
        technician_id="88",
        job_id="201",
        latitude=12.9717, # inside geofence
        longitude=77.5947,
        tenant_id="tenant-1",
        timestamp=datetime.now(timezone.utc)
    )
    monitor.process_ping(db, ping1)
    
    # Counter should be 1
    assert fake_redis.get("geofence:entry:201") == "1"
    
    # Send 2nd ping inside geofence
    ping2 = GPSPing(
        id="ping-2",
        technician_id="88",
        job_id="201",
        latitude=12.97165,
        longitude=77.59465,
        tenant_id="tenant-1",
        timestamp=datetime.now(timezone.utc)
    )
    monitor.process_ping(db, ping2)
    assert fake_redis.get("geofence:entry:201") == "2"
    
    # Send 3rd ping inside geofence -> Should trigger task and set cooldown
    ping3 = GPSPing(
        id="ping-3",
        technician_id="88",
        job_id="201",
        latitude=12.9716,
        longitude=77.5946,
        tenant_id="tenant-1",
        timestamp=datetime.now(timezone.utc)
    )
    
    # Patch the task.delay to execute synchronously or mock it
    with patch("app.tasks.auto_transition_on_geofence.delay") as mock_delay:
        monitor.process_ping(db, ping3)
        mock_delay.assert_called_once()
        
    # Counter key should be deleted and cooldown key created
    assert not fake_redis.exists("geofence:entry:201")
    assert fake_redis.get("geofence:cooldown:201") == "active"
    db.close()

def test_ping_outside_resets_counter(setup_db):
    db = TestingSessionLocal()
    
    job = Job(
        id=201,
        customer_name="Alice Geofence",
        location="12.9716, 77.5946",
        issue_description="Leak",
        priority="P2",
        service_type="Plumbing",
        contact_number="+15555555555",
        preferred_service_date=date.today(),
        status="EN_ROUTE",
        assigned_technician_id=88,
        site_latitude=12.9716,
        site_longitude=77.5946,
        geofence_radius=100.0,
        gps_active=True,
        tenant_id="tenant-1"
    )
    db.add(job)
    db.commit()
    
    monitor = GeofenceMonitor()
    
    # Send 1st ping inside
    ping1 = GPSPing(
        id="ping-1",
        technician_id="88",
        job_id="201",
        latitude=12.9717,
        longitude=77.5947,
        tenant_id="tenant-1",
        timestamp=datetime.now(timezone.utc)
    )
    monitor.process_ping(db, ping1)
    assert fake_redis.get("geofence:entry:201") == "1"
    
    # Send 2nd ping OUTSIDE geofence (far away)
    ping2 = GPSPing(
        id="ping-2",
        technician_id="88",
        job_id="201",
        latitude=13.5000, # far outside
        longitude=78.5000,
        tenant_id="tenant-1",
        timestamp=datetime.now(timezone.utc)
    )
    monitor.process_ping(db, ping2)
    
    # Counter should be reset/deleted
    assert not fake_redis.exists("geofence:entry:201")
    db.close()

def test_celery_task_executes_transition(setup_db):
    db = TestingSessionLocal()
    
    job = Job(
        id=201,
        customer_name="Alice Geofence",
        location="12.9716, 77.5946",
        issue_description="Leak",
        priority="P2",
        service_type="Plumbing",
        contact_number="+15555555555",
        preferred_service_date=date.today(),
        status="EN_ROUTE",
        assigned_technician_id=88,
        site_latitude=12.9716,
        site_longitude=77.5946,
        geofence_radius=100.0,
        gps_active=True,
        tenant_id="tenant-1"
    )
    db.add(job)
    db.commit()
    
    # Run auto transition Celery task synchronously
    with patch("app.tasks.process_job_status_transition_task.delay") as mock_trans:
        auto_transition_on_geofence(201, "ping-3", 15.5)
        
    # Retrieve job status
    db.refresh(job)
    assert job.status == "ON_SITE"
    assert job.on_site_by == "system"
    db.close()
