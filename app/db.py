from __future__ import annotations
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker
from app.config import settings
from app.models import Base


def create_db_engine(url: str | None = None):
    db_url = url or settings.database_url
    if db_url.startswith("sqlite:///"):
        file_path = db_url.removeprefix("sqlite:///")
        if file_path != ":memory:":
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
    opts = {"connect_args": {"check_same_thread": False}} if db_url.startswith("sqlite") else {
        "pool_pre_ping": True, "pool_size": 3, "max_overflow": 2, "pool_recycle": 300
    }
    if db_url.startswith("postgresql+psycopg") and make_url(db_url).port == 6543:
        opts["connect_args"] = {"prepare_threshold": None}  # Supabase transaction pooler
    return create_engine(db_url, **opts)

engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

def init_db():
    Base.metadata.create_all(engine)

def get_session():
    with SessionLocal() as db:
        yield db
