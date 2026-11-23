"""FastAPI control-plane integration tests for HITL endpoints (Postgres + Mailpit)."""

from __future__ import annotations

import uuid

import httpx
import psycopg
from fastapi.testclient import TestClient

from api.app import app, settings


def _cleanup_actions(action_ids: list[str]) -> None:
    if not action_ids:
        return
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM pending_actions WHERE action_id = ANY(%s)",
                (action_ids,),
            )
            conn.commit()


def test_hitl_propose_approve_delivers_mail_and_blocks_replay() -> None:
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    created_ids: list[str] = []
    try:
        prop = client.post(
            "/api/hitl/propose",
            json={
                "session_id": f"sess-{tag}",
                "tool_name": "send_email",
                "payload": {
                    "to": "ops-leads@example.com",
                    "subject": f"HITL Approve {tag}",
                    "body": "Locked summary payload.",
                },
            },
        )
        assert prop.status_code == 200, prop.text
        action = prop.json()
        created_ids.append(action["action_id"])
        assert action["status"] == "pending"

        # Tampered hash must return 400
        tampered = client.post(
            "/api/hitl/approve",
            json={"action_id": action["action_id"], "payload_hash": "0" * 64},
        )
        assert tampered.status_code == 400

        # Real locked approval executes once and sends email to Mailpit
        ok = client.post(
            "/api/hitl/approve",
            json={"action_id": action["action_id"], "payload_hash": action["payload_hash"]},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "executed"

        mail_resp = httpx.get(
            f"{settings.mailpit_api_url}/api/v1/search?query={tag}",
            timeout=5.0,
        )
        assert mail_resp.status_code == 200
        assert any(tag in (m.get("Subject") or "") for m in mail_resp.json().get("messages", []))

        # Replaying approve on already-executed action must return 409
        replay = client.post(
            "/api/hitl/approve",
            json={"action_id": action["action_id"], "payload_hash": action["payload_hash"]},
        )
        assert replay.status_code == 409
    finally:
        _cleanup_actions(created_ids)


def test_hitl_reject_prevents_delivery_and_blocks_later_approve() -> None:
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    created_ids: list[str] = []
    try:
        prop = client.post(
            "/api/hitl/propose",
            json={
                "session_id": f"sess-{tag}",
                "tool_name": "send_email",
                "payload": {
                    "to": "ops-leads@example.com",
                    "subject": f"HITL Reject {tag}",
                    "body": "Should never be sent.",
                },
            },
        )
        assert prop.status_code == 200
        action = prop.json()
        created_ids.append(action["action_id"])

        rej = client.post("/api/hitl/reject", json={"action_id": action["action_id"]})
        assert rej.status_code == 200
        assert rej.json()["status"] == "rejected"

        # Approving a rejected action must return 409
        late_approve = client.post(
            "/api/hitl/approve",
            json={"action_id": action["action_id"], "payload_hash": action["payload_hash"]},
        )
        assert late_approve.status_code == 409

        mail_resp = httpx.get(
            f"{settings.mailpit_api_url}/api/v1/search?query={tag}",
            timeout=5.0,
        )
        assert mail_resp.status_code == 200
        assert mail_resp.json().get("messages", []) == []
    finally:
        _cleanup_actions(created_ids)


def test_hitl_unknown_expired_and_policy_violation_errors() -> None:
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    created_ids: list[str] = []
    try:
        # Unknown action_id -> 404
        assert (
            client.post(
                "/api/hitl/approve",
                json={"action_id": "act-missing", "payload_hash": "a" * 64},
            ).status_code
            == 404
        )
        assert client.post("/api/hitl/reject", json={"action_id": "act-missing"}).status_code == 404

        # Policy violation -> 400
        bad = client.post(
            "/api/hitl/propose",
            json={
                "session_id": f"sess-{tag}",
                "tool_name": "send_email",
                "payload": {
                    "to": "attacker@evil.com",
                    "subject": "Exfil",
                    "body": "Nope",
                },
            },
        )
        assert bad.status_code == 400

        # Expired TTL -> 400
        exp = client.post(
            "/api/hitl/propose",
            json={
                "session_id": f"sess-{tag}",
                "tool_name": "send_email",
                "ttl_seconds": 0,
                "payload": {
                    "to": "ops-leads@example.com",
                    "subject": f"Expired {tag}",
                    "body": "Expired TTL test.",
                },
            },
        )
        assert exp.status_code == 200
        exp_act = exp.json()
        created_ids.append(exp_act["action_id"])

        res = client.post(
            "/api/hitl/approve",
            json={"action_id": exp_act["action_id"], "payload_hash": exp_act["payload_hash"]},
        )
        assert res.status_code == 400
    finally:
        _cleanup_actions(created_ids)


def test_hitl_inline_edit_relocks_hash_enforces_policy_and_executes() -> None:
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    created_ids: list[str] = []
    try:
        prop = client.post(
            "/api/hitl/propose",
            json={
                "session_id": f"sess-{tag}",
                "tool_name": "send_email",
                "payload": {
                    "to": "wrong@example.com",
                    "subject": f"Initial Subject {tag}",
                    "body": "Initial body",
                },
            },
        )
        assert prop.status_code == 200, prop.text
        action = prop.json()
        created_ids.append(action["action_id"])
        old_hash = action["payload_hash"]

        # Disallowed edit domain must fail policy validation without changing lock
        bad_edit = client.post(
            "/api/hitl/update",
            json={
                "action_id": action["action_id"],
                "payload": {
                    "to": "attacker@evil.com",
                    "subject": "Exfil",
                    "body": "Blocked",
                },
            },
        )
        assert bad_edit.status_code == 400

        # Valid inline edit updates payload & mints a brand-new SHA-256 hash
        ok_edit = client.post(
            "/api/hitl/update",
            json={
                "action_id": action["action_id"],
                "payload": {
                    "to": "ops-leads@example.com",
                    "subject": f"Corrected Subject {tag}",
                    "body": "Corrected operator summary.",
                },
            },
        )
        assert ok_edit.status_code == 200, ok_edit.text
        updated = ok_edit.json()
        new_hash = updated["payload_hash"]
        assert new_hash != old_hash
        assert updated["payload"]["to"] == "ops-leads@example.com"

        # Stale hash before edit must be rejected cleanly
        stale = client.post(
            "/api/hitl/approve",
            json={"action_id": action["action_id"], "payload_hash": old_hash},
        )
        assert stale.status_code == 400

        # Approving with the newly locked hash executes with edited values
        ok = client.post(
            "/api/hitl/approve",
            json={"action_id": action["action_id"], "payload_hash": new_hash},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "executed"

        mail_resp = httpx.get(
            f"{settings.mailpit_api_url}/api/v1/search?query={tag}",
            timeout=5.0,
        )
        assert mail_resp.status_code == 200
        msgs = mail_resp.json().get("messages", [])
        assert len(msgs) == 1
        assert f"Corrected Subject {tag}" in msgs[0]["Subject"]
        assert msgs[0]["To"][0]["Address"] == "ops-leads@example.com"

        # Post-execution edit must return 409 conflict
        post_edit = client.post(
            "/api/hitl/update",
            json={
                "action_id": action["action_id"],
                "payload": {
                    "to": "ops-leads@example.com",
                    "subject": "Too late",
                    "body": "Too late",
                },
            },
        )
        assert post_edit.status_code == 409
    finally:
        _cleanup_actions(created_ids)
