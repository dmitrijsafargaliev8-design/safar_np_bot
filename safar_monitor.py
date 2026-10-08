"""SAFAR server-side carrier observation: read-only and fail closed.

A trusted scheduler MAY invoke an explicitly enabled internal route. No
request here creates, cancels or redirects a Nova Poshta shipment. All carrier
status observations retain the original sender profile and immutable TTN.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
import uuid

from safar_operations import tracking

logger = logging.getLogger(__name__)
INTERVAL_SECONDS = 15 * 60
MAX_BATCH = 4


def ready(pipe):
    if pipe.db.backend == "sqlite":
        return True
    result = pipe.db.execute(
        "SELECT to_regclass('safar_orders.tracking_snapshots') AS relation"
    ).fetchone()
    return bool(result and result["relation"])


def _eligible(job):
    receipt = job.get("result") or {}
    number = receipt.get("ttn") if isinstance(receipt, dict) else ""
    return (job.get("state") == "created"
            and isinstance(number, str)
            and re.fullmatch(r"\d{14}", number)
            and isinstance(job.get("sender_profile") or "default", str))


def monitor_pass(pipe, *, batch=MAX_BATCH, clock=time.time):
    """Check up to four oldest eligible carrier observations.

    Uses a durable attempt timestamp so network failures do not starve all
    later orders. A status change creates one private audit event, but NEVER
    automatically creates a return case, TTN or money transaction.
    """
    if type(batch) is not int or not 1 <= batch <= MAX_BATCH:
        raise ValueError("Invalid bounded monitor batch")
    now = float(clock())
    if not math.isfinite(now) or now <= 0:
        raise ValueError("Invalid monitoring clock")
    if not ready(pipe):
        raise RuntimeError("Private tracking migration not installed")

    with pipe.lock:
        candidates = pipe.db.execute(
            "SELECT j.body FROM jobs j "
            "LEFT JOIN tracking_snapshots s ON s.order_key=j.key "
            "WHERE j.state='created' AND "
            "(s.attempted_at IS NULL OR s.attempted_at<=?) "
            "ORDER BY COALESCE(s.attempted_at,0), j.updated DESC LIMIT ?",
            (now - INTERVAL_SECONDS, batch * 6),
        ).fetchall()

    checked, changed, failed, skipped = 0, 0, 0, 0
    for candidate in candidates:
        if checked + failed >= batch:
            break
        job = json.loads(candidate["body"])
        if not _eligible(job):
            skipped += 1
            continue
        number = job["result"]["ttn"]
        profile = job.get("sender_profile") or "default"
        try:
            # Shared existing client: read-only, original sender, bounded cache.
            observed = tracking(pipe, job, clock=clock)
            if observed.get("ttn") != number or observed.get("sender_profile") != profile:
                raise ValueError("Carrier observation identity mismatch")
            status_code = str(observed["status_code"])
            status_text = str(observed["status"])[:180]
            phase = str(observed["phase"])[:45]
            if not status_code.isdigit() or not status_text:
                raise ValueError("Carrier returned an invalid status")
            error_code = ""
        except Exception as exc:
            # No URLs, credentials, source orders or customer PII in logs.
            error_code = type(exc).__name__[:60]
            status_code = status_text = phase = ""
            logger.warning("SAFAR_MONITOR_READ_FAILED kind=%s", error_code)

        with pipe.lock, pipe.db:
            # Validate immutable identity before committing the observation.
            fresh = pipe._job(job["key"])
            if (fresh is None or not _eligible(fresh)
                or fresh["result"]["ttn"] != number
                or (fresh.get("sender_profile") or "default") != profile):
                skipped += 1
                continue
            prev = pipe.db.execute(
                "SELECT status_code,checked_at,attempted_at FROM tracking_snapshots WHERE order_key=?",
                (job["key"],),
            ).fetchone()
            if prev and float(prev["attempted_at"]) > now - INTERVAL_SECONDS:
                skipped += 1
                continue

            if error_code:
                failed += 1
                if prev is None:
                    pipe.db.execute(
                        "INSERT INTO tracking_snapshots "
                        "(order_key,chat_id,owner_id,ttn,sender_profile,status_code,status_text,"
                        "phase,checked_at,attempted_at,error_code) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (job["key"], job["chat_id"], job["owner_id"], number, profile,
                         "", "", "", 0, now, error_code),
                    )
                else:
                    # Retain the last verified carrier status on a failed read.
                    pipe.db.execute(
                        "UPDATE tracking_snapshots SET attempted_at=?,error_code=? WHERE order_key=?",
                        (now, error_code, job["key"]),
                    )
                continue

            checked += 1
            old_code = str(prev["status_code"] or "") if prev else ""
            changed_code = old_code != status_code
            if prev is None:
                pipe.db.execute(
                    "INSERT INTO tracking_snapshots "
                    "(order_key,chat_id,owner_id,ttn,sender_profile,status_code,status_text,"
                    "phase,checked_at,attempted_at,error_code) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (job["key"], job["chat_id"], job["owner_id"], number, profile,
                     status_code, status_text, phase, now, now, ""),
                )
            else:
                pipe.db.execute(
                    "UPDATE tracking_snapshots SET status_code=?,status_text=?,phase=?,"
                    "checked_at=?,attempted_at=?,error_code='' WHERE order_key=?",
                    (status_code, status_text, phase, now, now, job["key"]),
                )
            if changed_code:
                # Audit just the observed code; no return, payment or warehouse mutation.
                pipe.db.execute(
                    "INSERT INTO tracking_events "
                    "(id,order_key,chat_id,owner_id,ttn,sender_profile,at,"
                    "status_code,status_text,phase,source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, job["key"], job["chat_id"], job["owner_id"],
                     number, profile, now, status_code, status_text, phase, "nova_poshta"),
                )
                changed += 1

    return {"attempted": checked + failed, "checked": checked,
            "changed": changed, "failed": failed, "skipped": skipped}


def order_timeline(pipe, chat_id, owner_id, key, *, limit=50):
    """Read-only, scoped carrier evidence; no customer PII exposed."""
    if not ready(pipe):
        raise RuntimeError("Private tracking migration not installed")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid timeline limit")
    with pipe.lock:
        current = pipe.db.execute(
            "SELECT * FROM tracking_snapshots "
            "WHERE order_key=? AND chat_id=? AND owner_id=?",
            (key, chat_id, owner_id),
        ).fetchone()
        events = pipe.db.execute(
            "SELECT at,status_code,status_text,phase FROM tracking_events "
            "WHERE order_key=? AND chat_id=? AND owner_id=? "
            "ORDER BY at DESC,id DESC LIMIT ?",
            (key, chat_id, owner_id, limit),
        ).fetchall()
    return {
        "last_verified": ({
            "checked_at": current["checked_at"], "attempted_at": current["attempted_at"],
            "status_code": current["status_code"], "status": current["status_text"],
            "phase": current["phase"], "error": bool(current["error_code"]),
            "source": "nova_poshta",
        } if current and current["status_code"] else None),
        "events": [
            {"at": row["at"], "code": row["status_code"],
             "status": row["status_text"], "phase": row["phase"],
             "source": "nova_poshta"} for row in events
        ],
    }
