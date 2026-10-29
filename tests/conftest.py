from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import get_db
from backend.app.main import create_app
from backend.app.models import Base


@pytest.fixture
def db_session_factory(monkeypatch) -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    from backend.app.services import delivery
    from backend.app.workers import dispatcher

    monkeypatch.setattr(delivery, "SessionLocal", factory)
    monkeypatch.setattr(dispatcher, "SessionLocal", factory)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(db_session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_get_db():
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
