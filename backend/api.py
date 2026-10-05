import asyncio
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from passlib.context import CryptContext
from quart import Quart, jsonify, request

from db import connect, ensure_schema
from rules import judge

SECRET = os.environ.get("JWT_SECRET", "yaw-align-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "technician": {
        "role": "writer",
        "password_hash": pwd.hash("tech123456"),
    },
    "observer": {
        "role": "reader",
        "password_hash": pwd.hash("obs123456"),
    },
}

app = Quart(__name__)


def _run_db(fn, *args, **kwargs):
    return fn(*args, **kwargs)


async def run_db(fn, *args, **kwargs):
    return await asyncio.to_thread(_run_db, fn, *args, **kwargs)


def seed_if_empty(conn):
    ensure_schema(conn)
    count = conn.execute("SELECT COUNT(*) AS n FROM yaw_logs").fetchone()["n"]
    if count > 0:
        return
    now = datetime.now(timezone.utc)
    samples = [
        ("W01", 0.4, "合格"),
        ("W07", 3.2, "偏航超差"),
    ]
    for code, err, expected_verdict in samples:
        verdict, reason = judge(err)
        assert verdict == expected_verdict
        conn.execute(
            """INSERT INTO yaw_logs
               (turbine_code, yaw_err_deg, status, verdict, reason,
                created_by, created_at, processed_at)
               VALUES (%s, %s, 'done', %s, %s, %s, %s, %s)""",
            (code, err, verdict, reason, "technician", now, now),
        )


@app.before_serving
async def startup():
    def init():
        with connect() as conn:
            seed_if_empty(conn)
            conn.commit()

    await run_db(init)


def parse_bearer():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


async def current_user():
    token = parse_bearer()
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_login(handler):
    @wraps(handler)
    async def wrapper(*args, **kwargs):
        user = await current_user()
        if user is None:
            return jsonify({"detail": "未登录"}), 401
        return await handler(user, *args, **kwargs)

    return wrapper


def require_writer(handler):
    @wraps(handler)
    async def wrapper(*args, **kwargs):
        user = await current_user()
        if user is None:
            return jsonify({"detail": "未登录"}), 401
        if user["role"] != "writer":
            return jsonify({"detail": "仅现场技师可提交偏航记录"}), 403
        return await handler(user, *args, **kwargs)

    return wrapper


@app.get("/api/health")
async def health():
    return jsonify({"status": "ok", "service": "yaw-align-log"})


