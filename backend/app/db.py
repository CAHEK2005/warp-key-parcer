from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
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
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(bind=engine)
    ensure_schema(engine)


def ensure_schema(engine) -> None:
    inspector = inspect(engine)
    if "hosts" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("hosts")}
    with engine.begin() as connection:
        if "auth_mode" not in columns:
            connection.execute(text("ALTER TABLE hosts ADD COLUMN auth_mode VARCHAR(20) NOT NULL DEFAULT 'key'"))
        if "encrypted_password" not in columns:
            connection.execute(text("ALTER TABLE hosts ADD COLUMN encrypted_password TEXT"))


def session_dependency(session_factory: sessionmaker[Session]):
    def get_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    return get_session
