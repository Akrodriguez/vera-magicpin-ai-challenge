from __future__ import annotations

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from bot import app, compose
from engine.store import store

client = TestClient(app)
EXPANDED_DIR = Path(__file__).resolve().parent.parent / "dataset" / "expanded"


def test_healthz():
    resp = client.get("/v1/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "contexts_loaded" in data
    assert isinstance(data["contexts_loaded"], dict)


def test_metadata():
    resp = client.get("/v1/metadata")
    assert resp.status_code == 200
    data = resp.json()
    assert "team_name" in data
    assert "model" in data
    assert "version" in data
    assert "approach" in data
    assert "submitted_at" in data


def test_context_push_and_versioning():
    # Push version 1
    cat_payload = {
        "slug": "dentists",
        "display_name": "Dentists",
        "voice": {"tone": "peer_clinical"},
        "offer_catalog": [{"title": "Dental Cleaning @ ₹299"}],
    }
    resp1 = client.post(
        "/v1/context",
        json={
            "scope": "category",
            "context_id": "test_dentists",
            "version": 1,
            "payload": cat_payload,
            "delivered_at": "2026-04-26T10:00:00Z",
        },
    )
    assert resp1.status_code == 200
    assert resp1.json()["accepted"] is True

    # Push same version 1 again -> idempotent 200 accepted
    resp2 = client.post(
        "/v1/context",
        json={
            "scope": "category",
            "context_id": "test_dentists",
            "version": 1,
            "payload": cat_payload,
            "delivered_at": "2026-04-26T10:00:00Z",
        },
    )
    assert resp2.status_code == 200
    assert resp2.json()["accepted"] is True

    # Push lower version 0 -> 409 conflict
    resp3 = client.post(
        "/v1/context",
        json={
            "scope": "category",
            "context_id": "test_dentists",
            "version": 0,
            "payload": cat_payload,
            "delivered_at": "2026-04-26T10:00:00Z",
        },
    )
    assert resp3.status_code == 409

    # Push higher version 2 -> 200 accepted
    resp4 = client.post(
        "/v1/context",
        json={
            "scope": "category",
            "context_id": "test_dentists",
            "version": 2,
            "payload": cat_payload,
            "delivered_at": "2026-04-26T10:05:00Z",
        },
    )
    assert resp4.status_code == 200
    assert resp4.json()["accepted"] is True

    # Invalid scope -> 400
    resp5 = client.post(
        "/v1/context",
        json={
            "scope": "invalid_scope",
            "context_id": "test_item",
            "version": 1,
            "payload": {},
            "delivered_at": "2026-04-26T10:00:00Z",
        },
    )
    assert resp5.status_code == 400
    assert resp5.json()["accepted"] is False


def test_tick_and_suppression():
    # Push merchant and trigger
    merchant_payload = {
        "merchant_id": "m_test_tick_001",
        "category_slug": "dentists",
        "identity": {"name": "Dr. Test's Clinic", "owner_first_name": "Test", "locality": "Saket", "city": "Delhi"},
        "offers": [{"title": "Dental Cleaning @ ₹299", "status": "active"}],
        "performance": {"views": 1500, "calls": 20, "ctr": 0.025},
    }
    client.post(
        "/v1/context",
        json={
            "scope": "merchant",
            "context_id": "m_test_tick_001",
            "version": 1,
            "payload": merchant_payload,
            "delivered_at": "2026-04-26T10:00:00Z",
        },
    )

    trg_payload = {
        "id": "trg_test_tick_001",
        "scope": "merchant",
        "kind": "curious_ask_due",
        "merchant_id": "m_test_tick_001",
        "payload": {},
        "urgency": 1,
        "suppression_key": "curious:test:001",
        "expires_at": "2026-05-01T00:00:00Z",
    }
    client.post(
        "/v1/context",
        json={
            "scope": "trigger",
            "context_id": "trg_test_tick_001",
            "version": 1,
            "payload": trg_payload,
            "delivered_at": "2026-04-26T10:00:00Z",
        },
    )

    # First tick -> produces action
    resp = client.post(
        "/v1/tick",
        json={"now": "2026-04-26T10:30:00Z", "available_triggers": ["trg_test_tick_001"]},
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    action = actions[0]
    assert action["merchant_id"] == "m_test_tick_001"
    assert "curious:test:001" == action["suppression_key"]
    assert len(action["body"]) > 10

    # Second tick immediately -> should be suppressed
    resp2 = client.post(
        "/v1/tick",
        json={"now": "2026-04-26T10:31:00Z", "available_triggers": ["trg_test_tick_001"]},
    )
    assert resp2.status_code == 200
    assert len(resp2.json()["actions"]) == 0


def test_reply_auto_reply_detection():
    auto_msg = "Thank you for contacting us! Our team will respond shortly."
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_test_auto_1",
            "merchant_id": "m_test",
            "from_role": "merchant",
            "message": auto_msg,
            "received_at": "2026-04-26T10:45:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "end"


def test_reply_intent_transition():
    commit_msg = "Ok lets do it. Whats next?"
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_test_intent_1",
            "merchant_id": "m_test",
            "from_role": "merchant",
            "message": commit_msg,
            "received_at": "2026-04-26T10:45:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "send"
    body_lower = data["body"].lower()

    actioning = ["done", "sending", "draft", "here", "confirm", "proceed", "next"]
    qualifying = ["would you", "do you", "can you tell", "what if", "how about"]

    assert any(w in body_lower for w in actioning), f"Expected action words in {body_lower}"
    assert not any(w in body_lower for w in qualifying), f"Unexpected qualifying words in {body_lower}"


def test_reply_hostile_exit():
    hostile_msg = "Stop messaging me. This is useless spam."
    resp = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_test_hostile_1",
            "merchant_id": "m_test",
            "from_role": "merchant",
            "message": hostile_msg,
            "received_at": "2026-04-26T10:45:00Z",
            "turn_number": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "end"


def test_30_canonical_test_pairs():
    test_pairs_path = EXPANDED_DIR / "test_pairs.json"
    assert test_pairs_path.exists(), "test_pairs.json must exist in expanded dataset"

    with open(test_pairs_path, encoding="utf-8") as f:
        pairs = json.load(f)["pairs"]

    assert len(pairs) == 30

    for pair in pairs:
        tid = pair["trigger_id"]
        mid = pair["merchant_id"]
        cid = pair.get("customer_id")

        trigger = store.get_trigger(tid)
        assert trigger is not None, f"Trigger {tid} not found"

        merchant = store.get_merchant(mid)
        assert merchant is not None, f"Merchant {mid} not found"

        cat_slug = merchant.get("category_slug", "generic")
        category = store.get_category(cat_slug) or {"slug": cat_slug}

        customer = store.get_customer(cid) if cid else None

        result = compose(category, merchant, trigger, customer)
        assert "body" in result
        assert len(result["body"]) > 20
        assert "cta" in result
        assert "suppression_key" in result
        assert "rationale" in result
        assert "send_as" in result

        # Check no forbidden substrings
        body = result["body"]
        for forbidden in ["None", "null", "undefined", "NaN"]:
            assert forbidden not in body, f"Found '{forbidden}' in body: {body}"


def test_deterministic_composition():
    test_pairs_path = EXPANDED_DIR / "test_pairs.json"
    with open(test_pairs_path, encoding="utf-8") as f:
        pairs = json.load(f)["pairs"][:5]

    for pair in pairs:
        tid = pair["trigger_id"]
        mid = pair["merchant_id"]
        cid = pair.get("customer_id")

        trigger = store.get_trigger(tid)
        merchant = store.get_merchant(mid)
        cat_slug = merchant.get("category_slug", "generic")
        category = store.get_category(cat_slug) or {"slug": cat_slug}
        customer = store.get_customer(cid) if cid else None

        out1 = compose(category, merchant, trigger, customer)
        out2 = compose(category, merchant, trigger, customer)

        assert out1["body"] == out2["body"]
        assert out1["cta"] == out2["cta"]
        assert out1["suppression_key"] == out2["suppression_key"]
        assert out1["rationale"] == out2["rationale"]
