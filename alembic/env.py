from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from gym_tracker.config import get_settings
from gym_tracker import models

# this is the Alembic Config object
config = context.config

settings = get_settings()
# Escape % for ConfigParser interpolation
url_for_ini = settings.SQLALCHEMY_DATABASE_URL.replace("%", "%%")
config.set_main_option("sqlalchemy.url", url_for_ini)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Provide your metadata to Alembic for autogenerate
target_metadata = models.Base.metadata

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

