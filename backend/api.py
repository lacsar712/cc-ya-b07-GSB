import asyncio
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from passlib.context import CryptContext
from quart import Quart, jsonify, request

from db import SCHEMA, connect
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
    conn.execute(SCHEMA)
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


def require_writer(handler=None, *, message="仅现场技师可提交偏航记录"):
    def decorate(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            user = await current_user()
            if user is None:
                return jsonify({"detail": "未登录"}), 401
            if user["role"] != "writer":
                return jsonify({"detail": message}), 403
            return await fn(user, *args, **kwargs)

        return wrapper

    if handler is not None:
        return decorate(handler)
    return decorate


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


class MergeRejected(Exception):
    """勾选记录不满足合成条件，必须拒收。"""

    def __init__(self, detail, status=400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


def parse_log_ids(body):
    raw = body.get("log_ids")
    if not isinstance(raw, list):
        return None
    ids = []
    for item in raw:
        if isinstance(item, bool):
            return None
        if isinstance(item, float) and not item.is_integer():
            return None
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            return None
    return ids


def fetch_merge_sources(conn, log_ids):
    """校验勾选记录：至少两笔、全部存在、全部已办结、同一结论类型。"""
    ids = sorted(set(log_ids))
    if len(ids) < MIN_MERGE_SOURCES:
        raise MergeRejected("至少勾选两笔已办结记录才能合成")
    rows = conn.execute(
        """SELECT id, turbine_code, yaw_err_deg, status, verdict
           FROM yaw_logs WHERE id = ANY(%s) ORDER BY id""",
        (ids,),
    ).fetchall()
    if len(rows) != len(ids):
        raise MergeRejected("勾选记录中包含不存在的编号")
    if any(row["status"] != "done" for row in rows):
        raise MergeRejected("勾选记录中混入未办结记录，无法合成")
    if len({row["verdict"] for row in rows}) != 1:
        raise MergeRejected("仅可合成同一结论类型的已办结记录")
    return rows


def merge_avg(conn, ids):
    """均值一律由后台计算，前端只负责展示。"""
    row = conn.execute(
        "SELECT AVG(yaw_err_deg) AS avg_err FROM yaw_logs WHERE id = ANY(%s)",
        (sorted(set(ids)),),
    ).fetchone()
    return float(row["avg_err"])


def merge_turbine_code(rows):
    codes = []
    for row in rows:
        if row["turbine_code"] not in codes:
            codes.append(row["turbine_code"])
    return codes[0] if len(codes) == 1 else "+".join(codes)


def merge_preview_payload(conn, rows):
    ids = [row["id"] for row in rows]
    return {
        "source_ids": ids,
        "source_count": len(ids),
        "turbine_code": merge_turbine_code(rows),
        "verdict": rows[0]["verdict"],
        "avg_yaw_err_deg": merge_avg(conn, ids),
    }


@app.post("/api/merges/preview")
@require_login
async def preview_merge(user):
    body = await request.get_json(force=True, silent=True) or {}
    ids = parse_log_ids(body)
    if ids is None:
        return jsonify({"detail": "log_ids 必须是编号数组"}), 400

    def query():
        with connect() as conn:
            rows = fetch_merge_sources(conn, ids)
            return merge_preview_payload(conn, rows)

    try:
        payload = await run_db(query)
    except MergeRejected as rejected:
        return jsonify({"detail": rejected.detail}), rejected.status
    return jsonify(payload)


@app.post("/api/merges")
@require_writer(message="观察员仅可预览，入队需现场技师账号")
async def create_merge(user):
    body = await request.get_json(force=True, silent=True) or {}
    ids = parse_log_ids(body)
    if ids is None:
        return jsonify({"detail": "log_ids 必须是编号数组"}), 400
    now = datetime.now(timezone.utc)

    def work():
        with connect() as conn:
            # 入队结果与合成流水必须在同一事务里记账，缺一边即整体回滚。
            with conn.transaction():
                rows = fetch_merge_sources(conn, ids)
                source_ids = [row["id"] for row in rows]
                avg_err = merge_avg(conn, source_ids)
                turbine_code = merge_turbine_code(rows)
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
                       (new_log_id, turbine_code, verdict, source_count,
                        avg_yaw_err_deg, created_by, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       RETURNING id, new_log_id, turbine_code, verdict, source_count,
                                 avg_yaw_err_deg, created_by, created_at""",
                    (
                        new_log["id"],
                        turbine_code,
                        rows[0]["verdict"],
                        len(source_ids),
                        avg_err,
                        user["username"],
                        now,
                    ),
                ).fetchone()
                for source_id in source_ids:
                    conn.execute(
                        """INSERT INTO yaw_merge_items (merge_id, log_id)
                           VALUES (%s, %s)""",
                        (merge["id"], source_id),
                    )
            conn.commit()
            merge["source_ids"] = source_ids
            return {"merge": merge, "log": new_log}

    try:
        payload = await run_db(work)
    except MergeRejected as rejected:
        return jsonify({"detail": rejected.detail}), rejected.status
    return jsonify(payload), 201


@app.get("/api/merges")
@require_login
async def list_merges(user):
    def query():
        with connect() as conn:
            merges = conn.execute(
                """SELECT id, new_log_id, turbine_code, verdict, source_count,
                          avg_yaw_err_deg, created_by, created_at
                   FROM yaw_merges ORDER BY id DESC"""
            ).fetchall()
            items = conn.execute(
                """SELECT merge_id, log_id FROM yaw_merge_items
                   ORDER BY merge_id, log_id"""
            ).fetchall()
        source_ids_by_merge = {}
        for item in items:
            source_ids_by_merge.setdefault(item["merge_id"], []).append(item["log_id"])
        for merge in merges:
            merge["source_ids"] = source_ids_by_merge.get(merge["id"], [])
        return merges

    return jsonify(await run_db(query))
