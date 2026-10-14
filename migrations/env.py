from alembic import context
from sqlalchemy import create_engine

from app.core.config import get_settings
from app.storage.models import Base

settings = context.config.attributes.get("settings") or get_settings()
settings.prepare_dirs()
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(
        url=settings.database_url, target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=engine.dialect.name == "sqlite",
            include_object=lambda obj, name, type_, reflected, compare_to: (
                not (reflected and compare_to is None)
            ),
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
