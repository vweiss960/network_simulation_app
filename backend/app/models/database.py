import os
from sqlalchemy import create_engine, event, inspect, text
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
    add_missing_columns()


def add_missing_columns() -> list[str]:
    """Additive schema migration: create_all() makes new tables but never
    alters existing ones, so add any model column the DB lacks (with its
    scalar default). Destructive changes (drops/renames) still need care."""
    added = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}'
                default = getattr(col.default, "arg", None)
                if isinstance(default, (str, int, float)) and not callable(default):
                    lit = "'" + default.replace("'", "''") + "'" if isinstance(default, str) else str(default)
                    ddl += f" DEFAULT {lit}"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
    return added
