import pytest
from datetime import datetime, date
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
from app.models import Job, Technician
from app.services.job_status_machine import (
    JobStatus,
    InvalidTransitionError,
    PermissionDeniedError,
    ReasonRequiredError,
    TransitionValidator
)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    fake_redis.flushall()
    # Mock transition tasks to run synchronously or prevent DB locks
    with patch("app.tasks.process_job_status_transition_task.delay") as mock_delay:
        db = TestingSessionLocal()
        yield db
        db.close()

def create_base_job() -> Job:
    return Job(
        customer_name="John Doe",
        location="123 Main St",
        issue_description="Leak repair",
        priority="P2",
        service_type="Plumbing",
        contact_number="+15555555555",
        preferred_service_date=date.today(),
        status="CREATED"
    )

def test_forbidden_jumps_skip_intermediate_states(setup_db):
    db = setup_db
    job = create_base_job()
    db.add(job)
    db.commit()

    # CREATED -> EN_ROUTE (Skips ASSIGNED)
    with pytest.raises(InvalidTransitionError) as exc_info:
        job.transition("EN_ROUTE", actor_id="dispatcher-1", actor_role="dispatcher")
    assert exc_info.value.error_code == "FORBIDDEN_JUMP"
    assert "Must assign technician before en route" in str(exc_info.value)

    # CREATED -> ON_SITE (Skips ASSIGNED and EN_ROUTE)
    with pytest.raises(InvalidTransitionError) as exc_info:
        job.transition("ON_SITE", actor_id="dispatcher-1", actor_role="dispatcher")
    assert exc_info.value.error_code == "FORBIDDEN_JUMP"
    assert "Must assign and start journey before on site" in str(exc_info.value)

def test_reverse_from_terminal_states(setup_db):
    db = setup_db
    job = create_base_job()
    job.status = "COMPLETED"
    db.add(job)
    db.commit()

    # COMPLETED -> ASSIGNED
    with pytest.raises(InvalidTransitionError) as exc_info:
        job.transition("ASSIGNED", actor_id="dispatcher-1", actor_role="dispatcher")
    assert exc_info.value.error_code == "FORBIDDEN_JUMP"
    assert "Cannot modify completed job" in str(exc_info.value)

def test_duplicate_self_transitions(setup_db):
    db = setup_db
    job = create_base_job()
    job.status = "ASSIGNED"
    db.add(job)
    db.commit()

    # ASSIGNED -> ASSIGNED
    with pytest.raises(InvalidTransitionError) as exc_info:
        job.transition("ASSIGNED", actor_id="dispatcher-1", actor_role="dispatcher")
    assert exc_info.value.error_code == "DUPLICATE_STATUS"
    assert "Job already in ASSIGNED status" in str(exc_info.value)

def test_prerequisite_en_route_requires_technician(setup_db):
    db = setup_db
    job = create_base_job()
    job.status = "ASSIGNED"
    db.add(job)
    db.commit()

    # Try EN_ROUTE without technician assigned
    with pytest.raises(InvalidTransitionError) as exc_info:
        job.transition("EN_ROUTE", actor_id="tech-1", actor_role="technician")
    assert exc_info.value.error_code == "PREREQUISITE_FAILED"
    assert "Technician must be assigned before starting journey" in str(exc_info.value)

    # Assign technician and retry
    job.assigned_technician_id = 99
    db.commit()
    # Now it should pass validation but check other transitions
    # Since ASSIGNED -> EN_ROUTE is allowed, let's verify
    job.transition("EN_ROUTE", actor_id="tech-99", actor_role="technician")
    assert job.status == "EN_ROUTE"

def test_prerequisite_on_site_requires_gps_active(setup_db):
    db = setup_db
    job = create_base_job()
    job.status = "EN_ROUTE"
    job.assigned_technician_id = 99
    job.gps_active = False
    db.add(job)
    db.commit()

    # Try ON_SITE without gps_active
    with pytest.raises(InvalidTransitionError) as exc_info:
        job.transition("ON_SITE", actor_id="tech-99", actor_role="technician")
    assert exc_info.value.error_code == "PREREQUISITE_FAILED"
    assert "GPS tracking must be active before arriving on site" in str(exc_info.value)

    # Set gps_active and retry
    job.gps_active = True
    db.commit()
    job.transition("ON_SITE", actor_id="tech-99", actor_role="technician")
    assert job.status == "ON_SITE"

def test_prerequisite_completed_requires_work_report(setup_db):
    db = setup_db
    job = create_base_job()
    job.status = "ON_SITE"
    job.assigned_technician_id = 99
    job.work_report = None
    db.add(job)
    db.commit()

    # Try COMPLETED without work_report
    with pytest.raises(InvalidTransitionError) as exc_info:
        job.transition("COMPLETED", actor_id="tech-99", actor_role="technician")
    assert exc_info.value.error_code == "PREREQUISITE_FAILED"
    assert "Work report must be submitted before completion" in str(exc_info.value)

    # Add work report and retry
    job.work_report = "Fixed water leakage."
    db.commit()
    job.transition("COMPLETED", actor_id="tech-99", actor_role="technician")
    assert job.status == "COMPLETED"

def test_admin_override_bypasses_forbidden_jumps(setup_db):
    db = setup_db
    job = create_base_job()
    db.add(job)
    db.commit()

    # CREATED -> ON_SITE bypasses validation with admin role override
    job.transition("ON_SITE", actor_id="admin-1", actor_role="admin", is_override=True)
    assert job.status == "ON_SITE"

def test_get_valid_transitions_helper(setup_db):
    db = setup_db
    job = create_base_job()
    job.status = "CREATED"
    db.add(job)
    db.commit()

    validator = TransitionValidator()
    transitions = validator.get_valid_transitions(job, "dispatcher")
    
    # Check that ASSIGNED is allowed, but others are rejected with reason
    assigned_t = next(t for t in transitions if t["status"] == "ASSIGNED")
    assert assigned_t["allowed"] is True
    
    en_route_t = next(t for t in transitions if t["status"] == "EN_ROUTE")
    assert en_route_t["allowed"] is False
    assert en_route_t["reason"] == "Must assign technician before en route"
