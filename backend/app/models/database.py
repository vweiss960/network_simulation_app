import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/network_modeler.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_column_if_missing(conn, table: str, column: str, col_type: str, default: str = ""):
    try:
        conn.execute(text(f"SELECT {column} FROM {table} LIMIT 1"))
    except Exception:
        default_clause = f"DEFAULT {default}" if default else ""
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type} {default_clause}"))
        conn.commit()


def init_db():
    os.makedirs("data", exist_ok=True)
    from app.models.user import User  # noqa: F401 — ensure users table is created
    from app.models.srlg import SrlgGroup, SrlgMember  # noqa: F401
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        _add_column_if_missing(conn, "node_instances", "location", "VARCHAR", "''")
        _add_column_if_missing(conn, "node_instances", "latitude", "FLOAT", "")
        _add_column_if_missing(conn, "node_instances", "longitude", "FLOAT", "")
        _add_column_if_missing(conn, "flows", "explicit_link_ids_json", "TEXT", "'[]'")
