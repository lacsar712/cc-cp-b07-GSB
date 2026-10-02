import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import judge_temp

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);

CREATE TABLE IF NOT EXISTS loading_packages (
    id serial PRIMARY KEY,
    loading_no text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'sealed',
    sealed_by text NOT NULL,
    sealed_at timestamptz NOT NULL DEFAULT now(),
    unbundled_by text,
    unbundled_at timestamptz,
    unbundle_reason text
);

CREATE TABLE IF NOT EXISTS loading_package_items (
    package_id int NOT NULL REFERENCES loading_packages(id),
    reading_id int NOT NULL REFERENCES probe_readings(id),
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    verdict text,
    reason text,
    reading_status text NOT NULL,
    created_by text,
    reading_created_at timestamptz,
    processed_at timestamptz,
    sealed_at timestamptz NOT NULL,
    PRIMARY KEY (package_id, reading_id)
);
CREATE INDEX IF NOT EXISTS idx_loading_package_items_reading
    ON loading_package_items (reading_id);

CREATE TABLE IF NOT EXISTS loading_unbundle_log (
    id serial PRIMARY KEY,
    package_id int NOT NULL,
    loading_no text NOT NULL,
    reading_ids integer[] NOT NULL,
    unbundled_by text NOT NULL,
    unbundled_at timestamptz NOT NULL DEFAULT now(),
    reason text
);
"""


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=5)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        samples = [
            ("探头A01", 4.2),
            ("探头B02", 12.5),
        ]
        for probe_id, temp_c in samples:
            verdict, reason = judge_temp(temp_c)
            await conn.execute(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, verdict, reason, status, created_by, processed_at)
                VALUES ($1, $2, $3, $4, 'done', 'logger', now())
                """,
                probe_id,
                temp_c,
                verdict,
                reason,
            )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("探头A01", 4.2),
        ("探头B02", 12.5),
    ]
    for probe_id, temp_c in samples:
        verdict, reason = judge_temp(temp_c)
        conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, temp_c, verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, %s, %s, 'done', 'logger', now())
            """,
            (probe_id, temp_c, verdict, reason),
        )
    conn.commit()
