import json
import os
import random
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import jwt
import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Path
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DB_DSN = (
    f"host={os.getenv('DB_HOST', 'localhost')} "
    f"port={os.getenv('DB_PORT', '5432')} "
    f"dbname={os.getenv('DB_NAME', 'gradia')} "
    f"user={os.getenv('DB_USER', 'gradia')} "
    f"password={os.getenv('DB_PASSWORD', 'gradia')}"
)
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))
RELEASE_AT = os.getenv("RELEASE_AT", "")  # ISO 8601. Empty = released.
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")

pool = ConnectionPool(DB_DSN, min_size=1, max_size=10, open=False)
cache = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)


def init_db():
    with pool.connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                student_id TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                gpa        NUMERIC(3,2) NOT NULL,
                courses    JSONB NOT NULL
            )
            """
        )
        count = conn.execute("SELECT count(*) FROM results").fetchone()[0]
        if count == 0:
            random.seed(42)
            subjects = ["Mathematics", "Networks", "Operating Systems",
                        "Databases", "Cloud Computing"]
            rows = []
            for i in range(1, 501):
                courses = [{"name": s, "grade": random.randint(50, 100)} for s in subjects]
                gpa = round(sum(c["grade"] for c in courses) / len(courses) / 25, 2)
                rows.append((str(20260000 + i), f"Student {i}", gpa, json.dumps(courses)))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO results (student_id, name, gpa, courses) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    rows,
                )


@asynccontextmanager
async def lifespan(_: FastAPI):
    pool.open(wait=True, timeout=30)
    init_db()
    yield
    pool.close()


app = FastAPI(title="Gradia Results Service", lifespan=lifespan)


def is_released() -> bool:
    if not RELEASE_AT:
        return True
    release = datetime.fromisoformat(RELEASE_AT)
    if release.tzinfo is None:
        release = release.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= release


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    try:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
    except Exception:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready"}


def current_student(authorization: str = Header(default="")) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return claims["sub"]


@app.get("/api/results/{student_id}")
def get_result(
    student_id: str = Path(pattern=r"^\d{4,12}$"),
    me: str = Depends(current_student),
):
    if me != student_id:
        raise HTTPException(status_code=403, detail="You can only view your own result")
    if not is_released():
        raise HTTPException(
            status_code=403,
            detail={"message": "Results have not been released yet",
                    "release_at": RELEASE_AT},
        )

    key = f"result:{student_id}"
    try:
        cached = cache.get(key)
        if cached:
            return {**json.loads(cached), "source": "cache"}
    except redis.RedisError:
        pass  # degrade gracefully: fall back to the database

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT student_id, name, gpa::float AS gpa, courses "
                "FROM results WHERE student_id = %s",
                (student_id,),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        cache.setex(key, CACHE_TTL, json.dumps(row))
    except redis.RedisError:
        pass

    return {**row, "source": "database"}
