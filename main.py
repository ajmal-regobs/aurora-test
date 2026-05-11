import os
import boto3
import psycopg2
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

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


def _connect():
    token = _get_iam_token()
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USERNAME,
        password=token,
        sslmode="require",
        connect_timeout=10,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/db-test")
def db_test():
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT version(), current_user, current_database();")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return {
            "status": "connected",
            "pg_version": row[0],
            "current_user": row[1],
            "current_database": row[2],
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(exc)})
