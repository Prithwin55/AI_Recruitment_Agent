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
    whole candidates table. IF NOT EXISTS so this is idempotent and applies to existing DBs too.
    Tenant-leading variants keep the now tenant-scoped aggregates index-only."""
    statements = (
        # Global stats (single-tenant / legacy): GROUP BY over the whole table.
        'CREATE INDEX IF NOT EXISTS "ix_candidates_stats" '
        'ON "candidates" ("processing_status", "phase1_decision", "phase2_status")',
        # Per-recruitment page counts: narrowed by recruitment_id, then grouped by the same dims.
        'CREATE INDEX IF NOT EXISTS "ix_candidates_recr_stats" '
        'ON "candidates" ("recruitment_id", "processing_status", "phase1_decision", "phase2_status")',
        # Tenant-scoped stats GROUP BY.
        'CREATE INDEX IF NOT EXISTS "ix_candidates_tenant_stats" '
        'ON "candidates" ("tenant_id", "processing_status", "phase1_decision", "phase2_status")',
        # Tenant-scoped per-recruitment lookups.
        'CREATE INDEX IF NOT EXISTS "ix_candidates_tenant_recr" '
        'ON "candidates" ("tenant_id", "recruitment_id")',
        # Tenant recruitment list (ordered by created_at) + count.
        'CREATE INDEX IF NOT EXISTS "ix_recruitments_tenant" '
        'ON "recruitments" ("tenant_id", "created_at")',
        # Per-tenant usage aggregation over a date window.
        'CREATE INDEX IF NOT EXISTS "ix_usage_events_tenant" '
        'ON "usage_events" ("tenant_id", "created_at")',
        # Per-tenant user lookup (login / listings).
        'CREATE INDEX IF NOT EXISTS "ix_users_tenant" ON "users" ("tenant_id")',
    )
    with engine.connect() as conn:
        for ddl in statements:
            try:
                conn.exec_driver_sql(ddl)
                conn.commit()
            except Exception:
                conn.rollback()


# Fixed id of the tenant that owns all data created before multi-tenancy. Stable so the backfill is
# idempotent and so both services agree on it.
DEFAULT_TENANT_ID = "00000000000000000000000000000001"

# Tables that gained a tenant_id column and need existing rows backfilled to the default tenant.
_TENANT_BACKFILL_TABLES = ("users", "recruitments", "candidates", "interview_sessions", "usage_events")


def _backfill_tenancy() -> None:
    """One-time, idempotent, race-tolerant backfill for the multi-tenancy migration.

    _run_lightweight_migrations() adds the tenant_id column but (a UUID FK has no scalar default)
    leaves existing rows NULL — which would make every tenant-scoped query hide all legacy data.
    Here we create a default tenant, assign every NULL tenant_id to it, promote the existing seeded
    account to that tenant's admin, and swap the old global-unique email index for a composite
    (tenant_id, email) one. Safe to run from both services concurrently on startup."""
    from .config import get_settings

    settings = get_settings()

    def _run(conn, sql: str, params: dict | None = None) -> None:
        try:
            conn.exec_driver_sql(sql, params or {})
            conn.commit()
        except Exception:
            conn.rollback()

    with engine.connect() as conn:
        # If the tenants table doesn't exist yet (create_all hasn't run), nothing to do.
        try:
            tables = {
                r[0] for r in conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        except Exception:
            return
        if "tenants" not in tables or "users" not in tables:
            return

        # 1) Ensure the default tenant exists. NB: this schema stores enums by NAME (SQLAlchemy's
        #    default), e.g. 'ACTIVE'/'TENANT_ADMIN' — raw SQL must match that, not the lower-case value.
        _run(
            conn,
            'INSERT OR IGNORE INTO tenants (id, slug, name, status, created_at) '
            "VALUES (:id, :slug, :name, 'ACTIVE', CURRENT_TIMESTAMP)",
            {"id": DEFAULT_TENANT_ID, "slug": settings.default_tenant_slug, "name": "Default"},
        )

        # 2) Backfill NULL tenant_id -> default tenant on every tenant-scoped table.
        for table in _TENANT_BACKFILL_TABLES:
            if table in tables:
                _run(
                    conn,
                    f'UPDATE "{table}" SET tenant_id = :tid WHERE tenant_id IS NULL',
                    {"tid": DEFAULT_TENANT_ID},
                )

        # 3a) Normalize enum columns to NAMES: the lightweight migrator backfilled the role column
        #     with the scalar-default's lower-case *value* ('recruiter'), and an earlier build wrote
        #     tenant status as a value too — the schema stores NAMES, so self-heal both.
        _run(conn, "UPDATE users SET role = 'RECRUITER' WHERE role IS NULL OR role = 'recruiter'")
        _run(conn, "UPDATE users SET role = 'TENANT_ADMIN' WHERE role = 'tenant_admin'")
        _run(conn, "UPDATE tenants SET status = 'ACTIVE' WHERE status = 'active'")
        _run(conn, "UPDATE tenants SET status = 'SUSPENDED' WHERE status = 'suspended'")

        # 3b) (removed) No longer promote a default tenant-admin — workspace accounts are recruiters
        #     provisioned by the super-admin panel; legacy TENANT_ADMIN rows keep working as-is.

        # 4) Swap the old global-unique email index for the composite (tenant_id, email). The old
        #    single-column unique index blocks the same email across tenants; drop it, add composite.
        try:
            index_rows = conn.exec_driver_sql('PRAGMA index_list("users")').fetchall()
            for row in index_rows:
                idx_name, is_unique = row[1], row[2]
                if not is_unique:
                    continue
                cols = [c[2] for c in conn.exec_driver_sql(f'PRAGMA index_info("{idx_name}")').fetchall()]
                if cols == ["email"]:  # old global-unique email index
                    _run(conn, f'DROP INDEX IF EXISTS "{idx_name}"')
        except Exception:
            conn.rollback()
        _run(
            conn,
            'CREATE UNIQUE INDEX IF NOT EXISTS "uq_users_tenant_email" ON "users" ("tenant_id", "email")',
        )


def init_db() -> None:
    from . import models  # noqa: F401  (ensure models are registered on Base)

    Base.metadata.create_all(bind=engine)
    _run_lightweight_migrations()
    _backfill_tenancy()
    _create_analytics_indexes()
