import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import judge_temp

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def _auth_header(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_user(request: web.Request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        raise web.HTTPUnauthorized(text=json.dumps({"detail": "未登录"}, ensure_ascii=False), content_type="application/json")
    return user


def require_writer(request: web.Request) -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": "仅记录员可执行该操作"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


def http_error(status: int, detail: str) -> web.HTTPException:
    cls = {
        400: web.HTTPBadRequest,
        403: web.HTTPForbidden,
        404: web.HTTPNotFound,
        409: web.HTTPConflict,
    }.get(status, web.HTTPBadRequest)
    return cls(
        text=json.dumps({"detail": detail}, ensure_ascii=False),
        content_type="application/json",
    )


def reading_out(r) -> dict:
    """序列化一行现行读数；frozen/loading_no 来自冻包关联（新入队时默认未冻）。"""
    keys = r.keys() if hasattr(r, "keys") else r
    return {
        "id": r["id"],
        "probe_id": r["probe_id"],
        "temp_c": r["temp_c"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
        "frozen": r["frozen"] if "frozen" in keys else False,
        "loading_no": r["package_loading_no"] if "package_loading_no" in keys else None,
    }


def snapshot_out(r) -> dict:
    """序列化包内快照——这是捆扎当刻的定格，独立于 probe_readings 之后的任何变动。"""
    keys = r.keys() if hasattr(r, "keys") else r
    return {
        "reading_id": r["reading_id"],
        "probe_id": r["probe_id"],
        "temp_c": r["temp_c"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "reading_status": r["reading_status"],
        "created_by": r["created_by"],
        "reading_created_at": r["reading_created_at"].isoformat()
        if r["reading_created_at"]
        else None,
        "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
        "sealed_at": r["sealed_at"].isoformat() if r["sealed_at"] else None,
        # 现行值来自实时 JOIN，仅用于并置对比；定格值仍是上面的 temp_c。
        "current_temp_c": r["current_temp_c"] if "current_temp_c" in keys else None,
        "current_status": r["current_status"] if "current_status" in keys else None,
    }


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "coldchain-probe-desk"})


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise web.HTTPUnauthorized(
            text=json.dumps({"detail": "用户名或密码错误"}, ensure_ascii=False),
            content_type="application/json",
        )
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT r.id, r.probe_id, r.temp_c, r.verdict, r.reason, r.status,
               r.created_by, r.created_at, r.processed_at,
               TRUE AS frozen, p.loading_no AS package_loading_no
        FROM probe_readings r
        JOIN loading_package_items i ON i.reading_id = r.id
        JOIN loading_packages p ON p.id = i.package_id
        UNION ALL
        SELECT r.id, r.probe_id, r.temp_c, r.verdict, r.reason, r.status,
               r.created_by, r.created_at, r.processed_at,
               FALSE AS frozen, NULL::text AS package_loading_no
        FROM probe_readings r
        WHERE NOT EXISTS (
            SELECT 1 FROM loading_package_items i WHERE i.reading_id = r.id
        )
        ORDER BY id DESC
        """
    )
    return web.json_response([reading_out(r) for r in rows])


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "探头编号不能为空"}, ensure_ascii=False),
            content_type="application/json",
        )
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc

    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        """
        INSERT INTO probe_readings (probe_id, temp_c, status, created_by, created_at)
        VALUES ($1, $2, 'pending', $3, now())
        RETURNING id, probe_id, temp_c, verdict, reason, status, created_by, created_at, processed_at
        """,
        probe_id,
        temp_c,
        user["username"],
    )
    return web.json_response(
        {
            "id": row["id"],
            "probe_id": row["probe_id"],
            "temp_c": row["temp_c"],
            "verdict": row["verdict"],
            "reason": row["reason"],
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "processed_at": None,
            "frozen": False,
            "loading_no": None,
            "message": "已入队，后台工人将认领并判定",
        },
        status=201,
    )


async def update_reading(request: web.Request) -> web.Response:
    """写接口：人为修订一笔读数的温度。冻进包内的读数一律拒绝（409）。"""
    require_writer(request)
    try:
        reading_id = int(request.match_info["reading_id"])
    except ValueError as exc:
        raise http_error(400, "读数编号不合法") from exc
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise http_error(400, "温度必须是数字") from exc

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        # 先判冻：包内快照存在即封写，不允许借任何写口改写。
        frozen_no = await conn.fetchval(
            """
            SELECT p.loading_no
            FROM loading_package_items i
            JOIN loading_packages p ON p.id = i.package_id
            WHERE i.reading_id = $1
            """,
            reading_id,
        )
        if frozen_no:
            raise http_error(
                409, f"读数已冻入装车单 {frozen_no}，包内读数禁止改写"
            )
        row = await conn.fetchrow(
            """
            UPDATE probe_readings
            SET temp_c = $2,
                verdict = NULL,
                reason = NULL,
                status = 'pending',
                processed_at = NULL
            WHERE id = $1
            RETURNING id, probe_id, temp_c, verdict, reason, status,
                      created_by, created_at, processed_at
            """,
            reading_id,
            temp_c,
        )
    if not row:
        raise http_error(404, "读数不存在")
    data = reading_out(row)
    data["frozen"] = False
    data["loading_no"] = None
    data["message"] = "温度已修订，读数重新进入判定队列"
    return web.json_response(data)


async def loading_overview(request: web.Request) -> web.Response:
    """装车捆扎落地页数据：待捆列表、已捆包（含定格快照）、拆捆流水。"""
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        pending_rows = await conn.fetch(
            """
            SELECT id, probe_id, temp_c, verdict, reason, status,
                   created_by, created_at, processed_at
            FROM probe_readings
            WHERE status = 'done'
              AND NOT EXISTS (
                  SELECT 1 FROM loading_package_items i WHERE i.reading_id = probe_readings.id
              )
            ORDER BY id
            """
        )
        package_rows = await conn.fetch(
            """
            SELECT id, loading_no, status, sealed_by, sealed_at,
                   unbundled_by, unbundled_at, unbundle_reason
            FROM loading_packages
            WHERE status = 'sealed'
            ORDER BY id DESC
            """
        )
        item_rows = await conn.fetch(
            """
            SELECT i.package_id, i.reading_id, i.probe_id, i.temp_c, i.verdict,
                   i.reason, i.reading_status, i.created_by, i.reading_created_at,
                   i.processed_at, i.sealed_at,
                   cur.temp_c AS current_temp_c, cur.status AS current_status
            FROM loading_package_items i
            JOIN probe_readings cur ON cur.id = i.reading_id
            ORDER BY i.reading_id
            """
        )
        log_rows = await conn.fetch(
            """
            SELECT id, package_id, loading_no, reading_ids,
                   unbundled_by, unbundled_at, reason
            FROM loading_unbundle_log
            ORDER BY id DESC
            """
        )

    items_by_pkg: dict = {}
    for it in item_rows:
        items_by_pkg.setdefault(it["package_id"], []).append(snapshot_out(it))
    packages = [
        {
            "id": p["id"],
            "loading_no": p["loading_no"],
            "status": p["status"],
            "sealed_by": p["sealed_by"],
            "sealed_at": p["sealed_at"].isoformat() if p["sealed_at"] else None,
            "items": items_by_pkg.get(p["id"], []),
        }
        for p in package_rows
    ]
    logs = [
        {
            "id": l["id"],
            "package_id": l["package_id"],
            "loading_no": l["loading_no"],
            "reading_ids": list(l["reading_ids"] or []),
            "unbundled_by": l["unbundled_by"],
            "unbundled_at": l["unbundled_at"].isoformat() if l["unbundled_at"] else None,
            "reason": l["reason"],
        }
        for l in log_rows
    ]
    return web.json_response(
        {
            "pending": [
                {
                    "id": r["id"],
                    "probe_id": r["probe_id"],
                    "temp_c": r["temp_c"],
                    "verdict": r["verdict"],
                    "reason": r["reason"],
                    "status": r["status"],
                    "created_by": r["created_by"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
                }
                for r in pending_rows
            ],
            "packages": packages,
            "logs": logs,
        }
    )


async def seal_package(request: web.Request) -> web.Response:
    """记录员勾选已办结读数 + 装车单号，一点捆扎；快照在同一事务定格。"""
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    loading_no = str(body.get("loading_no", "")).strip()
    if not loading_no:
        raise http_error(400, "装车单号不能为空")
    raw_ids = body.get("reading_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        raise http_error(400, "请至少勾选一笔读数")
    try:
        reading_ids = [int(x) for x in raw_ids]
    except (TypeError, ValueError) as exc:
        raise http_error(400, "读数编号不合法") from exc
    if len(set(reading_ids)) != len(reading_ids):
        raise http_error(400, "勾选读数存在重复")

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        try:
            # 包行先上排他锁级事务，保证同号并发捆扎只有一笔成功。
            async with conn.transaction():
                dup = await conn.fetchval(
                    "SELECT 1 FROM loading_packages WHERE loading_no = $1",
                    loading_no,
                )
                if dup:
                    raise http_error(409, f"装车单号 {loading_no} 已存在")
                rows = await conn.fetch(
                    """
                    SELECT id, probe_id, temp_c, verdict, reason, status,
                           created_by, created_at, processed_at
                    FROM probe_readings
                    WHERE id = ANY($1)
                    ORDER BY id
                    FOR UPDATE
                    """,
                    reading_ids,
                )
                found = {r["id"] for r in rows}
                missing = [i for i in reading_ids if i not in found]
                if missing:
                    raise http_error(404, f"读数不存在：{missing}")
                bad_status = [r["id"] for r in rows if r["status"] != "done"]
                if bad_status:
                    raise http_error(
                        409, f"读数 {bad_status} 非已办结，候审或处理中的读数不得入捆"
                    )
                already = await conn.fetch(
                    """
                    SELECT i.reading_id
                    FROM loading_package_items i
                    WHERE i.reading_id = ANY($1)
                    """,
                    reading_ids,
                )
                if already:
                    ids = [r["reading_id"] for r in already]
                    raise http_error(409, f"读数 {ids} 已在其他装车包内")

                package_id = await conn.fetchval(
                    """
                    INSERT INTO loading_packages (loading_no, sealed_by)
                    VALUES ($1, $2)
                    RETURNING id
                    """,
                    loading_no,
                    user["username"],
                )
                await conn.executemany(
                    """
                    INSERT INTO loading_package_items
                        (package_id, reading_id, probe_id, temp_c, verdict, reason,
                         reading_status, created_by, reading_created_at,
                         processed_at, sealed_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, now())
                    """,
                    [
                        (
                            package_id,
                            r["id"],
                            r["probe_id"],
                            r["temp_c"],
                            r["verdict"],
                            r["reason"],
                            r["status"],
                            r["created_by"],
                            r["created_at"],
                            r["processed_at"],
                        )
                        for r in rows
                    ],
                )
        except asyncpg.UniqueViolationError as exc:
            raise http_error(409, f"装车单号 {loading_no} 已存在") from exc

    return web.json_response(
        {"message": f"已按装车单 {loading_no} 捆扎 {len(reading_ids)} 笔读数并冻入包内"},
        status=201,
    )


async def unbundle_package(request: web.Request) -> web.Response:
    """记录员拆捆：写流水后删快照、置包为 unbundled，读数回到待捆列表。"""
    user = require_writer(request)
    try:
        package_id = int(request.match_info["package_id"])
    except ValueError as exc:
        raise http_error(400, "装车包编号不合法") from exc
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    reason = str(body.get("reason", "")).strip() or None

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            pkg = await conn.fetchrow(
                "SELECT id, loading_no, status FROM loading_packages WHERE id = $1 FOR UPDATE",
                package_id,
            )
            if not pkg:
                raise http_error(404, "装车包不存在")
            if pkg["status"] != "sealed":
                raise http_error(409, "该装车包已拆开")
            reading_ids = [
                r["reading_id"]
                for r in await conn.fetch(
                    "SELECT reading_id FROM loading_package_items WHERE package_id = $1 ORDER BY reading_id",
                    package_id,
                )
            ]
            await conn.execute(
                """
                INSERT INTO loading_unbundle_log
                    (package_id, loading_no, reading_ids, unbundled_by, reason)
                VALUES ($1, $2, $3, $4, $5)
                """,
                package_id,
                pkg["loading_no"],
                reading_ids,
                user["username"],
                reason,
            )
            await conn.execute(
                "DELETE FROM loading_package_items WHERE package_id = $1",
                package_id,
            )
            await conn.execute(
                """
                UPDATE loading_packages
                SET status = 'unbundled', unbundled_by = $2,
                    unbundled_at = now(), unbundle_reason = $3
                WHERE id = $1
                """,
                package_id,
                user["username"],
                reason,
            )
    return web.json_response({"message": f"装车单 {pkg['loading_no']} 已拆捆，读数回到待捆列表"})


async def on_startup(app: web.Application) -> None:
    pool = await create_pool()
    app["pool"] = pool
    await ensure_schema_async(pool)
    await seed_if_empty(pool)


async def on_cleanup(app: web.Application) -> None:
    pool: asyncpg.Pool = app.get("pool")
    if pool:
        await pool.close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.router.add_patch("/api/readings/{reading_id}", update_reading)
    app.router.add_put("/api/readings/{reading_id}", update_reading)
    app.router.add_get("/api/loading/overview", loading_overview)
    app.router.add_post("/api/loading/packages", seal_package)
    app.router.add_post("/api/loading/packages/{package_id}/unbundle", unbundle_package)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
