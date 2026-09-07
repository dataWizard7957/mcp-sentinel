import json
import pytest
from fastapi.testclient import TestClient
import httpx
from task_3_streaming_guardrail.stream_proxy import app, PIIStreamingBuffer

client = TestClient(app)


# ------------------------------------------------------------------
# Unit Tests for PIIStreamingBuffer
# ------------------------------------------------------------------

def test_01_buffer_redacts_split_ssn():
    """Verify SSN split across multiple delta chunks is redacted."""
    buf = PIIStreamingBuffer(safe_margin=12)
    
    # Token stream: "SSN is ", "123-45", "-6789", " please."
    chunks = ["SSN is ", "123-45", "-6789", " please."]
    output = ""
    for c in chunks:
        output += buf.process_delta(c)
    output += buf.flush()

    assert "123-45-6789" not in output
    assert "[REDACTED]" in output
    assert "SSN is [REDACTED] please." in output


def test_02_buffer_redacts_split_email():
    """Verify email address split across tokens is redacted."""
    buf = PIIStreamingBuffer(safe_margin=15)
    
    chunks = ["Reach me at ", "john.doe", "@comp", "any.org", " today."]
    output = ""
    for c in chunks:
        output += buf.process_delta(c)
    output += buf.flush()

    assert "john.doe@company.org" not in output
    assert "[REDACTED]" in output
    assert "Reach me at [REDACTED] today." in output


def test_03_buffer_redacts_split_credit_card():
    """Verify credit card number split across tokens is redacted."""
    buf = PIIStreamingBuffer(safe_margin=20)
    
    chunks = ["Card: ", "4532 ", "1122 ", "3344 ", "5566", "."]
    output = ""
    for c in chunks:
        output += buf.process_delta(c)
    output += buf.flush()

    assert "4532 1122 3344 5566" not in output
    assert "[REDACTED]" in output


def test_04_buffer_preserves_safe_text():
    """Verify clean text passes through without alteration."""
    buf = PIIStreamingBuffer(safe_margin=10)
    
    chunks = ["Hello ", "world, ", "this ", "is a ", "clean sentence."]
    output = ""
    for c in chunks:
        output += buf.process_delta(c)
    output += buf.flush()

    assert output == "Hello world, this is a clean sentence."


# ------------------------------------------------------------------
# Integration Test for FastAPI Streaming Endpoint
# ------------------------------------------------------------------

def test_05_stream_proxy_endpoint_integration(monkeypatch):
    """Simulate upstream LLM SSE streaming and verify output redaction."""
    raw_sse_events = [
        b'data: {"choices": [{"delta": {"content": "User email: "}}]}\n\n',
        b'data: {"choices": [{"delta": {"content": "admin@"}}]}\n\n',
        b'data: {"choices": [{"delta": {"content": "securecorp.com"}}]}\n\n',
        b'data: {"choices": [{"delta": {"content": " is on file."}}]}\n\n',
        b'data: [DONE]\n\n'
    ]

    async def mock_aiter_bytes(self):
        for event in raw_sse_events:
            yield event

    async def mock_send(self, request, **kwargs):
        resp = httpx.Response(status_code=200, headers={"content-type": "text/event-stream"})
        resp.aiter_bytes = mock_aiter_bytes.__get__(resp, httpx.Response)
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "send", mock_send)

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Get user info"}]
    }

    response = client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 200

    # Collect and parse text chunks from SSE stream
    full_text = ""
    for line in response.text.split("\n"):
        line = line.strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            data = json.loads(line[6:])
            full_text += data["choices"][0]["delta"].get("content", "")

    assert "admin@securecorp.com" not in full_text
    assert "[REDACTED]" in full_text
    assert "User email: [REDACTED] is on file." in full_text