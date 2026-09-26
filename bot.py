from __future__ import annotations

import time
import os
from typing import Any, Optional
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from engine.composer import compose as vera_compose
from engine.conversation import conversation_manager
from engine.models import CtxBody, ReplyBody, TickBody
from engine.store import store

app = FastAPI(title="VERA Merchant AI Assistant", version="1.0.0")


@app.get("/v1/healthz")
async def healthz():
    return {
        "status": "ok",
        "uptime_seconds": store.get_uptime_seconds(),
        "contexts_loaded": store.get_counts(),
    }


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": os.getenv("VERA_TEAM_NAME", "Team Vera AI"),
        "team_members": [x.strip() for x in os.getenv("VERA_TEAM_MEMBERS", "Ansh Kumar").split(",") if x.strip()],
        "model": os.getenv("VERA_MODEL", "deterministic-vera-v1"),
        "approach": "deterministic 4-context grounded composer + intent state machine",
        "contact_email": os.getenv("VERA_CONTACT_EMAIL", ""),
        "version": os.getenv("VERA_VERSION", "1.0.0"),
        "submitted_at": os.getenv("VERA_SUBMITTED_AT", ""),
    }


@app.post("/v1/context")
async def push_context(body: CtxBody):
    accepted, status_code, resp_data = store.push(
        scope=body.scope,
        context_id=body.context_id,
        version=body.version,
        payload=body.payload,
        delivered_at=body.delivered_at,
    )
    if status_code != 200:
        return JSONResponse(status_code=status_code, content=resp_data)
    return resp_data


@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []
    seen_convs = set()

    now_epoch = None
    if body.now:
        try:
            from datetime import datetime
            now_epoch = datetime.fromisoformat(body.now.replace("Z", "+00:00")).timestamp()
        except Exception:
            now_epoch = time.time()

    for trg_id in body.available_triggers:
        if len(actions) >= 20:
            break

        trg = store.get_trigger(trg_id)
        if not trg:
            continue

        # Check suppression
        suppression_key = trg.get("suppression_key", "")
        if suppression_key and store.is_suppressed(suppression_key, now_epoch):
            continue

        payload = trg.get("payload", {})
        merchant_id = trg.get("merchant_id") or payload.get("merchant_id")
        if not merchant_id:
            continue

        merchant = store.get_merchant(merchant_id)
        if not merchant:
            continue

        cat_slug = merchant.get("category_slug") or payload.get("category")
        category = store.get_category(cat_slug) if cat_slug else {}
        if not category:
            category = {"slug": cat_slug or "generic"}

        customer_id = trg.get("customer_id") or payload.get("customer_id")
        customer = store.get_customer(customer_id) if customer_id else None

        composed = vera_compose(category, merchant, trg, customer)
        conv_id = composed.get("conversation_id", "")
        if conv_id in seen_convs:
            continue
        seen_convs.add(conv_id)

        # Mark suppression
        if suppression_key:
            store.record_suppression(suppression_key, trg.get("expires_at"), now_epoch)

        actions.append(composed)

    return {"actions": actions}


@app.post("/v1/teardown")
async def teardown():
    """Optional judge teardown hook: wipe in-memory test state."""
    store.reset()
    conversation_manager.reset()
    return {"ok": True}


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    resp = conversation_manager.handle_reply(
        conversation_id=body.conversation_id,
        merchant_id=body.merchant_id,
        customer_id=body.customer_id,
        from_role=body.from_role,
        message=body.message,
        received_at=body.received_at,
        turn_number=body.turn_number,
    )
    return resp


def compose(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Official composition entry point (§7.1).
    """
    return vera_compose(category, merchant, trigger, customer)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=8080, log_level="info")
