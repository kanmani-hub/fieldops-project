from alembic import command
import json
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
import tempfile
import pytest
from alembic.config import Config

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except Exception:
        pass

@pytest.fixture
def alembic_config(temp_db):
    alembic_cfg = Config("alembic.ini")
    db_url = f"sqlite:///{temp_db}"
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    os.environ["DATABASE_URL"] = db_url
    yield alembic_cfg
    if "DATABASE_URL" in os.environ:
        del os.environ["DATABASE_URL"]

def test_task_5_2_migration(alembic_config, temp_db):
    engine = create_engine(f"sqlite:///{temp_db}")
    
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE notification_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(255) NOT NULL,
                type VARCHAR(50) NOT NULL,
                channel VARCHAR(50) NOT NULL,
                locale VARCHAR(10) NOT NULL,
                format VARCHAR(50) NOT NULL,
                title_template TEXT,
                body_template TEXT NOT NULL,
                variables TEXT,
                version INTEGER DEFAULT 1,
                is_active BOOLEAN DEFAULT 1,
                tenant_id VARCHAR(50) NOT NULL,
                agent_type VARCHAR(50) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE template_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL,
                version_number INTEGER NOT NULL,
                title_template TEXT,
                body_template TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by VARCHAR(255),
                change_summary TEXT,
                is_active BOOLEAN DEFAULT 0,
                FOREIGN KEY(template_id) REFERENCES notification_templates(id)
            )
        """))

    # 1. Upgrade to revision 89cc7a683f0e
    # Since earlier migrations contain Postgres-specific syntax, we create tables manually and stamp.
    command.stamp(alembic_config, "89cc7a683f0e")
    
    # 2. Insert realistic existing rows
    engine = create_engine(f"sqlite:///{temp_db}")
    with Session(engine) as session:
        # a template with no history
        session.execute(text("""
            INSERT INTO notification_templates (id, name, type, channel, locale, format, title_template, body_template, variables, version, is_active, tenant_id, agent_type, created_at)
            VALUES (1, 'T1', 't', 'sms', 'en', 'text', 'title', 'body', '[]', 1, 1, 'tenant-1', 'agent', CURRENT_TIMESTAMP)
        """))
        
        # a template whose current version is greater than 1
        session.execute(text("""
            INSERT INTO notification_templates (id, name, type, channel, locale, format, title_template, body_template, variables, version, is_active, tenant_id, agent_type, created_at)
            VALUES (2, 'T2', 't', 'sms', 'en', 'text', 'title', 'body2', '["var"]', 3, 1, 'tenant-1', 'agent', CURRENT_TIMESTAMP)
        """))
        session.execute(text("""
            INSERT INTO template_versions (template_id, version_number, body_template, created_by, change_summary, is_active)
            VALUES (2, 3, 'body2', 'user', 'v3', 1)
        """))
        session.execute(text("""
            INSERT INTO template_versions (template_id, version_number, body_template, created_by, change_summary, is_active)
            VALUES (2, 1, 'body1', 'user', 'v1', 0)
        """))
        
        # an inactive template
        session.execute(text("""
            INSERT INTO notification_templates (id, name, type, channel, locale, format, title_template, body_template, variables, version, is_active, tenant_id, agent_type, created_at)
            VALUES (3, 'T3', 't', 'sms', 'en', 'text', 'title', 'body', '[]', 1, 0, 'tenant-1', 'agent', CURRENT_TIMESTAMP)
        """))
        session.execute(text("""
            INSERT INTO template_versions (template_id, version_number, body_template, created_by, change_summary, is_active)
            VALUES (3, 1, 'body', 'user', 'v1', 0)
        """))
        
        # variables stored as JSON string
        session.execute(text("""
            INSERT INTO notification_templates (id, name, type, channel, locale, format, title_template, body_template, variables, version, is_active, tenant_id, agent_type, created_at)
            VALUES (4, 'T4', 't', 'sms', 'en', 'text', 'title', 'body', '["customer_name", "eta"]', 1, 1, 'tenant-1', 'agent', CURRENT_TIMESTAMP)
        """))
        
        # duplicate version numbers
        session.execute(text("""
            INSERT INTO notification_templates (id, name, type, channel, locale, format, title_template, body_template, variables, version, is_active, tenant_id, agent_type, created_at)
            VALUES (5, 'T5', 't', 'sms', 'en', 'text', 'title', 'body', '[]', 1, 1, 'tenant-1', 'agent', CURRENT_TIMESTAMP)
        """))
        session.execute(text("""
            INSERT INTO template_versions (template_id, version_number, body_template, created_by, change_summary, is_active)
            VALUES (5, 1, 'body-dup1', 'user', 'v1', 1)
        """))
        session.execute(text("""
            INSERT INTO template_versions (template_id, version_number, body_template, created_by, change_summary, is_active)
            VALUES (5, 1, 'body-dup2', 'user', 'v1', 0)
        """))
        session.execute(text("""
            INSERT INTO template_versions (template_id, version_number, body_template, created_by, change_summary, is_active)
            VALUES (5, 1, 'body-dup3', 'user', 'v1', 0)
        """))
        
        # multiple active rows
        session.execute(text("""
            INSERT INTO notification_templates (id, name, type, channel, locale, format, title_template, body_template, variables, version, is_active, tenant_id, agent_type, created_at)
            VALUES (6, 'T6', 't', 'sms', 'en', 'text', 'title', 'body', '[]', 2, 1, 'tenant-1', 'agent', CURRENT_TIMESTAMP)
        """))
        session.execute(text("""
            INSERT INTO template_versions (template_id, version_number, body_template, created_by, change_summary, is_active)
            VALUES (6, 1, 'body', 'user', 'v1', 1)
        """))
        session.execute(text("""
            INSERT INTO template_versions (template_id, version_number, body_template, created_by, change_summary, is_active)
            VALUES (6, 2, 'body', 'user', 'v2', 1)
        """))
        session.commit()
    
    # 3. Upgrade to 5a33c0bd93b5
    command.upgrade(alembic_config, "5a33c0bd93b5")
    
    # 4. Assertions
    with Session(engine) as session:
        # every template has history
        for i in range(1, 7):
            count = session.execute(text("SELECT COUNT(*) FROM template_versions WHERE template_id = :id"), {"id": i}).scalar()
            assert count > 0, f"Template {i} has no history"
            
        # all history rows are preserved (T5 had 3 rows + T6 had 2 + T1 had 0 (now 1) + T2 had 2 + T3 had 1 + T4 had 0 (now 1) = 10 rows total)
        total_rows = session.execute(text("SELECT COUNT(*) FROM template_versions")).scalar()
        assert total_rows == 10
        
        # version numbers are unique per template
        for i in range(1, 7):
            dups = session.execute(text(
                "SELECT version_number FROM template_versions WHERE template_id = :id GROUP BY version_number HAVING COUNT(*) > 1"
            ), {"id": i}).scalar()
            assert dups is None, f"Template {i} has duplicate versions"
            
        # exactly one active non-deleted version exists per template
        for i in range(1, 7):
            active_count = session.execute(text(
                "SELECT COUNT(*) FROM template_versions WHERE template_id = :id AND is_active = 1 AND is_deleted = 0"
            ), {"id": i}).scalar()
            assert active_count == 1, f"Template {i} does not have exactly 1 active version"
            
        # current template version matches the active history row
        for i in range(1, 7):
            t_version = session.execute(text("SELECT version FROM notification_templates WHERE id = :id"), {"id": i}).scalar()
            v_version = session.execute(text("SELECT version_number FROM template_versions WHERE template_id = :id AND is_active = 1"), {"id": i}).scalar()
            assert t_version == v_version, f"Template {i} version mismatch"
            
        # inactive template snapshot remains inactive
        t3_active = session.execute(text("SELECT template_is_active FROM template_versions WHERE template_id = 3 AND is_active = 1")).scalar()
        assert t3_active == 0
        
        # variables deserialize to a list
        t4_vars = session.execute(text("SELECT variables FROM template_versions WHERE template_id = 4 AND is_active = 1")).scalar()
        import json
        if isinstance(t4_vars, str):
            t4_vars_list = json.loads(t4_vars)
            if isinstance(t4_vars_list, str):
                t4_vars_list = json.loads(t4_vars_list)
        else:
            t4_vars_list = t4_vars
        assert t4_vars_list == ["customer_name", "eta"]
        
        # rollback/source and deletion columns exist
        cols = session.execute(text("PRAGMA table_info(template_versions)")).fetchall()
        col_names = [c[1] for c in cols]
        assert 'restored_from_version' in col_names
        assert 'is_deleted' in col_names
        assert 'deleted_at' in col_names
        
    # 5. Downgrade to 89cc7a683f0e
    command.downgrade(alembic_config, "89cc7a683f0e")
    
    # 6. Assert original columns still exist
    with Session(engine) as session:
        cols = session.execute(text("PRAGMA table_info(template_versions)")).fetchall()
        col_names = [c[1] for c in cols]
        assert 'body_template' in col_names
        assert 'is_active' in col_names
        assert 'is_deleted' not in col_names
        
        # database remains readable
        total = session.execute(text("SELECT COUNT(*) FROM template_versions")).scalar()
        assert total > 0
    engine.dispose()
