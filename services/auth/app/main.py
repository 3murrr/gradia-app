import hashlib
import hmac
import os
import secrets
import time
from contextlib import asynccontextmanager

import jwt
from fastapi import FastAPI, HTTPException
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

DB_DSN = (
    f"host={os.getenv('DB_HOST', 'localhost')} "
    f"port={os.getenv('DB_PORT', '5432')} "
    f"dbname={os.getenv('DB_NAME', 'gradia')} "
    f"user={os.getenv('DB_USER', 'gradia')} "
    f"password={os.getenv('DB_PASSWORD', 'gradia')}"
)
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
TOKEN_TTL = int(os.getenv("TOKEN_TTL", "3600"))
PBKDF2_ITER = 60_000

pool = ConnectionPool(DB_DSN, min_size=1, max_size=5, open=False)


def hash_secret(value: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", value.encode(), salt, PBKDF2_ITER)


def init_db():
    with pool.connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS students_auth (
                student_id  TEXT PRIMARY KEY,
                salt        TEXT NOT NULL,
                secret_hash TEXT NOT NULL
            )
            """
        )
        count = conn.execute("SELECT count(*) FROM students_auth").fetchone()[0]
        if count == 0:
            rows = []
            for i in range(1, 501):
                national_id = f"3{i:013d}"  # fake test data
                salt = secrets.token_bytes(16)
                rows.append((str(20260000 + i), salt.hex(),
                             hash_secret(national_id, salt).hex()))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO students_auth (student_id, salt, secret_hash) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    rows,
                )


@asynccontextmanager
async def lifespan(_: FastAPI):
    pool.open(wait=True, timeout=30)
    init_db()
    yield
    pool.close()


app = FastAPI(title="Gradia Auth Service", lifespan=lifespan)


class LoginRequest(BaseModel):
    student_id: str = Field(pattern=r"^\d{4,12}$")
    national_id: str = Field(pattern=r"^\d{14}$")


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


@app.post("/api/login")
def login(body: LoginRequest):
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT salt, secret_hash FROM students_auth WHERE student_id = %s",
            (body.student_id,),
        ).fetchone()

    if row is None:
        # dummy hash so unknown IDs take the same time as wrong passwords
        hash_secret(body.national_id, b"0" * 16)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    salt, stored = bytes.fromhex(row[0]), bytes.fromhex(row[1])
    if not hmac.compare_digest(hash_secret(body.national_id, salt), stored):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    now = int(time.time())
    token = jwt.encode(
        {"sub": body.student_id, "iat": now, "exp": now + TOKEN_TTL},
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"access_token": token, "token_type": "bearer", "expires_in": TOKEN_TTL}