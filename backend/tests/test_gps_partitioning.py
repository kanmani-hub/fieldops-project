

import os
import pytest
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from app.models import Job, Technician
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
import uuid
from dotenv import load_dotenv
from pathlib import Path
import app.database

# Load environment variables
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

from alembic.config import Config
from alembic import command

# Connect to actual PostgreSQL database
DATABASE_URL = os.getenv("DATABASE_URL")

@pytest.fixture(scope="module")
def pg_engine():
    if not DATABASE_URL or not DATABASE_URL.startswith("postgresql"):
        pytest.skip("PostgreSQL is not configured in DATABASE_URL")
    try:
        engine = create_engine(DATABASE_URL)
        # Force immediate connection check
        with engine.connect() as conn:
            pass
    except Exception as e:
        pytest.skip(f"PostgreSQL server connection failed: {e}")
        return

    # Run alembic migrations to prepare partition tables
    try:
        alembic_cfg = Config("alembic.ini")
        try:
            command.downgrade(alembic_cfg, "base")
        except Exception as e:
            print(f"Failed to downgrade: {e}")

        # Drop all alembic-managed tables first to start clean and prevent create_all schema conflicts
        tables_to_drop = [
            "gps_pings",
            "tenants",
            "tenant_gps_configurations",
            "gps_purge_audit_logs",
            "gps_rejected_ping_logs",
            "eta_history",
            "ai_guardrail_violations",
            "ai_brand_safety_rules",
            "agent_state_records",
            "communication_channel_configurations",
            "communication_configuration_audits",
            "alembic_version"
        ]
        with engine.connect() as conn:
            for table in tables_to_drop:
                conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE;"))
            # Clean up columns added to baseline tables by later migrations
            conn.execute(text("ALTER TABLE notification_templates DROP COLUMN IF EXISTS variables CASCADE;"))
            conn.execute(text("ALTER TABLE notification_templates DROP COLUMN IF EXISTS tenant_id CASCADE;"))
            conn.execute(text("ALTER TABLE notification_templates DROP COLUMN IF EXISTS agent_type CASCADE;"))
            conn.execute(text("ALTER TABLE notification_templates DROP COLUMN IF EXISTS is_deleted CASCADE;"))
            conn.execute(text("ALTER TABLE notification_templates DROP COLUMN IF EXISTS deleted_at CASCADE;"))
            conn.execute(text("ALTER TABLE notification_templates DROP COLUMN IF EXISTS deleted_by CASCADE;"))
            conn.execute(text("ALTER TABLE template_versions DROP COLUMN IF EXISTS name CASCADE;"))
            conn.execute(text("ALTER TABLE template_versions DROP COLUMN IF EXISTS type CASCADE;"))
            conn.execute(text("ALTER TABLE template_versions DROP COLUMN IF EXISTS channel CASCADE;"))
            conn.execute(text("ALTER TABLE template_versions DROP COLUMN IF EXISTS locale CASCADE;"))
            conn.execute(text("ALTER TABLE template_versions DROP COLUMN IF EXISTS format CASCADE;"))
            conn.execute(text("ALTER TABLE template_versions DROP COLUMN IF EXISTS agent_type CASCADE;"))
            conn.execute(text("ALTER TABLE template_versions DROP COLUMN IF EXISTS variables CASCADE;"))
            conn.execute(text("ALTER TABLE template_versions DROP COLUMN IF EXISTS template_is_active CASCADE;"))
            conn.execute(text("ALTER TABLE template_versions DROP COLUMN IF EXISTS restored_from_version CASCADE;"))
            conn.execute(text("ALTER TABLE template_versions DROP COLUMN IF EXISTS is_deleted CASCADE;"))
            conn.execute(text("ALTER TABLE template_versions DROP COLUMN IF EXISTS deleted_at CASCADE;"))
            conn.execute(text("ALTER TABLE template_versions DROP COLUMN IF EXISTS deleted_by CASCADE;"))
            # Clean up constraints/indexes added by migrations
            conn.execute(text("ALTER TABLE template_versions DROP CONSTRAINT IF EXISTS uq_template_version CASCADE;"))
            conn.execute(text("DROP INDEX IF EXISTS idx_active_template_version CASCADE;"))
            conn.execute(text("DROP INDEX IF EXISTS ix_notification_templates_is_deleted CASCADE;"))
            conn.execute(text("DROP INDEX IF EXISTS idx_managed_prompt_lookup CASCADE;"))
            conn.execute(text("DROP INDEX IF EXISTS ix_notification_templates_agent_type CASCADE;"))
            conn.execute(text("DROP INDEX IF EXISTS ix_notification_templates_tenant_id CASCADE;"))
            conn.execute(text("ALTER TABLE notification_templates DROP CONSTRAINT IF EXISTS uq_notification_templates_lookup CASCADE;"))
            conn.commit()

        command.upgrade(alembic_cfg, "head")
        # Ensure June and July 2026 partitions are created for time-independent testing
        with engine.connect() as conn:
            conn.execute(text("SELECT create_gps_ping_partition('2026-06-15 12:00:00+00');"))
            conn.execute(text("SELECT create_gps_ping_partition('2026-07-15 12:00:00+00');"))
            conn.commit()
    except Exception as e:
        print(f"Failed to run alembic migrations: {e}")

    yield engine
    engine.dispose()

