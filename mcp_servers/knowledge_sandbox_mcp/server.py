"""FastMCP server exposing Qdrant Runbooks RAG and Docker Python Sandbox."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from mcp_servers.knowledge_sandbox_mcp.service import KnowledgeSandboxService

mcp = MCPServer("sadaops-knowledge-sandbox")
_service = KnowledgeSandboxService()


@mcp.tool(
    annotations=ToolAnnotations(
        title="Search Incident Runbooks",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def search_runbooks(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """Search engineering incident runbooks (supports English and Arabic queries)."""
    return _service.search_runbooks(query=query, top_k=top_k)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Run Isolated Python Calculation",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def run_python_calc(code: str, timeout_s: int = 5) -> dict[str, Any]:
    """Run deterministic Python calculations in an isolated networkless Docker sandbox."""
    return _service.run_python_calc(code=code, timeout_s=timeout_s)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    mcp.run()
