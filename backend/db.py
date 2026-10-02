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

CREATE TABLE IF NOT EXISTS shipment_packages (
    id serial PRIMARY KEY,
    shipment_no text NOT NULL,
    status text NOT NULL DEFAULT 'sealed',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    unbundled_at timestamptz,
    unbundled_by text
);

ALTER TABLE probe_readings
    ADD COLUMN IF NOT EXISTS sealed_package_id integer
    REFERENCES shipment_packages(id);

-- 包内条目是捆扎当刻的不可变快照：拆捆、改温都不许动它
CREATE TABLE IF NOT EXISTS shipment_items (
    id serial PRIMARY KEY,
    package_id integer NOT NULL REFERENCES shipment_packages(id),
    reading_id integer NOT NULL REFERENCES probe_readings(id),
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    verdict text,
    reason text,
    reading_status text NOT NULL,
    reading_created_by text,
    reading_created_at timestamptz,
    processed_at timestamptz,
    frozen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (package_id, reading_id)
);
CREATE INDEX IF NOT EXISTS idx_shipment_items_package ON shipment_items (package_id, id);

-- 捆扎 / 拆捆流水，只追加
CREATE TABLE IF NOT EXISTS shipment_events (
    id serial PRIMARY KEY,
    package_id integer NOT NULL REFERENCES shipment_packages(id),
    shipment_no text NOT NULL,
    event_type text NOT NULL,
    actor text NOT NULL,
    detail text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_shipment_events_time ON shipment_events (id DESC);

CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);
CREATE INDEX IF NOT EXISTS idx_probe_readings_sealed ON probe_readings (sealed_package_id)
    WHERE sealed_package_id IS NOT NULL;

-- 冻的是「包内快照」而不是现行行：总表现行数字允许随后修正，
-- 但任何写接口都不许把快照改成新值（包内只能读 shipment_items）。
-- 早期版本曾在现行行上整体冻结，这里摘掉那套触发器。
DROP TRIGGER IF EXISTS trg_freeze_sealed_reading_upd ON probe_readings;
DROP TRIGGER IF EXISTS trg_freeze_sealed_reading_del ON probe_readings;
DROP FUNCTION IF EXISTS freeze_sealed_reading_fn();

-- 装车包：单号不可改，sealed 只能单向变 unbundled。
-- 拆捆后由接口在同一事务把读数的 sealed_package_id 清空、读数回到待捆池；
-- shipment_items 快照行原样保留（“重开包内仍停在捆扎当刻”）。
CREATE OR REPLACE FUNCTION shipment_package_guard_fn() RETURNS trigger AS $$
BEGIN
    IF NEW.status NOT IN ('sealed', 'unbundled') THEN
        RAISE EXCEPTION '非法装车包状态 %', NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.shipment_no IS DISTINCT FROM OLD.shipment_no THEN
        RAISE EXCEPTION '装车单号不可修改'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'unbundled' AND NEW.status = 'sealed' THEN
        RAISE EXCEPTION '装车包 % 已拆捆，不能重新密封', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shipment_package_guard ON shipment_packages;
CREATE TRIGGER trg_shipment_package_guard
    BEFORE UPDATE ON shipment_packages
    FOR EACH ROW EXECUTE FUNCTION shipment_package_guard_fn();

-- 快照与流水只追加
CREATE OR REPLACE FUNCTION shipment_append_only_fn() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% 为只追加留档，禁止 %', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shipment_items_upd ON shipment_items;
CREATE TRIGGER trg_shipment_items_upd
    BEFORE UPDATE ON shipment_items
    FOR EACH ROW EXECUTE FUNCTION shipment_append_only_fn();
DROP TRIGGER IF EXISTS trg_shipment_items_del ON shipment_items;
CREATE TRIGGER trg_shipment_items_del
    BEFORE DELETE ON shipment_items
    FOR EACH ROW EXECUTE FUNCTION shipment_append_only_fn();
DROP TRIGGER IF EXISTS trg_shipment_events_upd ON shipment_events;
CREATE TRIGGER trg_shipment_events_upd
    BEFORE UPDATE ON shipment_events
    FOR EACH ROW EXECUTE FUNCTION shipment_append_only_fn();
DROP TRIGGER IF EXISTS trg_shipment_events_del ON shipment_events;
CREATE TRIGGER trg_shipment_events_del
    BEFORE DELETE ON shipment_events
    FOR EACH ROW EXECUTE FUNCTION shipment_append_only_fn();
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
