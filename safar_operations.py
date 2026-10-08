"""App operations reuse journal identities; this module never creates a TTN."""
from __future__ import annotations

import re
import time

from np_client import NovaPoshtaTemporaryError


def sender_summary(pipe, registry, chat_id, owner_id):
    """Configuration readiness is deliberately distinct from carrier verification."""
    labels = registry.available() if registry is not None else {
        profile: ("Основний кабінет НП" if profile == "default" else profile)
        for profile in pipe.sender_clients
    }
    selected = pipe.sender_preference(chat_id, owner_id)
    primary = pipe.default_sender_profile
    profiles = [{
        "id": profile, "label": str(label)[:60],
        "configured": profile in pipe.sender_clients,
        "selected": profile == selected, "primary": profile == primary,
    } for profile, label in labels.items()]
    return {"profiles": profiles, "selected": selected, "primary": primary,
            "fop_pending": "fop" not in labels}


def select_sender(pipe, registry, chat_id, owner_id, profile_id):
    if not isinstance(profile_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", profile_id):
        raise ValueError("Invalid sender profile")
    labels = registry.available() if registry is not None else pipe.sender_clients
    if profile_id not in labels or profile_id not in pipe.sender_clients:
        raise ValueError("Sender profile unavailable")
    pipe.set_sender_preference(chat_id, owner_id, profile_id)
    return sender_summary(pipe, registry, chat_id, owner_id)


def tracking(pipe, job, *, force=False, clock=time.time):
    """Read the exact historical account and number, with a bounded private cache.

    A cached tracking result never changes jobs, receipt state or deletion guards.
    Those guards independently consult the existing carrier pipeline on /retry.
    """
    number = str((job.get("result") or {}).get("ttn") or "")
    if not re.fullmatch(r"\d{14}", number):
        raise ValueError("No issued shipment number")
    profile = job.get("sender_profile") or "default"
    cache_key = (job["chat_id"], job["owner_id"], job["key"], profile, number)
    now = clock()
    with pipe.lock:
        cache = getattr(pipe, "_safar_tracking_cache", None)
        if not isinstance(cache, dict):
            cache = pipe._safar_tracking_cache = {}
        cached = cache.get(cache_key)
        if cached and now - cached["checked_at"] < 60:
            # The refresh button also respects the TTL to avoid carrier hammering.
            return {**cached, "cached": True}
    client = pipe._sender_client(profile)
    row = client.get_ttn_status(number, phone=(job.get("order") or {}).get("phone", ""))
    if not isinstance(row, dict) or str(row.get("Number") or "") != number:
        raise NovaPoshtaTemporaryError("Shipment status unavailable")
    code = str(row.get("StatusCode") or "").strip()
    if not code.isdigit() or code == "0":
        raise NovaPoshtaTemporaryError("Shipment status unavailable")
    delivered = code in {"9", "10", "11"}
    phase = ("delivered" if delivered else "deleted" if code == "2" else
             "issued" if code == "1" else "unknown" if code == "3" else
             "ready_for_pickup" if code in {"7", "8"} else
             "in_transit" if code in {"4", "5", "6"} else "carrier_status")
    result = {
        "ttn": number, "status_code": code,
        "status": str(row.get("Status") or "Статус не вказано")[:180],
        "phase": phase, "delivered": delivered, "deleted": code == "2",
        "checked_at": now, "cached": False, "sender_profile": profile,
        "received_at": str(row.get("DateReceived") or "")[:40] if delivered else "",
        "estimated_delivery_at": str(row.get("ScheduledDeliveryDate") or "")[:40]
        if code not in {"2", "3"} else "",
    }
    with pipe.lock:
        if len(cache) >= 256:
            cache.pop(min(cache, key=lambda key: cache[key]["checked_at"]))
        cache[cache_key] = result
    return result
