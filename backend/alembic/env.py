from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool
import os
from pathlib import Path
from dotenv import load_dotenv
from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

import re


GPS_PARTITION_PATTERN = re.compile(
    r"^gps_pings_\d{4}_\d{2}$"
)

IGNORED_TABLES = {
    "redispatch_attempts",
    "gps_pings",   
}

def is_ignored_table(table_name: str | None) -> bool:
    if not table_name:
        return False

    return (
        table_name in IGNORED_TABLES
        or GPS_PARTITION_PATTERN.fullmatch(table_name) is not None
    )


def include_name(
        name: str | None,
        type_: str,
        parent_names: dict,
    ) -> bool:
        """
        Exclude manually managed tables during database reflection.
        
        """

        if type_ == "table" and is_ignored_table(name):
                return False

        return True

def include_object(
    obj,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to,
) -> bool:
    """
    Exclude ignored tables from both database reflection
    and SQLAlchemy model metadata.
    """

    if type_ == "table":
        table_name = name
    else:
        table = getattr(obj, "table", None)
        table_name = getattr(table, "name", None)

    if is_ignored_table(table_name):
        return False

    return True

# Load environment variables relative to env.py
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# override sqlalchemy.url from .env (escaping % for configparser interpolation)
config.set_main_option("sqlalchemy.url", os.getenv("DATABASE_URL").replace("%", "%%"))

from app.models import Base
# Import new multi-tenant models so Alembic discovers them
from app.models.user import User, RefreshToken  # noqa: F401
from app.models.organization import Organization  # noqa: F401
from app.models.enterprise_audit import EnterpriseAuditLog  # noqa: F401
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
        include_object=include_object,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_name=include_name,
            include_object=include_object,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
