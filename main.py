import os
import boto3
import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Aurora IAM Auth Test")

DB_HOST     = os.environ["DB_HOST"]
DB_PORT     = int(os.environ.get("DB_PORT", "5432"))
DB_NAME     = os.environ["DB_NAME"]
DB_USERNAME = os.environ["DB_USERNAME"]
AWS_REGION  = os.environ.get("AWS_REGION", "us-east-1")


def _get_iam_token() -> str:
    client = boto3.client("rds", region_name=AWS_REGION)
    return client.generate_db_auth_token(
        DBHostname=DB_HOST,
        Port=DB_PORT,
        DBUsername=DB_USERNAME,
        Region=AWS_REGION,
    )


@contextmanager
def get_conn():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USERNAME,
        password=_get_iam_token(),
        sslmode="require",
        connect_timeout=10,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_table():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id      SERIAL PRIMARY KEY,
                    name    TEXT NOT NULL,
                    value   TEXT NOT NULL
                )
            """)


@app.on_event("startup")
def startup():
    _ensure_table()


# ── Schemas ──────────────────────────────────────────────────────────────────

class ItemCreate(BaseModel):
    name: str
    value: str

class ItemUpdate(BaseModel):
    name: Optional[str] = None
    value: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/db-test")
def db_test():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version(), current_user, current_database();")
                row = cur.fetchone()
        return {
            "status": "connected",
            "pg_version": row[0],
            "current_user": row[1],
            "current_database": row[2],
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(exc)})


@app.post("/items", status_code=201)
def create_item(body: ItemCreate):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO items (name, value) VALUES (%s, %s) RETURNING *",
                (body.name, body.value),
            )
            row = cur.fetchone()
    return dict(row)


@app.get("/items")
def list_items():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM items ORDER BY id")
            rows = cur.fetchall()
    return [dict(r) for r in rows]


@app.get("/items/{item_id}")
def get_item(item_id: int):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM items WHERE id = %s", (item_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")
    return dict(row)


@app.put("/items/{item_id}")
def update_item(item_id: int, body: ItemUpdate):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"UPDATE items SET {set_clause} WHERE id = %s RETURNING *",
                (*fields.values(), item_id),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")
    return dict(row)


@app.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM items WHERE id = %s RETURNING id", (item_id,))
            deleted = cur.fetchone()
    if not deleted:
        raise HTTPException(status_code=404, detail="Item not found")
