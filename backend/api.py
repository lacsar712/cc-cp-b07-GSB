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


def require_writer(request: web.Request, action: str = "写操作") -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": f"观察岗只能翻包，{action}仅记录员可操作"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


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


def _reading_out(r) -> dict:
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
        "sealed_package_id": r["sealed_package_id"],
    }


READING_COLS = (
    "id, probe_id, temp_c, verdict, reason, status, created_by, "
    "created_at, processed_at, sealed_package_id"
)


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        f"SELECT {READING_COLS} FROM probe_readings ORDER BY id DESC"
    )
    return web.json_response([_reading_out(r) for r in rows])


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request, "提交读数")
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
        f"""
        INSERT INTO probe_readings (probe_id, temp_c, status, created_by, created_at)
        VALUES ($1, $2, 'pending', $3, now())
        RETURNING {READING_COLS}
        """,
        probe_id,
        temp_c,
        user["username"],
    )
    out = _reading_out(row)
    out["message"] = "已入队，后台工人将认领并判定"
    return web.json_response(out, status=201)


async def patch_reading(request: web.Request) -> web.Response:
    """记录员改温度，改的是总表现行数字。

    包内读数走 shipment_items 快照表（只追加触发器封死写口），
    所以现行值随后怎么改，已捆/已拆的包内都停在捆扎当刻。
    """
    require_writer(request, "修改读数")
    try:
        reading_id = int(request.match_info["id"])
    except ValueError as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "读数编号非法"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc

    pool: asyncpg.Pool = request.app["pool"]
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"SELECT {READING_COLS} FROM probe_readings WHERE id = $1 FOR UPDATE",
                    reading_id,
                )
                if not row:
                    raise web.HTTPNotFound(
                        text=json.dumps({"detail": "读数不存在"}, ensure_ascii=False),
                        content_type="application/json",
                    )
                if row["status"] == "done":
                    verdict, reason = judge_temp(temp_c)
                    updated = await conn.fetchrow(
                        f"""
                        UPDATE probe_readings
                           SET temp_c = $2, verdict = $3, reason = $4, processed_at = now()
                         WHERE id = $1
                        RETURNING {READING_COLS}
                        """,
                        reading_id,
                        temp_c,
                        verdict,
                        reason,
                    )
                else:
                    updated = await conn.fetchrow(
                        f"""
                        UPDATE probe_readings SET temp_c = $2 WHERE id = $1
                        RETURNING {READING_COLS}
                        """,
                        reading_id,
                        temp_c,
                    )
    except asyncpg.exceptions.CheckViolationError as exc:
        raise web.HTTPConflict(
            text=json.dumps(
                {"detail": f"读数已冻入密封装车包，写口冻结：{exc}"},
                ensure_ascii=False,
            ),
            content_type="application/json",
        ) from exc
    return web.json_response(_reading_out(updated))


def _package_out(p) -> dict:
    return {
        "id": p["id"],
        "shipment_no": p["shipment_no"],
        "status": p["status"],
        "created_by": p["created_by"],
        "created_at": p["created_at"].isoformat() if p["created_at"] else None,
        "unbundled_at": p["unbundled_at"].isoformat() if p["unbundled_at"] else None,
        "unbundled_by": p["unbundled_by"],
    }


