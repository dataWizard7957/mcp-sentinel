import sys
import re
from typing import Any
from pydantic import BaseModel, Field, field_validator
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS

# Initialize MCPServer instance
mcp = MCPServer("Customer Operations Server")


# ------------------------------------------------------------------
# Layer 2: Pydantic Input Validation Models
# ------------------------------------------------------------------

class GetCustomerRecordInput(BaseModel):
    customer_id: str = Field(
        ...,
        description="Customer ID strictly formatted as CUST-XXXXX",
        examples=["CUST-12345"]
    )

    @field_validator("customer_id")
    @classmethod
    def validate_customer_id(cls, v: str) -> str:
        if not re.match(r"^CUST-\d{5}$", v):
            raise ValueError("customer_id must strictly match format CUST-XXXXX (5 digits)")
        return v


class TriggerRefundInput(BaseModel):
    customer_id: str = Field(
        ...,
        description="Customer ID strictly formatted as CUST-XXXXX"
    )
    amount: float = Field(
        ...,
        gt=0,
        description="Refund amount in USD (must be positive)"
    )
    reason: str = Field(
        ...,
        min_length=10,
        description="Detailed reason for refund (minimum 10 characters)"
    )

    @field_validator("customer_id")
    @classmethod
    def validate_customer_id(cls, v: str) -> str:
        if not re.match(r"^CUST-\d{5}$", v):
            raise ValueError("customer_id must strictly match format CUST-XXXXX (5 digits)")
        return v


# ------------------------------------------------------------------
# Layer 3: Tool Definitions & Layer 1: Protocol Error Mapping
# ------------------------------------------------------------------

@mcp.tool()
def get_customer_record(customer_id: str) -> str:
    """Fetch profile and account standing for a customer by CUST-XXXXX ID."""
    sys.stderr.write(f"[INFO] Processing get_customer_record for ID: {customer_id}\n")

    try:
        validated = GetCustomerRecordInput(customer_id=customer_id)
    except Exception as err:
        # MCP 2.x MCPError signature takes (code, message)
        raise MCPError(
            code=INVALID_PARAMS,
            message=f"Invalid parameter format: {str(err)}"
        )

    return f"Record for {validated.customer_id}: Status=ACTIVE, Tier=GOLD, RiskScore=LOW"


@mcp.tool()
def trigger_refund(customer_id: str, amount: float, reason: str) -> str:
    """Issue a partial or full refund for a valid customer."""
    sys.stderr.write(f"[INFO] Processing refund trigger: {customer_id}, ${amount}\n")

    try:
        validated = TriggerRefundInput(
            customer_id=customer_id,
            amount=amount,
            reason=reason
        )
    except Exception as err:
        # MCP 2.x MCPError signature takes (code, message)
        raise MCPError(
            code=INVALID_PARAMS,
            message=f"Invalid parameter format: {str(err)}"
        )

    return (
        f"Refund SUCCESS: Transferred ${validated.amount:.2f} "
        f"to {validated.customer_id}. Reason logged: '{validated.reason}'"
    )


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")