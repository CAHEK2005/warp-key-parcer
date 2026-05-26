from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base


def make_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    pool_kwargs = {}
    if database_url == "sqlite:///:memory:":
        pool_kwargs = {"poolclass": StaticPool}
    return create_engine(database_url, connect_args=connect_args, **pool_kwargs)


def make_session_factory(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=make_engine(database_url), autoflush=False, autocommit=False)


def init_db(session_factory: sessionmaker[Session]) -> None:
    Base.metadata.create_all(bind=session_factory.kw["bind"])


def session_dependency(session_factory: sessionmaker[Session]):
    def get_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    return get_session
