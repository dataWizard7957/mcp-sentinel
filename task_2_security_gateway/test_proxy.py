import base64
import json
import pytest
from fastapi.testclient import TestClient
import httpx
from task_2_security_gateway.proxy import app

client = TestClient(app)

ADMIN_TOKEN = base64.b64encode(json.dumps({"role": "admin"}).encode()).decode()
VIEWER_TOKEN = base64.b64encode(json.dumps({"role": "viewer"}).encode()).decode()


def test_01_unauthenticated_admin_tool_call():
    """Unauthenticated call to admin_ tool must return -32001."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "admin_reset_key", "arguments": {}}
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 1
    assert data["error"]["code"] == -32001
    assert "Unauthorized Tool Call" in data["error"]["message"]


def test_02_viewer_role_denied_admin_tool():
    """Viewer role attempting admin_ tool must return -32001."""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "admin_rotate_token", "arguments": {}}
    }
    resp = client.post(
        "/mcp",
        json=payload,
        headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 2
    assert data["error"]["code"] == -32001


def test_03_admin_role_allowed_admin_tool(monkeypatch):
    """Admin role calling admin_ tool must forward to downstream."""
    async def mock_post(self, *args, **kwargs):
        return httpx.Response(
            status_code=200,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "result": {"content": [{"type": "text", "text": "Key rotated successfully"}]}
            }
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "admin_rotate_token", "arguments": {}}
    }
    resp = client.post(
        "/mcp",
        json=payload,
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 3
    assert data["result"]["content"][0]["text"] == "Key rotated successfully"


def test_04_viewer_allowed_standard_tool(monkeypatch):
    """Viewer role calling standard non-admin tool must forward."""
    async def mock_post(self, *args, **kwargs):
        return httpx.Response(
            status_code=200,
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "result": {"content": [{"type": "text", "text": "Customer record details"}]}
            }
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    payload = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "get_customer_record", "arguments": {"customer_id": "CUST-12345"}}
    }
    resp = client.post(
        "/mcp",
        json=payload,
        headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["content"][0]["text"] == "Customer record details"


def test_05_tools_list_forwarded_transparently(monkeypatch):
    """tools/list is passed through regardless of role."""
    async def mock_post(self, *args, **kwargs):
        return httpx.Response(
            status_code=200,
            json={"jsonrpc": "2.0", "id": 5, "result": {"tools": [{"name": "tool_a"}]}}
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    payload = {"jsonrpc": "2.0", "id": 5, "method": "tools/list"}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    assert "tools" in resp.json()["result"]