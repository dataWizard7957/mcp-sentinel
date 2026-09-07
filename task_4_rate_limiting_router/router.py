import json
import logging
import sqlite3
import time
from typing import Any, Dict, Optional
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rate_limiting_router")

app = FastAPI(title="Resilient Rate-Limiting Router")

PRIMARY_URL = "http://localhost:8001/v1/chat/completions"
FALLBACK_URL = "http://localhost:8002/v1/chat/completions"

DB_FILE = "task_4_rate_limiting_router/ratelimit.db"
WINDOW_SECONDS = 60
MAX_TOKENS = 50_000
ESTIMATED_TOKENS_PER_REQ = 500  # Conservative estimate or extracted from payload


# ------------------------------------------------------------------
# SQLite Persistent Token-Aware Rate Limiter
# ------------------------------------------------------------------

def init_db(db_path: Optional[str] = None):
    target_db = db_path or DB_FILE
    conn = sqlite3.connect(target_db)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_log (
                client_id TEXT NOT NULL,
                tokens INTEGER NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_client_ts ON token_log(client_id, timestamp)")
        conn.commit()
    finally:
        conn.close()


init_db()


def is_rate_limited(client_id: str, tokens: int = ESTIMATED_TOKENS_PER_REQ, db_path: Optional[str] = None) -> bool:
    """
    Checks if client has exceeded MAX_TOKENS within WINDOW_SECONDS.
    Prunes expired entries and records new tokens atomically.
    """
    target_db = db_path or DB_FILE
    now = time.time()
    cutoff = now - WINDOW_SECONDS

    conn = sqlite3.connect(target_db)
    try:
        cursor = conn.cursor()
        # Clean expired records
        cursor.execute("DELETE FROM token_log WHERE timestamp < ?", (cutoff,))

        # Sum recent token usage
        cursor.execute(
            "SELECT COALESCE(SUM(tokens), 0) FROM token_log WHERE client_id = ? AND timestamp >= ?",
            (client_id, cutoff)
        )
        current_tokens = cursor.fetchone()[0]

        if current_tokens + tokens > MAX_TOKENS:
            return True

        # Record this request's token weight
        cursor.execute(
            "INSERT INTO token_log (client_id, tokens, timestamp) VALUES (?, ?, ?)",
            (client_id, tokens, now)
        )
        conn.commit()
        return False
    finally:
        conn.close()


# ------------------------------------------------------------------
# Router Endpoint with Automatic Failover
# ------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def route_chat_completion(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_client_id: Optional[str] = Header("default_tenant")
):
    # Determine tenant key from Authorization Bearer or X-Client-Id
    client_key = authorization.replace("Bearer ", "").strip() if authorization else x_client_id

    # 1. Token Rate Limiting Check
    if is_rate_limited(client_key, tokens=ESTIMATED_TOKENS_PER_REQ):
        logger.warning(f"Token rate limit exceeded for client: {client_key}")
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": f"Tenant '{client_key}' exceeded {MAX_TOKENS} tokens per {WINDOW_SECONDS}s."
                }
            }
        )

    body_bytes = await request.body()
    headers = {"Content-Type": "application/json"}

    # 2. Try Primary Provider (3.0s / 3000ms timeout)
    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            primary_resp = await client.post(PRIMARY_URL, content=body_bytes, headers=headers)
            # Route successfully if healthy
            if primary_resp.status_code < 400:
                return Response(
                    content=primary_resp.content,
                    status_code=primary_resp.status_code,
                    media_type="application/json"
                )

            # Fallback on primary 429 or 5xx
            if primary_resp.status_code == 429 or primary_resp.status_code >= 500:
                logger.warning(f"Primary returned HTTP {primary_resp.status_code}. Initiating failover.")
            else:
                # 4xx client errors (e.g. 400 Bad Request) return directly without failover
                return Response(content=primary_resp.content, status_code=primary_resp.status_code, media_type="application/json")

        except (httpx.RequestError, httpx.TimeoutException) as exc:
            logger.warning(f"Primary provider timed out or failed ({type(exc).__name__}). Initiating failover.")

        # 3. Fallback Provider
        try:
            fallback_resp = await client.post(FALLBACK_URL, content=body_bytes, headers=headers)
            return Response(
                content=fallback_resp.content,
                status_code=fallback_resp.status_code,
                headers={"X-Provider-Used": "fallback"},
                media_type="application/json"
            )
        except Exception as exc:
            logger.error(f"Both primary and fallback providers failed: {exc}")
            return JSONResponse(
                status_code=status.HTTP_502_BAD_GATEWAY,
                content={
                    "error": {
                        "code": "service_unavailable",
                        "message": "All upstream model providers are currently unreachable."
                    }
                }
            )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)