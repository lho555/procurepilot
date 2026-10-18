from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, URL, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import database_url


class Base(DeclarativeBase):
    pass


def build_engine(url: URL | str) -> Engine:
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args=(
            {"check_same_thread": False} if str(url).startswith("sqlite")
            else {"connect_timeout": 5, "read_timeout": 5, "write_timeout": 5}
        ),
    )
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _record) -> None:
            connection.execute("PRAGMA foreign_keys=ON")
    return engine


@lru_cache
def get_engine() -> Engine:
    return build_engine(database_url())


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
