"""Pluggable GitHub engineering adapter (Fixture default + Live REST API)."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from agent.safety.prompt_guard import resolve_connected_repo

_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def validate_repo_slug(repo: str, allowed_repos: frozenset[str] | None = None) -> str:
    """Validate GitHub owner/repo format and optional allowlist to block path traversal."""
    cleaned = (repo or "").strip()
    if not _REPO_SLUG_RE.match(cleaned) or ".." in cleaned:
        raise ValueError(f"Invalid GitHub repository slug: {repo!r}")
    if allowed_repos and cleaned not in allowed_repos:
        raise ValueError(
            f"Repository '{cleaned}' is outside allowed repos: {sorted(allowed_repos)}"
        )
    return cleaned


class CreateIssueInput(BaseModel):
    """Validated payload for creating a GitHub issue."""

    repo: str = Field(default="bejad21/sadaops-demo", min_length=3, max_length=120)
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=3, max_length=4000)
    labels: list[str] = Field(default_factory=lambda: ["ops-voice"])


class GitHubAdapter(Protocol):
    """Protocol implemented by fixture and live GitHub repository adapters."""

    async def list_repos(
        self, visibility: str = "all", limit: int = 30
    ) -> dict[str, Any]:
        """List accessible repositories with public/private summary counts."""
        ...

    async def list_open_prs(self, repo: str = "bejad21/sadaops-demo") -> list[dict[str, Any]]:
        """List open pull requests for a repository."""
        ...

    async def list_commits(
        self, repo: str = "bejad21/sadaops-demo", limit: int = 10, branch: str = ""
    ) -> list[dict[str, Any]]:
        """List recent commits for a repository and optional branch."""
        ...

    async def get_ci_logs(
        self, repo: str = "bejad21/sadaops-demo", run_id: int | None = None
    ) -> dict[str, Any]:
        """Retrieve GitHub Actions workflow runs and failing job/step diagnostics."""
        ...

    async def get_issue_details(self, repo: str, issue_number: int) -> dict[str, Any]:
        """Retrieve metadata and body for a single GitHub issue."""
        ...

    async def create_issue(self, payload: CreateIssueInput) -> dict[str, Any]:
        """Create a new GitHub issue in the target repository."""
        ...

    async def github_request(
        self, path: str, params: dict[str, Any] | None = None, limit: int = 20
    ) -> Any:
        """Execute a read-only GET request against an allowlisted GitHub REST API path."""
        ...


_NOISY_GITHUB_KEYS: frozenset[str] = frozenset(
    {
        "node_id",
        "gravatar_id",
        "followers_url",
        "following_url",
        "gists_url",
        "starred_url",
        "subscriptions_url",
        "organizations_url",
        "repos_url",
        "events_url",
        "received_events_url",
        "keys_url",
        "collaborators_url",
        "teams_url",
        "hooks_url",
        "issue_events_url",
        "assignees_url",
        "branches_url",
        "tags_url",
        "blobs_url",
        "git_tags_url",
        "git_refs_url",
        "trees_url",
        "statuses_url",
        "languages_url",
        "stargazers_url",
        "contributors_url",
        "subscribers_url",
        "subscription_url",
        "commits_url",
        "git_commits_url",
        "comments_url",
        "issue_comment_url",
        "contents_url",
        "compare_url",
        "merges_url",
        "archive_url",
        "downloads_url",
        "issues_url",
        "pulls_url",
        "milestones_url",
        "notifications_url",
        "labels_url",
        "releases_url",
        "deployments_url",
    }
)


def _validate_github_api_path(path: str) -> str:
    """Validate a relative GitHub REST path against traversal, SSRF, and repo allowlists."""
    cleaned = (path or "").strip()
    if (
        not cleaned
        or "://" in cleaned
        or ".." in cleaned
        or "@" in cleaned
        or any(ch.isspace() for ch in cleaned)
    ):
        raise ValueError(f"Invalid GitHub API path: {cleaned!r}")
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    parts = [p for p in cleaned.split("?", 1)[0].split("/") if p]
    if len(parts) >= 3 and parts[0].lower() == "repos":
        slug = validate_repo_slug(f"{parts[1]}/{parts[2]}")
        from agent.safety.prompt_guard import resolve_allowed_github_repos

        allowed_repos, allow_all = resolve_allowed_github_repos()
        if not allow_all and slug.lower() not in {r.lower() for r in allowed_repos}:
            raise ValueError(f"Repository '{slug}' is outside GITHUB_ALLOWED_REPOS.")
    return cleaned


def _compact_github_payload(data: Any, limit: int = 20, _depth: int = 0) -> Any:
    """Strip noisy hypermedia URLs and cap list lengths so GitHub JSON stays token-safe."""
    if _depth > 5:
        return "..."
    safe_limit = max(1, min(int(limit), 30))
    if isinstance(data, list):
        return [
            _compact_github_payload(item, limit=safe_limit, _depth=_depth + 1)
            for item in data[:safe_limit]
        ]
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            if k in _NOISY_GITHUB_KEYS:
                continue
            if k == "items" and isinstance(v, list):
                out[k] = _compact_github_payload(
                    v[:safe_limit], limit=safe_limit, _depth=_depth + 1
                )
            else:
                out[k] = _compact_github_payload(v, limit=safe_limit, _depth=_depth + 1)
        return out
    if isinstance(data, str) and len(data) > 1200:
        return data[:1200] + "...[truncated]"
    return data


class FixtureGitHubAdapter:
    """Realistic offline GitHub repository adapter for local dev & reproducible evals."""

    def __init__(self) -> None:
        self._prs: list[dict[str, Any]] = [
            {
                "number": 412,
                "repo": "bejad21/sadaops-demo",
                "title": "fix(auth-gateway): increase Redis pool size and add circuit breaker",
                "author": "tariq",
                "state": "open",
                "ci_status": "passing",
                "updated_at": "2026-11-19T08:10:00Z",
            },
            {
                "number": 415,
                "repo": "bejad21/sadaops-demo",
                "title": "feat(billing-worker): batch webhook retries with exponential jitter",
                "author": "sara",
                "state": "open",
                "ci_status": "failing",
                "updated_at": "2026-11-19T09:02:00Z",
            },
        ]
        self._ci_runs: dict[int, dict[str, Any]] = {
            8891: {
                "run_id": 8891,
                "pr_number": 415,
                "workflow": "ci-billing-worker",
                "status": "failed",
                "summary": "2 tests failed in tests/test_retry_queue.py",
                "log_excerpt": (
                    "FAILED tests/test_retry_queue.py::test_dead_letter_cap - "
                    "AssertionError: expected queue_depth <= 500, got 1540\n"
                    "Hint: Payment gateway timeout exceeded 3.5s threshold."
                ),
            }
        }
        self._issues: dict[int, dict[str, Any]] = {
            101: {
                "number": 101,
                "repo": "bejad21/sadaops-demo",
                "title": "auth-gateway 502 spikes during JWT key rotation",
                "state": "open",
                "author": "tariq",
                "body": "Seeing intermittent 502s when Redis pool hits max_connections=20.",
                "labels": ["sev1", "auth-gateway"],
            },
            102: {
                "number": 102,
                "repo": "bejad21/sadaops-demo",
                "title": "Qdrant upsert timeouts on 100-page PDFs",
                "state": "open",
                "author": "bejad",
                "body": "Batch size 256 triggers 10s HTTP timeout; batch size 64 resolves it.",
                "labels": ["sev3", "search-indexer"],
            },
        }
        self._next_issue_number = 103

    async def list_open_prs(self, repo: str = "bejad21/sadaops-demo") -> list[dict[str, Any]]:
        target = validate_repo_slug(resolve_connected_repo(repo))
        return [pr for pr in self._prs if pr["repo"] == target]

    async def get_ci_logs(
        self, repo: str = "bejad21/sadaops-demo", run_id: int | None = None
    ) -> dict[str, Any]:
        target = validate_repo_slug(resolve_connected_repo(repo))
        if target != "bejad21/sadaops-demo":
            return {"error": f"Repository '{target}' not seeded in fixture adapter."}
        target_id = run_id or 8891
        run = self._ci_runs.get(target_id)
        if not run:
            return {"error": f"CI run {target_id} not found in {target}"}
        return run

    async def get_issue_details(self, repo: str, issue_number: int) -> dict[str, Any]:
        target = validate_repo_slug(resolve_connected_repo(repo))
        if target != "bejad21/sadaops-demo":
            return {"error": f"Repository '{target}' not seeded in fixture adapter."}
        issue = self._issues.get(issue_number)
        if not issue:
            return {"error": f"Issue #{issue_number} not found in {target}"}
        return issue

    async def list_repos(self, visibility: str = "all", limit: int = 30) -> dict[str, Any]:
        return {
            "total_count": 1,
            "public_count": 1,
            "private_count": 0,
            "visibility": visibility,
            "repos": [
                {
                    "full_name": "bejad21/sadaops-demo",
                    "private": False,
                    "default_branch": "main",
                    "open_issues_count": 2,
                    "language": "Python",
                }
            ][:limit],
            "mode": "fixture",
        }

    async def list_commits(
        self, repo: str = "bejad21/sadaops-demo", limit: int = 10, branch: str = ""
    ) -> list[dict[str, Any]]:
        target = validate_repo_slug(resolve_connected_repo(repo))
        if target != "bejad21/sadaops-demo":
            return []
        return [
            {
                "sha": "a1b2c3d",
                "repo": target,
                "message": "fix(auth-gateway): increase Redis pool size",
                "author": "tariq",
                "date": "2026-11-19T08:05:00Z",
            },
            {
                "sha": "e4f5g6h",
                "repo": target,
                "message": "feat(billing-worker): batch webhook retries",
                "author": "sara",
                "date": "2026-11-19T07:45:00Z",
            },
        ][:limit]

    async def create_issue(self, payload: CreateIssueInput) -> dict[str, Any]:
        num = self._next_issue_number
        self._next_issue_number += 1
        created = {
            "number": num,
            "repo": payload.repo,
            "title": payload.title,
            "body": payload.body,
            "labels": payload.labels,
            "state": "open",
            "url": f"https://github.com/{payload.repo}/issues/{num}",
            "mode": "fixture",
        }
        self._issues[num] = created
        return created

    async def github_request(
        self, path: str, params: dict[str, Any] | None = None, limit: int = 20
    ) -> Any:
        """Simulate read-only GitHub REST requests against seeded fixture data."""
        cleaned = _validate_github_api_path(path).split("?", 1)[0]
        if cleaned == "/user/repos":
            return await self.list_repos(limit=limit)
        if cleaned.endswith("/pulls"):
            slug = "/".join(cleaned.strip("/").split("/")[1:3])
            return await self.list_open_prs(repo=slug)
        if cleaned.endswith("/commits"):
            slug = "/".join(cleaned.strip("/").split("/")[1:3])
            return await self.list_commits(repo=slug, limit=limit)
        if cleaned.endswith("/branches"):
            slug = "/".join(cleaned.strip("/").split("/")[1:3])
            if slug != "bejad21/sadaops-demo":
                return []
            return [
                {"name": "main", "protected": True},
                {"name": "fix/auth-pool", "protected": False},
            ][:limit]
        return {"path": cleaned, "params": params or {}, "items": [], "mode": "fixture"}


class LiveGitHubAdapter:
    """Real GitHub REST API adapter enabled when GITHUB_MODE=live and GITHUB_TOKEN is set."""

    def __init__(
        self,
        token: str,
        default_repo: str = "",
        client: httpx.AsyncClient | None = None,
        allowed_repos: frozenset[str] | None = None,
    ) -> None:
        self.token = token
        cleaned_default = (default_repo or "").strip()
        self.default_repo = validate_repo_slug(cleaned_default) if cleaned_default else ""
        self._external_client = client
        self._cached_repos: list[dict[str, Any]] | None = None
        raw_allowed = os.getenv("GITHUB_ALLOWED_REPOS", "").strip()
        if allowed_repos is not None:
            self.allowed_repos: frozenset[str] | None = allowed_repos
        elif raw_allowed in ("*", "all"):
            self.allowed_repos = None
        elif raw_allowed:
            self.allowed_repos = frozenset(r.strip() for r in raw_allowed.split(",") if r.strip())
        elif self.default_repo:
            self.allowed_repos = frozenset({self.default_repo})
        else:
            # No GITHUB_REPO pin configured -> allow any repo accessible by the PAT
            self.allowed_repos = None
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _resolve_repo(self, repo: str) -> str:
        resolved = resolve_connected_repo(repo, default_repo=self.default_repo or None)
        return validate_repo_slug(resolved, allowed_repos=self.allowed_repos)

    async def _resolve_repo_async(self, repo: str) -> str:
        cleaned = (repo or "").strip()
        if cleaned and "/" not in cleaned and not any(ch.isspace() for ch in cleaned):
            data = await self.list_repos(limit=100)
            needle = cleaned.lower()
            for item in data.get("repos", []):
                full = str(item.get("full_name", ""))
                short = full.split("/", 1)[-1].lower()
                if short == needle or needle in short:
                    return validate_repo_slug(full, allowed_repos=self.allowed_repos)
        if not cleaned and not self.default_repo:
            data = await self.list_repos(limit=5)
            repos = data.get("repos", [])
            if repos:
                return validate_repo_slug(
                    str(repos[0]["full_name"]), allowed_repos=self.allowed_repos
                )
        return self._resolve_repo(cleaned)

    @asynccontextmanager
    async def _client_session(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._external_client is not None:
            yield self._external_client
            return
        async with httpx.AsyncClient(timeout=12.0) as client:
            yield client

    async def list_repos(self, visibility: str = "all", limit: int = 30) -> dict[str, Any]:
        """List repositories accessible to the authenticated GitHub PAT."""
        safe_limit = max(1, min(int(limit), 100))
        vis = (visibility or "all").strip().lower()
        if vis not in ("all", "public", "private"):
            vis = "all"

        all_items: list[dict[str, Any]] = []
        async with self._client_session() as client:
            for page in range(1, 4):
                r = await client.get(
                    "https://api.github.com/user/repos",
                    headers=self._headers,
                    params={
                        "affiliation": "owner,collaborator,organization_member",
                        "sort": "updated",
                        "per_page": 100,
                        "page": page,
                    },
                )
                r.raise_for_status()
                batch = r.json()
                if not isinstance(batch, list) or not batch:
                    break
                all_items.extend(batch)
                if len(batch) < 100:
                    break

        public_count = sum(1 for item in all_items if not item.get("private"))
        private_count = sum(1 for item in all_items if item.get("private"))
        filtered = [
            item
            for item in all_items
            if vis == "all"
            or (vis == "public" and not item.get("private"))
            or (vis == "private" and item.get("private"))
        ]
        compact_repos = [
            {
                "full_name": item.get("full_name"),
                "private": bool(item.get("private")),
                "default_branch": item.get("default_branch"),
                "open_issues_count": item.get("open_issues_count", 0),
                "language": item.get("language") or "",
                "updated_at": item.get("updated_at"),
            }
            for item in filtered[:safe_limit]
        ]
        self._cached_repos = compact_repos
        return {
            "total_count": len(all_items),
            "public_count": public_count,
            "private_count": private_count,
            "visibility": vis,
            "repos": compact_repos,
            "mode": "live",
        }

    async def list_open_prs(self, repo: str = "") -> list[dict[str, Any]]:
        target = await self._resolve_repo_async(repo)
        async with self._client_session() as client:
            r = await client.get(
                f"https://api.github.com/repos/{target}/pulls?state=open&per_page=10",
                headers=self._headers,
            )
            r.raise_for_status()
            return [
                {
                    "number": pr["number"],
                    "repo": target,
                    "title": pr["title"],
                    "author": pr["user"]["login"],
                    "state": pr["state"],
                    "updated_at": pr["updated_at"],
                }
                for pr in r.json()
            ]

    async def list_commits(
        self, repo: str = "", limit: int = 10, branch: str = ""
    ) -> list[dict[str, Any]]:
        target = await self._resolve_repo_async(repo)
        safe_limit = max(1, min(int(limit), 30))
        params: dict[str, Any] = {"per_page": safe_limit}
        if branch.strip():
            params["sha"] = branch.strip()
        async with self._client_session() as client:
            r = await client.get(
                f"https://api.github.com/repos/{target}/commits",
                headers=self._headers,
                params=params,
            )
            r.raise_for_status()
            return [
                {
                    "sha": str(c.get("sha", ""))[:7],
                    "repo": target,
                    "message": str((c.get("commit") or {}).get("message", "")).splitlines()[0][
                        :90
                    ],
                    "author": ((c.get("commit") or {}).get("author") or {}).get("name", ""),
                    "date": ((c.get("commit") or {}).get("author") or {}).get("date", ""),
                }
                for c in r.json()
            ]

    async def get_ci_logs(self, repo: str = "", run_id: int | None = None) -> dict[str, Any]:
        target = await self._resolve_repo_async(repo)
        async with self._client_session() as client:
            if run_id is None:
                r = await client.get(
                    f"https://api.github.com/repos/{target}/actions/runs?per_page=5",
                    headers=self._headers,
                )
                r.raise_for_status()
                runs = r.json().get("workflow_runs", [])
                if not runs:
                    return {"repo": target, "runs": [], "message": "No workflow runs found."}
                latest = runs[0]
                summary_runs = [
                    {
                        "run_id": run["id"],
                        "workflow": run["name"],
                        "status": run.get("conclusion") or run.get("status"),
                        "branch": run.get("head_branch"),
                        "created_at": run.get("created_at"),
                    }
                    for run in runs
                ]
                failed_jobs: list[dict[str, Any]] = []
                failing_run = next(
                    (run for run in runs if run.get("conclusion") in ("failure", "timed_out")),
                    None,
                )
                if failing_run is not None:
                    jr = await client.get(
                        f"https://api.github.com/repos/{target}/actions/runs/{failing_run['id']}/jobs",
                        headers=self._headers,
                    )
                    if jr.status_code == 200:
                        for j in jr.json().get("jobs", []):
                            if j.get("conclusion") in ("failure", "timed_out"):
                                failed_jobs.append(
                                    {
                                        "run_id": failing_run["id"],
                                        "job": j["name"],
                                        "failed_steps": [
                                            s["name"]
                                            for s in j.get("steps", [])
                                            if s.get("conclusion") == "failure"
                                        ],
                                    }
                                )
                return {
                    "repo": target,
                    "run_id": latest["id"],
                    "workflow": latest["name"],
                    "status": latest.get("conclusion") or latest.get("status"),
                    "url": latest.get("html_url", ""),
                    "recent_runs": summary_runs,
                    "failed_jobs": failed_jobs,
                }
            r = await client.get(
                f"https://api.github.com/repos/{target}/actions/runs/{run_id}/jobs",
                headers=self._headers,
            )
            r.raise_for_status()
            jobs = r.json().get("jobs", [])
            return {
                "repo": target,
                "run_id": run_id,
                "jobs": [
                    {
                        "name": j["name"],
                        "status": j.get("conclusion") or j.get("status"),
                        "failed_steps": [
                            s["name"]
                            for s in j.get("steps", [])
                            if s.get("conclusion") == "failure"
                        ],
                    }
                    for j in jobs
                ],
            }

    async def get_issue_details(self, repo: str, issue_number: int) -> dict[str, Any]:
        target = await self._resolve_repo_async(repo)
        async with self._client_session() as client:
            r = await client.get(
                f"https://api.github.com/repos/{target}/issues/{issue_number}",
                headers=self._headers,
            )
            r.raise_for_status()
            d = r.json()
            return {
                "number": d["number"],
                "repo": target,
                "title": d["title"],
                "state": d["state"],
                "author": d["user"]["login"],
                "body": (d.get("body") or "")[:1500],
            }

    async def create_issue(self, payload: CreateIssueInput) -> dict[str, Any]:
        target = await self._resolve_repo_async(payload.repo)
        body_json = {"title": payload.title, "body": payload.body, "labels": payload.labels}
        async with self._client_session() as client:
            r = await client.post(
                f"https://api.github.com/repos/{target}/issues",
                headers=self._headers,
                json=body_json,
            )
            r.raise_for_status()
            d = r.json()
            return {
                "number": d["number"],
                "repo": target,
                "title": d["title"],
                "state": d["state"],
                "url": d["html_url"],
                "mode": "live",
            }

    async def github_request(
        self, path: str, params: dict[str, Any] | None = None, limit: int = 20
    ) -> Any:
        """Execute a read-only GET request against an allowlisted GitHub REST API path."""
        cleaned = _validate_github_api_path(path)
        safe_limit = max(1, min(int(limit), 30))
        query_params = dict(params or {})
        if "per_page" not in query_params and "?" not in cleaned:
            query_params["per_page"] = safe_limit
        async with self._client_session() as client:
            r = await client.get(
                f"https://api.github.com{cleaned}",
                headers=self._headers,
                params=query_params or None,
            )
            if r.status_code >= 400:
                return {
                    "error": f"GitHub API returned HTTP {r.status_code}",
                    "path": cleaned,
                    "details": r.text[:300],
                }
            return _compact_github_payload(r.json(), limit=safe_limit)


def build_github_adapter() -> GitHubAdapter:
    """Return LiveGitHubAdapter if GITHUB_MODE=live and token is set, else FixtureGitHubAdapter."""
    mode = os.getenv("GITHUB_MODE", "fixture").lower()
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPO", "").strip()
    if mode == "live" and token:
        return LiveGitHubAdapter(token=token, default_repo=repo)
    return FixtureGitHubAdapter()