@pytest.fixture(scope="function")
def pg_session(pg_engine):
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    
    # Seed dummy technician and job if empty to prevent SELECT LIMIT 1 returning None
    tech_count = session.execute(text("SELECT COUNT(*) FROM technicians;")).scalar()
    if tech_count == 0:
        tech = Technician(
            tech_id='tech-dummy-part',
            technician_name='Part Tech',
            technician_skill='HVAC',
            technician_location='0,0'
        )
        session.add(tech)
        session.commit()
    
    job_count = session.execute(text("SELECT COUNT(*) FROM jobs;")).scalar()
    if job_count == 0:
        job = Job(
            id=99991,
            customer_name='Cust',
            location='0,0',
            issue_description='Desc',
            priority='HIGH',
            service_type='HVAC',
            contact_number='1234567890',
            preferred_service_date=datetime.now(timezone.utc).date(),
            status='active'
        )
        session.add(job)
        session.commit()
    
    yield session
    session.rollback()
    session.close()

def test_partitioned_table_exists(pg_engine):
    inspector = inspect(pg_engine)
    tables = inspector.get_table_names()
    assert "gps_pings" in tables
    # Check that partitions exist
    # Current month (June 2026) and next month (July 2026) partitions should exist
    assert "gps_pings_2026_06" in tables
    assert "gps_pings_2026_07" in tables

def test_default_values_and_uuid_generation(pg_session):
    # Clear the table first to avoid duplicate keys in other tests
    pg_session.execute(text("TRUNCATE TABLE gps_pings CASCADE;"))
    pg_session.commit()
    
    # Get a technician and job
    tech_id = pg_session.execute(text("SELECT tech_id FROM technicians LIMIT 1;")).scalar()
    job_id = pg_session.execute(text("SELECT id FROM jobs LIMIT 1;")).scalar()
    tenant_id = "d7b38d38-2d88-468f-9a1b-3f4119d8544e" # default tenant
    
    if not tech_id or not job_id:
        # Seed dummy ones
        tech = Technician(
            tech_id='tech-dummy-part',
            technician_name='Part Tech',
            technician_skill='HVAC',
            technician_location='0,0'
        )
        pg_session.add(tech)
        job = Job(
            id=99991,
            customer_name='Cust',
            location='0,0',
            issue_description='Desc',
            priority='HIGH',
            service_type='HVAC',
            contact_number='1234567890',
            preferred_service_date=datetime.now(timezone.utc).date(),
            status='active'
        )
        pg_session.add(job)
        pg_session.commit()
        tech_id = 'tech-dummy-part'
        job_id = 99991

    # Insert ping with defaults for id and created_at
    # Note: timestamp is set to June 2026 (partition gps_pings_2026_06)
    pg_session.execute(text("""
        INSERT INTO gps_pings (technician_id, job_id, latitude, longitude, timestamp, tenant_id)
        VALUES (:tech_id, :job_id, 13.0827, 80.2707, '2026-06-15 12:00:00+00', :tenant_id)
    """), {"tech_id": tech_id, "job_id": job_id, "tenant_id": tenant_id})
    pg_session.commit()

    # Query back
    result = pg_session.execute(text("SELECT id, created_at, latitude FROM gps_pings;")).first()
    assert result is not None
    assert isinstance(result[0], uuid.UUID) # id generated as UUID
    assert isinstance(result[1], datetime)  # created_at generated
    assert float(result[2]) == 13.0827

