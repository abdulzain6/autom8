from dotenv import load_dotenv
import os

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../.env'))

from logging.config import fileConfig
from alembic import context
from sqlalchemy import pool
from sqlalchemy import create_engine, pool
from aci.common.db.sql_models import Base


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def _check_and_get_env_variable(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"Environment variable '{name}' is not set")
    if value == "":
        raise ValueError(f"Environment variable '{name}' is empty string")
    return value


def _get_db_url() -> str:
    # construct db url from env variables
    DB_SCHEME = _check_and_get_env_variable("ALEMBIC_DB_SCHEME")
    DB_USER = _check_and_get_env_variable("ALEMBIC_DB_USER")
    DB_PASSWORD = _check_and_get_env_variable("ALEMBIC_DB_PASSWORD")
    DB_HOST = _check_and_get_env_variable("ALEMBIC_DB_HOST")
    DB_PORT = _check_and_get_env_variable("ALEMBIC_DB_PORT")
    DB_NAME = _check_and_get_env_variable("ALEMBIC_DB_NAME")
    return f"{DB_SCHEME}://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=_get_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # FIX: We bypass engine_from_config and create the engine directly
    # to ensure our connection arguments are respected.
    connectable = create_engine(
        _get_db_url(),
        poolclass=pool.NullPool,
        pool_pre_ping=True,  # Detect and discard stale connections
        connect_args={
            "prepare_threshold": None,  # Disable prepared statements
            "connect_timeout": 60,  # Connection timeout
        },
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
