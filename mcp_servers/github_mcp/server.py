"""FastMCP server exposing GitHub repository & CI inspection tools."""

from __future__ import annotations

from typing import Any

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from mcp_servers.github_mcp.adapter import CreateIssueInput, build_github_adapter

load_dotenv()
mcp = MCPServer("sadaops-github")
_adapter = build_github_adapter()


@mcp.tool(
    annotations=ToolAnnotations(
        title="List GitHub Repositories",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def list_repos(visibility: str = "all", limit: int = 30) -> dict[str, Any]:
    """List repositories in the connected GitHub account with public/private/total counts."""
    return await _adapter.list_repos(visibility=visibility, limit=limit)


@mcp.tool(
    annotations=ToolAnnotations(
        title="List Repository Commits",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def list_commits(
    repo: str = "bejad21/sadaops-demo", limit: int = 10, branch: str = ""
) -> list[dict[str, Any]]:
    """List recent commits for a GitHub repository."""
    return await _adapter.list_commits(repo=repo, limit=limit, branch=branch)


@mcp.tool(
    annotations=ToolAnnotations(
        title="List Open Pull Requests",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def list_open_prs(repo: str = "bejad21/sadaops-demo") -> list[dict[str, Any]]:
    """List open pull requests and their CI status for a repository."""
    return await _adapter.list_open_prs(repo=repo)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get CI Run Logs",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def get_ci_logs(
    repo: str = "bejad21/sadaops-demo", run_id: int | None = None
) -> dict[str, Any]:
    """Fetch failing CI run details and log excerpts."""
    return await _adapter.get_ci_logs(repo=repo, run_id=run_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get GitHub Issue Details",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def get_issue_details(
    issue_number: int, repo: str = "bejad21/sadaops-demo"
) -> dict[str, Any]:
    """Retrieve details for a specific GitHub issue."""
    return await _adapter.get_issue_details(repo=repo, issue_number=issue_number)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create GitHub Issue",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def create_issue(
    title: str, body: str, repo: str = "bejad21/sadaops-demo", labels: list[str] | None = None
) -> dict[str, Any]:
    """Create a GitHub issue (write action — gated by HITL when invoked from voice agent)."""
    payload = CreateIssueInput(repo=repo, title=title, body=body, labels=labels or ["ops-voice"])
    return await _adapter.create_issue(payload)


@mcp.tool(
    annotations=ToolAnnotations(
        title="GitHub REST Read Request",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def github_request(
    path: str, params: dict[str, Any] | None = None, limit: int = 20
) -> Any:
    """Execute a read-only GET request against any GitHub REST API path."""
    return await _adapter.github_request(path=path, params=params, limit=limit)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    mcp.run()
