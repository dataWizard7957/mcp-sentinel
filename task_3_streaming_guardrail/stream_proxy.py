import asyncio
import json
import logging
import re
from typing import AsyncGenerator, Dict, Any, List, Tuple, Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pii_gateway")

app = FastAPI(title="LLM Gateway Streaming PII Guardrail")

LLM_PROVIDER_URL = "http://localhost:8001/v1/chat/completions"

# ------------------------------------------------------------------
# Regex Patterns for PII Detection
# ------------------------------------------------------------------

PII_PATTERNS: List[Tuple[str, re.Pattern]] = [
    # Emails
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", re.IGNORECASE)),
    # US Social Security Numbers (SSN)
    ("SSN", re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b")),
    # Credit Card Numbers (13-19 digits with optional delimiters)
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
]

SAFE_MARGIN = 64  # Large enough to hold any partial PII token span


def redact_pii(text: str) -> str:
    """Scans text and replaces detected PII patterns with [REDACTED]."""
    for _, pattern in PII_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


# ------------------------------------------------------------------
# Streaming Buffer Engine
# ------------------------------------------------------------------

class PIIStreamingBuffer:
    """
    Manages a sliding window buffer across Server-Sent Event (SSE) delta chunks 
    to handle regex pattern matches spanning chunk boundaries.
    """
    def __init__(self, safe_margin: int = SAFE_MARGIN):
        self.buffer = ""
        self.safe_margin = safe_margin

    def process_delta(self, delta_text: str) -> str:
        self.buffer += delta_text
        self.buffer = redact_pii(self.buffer)

        if len(self.buffer) > self.safe_margin:
            emit_length = len(self.buffer) - self.safe_margin
            to_emit = self.buffer[:emit_length]
            self.buffer = self.buffer[emit_length:]
            return to_emit

        return ""

    def flush(self) -> str:
        final_text = redact_pii(self.buffer)
        self.buffer = ""
        return final_text


async def stream_guardrail_processor(
    upstream_response: httpx.Response,
    client: httpx.AsyncClient
) -> AsyncGenerator[bytes, None]:
    """
    Assembles raw byte chunks into complete SSE lines, redacts PII across 
    delta tokens, and yields valid SSE data frames.
    """
    pii_buffer = PIIStreamingBuffer()
    incomplete_line = ""

    try:
        async for raw_chunk in upstream_response.aiter_bytes():
            text_block = incomplete_line + raw_chunk.decode("utf-8", errors="replace")
            lines = text_block.split("\n")
            # Keep whatever partial line wasn't terminated by \n for the next chunk
            incomplete_line = lines.pop()

            for line in lines:
                line_str = line.strip()

                if not line_str or line_str.startswith(":"):
                    continue

                if line_str == "data: [DONE]":
                    remaining_text = pii_buffer.flush()
                    if remaining_text:
                        flush_payload = {
                            "choices": [{"delta": {"content": remaining_text}, "finish_reason": None}]
                        }
                        yield f"data: {json.dumps(flush_payload)}\n\n".encode("utf-8")
                    yield b"data: [DONE]\n\n"
                    return

                if line_str.startswith("data: "):
                    payload_raw = line_str[6:].strip()
                    try:
                        data_json = json.loads(payload_raw)
                        choices = data_json.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            delta_content = delta.get("content", "")

                            if delta_content:
                                safe_text = pii_buffer.process_delta(delta_content)
                                if safe_text:
                                    data_json["choices"][0]["delta"]["content"] = safe_text
                                    yield f"data: {json.dumps(data_json)}\n\n".encode("utf-8")
                            else:
                                # Frame has finish_reason or role without content
                                yield f"data: {json.dumps(data_json)}\n\n".encode("utf-8")
                        else:
                            yield f"{line_str}\n\n".encode("utf-8")

                    except json.JSONDecodeError:
                        yield f"{line_str}\n\n".encode("utf-8")

        # In case stream terminates without [DONE]
        final_text = pii_buffer.flush()
        if final_text:
            flush_payload = {
                "choices": [{"delta": {"content": final_text}, "finish_reason": None}]
            }
            yield f"data: {json.dumps(flush_payload)}\n\n".encode("utf-8")

    finally:
        await upstream_response.aclose()
        await client.aclose()


# ------------------------------------------------------------------
# LLM Proxy Endpoint
# ------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions_proxy(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    body["stream"] = True
    client = httpx.AsyncClient(timeout=30.0)

    try:
        req = client.build_request(
            "POST",
            LLM_PROVIDER_URL,
            json=body,
            headers={"Content-Type": "application/json"}
        )
        upstream_response = await client.send(req, stream=True)

        if upstream_response.status_code != 200:
            error_content = await upstream_response.aread()
            await client.aclose()
            return StreamingResponse(
                content=iter([error_content]),
                status_code=upstream_response.status_code,
                media_type="application/json"
            )

        return StreamingResponse(
            stream_guardrail_processor(upstream_response, client),
            media_type="text/event-stream"
        )

    except httpx.RequestError as exc:
        await client.aclose()
        logger.error(f"Downstream LLM connection failure: {exc}")
        raise HTTPException(status_code=502, detail="Upstream LLM Provider Unreachable")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)