import pytest
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import uuid
import alembic.config
import alembic.command
from unittest.mock import patch

from app.models import Technician, Job, CustomerProfile, CustomerPreferenceAudit, Base
from app.services.ai.FieldOpsAI.repositories.customer_profile_repository import CustomerProfileRepository
from app.services.ai.FieldOpsAI.services.customer_preference_service import (
    CustomerPreferenceService,
    InvalidCustomerIdentifierError,
    CustomerPreferenceConflictError,
    CustomerPreferenceValidationError,
    CustomerPreferencePersistenceError,
)
from app.services.ai.FieldOpsAI.schemas.customer_profile import CustomerPreferenceUpdate, CustomerPreferenceResponse, CustomerPreferenceDecision
from pydantic import ValidationError

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture
def repo(db_session: Session):
    return CustomerProfileRepository(db_session)

@pytest.fixture
def service(repo):
    return CustomerPreferenceService(repo)

# --- 1. Missing profile behavior ---
def test_missing_profile_compatibility(service):
    res = service.get_preferences("t1", "c1")
    assert res.source == "COMPATIBILITY_DEFAULT"
    assert res.revision == 0
    assert res.sms_enabled is True
    assert res.push_enabled is False

def test_missing_profile_no_insertion(repo, service):
    service.get_preferences("t2", "c2")
    assert repo.get_by_customer("t2", "c2") is None

def test_missing_profile_evaluation_no_insertion(repo, service):
    dec = service.evaluate_channel("t3", "c3", "SMS")
    assert dec.allowed is True
    assert repo.get_by_customer("t3", "c3") is None

def test_defaults_contain_no_pii(service):
    res = service.get_preferences("t4", "c4")
    assert not hasattr(res, "customer_name")
    assert not hasattr(res, "email")
    assert not hasattr(res, "phone")

# --- 2. Profile Creation ---
def test_explicit_update_creates_profile(repo, service):
    payload = CustomerPreferenceUpdate(sms_enabled=False)
    res = service.update_preferences("tenant1", "cust1", payload, "actor1", "CUSTOMER")
    
    assert res.source == "PROFILE"
    assert res.revision == 1
    assert res.sms_enabled is False
    assert res.email_enabled is True
    
    audits = repo.list_audits("tenant1", res.profile_id)
    assert len(audits) == 1
    audit = audits[0]
    assert audit.previous_revision == 0
    assert audit.new_revision == 1
    # Check that it includes defaults and explicit values ONLY for supplied fields
    cf = audit.changed_fields
    assert cf["sms_enabled"] == {"old": True, "new": False}
    assert "email_enabled" not in cf

from sqlalchemy.exc import IntegrityError

