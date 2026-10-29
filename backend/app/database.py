from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import get_settings

settings = get_settings()
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_timeout=settings.dependency_timeout_seconds,
    connect_args={
        "connect_timeout": settings.dependency_timeout_seconds,
        "options": (
            f"-c statement_timeout={settings.dependency_timeout_seconds * 1000} "
            f"-c lock_timeout={settings.dependency_timeout_seconds * 1000}"
        ),
    },
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
