import json
import subprocess
import sys
import pytest


class InteractiveMCPClient:
    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, "-u", "task_1_mcp_server/server.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        self.captured_stderr = ""

    def send_notification(self, payload: dict):
        line = json.dumps(payload) + "\n"
        self.process.stdin.write(line)
        self.process.stdin.flush()

    def send_request(self, payload: dict) -> dict:
        line = json.dumps(payload) + "\n"
        self.process.stdin.write(line)
        self.process.stdin.flush()

        resp_line = self.process.stdout.readline()
        if not resp_line:
            _, stderr = self.process.communicate()
            raise RuntimeError(f"Server closed connection unexpectedly. Stderr: {stderr}")
        return json.loads(resp_line.strip())

    def close(self):
        self.process.stdin.close()
        self.process.stdout.close()
        self.captured_stderr = self.process.stderr.read()
        self.process.stderr.close()
        self.process.wait(timeout=3)
        return self.captured_stderr


@pytest.fixture(scope="module")
def mcp():
    """Initializes the MCP server subprocess and performs the handshake once."""
    client = InteractiveMCPClient()

    # Step 1: Handshake initialize
    init_res = client.send_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"}
        }
    })
    assert "capabilities" in init_res.get("result", {})

    # Step 2: Handshake initialized notification
    client.send_notification({"jsonrpc": "2.0", "method": "notifications/initialized"})

    yield client

    # Teardown: close server and verify stdio logging isolation
    stderr_logs = client.close()
    assert "[INFO]" in stderr_logs, "Logs must be written exclusively to stderr"


def test_01_valid_customer_record(mcp):
    res = mcp.send_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "get_customer_record",
            "arguments": {"customer_id": "CUST-12345"}
        }
    })
    assert res.get("result", {}).get("isError") is False
    assert "Record for CUST-12345" in res["result"]["content"][0]["text"]


def test_02_invalid_customer_id_format(mcp):
    res = mcp.send_request({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "get_customer_record",
            "arguments": {"customer_id": "BAD_ID"}
        }
    })
    assert res.get("error", {}).get("code") == -32602
    assert "CUST-XXXXX" in res["error"]["message"]


def test_03_valid_refund(mcp):
    res = mcp.send_request({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "trigger_refund",
            "arguments": {
                "customer_id": "CUST-99999",
                "amount": 49.99,
                "reason": "Item arrived damaged in transit"
            }
        }
    })
    assert res.get("result", {}).get("isError") is False
    assert "Refund SUCCESS" in res["result"]["content"][0]["text"]


def test_04_negative_refund_amount(mcp):
    res = mcp.send_request({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "trigger_refund",
            "arguments": {
                "customer_id": "CUST-12345",
                "amount": -10.0,
                "reason": "Valid refund reason text"
            }
        }
    })
    assert res.get("error", {}).get("code") == -32602
    assert "greater_than" in res["error"]["message"]


def test_05_short_refund_reason(mcp):
    res = mcp.send_request({
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "trigger_refund",
            "arguments": {
                "customer_id": "CUST-12345",
                "amount": 25.0,
                "reason": "broken"
            }
        }
    })
    assert res.get("error", {}).get("code") == -32602
    assert "string_too_short" in res["error"]["message"]