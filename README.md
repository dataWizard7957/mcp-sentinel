# 🛡️ MCP Sentinel
A python based gateway featuring an MCP tool server, an RBAC security proxy, a streaming PII redaction guardrail, and an SQLite-backed rate-limiting router.

---

## 📁 Repository Structure

```text
mcp-sentinel/
├── task_1_mcp_server/
│   ├── server.py              # STDIO MCP Server 
│   └── test_server.py         # STDIO isolation & schema tests
├── task_2_security_gateway/
│   ├── proxy.py               # HTTP/JSON-RPC Security Proxy & RBAC Gateway
│   └── test_proxy.py          # Authorization & tool filtering tests
├── task_3_streaming_guardrail/
│   ├── stream_proxy.py        # Real-time Streaming PII Guardrail
│   └── test_stream_proxy.py   # buffer tests
├── task_4_rate_limiting_router/
│   ├── router.py              # SQLite Rate-Limiting & Fallback Router
│   └── test_router.py         # Token limits and failover tests
├── requirements.txt           # dependencies
├── .gitignore           
└── README.md

```

---

## 🚀 Setup & Installation

### Prerequisites

* **Python**: `3.10+`
* **pip**: `23.0+`

### Install Dependencies

```bash
# Create and activate virtual environment
python -m venv venv

# On Windows:
.\venv\Scripts\Activate.ps1
# On Linux/macOS:
source venv/bin/activate

# Install requirements
pip install -r requirements.txt

```

---

## 🧪 Running the Test scripts

Run individual tests scripts:

```bash
python -m pytest task_1_mcp_server/test_server.py -v
python -m pytest task_2_security_gateway/test_proxy.py -v
python -m pytest task_3_streaming_guardrail/test_stream_proxy.py -v
python -m pytest task_4_rate_limiting_router/test_router.py -v

```

---

## 🛠️ Modules & Verification Guide

### Task 1: Runnable MCP Server (STDIO Transport)

Custom MCP server using the official MCP Python SDK with Pydantic validation and strict STDIO stream isolation (`stdout` reserved exclusively for JSON-RPC messages; all diagnostics directed to `stderr`).

* **Tools**:
* `get_customer_record`: Accepts `customer_id` formatted as `CUST-XXXXX` (5 digits).
* `trigger_refund`: Accepts `customer_id`, `amount` (`gt=0`), and `reason` (`min_length=10`).


* **Validation & Errors**: Malformed inputs return standard JSON-RPC `-32602` (`INVALID_PARAMS`).

#### Run Server:

```bash
python task_1_mcp_server/server.py

```

#### Verification Payload (`stdin`):

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_customer_record", "arguments": {"customer_id": "CUST-12345"}}}

```

---

### Task 2: MCP Security Gateway Proxy

FastAPI reverse proxy intercepting incoming MCP JSON-RPC payloads to enforce role-based access control (RBAC).

* `tools/list`: Forwarded downstream without modification.
* `tools/call` with `admin_*` prefix: Blocks callers without the `admin` role, returning JSON-RPC error `-32001` without contacting downstream.
* Non-admin tools: Forwarded downstream to `http://localhost:8001/mcp`.

#### Run Proxy:

```bash
python task_2_security_gateway/proxy.py

```

#### Verification via `curl`:

```bash
# 1. Unauthorized Call (Role: viewer -> Expect Error -32001)
curl -X POST [http://127.0.0.1:8000/mcp](http://127.0.0.1:8000/mcp) \
  -H "Authorization: Bearer eyJyb2xlIjogInZpZXdlciJ9" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "admin_reset_key", "arguments": {}}}'

# 2. Authorized Call (Role: admin -> Forwarded)
curl -X POST [http://127.0.0.1:8000/mcp](http://127.0.0.1:8000/mcp) \
  -H "Authorization: Bearer eyJyb2xlIjogImFkbWluIn0=" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "admin_reset_key", "arguments": {}}}'

```

---

### Task 3: LLM Gateway Streaming Guardrail (PII Redaction)

Streaming reverse proxy that intercepts Server-Sent Event (SSE) delta chunks and redacts sensitive patterns (Emails, SSNs, Credit Cards) in real time using an asynchronous sliding-window buffer (`SAFE_MARGIN = 64`).

* **Chunk Boundary Safety**: Matches and redacts patterns split across network packets or token boundaries.
* **Low Latency**: Emits safe prefixes progressively without accumulating the entire response in memory.

#### Run Streaming Proxy:

```bash
python task_3_streaming_guardrail/stream_proxy.py

```

#### Verification via `curl`:

```bash
curl -N -X POST [http://127.0.0.1:8000/v1/chat/completions](http://127.0.0.1:8000/v1/chat/completions) \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "My email is user@example.com"}], "stream": true}'

```

---

### Task 4: Rate-Limiting & Model Fallback Router

Resilient model router backed by an on-disk SQLite sliding-window log.

* **Rate Limiting**: Enforces token usage caps per tenant key over a sliding window; returns HTTP `429 Too Many Requests` on exhaustion.
* **Automatic Failover**: Routes to a backup secondary model provider (`http://localhost:8002`) if the primary model returns HTTP `429`, 5xx, or times out after **3000ms**.
* **Header Tracing**: Successful fallbacks include the response header `X-Provider-Used: fallback`.

#### Run Router:

```bash
python task_4_rate_limiting_router/router.py

```

#### Verification via `curl`:

```bash
curl -X POST [http://127.0.0.1:8000/v1/chat/completions](http://127.0.0.1:8000/v1/chat/completions) \
  -H "X-Client-Id: tenant_key_123" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Ping"}]}'

```

---