def _item_out(r) -> dict:
    return {
        "id": r["id"],
        "package_id": r["package_id"],
        "reading_id": r["reading_id"],
        "probe_id": r["probe_id"],
        "temp_c": r["temp_c"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "reading_status": r["reading_status"],
        "reading_created_by": r["reading_created_by"],
        "reading_created_at": r["reading_created_at"].isoformat() if r["reading_created_at"] else None,
        "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
        "frozen_at": r["frozen_at"].isoformat() if r["frozen_at"] else None,
    }


def _event_out(r) -> dict:
    return {
        "id": r["id"],
        "package_id": r["package_id"],
        "shipment_no": r["shipment_no"],
        "event_type": r["event_type"],
        "actor": r["actor"],
        "detail": r["detail"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


async def shipments_overview(request: web.Request) -> web.Response:
    """装车捆扎落地页：待捆列表 + 已捆包（含当刻快照）+ 捆扎/拆捆流水。登录即可翻包。"""
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    pending = await pool.fetch(
        f"""
        SELECT {READING_COLS}
        FROM probe_readings
        WHERE status = 'done' AND sealed_package_id IS NULL
        ORDER BY id
        """
    )
    packages = await pool.fetch(
        """
        SELECT id, shipment_no, status, created_by, created_at, unbundled_at, unbundled_by
        FROM shipment_packages
        ORDER BY id DESC
        """
    )
    items = await pool.fetch(
        """
        SELECT id, package_id, reading_id, probe_id, temp_c, verdict, reason,
               reading_status, reading_created_by, reading_created_at, processed_at, frozen_at
        FROM shipment_items
        ORDER BY package_id DESC, id
        """
    )
    events = await pool.fetch(
        """
        SELECT id, package_id, shipment_no, event_type, actor, detail, created_at
        FROM shipment_events
        ORDER BY id DESC
        """
    )
    return web.json_response(
        {
            "pending": [_reading_out(r) for r in pending],
            "packages": [_package_out(p) for p in packages],
            "items": [_item_out(r) for r in items],
            "events": [_event_out(r) for r in events],
        }
    )


async def bundle_shipment(request: web.Request) -> web.Response:
    """记录员勾选已办结读数 + 装车单号，一点捆扎：读数快照冻进包内。"""
    user = require_writer(request, "捆扎")
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc

    shipment_no = str(body.get("shipment_no", "")).strip()
    if not shipment_no:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "装车单号不能为空"}, ensure_ascii=False),
            content_type="application/json",
        )
    raw_ids = body.get("reading_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "请至少勾选一笔已办结读数"}, ensure_ascii=False),
            content_type="application/json",
        )
    try:
        reading_ids = sorted({int(x) for x in raw_ids})
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "读数编号非法"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc
    if len(reading_ids) > 500:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "单次最多捆扎 500 笔"}, ensure_ascii=False),
            content_type="application/json",
        )

    pool: asyncpg.Pool = request.app["pool"]
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    f"""
                    SELECT {READING_COLS}
                    FROM probe_readings
                    WHERE id = ANY($1::int[])
                    ORDER BY id
                    FOR UPDATE
                    """,
                    reading_ids,
                )
                if len(rows) != len(reading_ids):
                    found = {r["id"] for r in rows}
                    missing = [i for i in reading_ids if i not in found]
                    raise web.HTTPBadRequest(
                        text=json.dumps(
                            {"detail": f"读数不存在：{missing}"}, ensure_ascii=False
                        ),
                        content_type="application/json",
                    )
                not_done = [
                    f"#{r['id']}（{r['status']}）"
                    for r in rows
                    if r["status"] != "done"
                ]
                if not_done:
                    raise web.HTTPBadRequest(
                        text=json.dumps(
                            {"detail": "候审或处理中的读数不得入捆：" + "、".join(not_done)},
                            ensure_ascii=False,
                        ),
                        content_type="application/json",
                    )
                already = [
                    f"#{r['id']}→包{r['sealed_package_id']}"
                    for r in rows
                    if r["sealed_package_id"] is not None
                ]
                if already:
                    raise web.HTTPBadRequest(
                        text=json.dumps(
                            {"detail": "读数已在装车包内，不能重复捆扎：" + "、".join(already)},
                            ensure_ascii=False,
                        ),
                        content_type="application/json",
                    )

                package = await conn.fetchrow(
                    """
                    INSERT INTO shipment_packages (shipment_no, status, created_by)
                    VALUES ($1, 'sealed', $2)
                    RETURNING id, shipment_no, status, created_by, created_at,
                              unbundled_at, unbundled_by
                    """,
                    shipment_no,
                    user["username"],
                )
                await conn.execute(
                    """
                    INSERT INTO shipment_items (
                        package_id, reading_id, probe_id, temp_c, verdict, reason,
                        reading_status, reading_created_by, reading_created_at, processed_at
                    )
                    SELECT $1, id, probe_id, temp_c, verdict, reason,
                           status, created_by, created_at, processed_at
                    FROM probe_readings
                    WHERE id = ANY($2::int[])
                    """,
                    package["id"],
                    reading_ids,
                )
                await conn.execute(
                    "UPDATE probe_readings SET sealed_package_id = $1 WHERE id = ANY($2::int[])",
                    package["id"],
                    reading_ids,
                )
                await conn.execute(
                    """
                    INSERT INTO shipment_events (package_id, shipment_no, event_type, actor, detail)
                    VALUES ($1, $2, 'bundled', $3, $4)
                    """,
                    package["id"],
                    shipment_no,
                    user["username"],
                    json.dumps(
                        {"count": len(reading_ids), "reading_ids": reading_ids},
                        ensure_ascii=False,
                    ),
                )
                items = await conn.fetch(
                    """
                    SELECT id, package_id, reading_id, probe_id, temp_c, verdict, reason,
                           reading_status, reading_created_by, reading_created_at,
                           processed_at, frozen_at
                    FROM shipment_items
                    WHERE package_id = $1
                    ORDER BY id
                    """,
                    package["id"],
                )
    except web.HTTPException:
        raise
    except asyncpg.exceptions.CheckViolationError as exc:
        raise web.HTTPConflict(
            text=json.dumps({"detail": str(exc)}, ensure_ascii=False),
            content_type="application/json",
        ) from exc

    return web.json_response(
        {
            "package": _package_out(package),
            "items": [_item_out(r) for r in items],
            "message": f"装车单 {shipment_no} 已捆扎 {len(items)} 笔，包内读数冻结在捆扎当刻",
        },
        status=201,
    )