def test_duplicate_tenant_customer_creation(db_session: Session):
    p1 = CustomerProfile(tenant_id="tx", customer_id="cx", updated_by="a1")
    p2 = CustomerProfile(tenant_id="tx", customer_id="cx", updated_by="a2")
    db_session.add(p1)
    db_session.commit()
    db_session.add(p2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

# --- 3. Profile update ---
def test_real_update_changes_fields(repo, service):
    payload = CustomerPreferenceUpdate(sms_enabled=False)
    res1 = service.update_preferences("t1", "c1", payload, "a1", "CUSTOMER")
    
    payload2 = CustomerPreferenceUpdate(email_enabled=False, preferred_locale="es")
    res2 = service.update_preferences("t1", "c1", payload2, "a2", "ADMIN")
    
    assert res2.revision == 2
    assert res2.sms_enabled is False
    assert res2.email_enabled is False
    assert res2.preferred_locale in ("es-ES", "es")
    
    audits = repo.list_audits("t1", res2.profile_id)
    assert len(audits) == 2

def test_noop_keeps_revision_unchanged(repo, service):
    payload = CustomerPreferenceUpdate(sms_enabled=False)
    res1 = service.update_preferences("t2", "c2", payload, "a1", "CUSTOMER")
    
    res2 = service.update_preferences("t2", "c2", payload, "a2", "CUSTOMER")
    assert res1.revision == res2.revision
    assert res1.updated_at == res2.updated_at
    
    audits = repo.list_audits("t2", res1.profile_id)
    assert len(audits) == 1

def test_sequential_update_behavior(repo, service):
    res1 = service.update_preferences("sq", "sqc", CustomerPreferenceUpdate(sms_enabled=False), "a1", "CUSTOMER")
    res2 = service.update_preferences("sq", "sqc", CustomerPreferenceUpdate(email_enabled=False), "a1", "CUSTOMER")
    res3 = service.update_preferences("sq", "sqc", CustomerPreferenceUpdate(push_enabled=True), "a1", "CUSTOMER")
    
    assert res1.revision == 1
    assert res2.revision == 2
    assert res3.revision == 3
    
    audits = repo.list_audits("sq", res1.profile_id)
    assert len(audits) == 3

# --- 4. Rollbacks ---
def test_audit_failure_rolls_back(repo, service, db_session: Session):
    payload = CustomerPreferenceUpdate(sms_enabled=False)
    res = service.update_preferences("t3", "c3", payload, "a1", "CUSTOMER")
    
    initial_rev = res.revision
    initial_time = res.updated_at
    initial_audits = len(repo.list_audits("t3", res.profile_id))
    
    with patch.object(repo, "add_audit", side_effect=ValueError("simulated error")):
        with pytest.raises(CustomerPreferencePersistenceError):
            service.update_preferences("t3", "c3", CustomerPreferenceUpdate(email_enabled=False), "a1", "CUSTOMER")
            
    # Reload and assert no changes
    db_session.expire_all()
    res_after = service.get_preferences("t3", "c3")
    assert res_after.revision == initial_rev
    assert res_after.updated_at == initial_time
    assert res_after.email_enabled is True
    assert len(repo.list_audits("t3", res.profile_id)) == initial_audits

def test_profile_failure_rolls_back(repo, service, db_session: Session):
    with patch.object(db_session, "flush", side_effect=ValueError("simulated flush error")):
        with pytest.raises(CustomerPreferencePersistenceError):
            service.update_preferences("t4", "c4", CustomerPreferenceUpdate(sms_enabled=False), "a1", "CUSTOMER")
            
    assert service.get_preferences("t4", "c4").source == "COMPATIBILITY_DEFAULT"
    assert len(db_session.query(CustomerPreferenceAudit).all()) == 0

def test_first_write_race():
    s1 = TestingSessionLocal()
    s2 = TestingSessionLocal()
    
    r1 = CustomerProfileRepository(s1)
    r2 = CustomerProfileRepository(s2)
    svc1 = CustomerPreferenceService(r1)
    svc2 = CustomerPreferenceService(r2)
    
    res1 = svc1.update_preferences("race", "r1", CustomerPreferenceUpdate(sms_enabled=False), "a1", "CUSTOMER")
    
    # Simulate a race where svc2 doesn't see the profile yet but tries to insert
    with patch.object(r2, "get_by_customer", return_value=None):
        with pytest.raises(CustomerPreferenceConflictError):
            svc2.update_preferences("race", "r1", CustomerPreferenceUpdate(email_enabled=False), "a2", "CUSTOMER")
        
    s1.close()
    s2.close()

# --- 5. Locale ---
def test_locale_normalization(service):
    payload = CustomerPreferenceUpdate(preferred_locale="EN")
    res = service.update_preferences("loc1", "c1", payload, "a1", "CUSTOMER")
    assert res.preferred_locale == "en"

def test_unsupported_locale_rejected(service):
    payload = CustomerPreferenceUpdate(preferred_locale="xx-yy")
    with pytest.raises(CustomerPreferenceValidationError):
        service.update_preferences("loc2", "c1", payload, "a1", "CUSTOMER")

# --- 6. Channel evaluation ---
def test_channel_evaluation(service):
    payload = CustomerPreferenceUpdate(sms_enabled=False, email_enabled=True, push_enabled=True, portal_enabled=False)
    service.update_preferences("ch1", "c1", payload, "a1", "CUSTOMER")
    
    assert service.evaluate_channel("ch1", "c1", "SMS").allowed is False
    assert service.evaluate_channel("ch1", "c1", "EMAIL").allowed is True
    assert service.evaluate_channel("ch1", "c1", "PUSH").allowed is True
    assert service.evaluate_channel("ch1", "c1", "PORTAL").allowed is False
    assert service.evaluate_channel("ch1", "c1", "IN_APP").allowed is False

def test_unknown_channel_rejected(service):
    with pytest.raises(CustomerPreferenceValidationError):
        service.evaluate_channel("ch1", "c1", "CARRIER_PIGEON")

# --- 7. Tenant isolation ---
def test_tenant_isolation(service, repo):
    payloadA = CustomerPreferenceUpdate(sms_enabled=False)
    resA = service.update_preferences("tA", "shared-cust", payloadA, "a1", "CUSTOMER")
    
    payloadB = CustomerPreferenceUpdate(email_enabled=False)
    resB = service.update_preferences("tB", "shared-cust", payloadB, "a2", "CUSTOMER")
    
    # Check reads
    assert service.get_preferences("tA", "shared-cust").sms_enabled is False
    assert service.get_preferences("tA", "shared-cust").email_enabled is True
    
    assert service.get_preferences("tB", "shared-cust").sms_enabled is True
    assert service.get_preferences("tB", "shared-cust").email_enabled is False
    
    # Check evaluations
    assert service.evaluate_channel("tA", "shared-cust", "SMS").allowed is False
    assert service.evaluate_channel("tB", "shared-cust", "SMS").allowed is True
    
    # Check audits isolation
    auditsA = repo.list_audits("tA", resA.profile_id)
    assert len(auditsA) == 1
    assert repo.list_audits("tB", resA.profile_id) == []
    
    # Incorrect tenant + profile_id returns empty collection
    assert repo.list_audits("invalid", resA.profile_id) == []

# --- 8. Validation ---
def test_validation_rejections(service):
    with pytest.raises(InvalidCustomerIdentifierError):
        service.get_preferences("", "c1")
        
    with pytest.raises(InvalidCustomerIdentifierError):
        service.get_preferences("t1", "x" * 51)
        
    with pytest.raises(InvalidCustomerIdentifierError):
        service.get_preferences("t1", "test\n")

def test_schema_rejections():
    with pytest.raises(ValidationError):
        CustomerPreferenceUpdate() # empty update
        
    with pytest.raises(ValidationError):
        CustomerPreferenceUpdate(sms_enabled=None, email_enabled=None, push_enabled=None, portal_enabled=None, preferred_locale=None)
        
    with pytest.raises(ValidationError):
        CustomerPreferenceUpdate(**{"tenant_id": "test", "sms_enabled": True})
        
    with pytest.raises(ValidationError):
        CustomerPreferenceUpdate(**{"customer_id": "test", "sms_enabled": True})
        
    with pytest.raises(ValidationError):
        CustomerPreferenceUpdate(**{"actor_id": "test", "sms_enabled": True})
        
    with pytest.raises(ValidationError):
        CustomerPreferenceUpdate(**{"revision": 1, "sms_enabled": True})
        
    with pytest.raises(ValidationError):
        CustomerPreferenceUpdate(**{"timestamp": "2024-01-01", "sms_enabled": True})
        
    with pytest.raises(ValidationError):
        CustomerPreferenceUpdate(**{"unknown_field": "x", "sms_enabled": True})
        
    # Test response models are frozen
    resp = CustomerPreferenceResponse(tenant_id="t", customer_id="c", sms_enabled=True, email_enabled=True, push_enabled=True, portal_enabled=True, preferred_locale="en", revision=1, source="PROFILE", profile_id="x", updated_at=None, updated_by=None)
    with pytest.raises(ValidationError):
        resp.revision = 2
        
    dec = CustomerPreferenceDecision(allowed=True, channel="SMS", reason_code="X", source="PROFILE", revision=1)
    with pytest.raises(ValidationError):
        dec.allowed = False

def test_invalid_actor_source(service):
    payload = CustomerPreferenceUpdate(sms_enabled=False)
    with pytest.raises(CustomerPreferenceValidationError):
        service.update_preferences("t1", "c1", payload, "a1", "HACKER")

# --- 9. Audit immutability ---
def test_audit_immutability(repo, service, db_session: Session):
    payload = CustomerPreferenceUpdate(sms_enabled=False)
    res = service.update_preferences("aud1", "c1", payload, "a1", "CUSTOMER")
    
    audit = repo.list_audits("aud1", res.profile_id)[0]
    
    with pytest.raises(ValueError, match="immutable"):
        audit.actor_id = "hacked"
        db_session.commit()
        
    db_session.rollback()

    with pytest.raises(ValueError, match="immutable"):
        db_session.delete(audit)
        db_session.commit()

    db_session.rollback()

# --- 10. Regression and boundaries ---
def test_existing_technician_preferences_unchanged(db_session: Session):
    tech = Technician(
        technician_name="Test Tech",
        technician_skill="Test",
        technician_location="Test",
        notification_preferences={"sms_enabled": True}
    )
    db_session.add(tech)
    db_session.commit()
    assert tech.notification_preferences == {"sms_enabled": True}
    assert tech.sms_opt_out == 0

def test_job_customer_columns_unchanged(db_session: Session):
    job = Job(
        customer_name="Test",
        location="Test",
        issue_description="Test",
        priority="HIGH",
        service_type="Test",
        contact_number="123",
        preferred_service_date=datetime.now().date(),
        customer_id="c1",
        customer_email="test@test.com"
    )
    db_session.add(job)
    db_session.commit()
    assert job.customer_id == "c1"
    assert job.customer_email == "test@test.com"
    assert job.contact_number == "123"

def test_audit_contains_no_pii(repo, service):
    payload = CustomerPreferenceUpdate(sms_enabled=False)
    res = service.update_preferences("pii1", "c1", payload, "a1", "CUSTOMER")
    
    audit = repo.list_audits("pii1", res.profile_id)[0]
    cf = audit.changed_fields
    assert "customer_name" not in cf
    assert "customer_email" not in cf
    assert "email_address" not in cf
    assert "phone" not in cf
    assert "phone_number" not in cf
    assert "contact_number" not in cf
    assert "job_address" not in cf
    assert "message" not in cf
    assert "message_body" not in cf
    assert "JWT" not in cf
    assert "token" not in cf
    
    approved_keys = {"sms_enabled", "email_enabled", "push_enabled", "portal_enabled", "preferred_locale"}
    for key in cf.keys():
        assert key in approved_keys

# --- 11. Migration verification ---
def test_migration_verification():
    import os
    db_file = "test_migration_customer.db"
    if os.path.exists(db_file):
        os.remove(db_file)
        
    db_url = f"sqlite:///{db_file}"
    alembic_cfg = alembic.config.Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    
    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    
    try:
        engine_mig = create_engine(db_url)
        with engine_mig.connect() as conn:
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"))
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('b15cb1f9d24e')"))
            conn.commit()
            
        alembic.command.upgrade(alembic_cfg, "1ad86b0a4f3f")
        
        # Verify current head
        script = alembic.script.ScriptDirectory.from_config(alembic_cfg)
        heads = script.get_heads()
        assert len(heads) == 1
        assert heads[0] == "1ad86b0a4f3f"
        
        from sqlalchemy import inspect
        insp = inspect(engine_mig)
        tables = insp.get_table_names()
        assert "customer_profiles" in tables
        assert "customer_preference_audits" in tables
        
        # SQLite timestamps and constraints
        with engine_mig.connect() as conn:
            conn.execute(text("INSERT INTO customer_profiles (id, tenant_id, customer_id, updated_by) VALUES ('1', 't1', 'c1', 'a1')"))
            conn.commit()
            res = conn.execute(text("SELECT created_at FROM customer_profiles")).scalar()
            assert res is not None
            
        alembic.command.downgrade(alembic_cfg, "b15cb1f9d24e")
        insp2 = inspect(engine_mig)
        tables2 = insp2.get_table_names()
        assert "customer_profiles" not in tables2
        assert "customer_preference_audits" not in tables2
        
    finally:
        engine_mig.dispose()
        if original_url:
            os.environ["DATABASE_URL"] = original_url
        elif "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]
        if os.path.exists(db_file):
            try:
                os.remove(db_file)
            except:
                pass

# --- 12. Edge Case Corrections ---
def test_mixed_null_payloads_rejected():
    with pytest.raises(ValidationError):
        CustomerPreferenceUpdate(sms_enabled=False, email_enabled=None)
    with pytest.raises(ValidationError):
        CustomerPreferenceUpdate(push_enabled=True, preferred_locale=None)
    with pytest.raises(ValidationError):
        CustomerPreferenceUpdate(portal_enabled=None, email_enabled=True)

def test_single_field_update_allowed():
    payload = CustomerPreferenceUpdate(sms_enabled=False)
    assert payload.sms_enabled is False

def test_noop_commit_ordering(repo, service):
    payload = CustomerPreferenceUpdate(sms_enabled=False)
    res_initial = service.update_preferences("noop", "c1", payload, "a1", "CUSTOMER")
    
    call_order = []
    
    original_commit = repo.db.commit
    def mock_commit():
        call_order.append("commit")
        original_commit()
        
    original_response_from_profile = service._response_from_profile
    def mock_response(profile):
        call_order.append("snapshot")
        return original_response_from_profile(profile)
        
    with patch.object(repo.db, 'commit', side_effect=mock_commit):
        with patch.object(service, '_response_from_profile', side_effect=mock_response):
            res_noop = service.update_preferences("noop", "c1", payload, "a2", "CUSTOMER")
            
    assert call_order == ["snapshot", "commit"]
    assert res_noop.revision == res_initial.revision
    assert res_noop.updated_at == res_initial.updated_at
    assert res_noop.updated_by == res_initial.updated_by
    assert len(repo.list_audits("noop", res_initial.profile_id)) == 1

def test_correlation_id_validation(service, db_session: Session):
    # None accepted and stored as None
    payload = CustomerPreferenceUpdate(sms_enabled=False)
    res = service.update_preferences("corr1", "c1", payload, "a1", "CUSTOMER", correlation_id=None)
    audit = db_session.query(CustomerPreferenceAudit).filter_by(customer_profile_id=res.profile_id).first()
    assert audit.correlation_id is None
    
    # valid value stripped and stored
    res2 = service.update_preferences("corr1", "c1", CustomerPreferenceUpdate(email_enabled=False), "a1", "CUSTOMER", correlation_id=" valid-id ")
    audit2 = db_session.query(CustomerPreferenceAudit).filter_by(customer_profile_id=res.profile_id).order_by(CustomerPreferenceAudit.new_revision.desc()).first()
    assert audit2.correlation_id == "valid-id"
    
    # blank rejected
    with pytest.raises(InvalidCustomerIdentifierError, match="cannot be blank"):
        service.update_preferences("corr1", "c1", CustomerPreferenceUpdate(push_enabled=True), "a1", "CUSTOMER", correlation_id="")
        
    # whitespace-only rejected
    with pytest.raises(InvalidCustomerIdentifierError, match="cannot be blank"):
        service.update_preferences("corr1", "c1", CustomerPreferenceUpdate(push_enabled=True), "a1", "CUSTOMER", correlation_id="   ")
        
    # overlong rejected
    with pytest.raises(InvalidCustomerIdentifierError, match="cannot exceed 100 characters"):
        service.update_preferences("corr1", "c1", CustomerPreferenceUpdate(push_enabled=True), "a1", "CUSTOMER", correlation_id="x" * 101)
        
    # control characters rejected
    with pytest.raises(InvalidCustomerIdentifierError, match="Invalid characters"):
        service.update_preferences("corr1", "c1", CustomerPreferenceUpdate(push_enabled=True), "a1", "CUSTOMER", correlation_id="id\nwith\nnewlines")
