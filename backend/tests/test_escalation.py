import pytest
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from app.models import Job, SLAEscalation, AuditEvent
from app.database import Base, get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.services.sla_escalation_service import SLAEscalationService
from app.worker import check_sla_escalations, check_cto_escalations

# Setup test DB
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_escalation.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    
    # Cleanup
    db.query(AuditEvent).delete()
    db.query(SLAEscalation).delete()
    db.query(Job).delete()
    db.commit()
    
    # We patch SessionLocal inside worker to use our test DB session factory
    with patch("app.worker.SessionLocal", new=TestingSessionLocal), \
         patch("app.worker.get_redis_client", return_value=None):
        yield db
        
    Base.metadata.drop_all(bind=engine)


def test_p1_sla_risk_triggers_escalation(setup_db):
    db = setup_db
    now = datetime.now(timezone.utc)
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P1", service_type="Service", contact_number="123",
        preferred_service_date=now.date(), status="QUEUED",
        created_at=now,
        sla_deadline=now + timedelta(minutes=25)
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    check_sla_escalations()
    
    db.refresh(job)
    assert job.status == "ESCALATED"
    
    escalation = db.query(SLAEscalation).filter(SLAEscalation.job_id == job.id).first()
    assert escalation is not None
    assert escalation.status == "ESCALATED"

def test_p2_sla_risk_triggers_escalation(setup_db):
    db = setup_db
    now = datetime.now(timezone.utc)
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P2", service_type="Service", contact_number="123",
        preferred_service_date=now.date(), status="QUEUED",
        created_at=now,
        sla_deadline=now + timedelta(minutes=29)
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    check_sla_escalations()
    
    db.refresh(job)
    assert job.status == "ESCALATED"

def test_p3_no_escalation(setup_db):
    db = setup_db
    now = datetime.now(timezone.utc)
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P3", service_type="Service", contact_number="123",
        preferred_service_date=now.date(), status="QUEUED",
        created_at=now,
        sla_deadline=now + timedelta(minutes=10)
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    check_sla_escalations()
    
    db.refresh(job)
    assert job.status == "QUEUED"

def test_sla_above_30min_no_escalation(setup_db):
    db = setup_db
    now = datetime.now(timezone.utc)
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P1", service_type="Service", contact_number="123",
        preferred_service_date=now.date(), status="QUEUED",
        created_at=now,
        sla_deadline=now + timedelta(minutes=40)
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    check_sla_escalations()
    
    db.refresh(job)
    assert job.status == "QUEUED"

@patch("app.services.sla_escalation_service.logger")
def test_manager_sms_delivered(mock_logger, setup_db):
    db = setup_db
    now = datetime.now(timezone.utc)
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P1", service_type="Service", contact_number="123",
        preferred_service_date=now.date(), status="QUEUED",
        created_at=now,
        sla_deadline=now + timedelta(minutes=10)
    )
    db.add(job)
    db.commit()
    
    SLAEscalationService.trigger_escalation(db, job)
    
    # Assert SMS simulated message was logged
    mock_logger.info.assert_any_call(f"SLAEscalationService: [SMS] Manager alerted for job {job.id} - SLA at risk!")

@patch("app.services.sla_escalation_service.logger")
def test_manager_email_delivered(mock_logger, setup_db):
    db = setup_db
    now = datetime.now(timezone.utc)
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P1", service_type="Service", contact_number="123",
        preferred_service_date=now.date(), status="QUEUED",
        created_at=now,
        sla_deadline=now + timedelta(minutes=10)
    )
    db.add(job)
    db.commit()
    
    SLAEscalationService.trigger_escalation(db, job)
    
    # Assert EMAIL simulated message was logged
    mock_logger.info.assert_any_call(f"SLAEscalationService: [EMAIL] Manager alerted for job {job.id} - SLA at risk!")

@patch("app.services.sla_escalation_service.logger")
def test_pagerduty_incident_created(mock_logger, setup_db):
    db = setup_db
    now = datetime.now(timezone.utc)
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P1", service_type="Service", contact_number="123",
        preferred_service_date=now.date(), status="QUEUED",
        created_at=now,
        sla_deadline=now + timedelta(minutes=10),
        attempt_count=2
    )
    db.add(job)
    db.commit()
    
    SLAEscalationService.trigger_pagerduty_incident(job)
    
    # Extract the payload from the logger calls
    payload_str = None
    for call in mock_logger.info.call_args_list:
        if "[PAGERDUTY]" in call.args[0]:
            payload_str = call.args[0].split("payload created: ")[1]
            break
            
    assert payload_str is not None, "PagerDuty payload not logged"
    payload = json.loads(payload_str)
    
    assert payload["incident"]["urgency"] == "critical"
    assert payload["incident"]["service"]["id"] == "PAGERDUTY_SERVICE_ID"
    assert payload["incident"]["escalation_policy"]["id"] == "PAGERDUTY_POLICY_ID"

def test_cto_escalation_after_15min(setup_db):
    db = setup_db
    now = datetime.now(timezone.utc)
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P1", service_type="Service", contact_number="123",
        preferred_service_date=now.date(), status="ESCALATED",
        created_at=now
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # Manager notified 16 minutes ago, no response
    escalation = SLAEscalation(
        job_id=job.id,
        manager_notified_at=now - timedelta(minutes=16),
        status="ESCALATED"
    )
    db.add(escalation)
    db.commit()
    db.refresh(escalation)
    
    check_cto_escalations()
    
    db.refresh(escalation)
    assert escalation.status == "ESCALATED_TO_CTO"
    assert escalation.cto_notified_at is not None

def test_audit_log_full_chain(setup_db):
    db = setup_db
    now = datetime.now(timezone.utc)
    
    job = Job(
        customer_name="Customer", location="0,0", issue_description="Issue",
        priority="P1", service_type="Service", contact_number="123",
        preferred_service_date=now.date(), status="QUEUED",
        created_at=now,
        sla_deadline=now + timedelta(minutes=10)
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # 1. Trigger SLA Escalation
    check_sla_escalations()
    
    # Advance time on the escalation to simulate waiting 15 mins
    escalation = db.query(SLAEscalation).filter(SLAEscalation.job_id == job.id).first()
    escalation.manager_notified_at = now - timedelta(minutes=16)
    db.commit()
    
    # 2. Trigger CTO Escalation
    check_cto_escalations()
    
    # Verify Audit Logs
    audits = db.query(AuditEvent).filter(
        AuditEvent.event_type.in_(["SLA_ESCALATION", "CTO_ESCALATION"])
    ).order_by(AuditEvent.created_at.asc()).all()
    
    assert len(audits) == 2
    assert audits[0].event_type == "SLA_ESCALATION"
    assert audits[0].old_status == "QUEUED"
    assert audits[0].new_status == "ESCALATED"
    
    assert audits[1].event_type == "CTO_ESCALATION"
    assert audits[1].old_status == "ESCALATED"
    assert audits[1].new_status == "ESCALATED_TO_CTO"