async def unbundle_package(request: web.Request) -> web.Response:
    """记录员拆捆：包解封、读数回到待捆池，快照原样留档，写一条拆捆流水。"""
    user = require_writer(request, "拆捆")
    try:
        package_id = int(request.match_info["id"])
    except ValueError as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "装车包编号非法"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            package = await conn.fetchrow(
                """
                SELECT id, shipment_no, status, created_by, created_at,
                       unbundled_at, unbundled_by
                FROM shipment_packages
                WHERE id = $1
                FOR UPDATE
                """,
                package_id,
            )
            if not package:
                raise web.HTTPNotFound(
                    text=json.dumps({"detail": "装车包不存在"}, ensure_ascii=False),
                    content_type="application/json",
                )
            if package["status"] != "sealed":
                raise web.HTTPConflict(
                    text=json.dumps(
                        {"detail": f"装车包 {package_id} 已拆捆，不能重复拆捆"},
                        ensure_ascii=False,
                    ),
                    content_type="application/json",
                )
            frozen_ids = [
                r["reading_id"]
                for r in await conn.fetch(
                    "SELECT reading_id FROM shipment_items WHERE package_id = $1 ORDER BY id",
                    package_id,
                )
            ]
            package = await conn.fetchrow(
                """
                UPDATE shipment_packages
                   SET status = 'unbundled', unbundled_at = now(), unbundled_by = $2
                 WHERE id = $1
                RETURNING id, shipment_no, status, created_by, created_at,
                          unbundled_at, unbundled_by
                """,
                package_id,
                user["username"],
            )
            # 读数回到待捆池；快照行不动（外键仍指向该读数）
            await conn.execute(
                "UPDATE probe_readings SET sealed_package_id = NULL WHERE sealed_package_id = $1",
                package_id,
            )
            await conn.execute(
                """
                INSERT INTO shipment_events (package_id, shipment_no, event_type, actor, detail)
                VALUES ($1, $2, 'unbundled', $3, $4)
                """,
                package_id,
                package["shipment_no"],
                user["username"],
                json.dumps(
                    {"count": len(frozen_ids), "reading_ids": frozen_ids},
                    ensure_ascii=False,
                ),
            )
    return web.json_response(
        {
            "package": _package_out(package),
            "message": f"装车包 {package_id}（{package['shipment_no']}）已拆捆，读数回到待捆列表，包内快照保留",
        }
    )


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
    app.router.add_patch("/api/readings/{id}", patch_reading)
    app.router.add_get("/api/shipments", shipments_overview)
    app.router.add_post("/api/shipments/bundle", bundle_shipment)
    app.router.add_post("/api/shipments/packages/{id}/unbundle", unbundle_package)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
