import base64
import json
import logging
from typing import Any, Dict, Optional
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
import httpx
from pydantic import BaseModel

# Configure logging to stderr
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mcp_gateway")

DOWNSTREAM_MCP_URL = "http://localhost:8001/mcp"

app = FastAPI(title="MCP Security Gateway Proxy")


# ------------------------------------------------------------------
# JSON-RPC Data Models
# ------------------------------------------------------------------

class ToolCallParams(BaseModel):
    name: str
    arguments: Optional[Dict[str, Any]] = None


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Any] = None
    method: str
    params: Optional[Any] = None


# ------------------------------------------------------------------
# Auth Helper Functions
# ------------------------------------------------------------------

def extract_role_from_token(authorization_header: Optional[str]) -> Optional[str]:
    """
    Extracts the user role from a Bearer token.
    Decodes base64-encoded JSON payload:
    Example: Bearer eyJyb2xlIjogImFkbWluIn0= -> {"role": "admin"}
    """
    if not authorization_header or not authorization_header.startswith("Bearer "):
        return None

    token = authorization_header.split(" ", 1)[1].strip()

    try:
        decoded_bytes = base64.b64decode(token)
        payload = json.loads(decoded_bytes.decode("utf-8"))
        return payload.get("role")
    except Exception as e:
        logger.warning(f"Failed to decode auth token: {e}")
        return None


def create_jsonrpc_error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    """Helper to structure standard JSON-RPC 2.0 error responses."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": code,
            "message": message
        }
    }


# ------------------------------------------------------------------
# Proxy Endpoint
# ------------------------------------------------------------------

@app.post("/mcp")
async def mcp_proxy_gateway(request: Request):
    # 1. Inspect Authorization Header
    auth_header = request.headers.get("Authorization")
    role = extract_role_from_token(auth_header)

    # 2. Parse Incoming JSON-RPC Body
    try:
        body_bytes = await request.body()
        payload = json.loads(body_bytes.decode("utf-8"))
        rpc_req = JsonRpcRequest(**payload)
    except Exception as e:
        logger.error(f"Malformed JSON-RPC request: {e}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=create_jsonrpc_error(None, -32700, "Parse error: Invalid JSON")
        )

    # 3. Fine-Grained Authorization Logic
    if rpc_req.method == "tools/call":
        tool_name = None
        if isinstance(rpc_req.params, dict):
            tool_name = rpc_req.params.get("name")

        logger.info(f"Intercepted 'tools/call' for tool: '{tool_name}' by role: '{role}'")

        # Intercept unauthorized admin tool calls
        if tool_name and tool_name.startswith("admin_"):
            if role != "admin":
                logger.warning(f"UNAUTHORIZED access attempt to {tool_name} by role: {role}")
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content=create_jsonrpc_error(
                        req_id=rpc_req.id,
                        code=-32001,
                        message=f"Unauthorized Tool Call: '{tool_name}' requires 'admin' role privileges."
                    )
                )

    elif rpc_req.method == "tools/list":
        logger.info(f"Forwarding transparent 'tools/list' request for role: '{role}'")

    # 4. Transparent Forwarding to Downstream MCP Server
    try:
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)

        async with httpx.AsyncClient() as client:
            downstream_response = await client.post(
                DOWNSTREAM_MCP_URL,
                content=body_bytes,
                headers=headers,
                timeout=10.0
            )

        return Response(
            content=downstream_response.content,
            status_code=downstream_response.status_code,
            headers=dict(downstream_response.headers)
        )

    except httpx.RequestError as exc:
        logger.error(f"Downstream connection error: {exc}")
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=create_jsonrpc_error(rpc_req.id, -32603, "Internal error: Downstream MCP server unreachable")
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)