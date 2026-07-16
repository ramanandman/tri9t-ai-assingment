"""
Database engine and session setup (SQLite via SQLAlchemy).

We use SQLite for the tree/version/selection data as specified by the
assignment's expected tech stack. LLM-generated test cases are stored
separately (see app/services/llm_generator.py / a JSON-file store),
not in this SQL database, because that data is unstructured/variable
shape and doesn't need relational querying the way the tree does.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ct200.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency - yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Call this once at startup."""
    import app.models  # noqa: F401 - ensure models are registered on Base
    Base.metadata.create_all(bind=engine)
