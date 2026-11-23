"""Integration & security tests for Qdrant EN/AR runbooks and Docker Python sandbox."""

from __future__ import annotations

import docker

from mcp_servers.knowledge_sandbox_mcp.service import KnowledgeSandboxService


def test_qdrant_retrieves_expected_runbook_in_english_and_arabic() -> None:
    svc = KnowledgeSandboxService()
    count = svc.ensure_runbooks_indexed()
    assert count >= 8

    en_hits = svc.search_runbooks("auth-gateway 502 Redis connection pool", top_k=2)
    assert en_hits
    assert en_hits[0]["id"] == "rb-001"

    ar_hits = svc.search_runbooks("امتلاء اتصالات Redis في auth-gateway", top_k=2)
    assert ar_hits
    assert ar_hits[0]["id"] == "rb-001"


def test_docker_sandbox_runs_math_and_blocks_network_and_loops() -> None:
    svc = KnowledgeSandboxService()

    ok = svc.run_python_calc("print(round((4120 * 0.042), 2))")
    assert ok["exit_code"] == 0
    assert "173.04" in ok["stdout"]

    # Bare trailing expressions must auto-print like a REPL
    bare = svc.run_python_calc("450 + 680 / 2")
    assert bare["exit_code"] == 0
    assert bare["stdout"].strip() == "790.0"

    multiline_expr = svc.run_python_calc("x = 12\nx * 3 + 4")
    assert multiline_expr["exit_code"] == 0
    assert multiline_expr["stdout"].strip() == "40"

    # Network access must fail (--network=none)
    net = svc.run_python_calc(
        "import urllib.request; urllib.request.urlopen('http://1.1.1.1', timeout=2)"
    )
    assert net["exit_code"] != 0

    # Read-only rootfs / privilege escalation must fail
    ro = svc.run_python_calc("open('/tmp_root_escape.txt', 'w').write('x')")
    assert ro["exit_code"] != 0

    # Infinite loop must be killed at timeout
    loop_res = svc.run_python_calc("while True: pass", timeout_s=2)
    assert loop_res["timed_out"] is True

    # Ensure zero orphan sandbox containers remain
    leftover = [
        c.name
        for c in docker.from_env().containers.list(all=True)
        if c.name.startswith("sadaops-sbx-")
    ]
    assert leftover == []
