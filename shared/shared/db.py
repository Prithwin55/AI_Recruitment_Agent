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


def _scalar_default_literal(column) -> str | None:
    """SQL literal for a column's model-side scalar default (e.g. False -> '0'), or None if the
    column has no simple scalar default (nullable columns, callable defaults like uuid/utcnow)."""
    default = getattr(column, "default", None)
    if default is None or not getattr(default, "is_scalar", False):
        return None
    val = default.arg
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return "'" + val.replace("'", "''") + "'"
    return None


def _run_lightweight_migrations() -> None:
    """Dev-stage schema evolution without a full migration tool: for each table that
    already existed before this process started, add any model columns missing from the
    actual SQLite table. Brand-new tables are skipped — create_all() just created them with
    every current column already. Safe under concurrent startup from both services: a
    losing race on ADD COLUMN raises 'duplicate column', which is caught and ignored.

    New columns that have a scalar model default are added WITH that default so existing rows
    are backfilled (SQLite's bare ADD COLUMN leaves them NULL, which then breaks response models
    that require a real value). Columns added by an earlier, defaultless version are self-healed
    by backfilling their NULLs — cheap and idempotent (a no-op once no NULLs remain).
    """
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            existing_columns = {
                row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table.name}")').fetchall()
            }
            if not existing_columns:
                continue
            for column in table.columns:
                default_literal = _scalar_default_literal(column)
                if column.name in existing_columns:
                    if default_literal is not None:
                        try:
                            conn.exec_driver_sql(
                                f'UPDATE "{table.name}" SET "{column.name}" = {default_literal} '
                                f'WHERE "{column.name}" IS NULL'
                            )
                            conn.commit()
                        except Exception:
                            conn.rollback()
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'
                if default_literal is not None:
                    ddl += f" DEFAULT {default_literal}"
                try:
                    conn.exec_driver_sql(ddl)
                    conn.commit()
                except Exception:
                    conn.rollback()


def _create_analytics_indexes() -> None:
    """Composite covering indexes for the dashboard aggregates. They let SQLite answer the
    stats GROUP BY / per-recruitment count queries with index-only scans instead of walking the
    whole candidates table. IF NOT EXISTS so this is idempotent and applies to existing DBs too."""
    statements = (
        # Global stats: GROUP BY over the whole table.
        'CREATE INDEX IF NOT EXISTS "ix_candidates_stats" '
        'ON "candidates" ("processing_status", "phase1_decision", "phase2_status")',
        # Per-recruitment page counts: narrowed by recruitment_id, then grouped by the same dims.
        'CREATE INDEX IF NOT EXISTS "ix_candidates_recr_stats" '
        'ON "candidates" ("recruitment_id", "processing_status", "phase1_decision", "phase2_status")',
    )
    with engine.connect() as conn:
        for ddl in statements:
            try:
                conn.exec_driver_sql(ddl)
                conn.commit()
            except Exception:
                conn.rollback()


def init_db() -> None:
    from . import models  # noqa: F401  (ensure models are registered on Base)

    Base.metadata.create_all(bind=engine)
    _run_lightweight_migrations()
    _create_analytics_indexes()
