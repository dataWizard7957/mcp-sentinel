import os
import sqlite3
import pytest
from fastapi.testclient import TestClient
import httpx
import task_4_rate_limiting_router.router as router_module
from task_4_rate_limiting_router.router import app, init_db, is_rate_limited, MAX_TOKENS

client = TestClient(app)
TEST_DB = "task_4_rate_limiting_router/test_ratelimit.db"


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    init_db(TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except PermissionError:
            pass


@pytest.fixture(autouse=True)
def clean_test_data(monkeypatch):
    monkeypatch.setattr(router_module, "DB_FILE", TEST_DB)
    conn = sqlite3.connect(TEST_DB)
    try:
        conn.execute("DELETE FROM token_log")
        conn.commit()
    finally:
        conn.close()
    yield


def test_01_token_limiter_allows_under_threshold():
    """Verify requests under the token ceiling are allowed."""
    assert is_rate_limited("tenant_1", tokens=10_000, db_path=TEST_DB) is False
    assert is_rate_limited("tenant_1", tokens=20_000, db_path=TEST_DB) is False


def test_02_token_limiter_blocks_exceeding_ceiling():
    """Verify exceeding token limit triggers rate limiting."""
    assert is_rate_limited("tenant_2", tokens=MAX_TOKENS, db_path=TEST_DB) is False
    assert is_rate_limited("tenant_2", tokens=1, db_path=TEST_DB) is True


def test_03_endpoint_returns_429_on_token_exhaustion():
    """Verify gateway returns 429 when tenant token budget is exhausted."""
    headers = {"X-Client-Id": "heavy_tenant"}
    # Exhaust budget
    assert is_rate_limited("heavy_tenant", tokens=MAX_TOKENS, db_path=TEST_DB) is False

    resp = client.post("/v1/chat/completions", json={"prompt": "hi"}, headers=headers)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limit_exceeded"


def test_04_fallback_on_primary_429(monkeypatch):
    """Verify that when primary returns 429, the router fails over to secondary."""
    async def mock_post(self, url, *args, **kwargs):
        if "8001" in str(url):
            return httpx.Response(status_code=429, json={"error": "rate limit"})
        if "8002" in str(url):
            return httpx.Response(status_code=200, json={"choices": [{"text": "from backup"}]})
        return httpx.Response(status_code=500)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    resp = client.post(
        "/v1/chat/completions",
        json={"prompt": "hello"},
        headers={"X-Client-Id": "tenant_test_429"}
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["text"] == "from backup"
    assert resp.headers.get("X-Provider-Used") == "fallback"


def test_05_fallback_on_primary_timeout(monkeypatch):
    """Verify fallback executes seamlessly if primary times out after 3000ms."""
    async def mock_post(self, url, *args, **kwargs):
        if "8001" in str(url):
            raise httpx.TimeoutException("Connection timed out after 3000ms")
        if "8002" in str(url):
            return httpx.Response(status_code=200, json={"choices": [{"text": "from backup after timeout"}]})
        return httpx.Response(status_code=500)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    resp = client.post(
        "/v1/chat/completions",
        json={"prompt": "hello"},
        headers={"X-Client-Id": "tenant_test_timeout"}
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["text"] == "from backup after timeout"
    assert resp.headers.get("X-Provider-Used") == "fallback"