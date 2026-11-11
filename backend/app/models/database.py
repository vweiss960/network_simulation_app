import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/plabs.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    # Enforce FK cascades (node delete → its links) and allow concurrent reads
    # while a long lab operation holds a write transaction.
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    if DATABASE_URL.startswith("sqlite:///"):
        db_dir = os.path.dirname(DATABASE_URL[len("sqlite:///"):])
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
    import app.models  # noqa: F401 — registers every table on Base.metadata
    Base.metadata.create_all(bind=engine)
