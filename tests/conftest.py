import pytest
from sqlalchemy.orm import Session
from app.db import create_db_engine
from app.models import Base
from app.seed import load_demo
from pathlib import Path

@pytest.fixture()
def db(tmp_path: Path):
    engine = create_db_engine("sqlite:///" + str(tmp_path / "test.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        load_demo(session, Path(__file__).parent.parent / "data/demo_catalog.json")
        yield session
    engine.dispose()
