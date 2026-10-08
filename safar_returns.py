"""Private return cases: cancellation of a TTN is not a customer return.

Return cases record an explicit operator observation. No carrier write or
financial/warehouse transition can be inferred from an issued waybill.
"""
import hashlib
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
        totals = _expense_totals(pipe, [row["id"] for row in rows])
    return [_public_with_total(row, totals.get(row["id"], 0)) for row in rows]



class ReturnExpenseConflict(ValueError):
    """Same finance action key cannot be reused for a different cost."""


def _expense_totals(pipe, case_ids):
    if not case_ids:
        return {}
    placeholders = ",".join("?" for _ in case_ids)
    records = pipe.db.execute(
        "SELECT case_id, details FROM return_events "
        "WHERE event_type='return_expense' AND case_id IN (" + placeholders + ")",
        tuple(case_ids),
    ).fetchall()
    totals = {case_id: 0 for case_id in case_ids}
    for row in records:
        evidence = json.loads(row["details"])
        cents = evidence.get("amount_kopeks")
        if type(cents) is int and cents > 0:
            totals[row["case_id"]] += cents
    return totals


def _public_with_total(row, cost_kopeks=0):
    result = _public(row)
    result["expense_total_kopeks"] = cost_kopeks
    result["finance_state"] = row["finance_state"]  # Never infer payout.
    return result


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
            totals = _expense_totals(pipe, [row["id"]])
            return _public_with_total(row, totals.get(row["id"], 0)), False
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
        totals = _expense_totals(pipe, [case_id])
    result = _public_with_total(row, totals.get(case_id, 0))
    result["events"] = [{"at": e["at"], "event_type": e["event_type"]}
                        for e in events]
    return result


def carrier_snapshot(pipe, chat_id, owner_id, case_id):
    """Live, read-only outbound tracking for a registered return case.

    Original sender is immutable: the currently selected sender must never
    replace it. Carrier status does not establish warehouse receipt or refund.
    """
    from safar_operations import tracking

    case = get_case(pipe, chat_id, owner_id, case_id)
    if case is None:
        return None
    job = pipe.order_for_key(chat_id, owner_id, case["order_id"])
    if job is None:
        raise ValueError("The original order cannot be checked")
    outbound = str((job.get("result") or {}).get("ttn") or "")
    if outbound != case["outbound_ttn"]:
        # A replacement TTN is not the original return reference.
        raise ValueError("Historical TTN changed; verify return manually")
    if (job.get("sender_profile") or "default") != case["sender_profile"]:
        raise ValueError("Historical sender changed; verify return manually")
    live = tracking(pipe, job)
    return {
        "outbound_ttn": case["outbound_ttn"],
        "sender_profile": case["sender_profile"],
        "status": live["status"],
        "status_code": live["status_code"],
        "phase": live["phase"],
        "checked_at": live["checked_at"],
        "cached": live["cached"],
        "warehouse_state": case["warehouse_state"],
        "finance_state": case["finance_state"],
        "source": "nova_poshta",
    }


def link_verified_easy_return(pipe, chat_id, owner_id, case_id, reverse_ttn):
    """Associate incoming Easy Return TTN ONLY on NP's verified original link.

    Official getStatusDocuments for incoming Easy Return exposes
    LightReturnNumber (original outbound TTN). This never orders a new return,
    alters COD, marks warehouse received or confirms a financial transaction.
    """
    if not isinstance(reverse_ttn, str) or not re.fullmatch(r"\d{14}", reverse_ttn):
        raise ValueError("Invalid incoming return TTN")
    case = get_case(pipe, chat_id, owner_id, case_id)
    if case is None:
        return None, False
    if case["reason"] != "easy_return_after_delivery":
        raise ValueError("Only an Easy Return can use this verified link")
    if reverse_ttn == case["outbound_ttn"]:
        raise ValueError("Inbound return TTN must differ from original TTN")
    # Carrier read uses the ORIGINAL sender account, never current preference.
    sender = pipe._sender_client(case["sender_profile"])
    observed = sender.get_ttn_status(reverse_ttn)
    if (not isinstance(observed, dict)
        or str(observed.get("Number") or "") != reverse_ttn
        or str(observed.get("LightReturnNumber") or "") != case["outbound_ttn"]):
        raise ValueError("Carrier did not verify Easy Return association")
    status_code = str(observed.get("StatusCode") or "").strip()
    if not status_code.isdigit() or int(status_code) in {0, 3}:
        raise ValueError("Carrier could not verify incoming return TTN")
    with pipe.lock, pipe.db:
        row = pipe.db.execute(
            "SELECT * FROM return_cases WHERE id=? AND chat_id=? AND owner_id=?",
            (case_id, chat_id, owner_id),
        ).fetchone()
        if row is None:
            return None, False
        if row["outbound_ttn"] != case["outbound_ttn"] or row["sender_profile"] != case["sender_profile"]:
            raise ValueError("Return case changed during carrier verification")
        if row["reverse_ttn"]:
            if row["reverse_ttn"] != reverse_ttn:
                raise ValueError("A different incoming TTN is already linked")
            totals = _expense_totals(pipe, [case_id])
            return _public_with_total(row, totals.get(case_id, 0)), False
        now = time.time()
        pipe.db.execute(
            "UPDATE return_cases SET reverse_ttn=?, updated=? WHERE id=?",
            (reverse_ttn, now, case_id),
        )
        pipe.db.execute(
            "INSERT INTO return_events (id,case_id,actor_id,at,event_type,details) "
            "VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, case_id, owner_id, now, "verified_easy_return_link",
             json.dumps({"reverse_ttn": reverse_ttn, "source": "nova_poshta",
                         "method": "TrackingDocument/getStatusDocuments"},
                        ensure_ascii=False, separators=(",", ":"))),
        )
        updated = pipe.db.execute(
            "SELECT * FROM return_cases WHERE id=?", (case_id,)
        ).fetchone()
        totals = _expense_totals(pipe, [case_id])
        return _public_with_total(updated, totals.get(case_id, 0)), True


