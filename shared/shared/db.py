from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _ensure_storage_dirs(settings) -> None:
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    settings.resumes_dir.mkdir(parents=True, exist_ok=True)
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    settings.transcripts_dir.mkdir(parents=True, exist_ok=True)


def _make_engine():
    settings = get_settings()
    _ensure_storage_dirs(settings)

    engine = create_engine(
        f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False, "timeout": 5},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope():
    """Short-lived session: open, do DB-only work, commit, close.

    Never hold this open across an `await`/network call (Claude, Deepgram,
    Azure, SMTP, Calendar) — that's what actually causes `database is
    locked` under WAL with two processes writing, not concurrent short
    transactions.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _run_lightweight_migrations() -> None:
    """Dev-stage schema evolution without a full migration tool: for each table that
    already existed before this process started, add any model columns missing from the
    actual SQLite table. Brand-new tables are skipped — create_all() just created them with
    every current column already. Safe under concurrent startup from both services: a
    losing race on ADD COLUMN raises 'duplicate column', which is caught and ignored.
    """
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            existing_columns = {
                row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table.name}")').fetchall()
            }
            if not existing_columns:
                continue
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                try:
                    conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}')
                    conn.commit()
                except Exception:
                    conn.rollback()


def init_db() -> None:
    from . import models  # noqa: F401  (ensure models are registered on Base)

    Base.metadata.create_all(bind=engine)
    _run_lightweight_migrations()
