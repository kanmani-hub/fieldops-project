import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment variables
from pathlib import Path

# Load environment variables relative to this file
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in environment variables")

engine = create_engine(
    DATABASE_URL,
    connect_args={"connect_timeout": 5}
)

def update_schema():
    # Import Base and models so metadata is registered
    from app.models import Base
    print("Creating all defined tables that do not exist yet...")
    Base.metadata.create_all(bind=engine)

    queries = [
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS current_jobs INTEGER DEFAULT 0;",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS max_jobs INTEGER DEFAULT 5;",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS tech_id VARCHAR(36) UNIQUE;",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50);",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS last_ping TIMESTAMP WITH TIME ZONE;",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS certifications_data JSON;",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS fcm_token VARCHAR(255);",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS device_type VARCHAR(20);",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS phone_number VARCHAR(20);",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS sms_opt_out INTEGER DEFAULT 0;",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS notification_preferences JSON DEFAULT '{\"sms_enabled\": true, \"push_enabled\": true, \"inapp_enabled\": true, \"email_enabled\": false}';",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS required_skill VARCHAR(100);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS assigned_technician_id INTEGER REFERENCES technicians(technician_id);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS sla_deadline TIMESTAMP WITH TIME ZONE;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS attempt_count INTEGER DEFAULT 0;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS previous_priority VARCHAR(10);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS bumped_at TIMESTAMP WITH TIME ZONE;",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS certifications_data JSON;",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS fcm_token VARCHAR(255);",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS device_type VARCHAR(20);",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS phone_number VARCHAR(20);",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS sms_opt_out INTEGER DEFAULT 0;",
        "ALTER TABLE technicians ADD COLUMN IF NOT EXISTS notification_preferences JSON DEFAULT '{\"sms_enabled\": true, \"push_enabled\": true, \"inapp_enabled\": true, \"email_enabled\": false}';",
        "CREATE TABLE IF NOT EXISTS audit_events (id SERIAL PRIMARY KEY, tech_id VARCHAR(36) NOT NULL, tenant_id VARCHAR(50) NOT NULL, event_type VARCHAR(50) NOT NULL, old_status VARCHAR(30), new_status VARCHAR(30) NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
        "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS reason TEXT;",
        "CREATE TABLE IF NOT EXISTS dispatcher_notifications (id SERIAL PRIMARY KEY, tech_id VARCHAR(36) NOT NULL, tenant_id VARCHAR(50) NOT NULL, message TEXT NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
        "CREATE TABLE IF NOT EXISTS redispatch_attempts (id SERIAL PRIMARY KEY, job_id INTEGER NOT NULL, attempt_number INTEGER NOT NULL, technician_id INTEGER, technician_name VARCHAR(100), event_type VARCHAR(30) NOT NULL, reason VARCHAR(255), queue_position INTEGER DEFAULT 1, next_dispatch_eta TIMESTAMP WITH TIME ZONE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
        "CREATE TABLE IF NOT EXISTS assignment_overrides (id SERIAL PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id), actor_name VARCHAR(100) NOT NULL, actor_role VARCHAR(30) NOT NULL, justification TEXT NOT NULL, previous_technician_id INTEGER REFERENCES technicians(technician_id), previous_technician_name VARCHAR(100), new_technician_id INTEGER NOT NULL REFERENCES technicians(technician_id), new_technician_name VARCHAR(100) NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);",
        # NOTE: gps_pings table is created by Alembic migration 9e973eaa3f1c as a partitioned table.
        # Do NOT create it here — it would conflict with the partitioned version.
        "CREATE INDEX IF NOT EXISTS idx_audit_events_tech_id ON audit_events(tech_id);",
        "CREATE INDEX IF NOT EXISTS idx_dispatcher_notifications_tech_id ON dispatcher_notifications(tech_id);",
        "CREATE INDEX IF NOT EXISTS idx_redispatch_attempts_job_id ON redispatch_attempts(job_id);",
        "CREATE INDEX IF NOT EXISTS idx_assignment_overrides_job_id ON assignment_overrides(job_id);",
        
        # New columns for SLA, geofencing, and lifecycle notifications
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS gps_active BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS work_report TEXT;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS customer_id VARCHAR(50);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS customer_email VARCHAR(100);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS geofence_radius DOUBLE PRECISION DEFAULT 100.0;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS site_latitude DOUBLE PRECISION;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS site_longitude DOUBLE PRECISION;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS site_address VARCHAR(255);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMP WITH TIME ZONE;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS en_route_at TIMESTAMP WITH TIME ZONE;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS on_site_at TIMESTAMP WITH TIME ZONE;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP WITH TIME ZONE;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP WITH TIME ZONE;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP WITH TIME ZONE;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS assigned_by VARCHAR(50);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS en_route_by VARCHAR(50);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS on_site_by VARCHAR(50);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS completed_by VARCHAR(50);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cancelled_by VARCHAR(50);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS closed_by VARCHAR(50);",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cancellation_reason TEXT;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS closure_reason TEXT;",
        
        # New columns for transition audit events
        "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS job_id VARCHAR(36);",
        "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS actor_id VARCHAR(50);",
        "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS details JSON;",
        "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS timestamp TIMESTAMP WITH TIME ZONE;",
        "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(36);",

        # New columns for shareable customer tracking links
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS share_token VARCHAR(36) UNIQUE;",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS share_token_expires_at TIMESTAMP WITH TIME ZONE;",

        # Fix notification_templates.is_active column type (INTEGER -> BOOLEAN) and ensure all columns exist
        "ALTER TABLE notification_templates ADD COLUMN IF NOT EXISTS variables JSON DEFAULT '[]';",
        "ALTER TABLE notification_templates ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50) DEFAULT 'tenant-1';",
        "ALTER TABLE notification_templates ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) DEFAULT 'CommsAgent';",
        "ALTER TABLE notification_templates ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE notification_templates ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE;",
        "ALTER TABLE notification_templates ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(50);",
        "ALTER TABLE notification_templates ALTER COLUMN is_active TYPE BOOLEAN USING is_active::BOOLEAN;",
    ]
    
    with engine.connect() as connection:
        for query in queries:
            print(f"Executing: {query}")
            try:
                connection.execute(text(query))
                connection.commit()
            except Exception as e:
                print(f"Error executing query: {e}")
                connection.rollback()
    print("Database schema updated successfully.")



if __name__ == "__main__":
    update_schema()