def confirm_warehouse_receipt(pipe, chat_id, owner_id, case_id, number, acknowledged):
    """Human-attested physical receipt, distinct from a carrier status.

    Only an authorized operator can make this transition. Neither receiving
    stock nor ordering a return is inferred from transport tracking; financial
    reconciliation remains unreviewed until independently confirmed.
    """
    if acknowledged is not True or not isinstance(number, str) or not re.fullmatch(r"\d{14}", number):
        raise ValueError("An explicit TTN-matched warehouse confirmation is required")
    if not isinstance(case_id, str) or not re.fullmatch(r"[0-9a-f]{32}", case_id):
        return None, False
    if not available(pipe):
        raise RuntimeError("Returns migration not installed")
    with pipe.lock, pipe.db:
        row = pipe.db.execute(
            "SELECT * FROM return_cases WHERE id=? AND chat_id=? AND owner_id=?",
            (case_id, chat_id, owner_id),
        ).fetchone()
        if row is None:
            return None, False
        if number not in {row["outbound_ttn"], row["reverse_ttn"]}:
            raise ValueError("The scanned/entered TTN does not belong to this case")
        if row["warehouse_state"] == "received":
            totals = _expense_totals(pipe, [case_id])
            return _public_with_total(row, totals.get(case_id, 0)), False
        if row["warehouse_state"] != "not_received":
            raise ValueError("Unsupported warehouse state; resolve manually")
        now = time.time()
        pipe.db.execute(
            "UPDATE return_cases SET warehouse_state=?,updated=? WHERE id=?",
            ("received", now, case_id),
        )
        pipe.db.execute(
            "INSERT INTO return_events (id,case_id,actor_id,at,event_type,details) "
            "VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, case_id, owner_id, now, "warehouse_received",
             json.dumps({"source": "operator", "matched_ttn": number,
                         "explicit_confirmation": True},
                        ensure_ascii=False, separators=(",", ":"))),
        )
        updated = pipe.db.execute(
            "SELECT * FROM return_cases WHERE id=?", (case_id,)
        ).fetchone()
        totals = _expense_totals(pipe, [case_id])
        return _public_with_total(updated, totals.get(case_id, 0)), True


def record_return_expense(pipe, chat_id, owner_id, case_id, amount, category,
                          evidence_ref, request_id, acknowledged):
    """Append-only operator-observed UAH expense with immutable idempotency.

    This does NOT verify bank statements, authorize payments, mutate NP or
    change the financial settlement state of the return.
    """
    if acknowledged is not True:
        raise ValueError("Explicit operator confirmation required")
    if not isinstance(case_id, str) or not re.fullmatch(r"[0-9a-f]{32}", case_id):
        return None, False
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", request_id):
        raise ValueError("Invalid expense idempotency key")
    if category not in {"return_delivery", "storage", "other"}:
        raise ValueError("Invalid expense category")
    if not isinstance(amount, str) or not re.fullmatch(r"(?:0|[1-9]\d{0,5})(?:\.\d{1,2})?", amount):
        raise ValueError("Invalid UAH expense amount")
    whole, dot, fractional = amount.partition(".")
    cents = int(whole) * 100 + int(fractional.ljust(2, "0")) if dot else int(whole) * 100
    if not 1 <= cents <= 10_000_000:
        raise ValueError("Expense amount is out of range")
    if (not isinstance(evidence_ref, str) or not 3 <= len(evidence_ref.strip()) <= 120
            or any(ord(char) < 32 or ord(char) == 127 for char in evidence_ref)):
        raise ValueError("Expense receipt reference is required")
    if not available(pipe):
        raise RuntimeError("Returns migration not installed")
    ref = evidence_ref.strip()
    record = {
        "amount_kopeks": cents,
        "category": category,
        "reference": ref,
        "source": "operator",
        "verified_by_bank": False,
        "acknowledged": True,
    }
    body = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    event_id = hashlib.sha256(
        f"safar:return-expense:{owner_id}:{case_id}:{request_id}".encode()
    ).hexdigest()[:32]
    with pipe.lock, pipe.db:
        row = pipe.db.execute(
            "SELECT * FROM return_cases WHERE id=? AND chat_id=? AND owner_id=?",
            (case_id, chat_id, owner_id),
        ).fetchone()
        if row is None:
            return None, False
        prior = pipe.db.execute(
            "SELECT details FROM return_events WHERE id=? AND case_id=?",
            (event_id, case_id),
        ).fetchone()
        if prior is not None:
            if prior["details"] != body:
                raise ReturnExpenseConflict("Expense request key already used for different details")
            totals = _expense_totals(pipe, [case_id])
            return _public_with_total(row, totals.get(case_id, 0)), False
        now = time.time()
        pipe.db.execute(
            "INSERT INTO return_events (id,case_id,actor_id,at,event_type,details) "
            "VALUES (?,?,?,?,?,?)",
            (event_id, case_id, owner_id, now, "return_expense", body),
        )
        pipe.db.execute(
            "UPDATE return_cases SET updated=? WHERE id=?",
            (now, case_id),
        )
        updated = pipe.db.execute(
            "SELECT * FROM return_cases WHERE id=?", (case_id,)
        ).fetchone()
        totals = _expense_totals(pipe, [case_id])
        return _public_with_total(updated, totals.get(case_id, 0)), True
