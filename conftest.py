"""Root pytest fixtures isolating automated tests from live user OAuth tokens."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_pytest_from_live_accounts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Prevent pytest from hitting live user Google OAuth or GitHub PAT credentials."""
    monkeypatch.setenv("SADAOPS_GOOGLE_TOKEN_PATH", str(tmp_path / "pytest_unused_token.json"))
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "")
    monkeypatch.setenv("GITHUB_MODE", "fixture")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_REPO", "")
    monkeypatch.delenv("GITHUB_ALLOWED_REPOS", raising=False)
