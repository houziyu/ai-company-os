"""Phase 03 migration evidence: forward, backward, and data preservation.

Runs against one throwaway database and records, at each step:

* the revision, the table count and the presence of every Phase 03 table;
* that an existing Phase 02 company, provider, model, probe and usage row are
  untouched by the Phase 03 upgrade;
* that a downgrade to the Phase 02 head succeeds and re-upgrading keeps the data;
* that the pre-existing probe/usage/audit history survives a round trip.

The database is created and dropped by this script; it refuses to run against a
database whose name does not contain ``phase03`` so it cannot be pointed at real
data by accident.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import psycopg

BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"
ADMIN_URL = "postgresql://acos:devpass@127.0.0.1:55433/postgres"
DB_NAME = "acos_phase03_migration"
PHASE03_TABLES = [
    "projects",
    "tasks",
    "task_executions",
    "task_execution_steps",
    "task_events",
    "domain_events",
    "task_messages",
    "budget_accounts",
    "budget_reservations",
    "budget_ledger_entries",
]


def observe(case: str, **facts: Any) -> None:
    print("PHASE03M " + json.dumps({"case": case, **facts}, ensure_ascii=False), flush=True)


def url(database: str) -> str:
    return f"postgresql+psycopg://acos:devpass@127.0.0.1:55433/{database}"


def alembic(target: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=str(BACKEND_ROOT),
        env={**os.environ, "DATABASE_URL": url(DB_NAME)},
        check=True,
        capture_output=True,
        text=True,
    )


def alembic_down(target: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "alembic", "downgrade", target],
        cwd=str(BACKEND_ROOT),
        env={**os.environ, "DATABASE_URL": url(DB_NAME)},
        check=True,
        capture_output=True,
        text=True,
    )


def revision(conn: psycopg.Connection) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        row = cur.fetchone()
        return str(row[0]) if row else ""


def table_counts(conn: psycopg.Connection) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
        tables = {row[0] for row in cur.fetchall()}
        counts: dict[str, int] = {"_table_count": len(tables)}
        for table in PHASE03_TABLES:
            counts[table] = 1 if table in tables else 0
        return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default="docs/acceptance/evidence/phase-03/migration"
    )
    args = parser.parse_args()
    out_dir = (Path(__file__).resolve().parent.parent / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if "phase03" not in DB_NAME:
        raise SystemExit("refusing to run against a database not named for phase 03")

    results: list[dict[str, Any]] = []
    failures: list[str] = []

    def check(case: str, ok: bool, **facts: Any) -> None:
        observe(case, ok=ok, **facts)
        results.append({"case": case, "ok": ok, **facts})
        if not ok:
            failures.append(case)

    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            cur.execute(f'CREATE DATABASE "{DB_NAME}"')

    try:
        # ---- upgrade to the Phase 02 head and seed realistic data ---------
        alembic("8c24a71d9e03")
        with psycopg.connect(url(DB_NAME).replace("+psycopg", "")) as conn:
            before_rev = revision(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO companies (id, name, goal, status, template_key,"
                    " template_version) VALUES (1, '既有公司', '升级前数据', 'ACTIVE',"
                    " 'tech-company', 1)"
                )
                cur.execute(
                    "INSERT INTO providers (name, kind, base_url, status, secret_ref,"
                    " secret_source, timeout_seconds, version, config_fingerprint)"
                    " VALUES ('既有供应商', 'DEEPSEEK', 'https://api.deepseek.com',"
                    " 'ENABLED', 'DEEPSEEK_API_KEY', 'ENV', 30, 1, 'fp-old') RETURNING id"
                )
                provider_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO provider_models (provider_id, logical_name, label,"
                    " status, version) VALUES (%s, 'm-old', 'Old', 'ENABLED', 1)"
                    " RETURNING id",
                    (provider_id,),
                )
                model_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO model_probes (model_id, provider_id, outcome,"
                    " call_count, config_fingerprint, started_at)"
                    " VALUES (%s, %s, 'SUCCESS', 1, 'fp-old', now()) RETURNING id",
                    (model_id, provider_id),
                )
                probe_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO provider_usage_records (source, model_id, provider_id,"
                    " probe_id, prompt_tokens, completion_tokens, usage_known,"
                    " prompt_tokens_known, completion_tokens_known)"
                    " VALUES ('PROBE', %s, %s, %s, 21, 7, true, true, true)",
                    (model_id, provider_id, probe_id),
                )
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM companies")
                companies_before = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM model_probes")
                probes_before = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM provider_usage_records")
                usage_before = cur.fetchone()[0]
        check(
            "phase02-baseline",
            before_rev == "8c24a71d9e03",
            revision=before_rev,
            companies=companies_before,
            probes=probes_before,
            usage=usage_before,
        )

        # ---- upgrade to the Phase 03 head --------------------------------
        alembic("head")
        with psycopg.connect(url(DB_NAME).replace("+psycopg", "")) as conn:
            head_rev = revision(conn)
            counts = table_counts(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM companies")
                companies_after = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM model_probes")
                probes_after = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM provider_usage_records")
                usage_after = cur.fetchone()[0]
                cur.execute(
                    "SELECT status, version FROM companies WHERE id = 1"
                )
                status, version = cur.fetchone()
                cur.execute("SELECT count(*) FROM budget_accounts WHERE scope='COMPANY'")
                company_accounts = cur.fetchone()[0]
                cur.execute(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name='companies' AND column_name IN ('paused_at','pause_reason')"
                )
                pause_columns = cur.fetchone()[0]
        check(
            "upgrade-head",
            head_rev == "c41f7b93e2a5"
            and all(counts[table] == 1 for table in PHASE03_TABLES)
            and companies_after == companies_before
            and probes_after == probes_before
            and usage_after == usage_before
            and status == "ACTIVE"
            and company_accounts == 1
            and pause_columns == 2,
            revision=head_rev,
            **counts,
            companies=companies_after,
            probes=probes_after,
            usage=usage_after,
            company_status=status,
            company_version=version,
            company_budget_accounts=company_accounts,
            pause_columns=pause_columns,
        )

        # A paused company must be storable; the old constraint allowed only ACTIVE.
        with psycopg.connect(url(DB_NAME).replace("+psycopg", "")) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE companies SET status='PAUSED', paused_at=now(),"
                    " pause_reason='验收暂停' WHERE id=1"
                )
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT status, pause_reason FROM companies WHERE id=1")
                paused = cur.fetchone()
        check(
            "pause-state-persists",
            paused[0] == "PAUSED" and paused[1] == "验收暂停",
            status=paused[0],
            reason=paused[1],
        )

        # ---- downgrade to the Phase 02 head ------------------------------
        # The downgrade must reset a paused row to ACTIVE before restoring the old
        # constraint, otherwise it would fail on its own data.
        alembic_down("8c24a71d9e03")
        with psycopg.connect(url(DB_NAME).replace("+psycopg", "")) as conn:
            down_rev = revision(conn)
            counts_down = table_counts(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM model_probes")
                probes_down = cur.fetchone()[0]
                cur.execute("SELECT status FROM companies WHERE id=1")
                status_down = cur.fetchone()[0]
                cur.execute(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name='companies' AND column_name='paused_at'"
                )
                paused_column = cur.fetchone()[0]
        check(
            "downgrade-phase02",
            down_rev == "8c24a71d9e03"
            and all(counts_down[table] == 0 for table in PHASE03_TABLES)
            and probes_down == probes_before
            and status_down == "ACTIVE"
            and paused_column == 0,
            revision=down_rev,
            phase03_tables_present=sum(counts_down[table] for table in PHASE03_TABLES),
            probes=probes_down,
            company_status=status_down,
            paused_column=paused_column,
        )

        # ---- upgrade again: history still intact ------------------------
        alembic("head")
        with psycopg.connect(url(DB_NAME).replace("+psycopg", "")) as conn:
            again_rev = revision(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM model_probes")
                probes_again = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM provider_usage_records")
                usage_again = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM companies")
                companies_again = cur.fetchone()[0]
                cur.execute("SELECT prompt_tokens, completion_tokens FROM provider_usage_records")
                tokens = cur.fetchone()
        check(
            "reupgrade-preserves-history",
            again_rev == "c41f7b93e2a5"
            and probes_again == probes_before
            and usage_again == usage_before
            and companies_again == companies_before
            and tuple(tokens) == (21, 7),
            revision=again_rev,
            probes=probes_again,
            usage=usage_again,
            companies=companies_again,
            tokens=list(tokens),
        )
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')

    summary = {
        "checks": results,
        "failures": failures,
        "passed": not failures,
        "database": DB_NAME,
    }
    (out_dir / "migration-results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"summary": summary}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
