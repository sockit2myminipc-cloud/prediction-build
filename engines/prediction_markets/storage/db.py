from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from engines.prediction_markets.storage.models import Base, StrategyVersion

load_dotenv()

_DEFAULT_DB = Path(__file__).resolve().parents[3] / "data" / "prediction_markets.db"


def _ensure_data_dir() -> None:
    _DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)


def database_url() -> str:
    return os.environ.get("DATABASE_URL") or f"sqlite:///{_DEFAULT_DB}"


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    url = database_url()
    if url.startswith("sqlite"):
        raw = url.replace("sqlite:///", "", 1)
        if not raw.startswith(":memory:"):
            Path(raw).parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    _engine = create_engine(url, echo=False, connect_args=connect_args)
    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()
    return _engine


def get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def get_session() -> Session:
    return get_sessionmaker()()


def init_db() -> None:
    _ensure_data_dir()
    Base.metadata.create_all(get_engine())
    get_sessionmaker()
    _seed_default_strategy()


def _seed_default_strategy() -> None:
    with get_session() as session:
        existing = session.scalars(
            select(StrategyVersion).where(StrategyVersion.strategy_name == "default").limit(1)
        ).first()
        if existing:
            return
        import datetime as dt

        v = StrategyVersion(
            strategy_name="default",
            version=1,
            feature_set_json={},
            rule_params_json={},
            created_at=dt.datetime.now(dt.UTC),
            created_by="human",
            status="champion",
            notes="seed",
        )
        session.add(v)
        session.commit()
