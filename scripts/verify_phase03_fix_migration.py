"""Verify the phase-03 fix migration upgrades, downgrades and re-upgrades with
data retention on a real PostgreSQL instance (never the user's database).

Run from ``backend`` with a dedicated database:

    python ../scripts/verify_phase03_fix_migration.py

It creates its own scratch database (``acos_mig_verify_<hex>``) on the server in
``TEST_ADMIN_DATABASE_URL``, runs the chain, and drops only that database.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ADMIN = os.environ.get(
    "TEST_ADMIN_DATABASE_URL",
    "postgresql+psycopg://acos:devpass@127.0.0.1:55432/postgres",
)
BACKEND = Path(__file__).resolve().parent.parent / "backend"
PREVIOUS = "c41f7b93e2a5"
HEAD = "e5b7c9d1a2f4"


def _admin():
    return create_engine(ADMIN, isolation_level="AUTOCOMMIT")


def main() -> int:
    name = f"acos_mig_verify_{uuid.uuid4().hex[:8]}"
    url = ADMIN.rsplit("/", 1)[0] + f"/{name}"
    engine = _admin()
    results: dict[str, object] = {"database": name, "previous": PREVIOUS, "head": HEAD}
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        os.environ["DATABASE_URL"] = url
        config = Config(str(BACKEND / "alembic.ini"))

        command.upgrade(config, PREVIOUS)
        with create_engine(url).connect() as connection:
            # ``domain_events.company_id`` is a real foreign key; a minimal company
            # row is inserted so the seeded events are representable. Nothing on
            # Phase 02 tables is modified by the migration under test.
            connection.execute(
                text(
                    """
                    INSERT INTO companies (
                        id, name, goal, status, template_key, template_version,
                        initialized_at, version
                    ) VALUES (
                        1, 'Verify Co', 'Migration verification', 'ACTIVE',
                        'tech-company-v1', 1, now(), 1
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO domain_events (event_type, company_id, payload)
                    VALUES ('TASK_CREATED', 1, '{"n": 1}'::jsonb),
                           ('TASK_STARTED', 1, '{"n": 2}'::jsonb)
                    """
                )
            )
            connection.commit()
            before = [
                int(row[0])
                for row in connection.execute(text("SELECT id FROM domain_events ORDER BY id"))
            ]
        results["rows_before"] = before

        command.upgrade(config, HEAD)
        with create_engine(url).connect() as connection:
            cursors = [
                (int(row[0]), int(row[1]) if row[1] is not None else None)
                for row in connection.execute(
                    text("SELECT id, published_seq FROM domain_events ORDER BY id")
                )
            ]
        results["id_to_cursor"] = cursors
        assert [row[0] for row in cursors] == before, "rows were not preserved"
        assert all(row[1] is not None for row in cursors), "backfill left NULL cursors"
        seqs = [row[1] for row in cursors]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "cursors not monotonic"

        # A new event after the backfill must take a higher cursor.
        with create_engine(url).connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO domain_events (event_type, company_id, payload)"
                    " VALUES ('TASK_SUCCEEDED', 1, '{}'::jsonb)"
                )
            )
            connection.commit()
        command.downgrade(config, PREVIOUS)
        command.upgrade(config, HEAD)

        with create_engine(url).connect() as connection:
            after = [
                (int(row[0]), int(row[1]) if row[1] is not None else None)
                for row in connection.execute(
                    text("SELECT id, published_seq FROM domain_events ORDER BY id")
                )
            ]
        results["after_roundtrip"] = after
        assert len(after) == len(before) + 1, "downgrade/upgrade lost rows"
        assert all(row[1] is not None for row in after), "re-upgrade failed to republish"
        assert results["id_to_cursor"] == [
            (row[0], row[1]) for row in after[: len(before)]
        ], "historical cursor order changed across the round-trip"

        results["status"] = "PASS"
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        results["status"] = "FAIL"
        results["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 1
    finally:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
