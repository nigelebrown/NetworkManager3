"""Alembic environment. The app runs migrations programmatically with its own connection
(`config.attributes["connection"]`); the CLI path uses SIMS_DATABASE_URL."""
from alembic import context
from sqlalchemy import create_engine

from backend.config import get_settings
from backend.database.models import Base

target_metadata = Base.metadata


def run_migrations() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        connection = create_engine(get_settings().database_url).connect()
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
