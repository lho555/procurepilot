from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.database import build_engine, get_session
from app.main import app
from app.seed import seed

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(BACKEND / "alembic.ini"))
    command.upgrade(config, "head")
    engine = build_engine(url)
    with Session(engine) as session, session.begin():
        seed(session)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def client(database):
    def session_override():
        with Session(database) as session:
            yield session
    app.dependency_overrides[get_session] = session_override
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_session, None)
