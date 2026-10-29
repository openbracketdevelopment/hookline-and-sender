import json
import threading
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request

from backend.app.config import get_settings
from backend.app.security import verify_request

app = FastAPI(title="Hook, Line & Sender Demo Receiver")
_lock = threading.Lock()
_attempts: dict[str, int] = {}
_processed: set[str] = set()


def _controls_enabled() -> bool:
    return get_settings().app_env == "development"


@app.post("/webhooks")
async def receive_webhook(
    request: Request,
    timestamp: Annotated[str | None, Header(alias="X-Hookline-Timestamp")] = None,
    signature: Annotated[str | None, Header(alias="X-Hookline-Signature")] = None,
    event_id: Annotated[str | None, Header(alias="X-Hookline-Event-Id")] = None,
    delivery_id: Annotated[str | None, Header(alias="X-Hookline-Delivery-Id")] = None,
) -> dict[str, Any]:
    raw_body = await request.body()
    settings = get_settings()
    if (
        not timestamp
        or not signature
        or not verify_request(
            settings.demo_signing_secret.get_secret_value(),
            timestamp,
            signature,
            raw_body,
            tolerance_seconds=settings.signature_tolerance_seconds,
        )
    ):
        raise HTTPException(status_code=401, detail="Invalid request signature")
    if not event_id or not delivery_id:
        raise HTTPException(status_code=400, detail="Missing stable delivery headers")
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("delivery_id") != delivery_id
        or not isinstance(payload.get("event"), dict)
        or payload["event"].get("id") != event_id
    ):
        raise HTTPException(status_code=400, detail="Request identifiers do not match")

    with _lock:
        if delivery_id in _processed:
            return {"status": "success", "duplicate": True}
        attempt = _attempts.get(delivery_id, 0) + 1
        _attempts[delivery_id] = attempt
        if attempt < 3:
            raise HTTPException(
                status_code=503,
                detail=f"Simulated temporary failure on attempt {attempt}",
            )
        _processed.add(delivery_id)
        return {"status": "success", "duplicate": False, "attempt": attempt}


@app.get("/demo/state")
def demo_state() -> dict[str, object]:
    if not _controls_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    with _lock:
        return {
            "attempts": dict(_attempts),
            "processed_delivery_ids": sorted(_processed),
        }


@app.post("/demo/reset")
def reset_demo() -> dict[str, str]:
    if not _controls_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    with _lock:
        _attempts.clear()
        _processed.clear()
    return {"status": "reset"}