def test_check_constraints_range(pg_session):
    # Retrieve valid tech/job ids
    tech_id = pg_session.execute(text("SELECT tech_id FROM technicians LIMIT 1;")).scalar()
    job_id = pg_session.execute(text("SELECT id FROM jobs LIMIT 1;")).scalar()
    tenant_id = "d7b38d38-2d88-468f-9a1b-3f4119d8544e"

    # Latitude out of range (> 90)
    with pytest.raises(IntegrityError):
        pg_session.execute(text("""
            INSERT INTO gps_pings (technician_id, job_id, latitude, longitude, timestamp, tenant_id)
            VALUES (:tech_id, :job_id, 90.1, 80.2707, '2026-06-15 12:00:00+00', :tenant_id)
        """), {"tech_id": tech_id, "job_id": job_id, "tenant_id": tenant_id})
        pg_session.commit()
    pg_session.rollback()

    # Longitude out of range (< -180)
    with pytest.raises(IntegrityError):
        pg_session.execute(text("""
            INSERT INTO gps_pings (technician_id, job_id, latitude, longitude, timestamp, tenant_id)
            VALUES (:tech_id, :job_id, 13.0827, -180.1, '2026-06-15 12:00:00+00', :tenant_id)
        """), {"tech_id": tech_id, "job_id": job_id, "tenant_id": tenant_id})
        pg_session.commit()
    pg_session.rollback()

def test_foreign_key_constraints(pg_session):
    tenant_id = "d7b38d38-2d88-468f-9a1b-3f4119d8544e"
    
    # Invalid technician_id (not existing in technicians)
    with pytest.raises(IntegrityError):
        pg_session.execute(text("""
            INSERT INTO gps_pings (technician_id, job_id, latitude, longitude, timestamp, tenant_id)
            VALUES ('non-existent-tech-uuid-string', 1, 13.0827, 80.2707, '2026-06-15 12:00:00+00', :tenant_id)
        """), {"tenant_id": tenant_id})
        pg_session.commit()
    pg_session.rollback()

    # Invalid job_id
    tech_id = pg_session.execute(text("SELECT tech_id FROM technicians LIMIT 1;")).scalar()
    with pytest.raises(IntegrityError):
        pg_session.execute(text("""
            INSERT INTO gps_pings (technician_id, job_id, latitude, longitude, timestamp, tenant_id)
            VALUES (:tech_id, 999999, 13.0827, 80.2707, '2026-06-15 12:00:00+00', :tenant_id)
        """), {"tech_id": tech_id, "tenant_id": tenant_id})
        pg_session.commit()
    pg_session.rollback()

def test_partition_pruning_explain(pg_session):
    # Insert data into two separate partitions
    pg_session.execute(text("TRUNCATE TABLE gps_pings CASCADE;"))
    pg_session.commit()
    
    tech_id = pg_session.execute(text("SELECT tech_id FROM technicians LIMIT 1;")).scalar()
    job_id = pg_session.execute(text("SELECT id FROM jobs LIMIT 1;")).scalar()
    tenant_id = "d7b38d38-2d88-468f-9a1b-3f4119d8544e"

    # Insert into June 2026
    pg_session.execute(text("""
        INSERT INTO gps_pings (technician_id, job_id, latitude, longitude, timestamp, tenant_id)
        VALUES (:tech_id, :job_id, 13.0, 80.0, '2026-06-15 12:00:00+00', :tenant_id)
    """), {"tech_id": tech_id, "job_id": job_id, "tenant_id": tenant_id})
    
    # Insert into July 2026
    pg_session.execute(text("""
        INSERT INTO gps_pings (technician_id, job_id, latitude, longitude, timestamp, tenant_id)
        VALUES (:tech_id, :job_id, 14.0, 81.0, '2026-07-15 12:00:00+00', :tenant_id)
    """), {"tech_id": tech_id, "job_id": job_id, "tenant_id": tenant_id})
    pg_session.commit()

    # Query with a date range filtering for June 2026 only (strictly within June UTC and Local to prevent partition overlap)
    # Explain Analyze is used to get the actual plan
    explain_query = """
        EXPLAIN ANALYZE 
        SELECT * FROM gps_pings 
        WHERE timestamp BETWEEN '2026-06-02 00:00:00+00' AND '2026-06-28 00:00:00+00';
    """
    plan = pg_session.execute(text(explain_query)).all()
    plan_text = "\n".join([row[0] for row in plan])
    
    # Verify that the query plan ONLY scanned the June partition (gps_pings_2026_06)
    # and did NOT scan the July partition (gps_pings_2026_07)
    assert "gps_pings_2026_06" in plan_text
    assert "gps_pings_2026_07" not in plan_text

