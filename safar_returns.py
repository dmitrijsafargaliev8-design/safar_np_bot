"""Private return cases: cancellation of a TTN is not a customer return.

Return cases record an explicit operator observation. No carrier write or
financial/warehouse transition can be inferred from an issued waybill.
"""
import json
import re
import time
import uuid

RETURN_REASONS = {
    "refused_by_recipient", "unclaimed", "easy_return_after_delivery",
    "customer_exchange", "other",
}


def available(pipe):
    """Do not query missing production migrations, failing safely."""
    if pipe.db.backend != "postgres":
        return True
    row = pipe.db.execute(
        "SELECT to_regclass('safar_orders.return_cases') AS relation"
    ).fetchone()
    return bool(row["relation"])


def _public(row):
    return {
        "id": row["id"], "order_id": row["order_key"],
        "outbound_ttn": row["outbound_ttn"], "reverse_ttn": row["reverse_ttn"] or "",
        "sender_profile": row["sender_profile"], "reason": row["reason"],
        "warehouse_state": row["warehouse_state"],
        "finance_state": row["finance_state"],
        "carrier_state": "unverified",
        "created_at": row["created"], "updated_at": row["updated"],
    }


def list_cases(pipe, chat_id, owner_id, *, limit=100):
    if not available(pipe):
        raise RuntimeError("Returns migration not installed")
    with pipe.lock:
        rows = pipe.db.execute(
            "SELECT * FROM return_cases WHERE chat_id=? AND owner_id=? "
            "ORDER BY updated DESC,id DESC LIMIT ?",
            (chat_id, owner_id, min(200, max(1, limit))),
        ).fetchall()
    return [_public(row) for row in rows]


def create_case(pipe, chat_id, owner_id, order_key, reason):
    """Idempotently open one real return case against an issued NP waybill."""
    if reason not in RETURN_REASONS or not isinstance(order_key, str):
        raise ValueError("Invalid return details")
    if len(order_key) > 170 or not order_key:
        raise ValueError("Invalid order key")
    if not available(pipe):
        raise RuntimeError("Returns migration not installed")
    with pipe.lock, pipe.db:
        row = pipe.db.execute(
            "SELECT * FROM return_cases WHERE chat_id=? AND owner_id=? AND order_key=?",
            (chat_id, owner_id, order_key),
        ).fetchone()
        if row:
            return _public(row), False
        job = pipe.order_for_key(chat_id, owner_id, order_key)
        if not job or job.get("state") != "created":
            raise ValueError("Issued live TTN required for return case")
        number = str((job.get("result") or {}).get("ttn") or "")
        if not re.fullmatch(r"\d{14}", number):
            raise ValueError("Issued TTN cannot be verified from journal")
        sender = job.get("sender_profile") or "default"
        created = time.time()
        case_id = uuid.uuid4().hex
        pipe.db.execute(
            "INSERT INTO return_cases "
            "(id,chat_id,owner_id,order_key,outbound_ttn,sender_profile,reason,"
            "warehouse_state,finance_state,reverse_ttn,created,updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (case_id, chat_id, owner_id, order_key, number, sender, reason,
             "not_received", "unreviewed", None, created, created),
        )
        pipe.db.execute(
            "INSERT INTO return_events (id,case_id,actor_id,at,event_type,details) "
            "VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, case_id, owner_id, created, "case_opened",
             json.dumps({"reason": reason, "source": "operator"},
                        ensure_ascii=False, separators=(",", ":"))),
        )
        row = pipe.db.execute("SELECT * FROM return_cases WHERE id=?", (case_id,)).fetchone()
        return _public(row), True


def get_case(pipe, chat_id, owner_id, case_id):
    if not isinstance(case_id, str) or not re.fullmatch(r"[0-9a-f]{32}", case_id):
        return None
    if not available(pipe):
        raise RuntimeError("Returns migration not installed")
    with pipe.lock:
        row = pipe.db.execute(
            "SELECT * FROM return_cases WHERE id=? AND chat_id=? AND owner_id=?",
            (case_id, chat_id, owner_id),
        ).fetchone()
        if row is None:
            return None
        events = pipe.db.execute(
            "SELECT at,event_type,details FROM return_events "
            "WHERE case_id=? ORDER BY at,id",
            (case_id,),
        ).fetchall()
    result = _public(row)
    result["events"] = [{"at": e["at"], "event_type": e["event_type"]}
                        for e in events]
    return result