@app.post("/api/auth/login")
async def login():
    body = await request.get_json(force=True, silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        return jsonify({"detail": "用户名或密码错误"}), 401
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return jsonify(
        {
            "access_token": token,
            "username": username,
            "role": user["role"],
        }
    )


@app.get("/api/logs")
@require_login
async def list_logs(user):
    def query():
        with connect() as conn:
            return conn.execute(
                """SELECT id, turbine_code, yaw_err_deg, status, verdict, reason,
                          created_by, created_at, processed_at
                   FROM yaw_logs ORDER BY id DESC"""
            ).fetchall()

    rows = await run_db(query)
    return jsonify(rows)


@app.post("/api/logs")
@require_writer
async def create_log(user):
    body = await request.get_json(force=True, silent=True) or {}
    turbine_code = (body.get("turbine_code") or "").strip()
    if not turbine_code:
        return jsonify({"detail": "机组编号不能为空"}), 400
    try:
        yaw_err_deg = float(body.get("yaw_err_deg"))
    except (TypeError, ValueError):
        return jsonify({"detail": "偏航误差必须是数字"}), 400

    now = datetime.now(timezone.utc)

    def insert():
        with connect() as conn:
            row = conn.execute(
                """INSERT INTO yaw_logs
                   (turbine_code, yaw_err_deg, status, verdict, reason,
                    created_by, created_at)
                   VALUES (%s, %s, 'pending', NULL, NULL, %s, %s)
                   RETURNING id, turbine_code, yaw_err_deg, status, verdict, reason,
                             created_by, created_at, processed_at""",
                (turbine_code, yaw_err_deg, user["username"], now),
            ).fetchone()
            conn.commit()
            return row

    row = await run_db(insert)
    return jsonify(row), 201


MIN_MERGE_SOURCES = 2


def load_merge_sources(conn, raw_ids):
    """校验勾选集合，返回 (error_detail, ids, rows)；均值一律由后台 SQL 计算。"""
    if not isinstance(raw_ids, list):
        return "ids 必须是记录编号数组", None, None
    try:
        ids = sorted({int(i) for i in raw_ids})
    except (TypeError, ValueError):
        return "记录编号必须是整数", None, None
    if len(ids) < MIN_MERGE_SOURCES:
        return "至少勾选两笔记录才能合成", None, None
    rows = conn.execute(
        """SELECT id, turbine_code, yaw_err_deg, status
           FROM yaw_logs WHERE id = ANY(%s) ORDER BY id""",
        (ids,),
    ).fetchall()
    if len(rows) != len(ids):
        return "包含不存在的记录编号", None, None
    if any(r["status"] != "done" for r in rows):
        return "仅可合成已办结记录，勾选中混入未办结记录", None, None
    if len({r["turbine_code"] for r in rows}) != 1:
        return "仅可组合同类（同机组）记录", None, None
    return None, ids, rows


def backend_avg(conn, ids):
    row = conn.execute(
        "SELECT AVG(yaw_err_deg) AS avg_err FROM yaw_logs WHERE id = ANY(%s)",
        (ids,),
    ).fetchone()
    return float(row["avg_err"])


@app.post("/api/merge/preview")
@require_login
async def merge_preview(user):
    body = await request.get_json(force=True, silent=True) or {}
    raw_ids = body.get("ids")

    def work():
        with connect() as conn:
            error, ids, rows = load_merge_sources(conn, raw_ids)
            if error:
                return {"error": error}
            return {
                "turbine_code": rows[0]["turbine_code"],
                "source_ids": ids,
                "source_count": len(ids),
                "avg_yaw_err_deg": backend_avg(conn, ids),
            }

    result = await run_db(work)
    if "error" in result:
        return jsonify({"detail": result["error"]}), 400
    return jsonify(result)


@app.post("/api/merge/enqueue")
@require_writer
async def merge_enqueue(user):
    body = await request.get_json(force=True, silent=True) or {}
    raw_ids = body.get("ids")
    now = datetime.now(timezone.utc)

    def work():
        with connect() as conn:
            error, ids, rows = load_merge_sources(conn, raw_ids)
            if error:
                return {"error": error}
            turbine_code = rows[0]["turbine_code"]
            avg_err = backend_avg(conn, ids)
            # 入队新单与合成流水同一事务提交，缺一边即整体回滚。
            new_log = conn.execute(
                """INSERT INTO yaw_logs
                   (turbine_code, yaw_err_deg, status, verdict, reason,
                    created_by, created_at)
                   VALUES (%s, %s, 'pending', NULL, NULL, %s, %s)
                   RETURNING id, turbine_code, yaw_err_deg, status, verdict, reason,
                             created_by, created_at, processed_at""",
                (turbine_code, avg_err, user["username"], now),
            ).fetchone()
            merge = conn.execute(
                """INSERT INTO yaw_merges
                   (new_log_id, turbine_code, source_ids, source_count,
                    avg_yaw_err_deg, created_by, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id, new_log_id, turbine_code, source_ids, source_count,
                             avg_yaw_err_deg, created_by, created_at""",
                (
                    new_log["id"],
                    turbine_code,
                    ids,
                    len(ids),
                    avg_err,
                    user["username"],
                    now,
                ),
            ).fetchone()
            conn.commit()
            return {"log": new_log, "merge": merge}

    result = await run_db(work)
    if "error" in result:
        return jsonify({"detail": result["error"]}), 400
    return jsonify(result), 201


@app.get("/api/merges")
@require_login
async def list_merges(user):
    def query():
        with connect() as conn:
            return conn.execute(
                """SELECT id, new_log_id, turbine_code, source_ids, source_count,
                          avg_yaw_err_deg, created_by, created_at
                   FROM yaw_merges ORDER BY id DESC"""
            ).fetchall()

    rows = await run_db(query)
    return jsonify(rows)