def test_index_usage_explain(pg_session):

    # Get valid technician and job
    tech_id = pg_session.execute(
        text("SELECT tech_id FROM technicians LIMIT 1;")
    ).scalar()

    job_id = pg_session.execute(
        text("SELECT id FROM jobs LIMIT 1;")
    ).scalar()

    tenant_id = "d7b38d38-2d88-468f-9a1b-3f4119d8544e"

    # Seed dummy technician/job if none exist
    if not tech_id:
        tech_id = "tech-val"
        pg_session.execute(text("""
            INSERT INTO technicians
            (tech_id, technician_name, technician_skill, technician_location)
            VALUES
            ('tech-val', 'Test Technician', 'HVAC', '0,0')
            ON CONFLICT DO NOTHING;
        """))

    if not job_id:
        job_id = 99991
        pg_session.execute(text("""
            INSERT INTO jobs
            (id, customer_name, location, issue_description,
             priority, service_type, contact_number,
             preferred_service_date, status)
            VALUES
            (99991,
             'Customer',
             '0,0',
             'Test Job',
             'HIGH',
             'HVAC',
             '1234567890',
             NOW()::DATE,
             'active')
            ON CONFLICT DO NOTHING;
        """))

    pg_session.commit()

    # Insert one GPS ping
    pg_session.execute(text("""
        INSERT INTO gps_pings
        (
            technician_id,
            job_id,
            latitude,
            longitude,
            timestamp,
            tenant_id
        )
        VALUES
        (
            :tech_id,
            :job_id,
            13.0827,
            80.2707,
            '2026-06-15 12:00:00+00',
            :tenant_id
        );
    """), {
        "tech_id": tech_id,
        "job_id": job_id,
        "tenant_id": tenant_id
    })

    pg_session.commit()

    # Verify composite index usage
    explain_query = """
        EXPLAIN
        SELECT *
        FROM gps_pings
        WHERE tenant_id = :tenant_id
          AND technician_id = :tech_id
          AND timestamp BETWEEN
              '2026-06-02 00:00:00+00'
          AND
              '2026-06-28 00:00:00+00';
    """

    pg_session.execute(text("SET enable_seqscan = off;"))

    plan = pg_session.execute(
        text(explain_query),
        {
            "tenant_id": tenant_id,
            "tech_id": tech_id
        }
    ).all()

    plan_text = "\n".join(row[0] for row in plan)

    pg_session.execute(text("SET enable_seqscan = on;"))

    assert (
        "tenant_id_technician_id_timestamp" in plan_text
        or
        "idx_gps_pings_tenant_tech_time" in plan_text
    )

    # Verify job index usage
    explain_query_job = """
        EXPLAIN
        SELECT *
        FROM gps_pings
        WHERE job_id = :job_id
          AND timestamp BETWEEN
              '2026-06-02 00:00:00+00'
          AND
              '2026-06-28 00:00:00+00';
    """

    pg_session.execute(text("SET enable_seqscan = off;"))

    plan_job = pg_session.execute(
        text(explain_query_job),
        {
            "job_id": job_id
        }
    ).all()

    plan_job_text = "\n".join(row[0] for row in plan_job)

    pg_session.execute(text("SET enable_seqscan = on;"))

    assert (
        "job_id_timestamp" in plan_job_text
        or
        "idx_gps_pings_job_time" in plan_job_text
    )
def test_auto_partition_creation(pg_engine, pg_session):
    inspector = inspect(pg_engine)
    if "gps_pings_2026_08" in inspector.get_table_names():
        pg_session.execute(text("DROP TABLE gps_pings_2026_08;"))
        pg_session.commit()

    # Call function for August 2026
    pg_session.execute(text("SELECT create_gps_ping_partition('2026-08-15 12:00:00+00');"))
    pg_session.commit()

    # Re-inspect tables
    inspector = inspect(pg_engine)
    assert "gps_pings_2026_08" in inspector.get_table_names()

def test_downgrade_and_upgrade_reversibility(pg_engine):
    # Run Alembic downgrade
    alembic_cfg = Config("alembic.ini")
    
    # Downgrade to base
    command.downgrade(alembic_cfg, "base")
    inspector = inspect(pg_engine)
    tables = inspector.get_table_names()
    assert "gps_pings" not in tables
    assert "gps_pings_2026_06" not in tables
    assert "tenants" not in tables

    # Re-upgrade to head to restore db state for application
    command.upgrade(alembic_cfg, "head")
    
    # Re-create June/July 2026 partitions
    with pg_engine.connect() as conn:
        conn.execute(text("SELECT create_gps_ping_partition('2026-06-15 12:00:00+00');"))
        conn.execute(text("SELECT create_gps_ping_partition('2026-07-15 12:00:00+00');"))
        conn.commit()

    inspector = inspect(pg_engine)
    tables = inspector.get_table_names()
    assert "gps_pings" in tables
    assert "gps_pings_2026_06" in tables
    assert "tenants" in tables
