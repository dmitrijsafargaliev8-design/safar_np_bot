"""Authenticated SAFAR PWA surface over the existing private order journal.

The Telegram bot remains the shipping control plane. App corrections only stage
validated proposals; the app never invokes shipment creation or deletion.
"""
from __future__ import annotations

import io
import math
import os
import re
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Blueprint, abort, jsonify, make_response, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException

from np_client import NovaPoshtaError
from order_edits import apply_field_patch
from order_pipeline import OrderCorrectionConflict
from safar_auth import (
    AuthError, AuthService, COOKIE_NAME, SESSION_AGE, RateLimiter, require_csrf,
    require_same_origin, verify_telegram_init_data,
)
from safar_operations import sender_summary, select_sender, tracking
from safar_returns import create_case, get_case, list_cases, carrier_snapshot, link_verified_easy_return


STATIC_ROOT = Path(__file__).resolve().parent / "safar_web"
MAX_PHOTO_BYTES = 6 * 1024 * 1024
STATES = {"collecting", "processing", "invalid", "failed", "uncertain", "created", "deleted"}
EDIT_FIELDS = {"full_name", "phone", "city", "warehouse", "cost", "cod_amount", "weight", "description"}
SAFE_ERRORS = {
    "invalid": "Перевірте обов’язкові поля замовлення.",
    "failed": "Обробку зупинено. Перевірте замовлення у Telegram.",
    "uncertain": "Результат створення ТТН не підтверджено. Повторне створення заблоковано.",
}


def _text(value, limit=180):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value))[:limit]


def _number(value, fallback=None):
    return value if type(value) in {int, float} and math.isfinite(value) and value >= 0 else fallback


def _photos(job):
    """Pick the largest valid Telegram image per message, retaining album order."""
    files, seen = [], set()
    for message in job.get("messages") or []:
        if not isinstance(message, dict):
            continue
        sizes = [photo for photo in message.get("photo") or []
                 if isinstance(photo, dict) and isinstance(photo.get("file_id"), str)
                 and photo["file_id"]]
        if not sizes:
            continue
        photo = max(sizes, key=lambda item: (_number(item.get("width"), 0)
                                           * _number(item.get("height"), 0)))
        identity = photo.get("file_unique_id") or photo["file_id"]
        if not isinstance(identity, str) or identity in seen:
            continue
        seen.add(identity)
        files.append(photo)
    return files


def _fields(order):
    order = order if isinstance(order, dict) else {}
    return {
        "full_name": _text(order.get("full_name"), 140),
        "phone": _text(order.get("phone"), 32),
        "city": _text(order.get("city"), 120),
        "warehouse": _text(order.get("warehouse"), 40),
        "cost": _number(order.get("cost")),
        "cod_amount": _number(order.get("cod_amount"), 0),
        "weight": _number(order.get("weight")),
        "description": _text(order.get("description"), 180),
    }


def _ttn(value):
    return str(value) if re.fullmatch(r"\d{14}", str(value or "")) else ""


def public_order(job, *, include_detail=False):
    """Whitelist journal fields; carrier refs, credentials and file IDs stay private."""
    order = job.get("order") if isinstance(job.get("order"), dict) else {}
    outcome = job.get("result") if isinstance(job.get("result"), dict) else {}
    key = _text(job.get("key"), 170)
    chat = job.get("chat_id")
    scope_query = f"?chat_id={chat}" if type(chat) is int else ""
    messages = [item for item in job.get("messages") or [] if isinstance(item, dict)]
    dates = [_number(item.get("date"), 0) for item in messages]
    created = _number(job.get("created_at") or job.get("created"), 0) or min(
        (stamp for stamp in dates if stamp), default=_number(job.get("updated_at"), 0))
    base = {
        "id": key,
        "source": "app" if job.get("source") == "app" else "telegram",
        "state": job.get("state") if job.get("state") in STATES else "invalid",
        "created_at": created,
        "updated_at": _number(job.get("updated_at"), 0),
        "recipient": _text(order.get("full_name"), 140) or "Нерозібране замовлення",
        "city": _text(order.get("city"), 120),
        "warehouse": _text(order.get("warehouse"), 40),
        "area": _text(order.get("area"), 90),
        "declared": _number(order.get("cost")),
        "cod": _number(order.get("cod_amount"), 0),
        "ttn": _ttn(outcome.get("ttn")),
        "sender_profile": _text(job.get("sender_profile") or "default", 32),
        "has_pending_edits": bool(job.get("app_correction") or job.get("pending_edits")),
        "photos": [{"index": index,
                    "url": f"/api/safar/orders/{quote(key, safe='')}/photo/{index}{scope_query}"}
                   for index in range(len(_photos(job)))],
    }
    if include_detail:
        original = list(dict.fromkeys(
            _text(item.get("caption") or item.get("text"), 8000).strip()
            for item in messages if item.get("caption") or item.get("text")))
        proposal = None
        draft = job.get("app_correction")
        if isinstance(draft, dict) and isinstance(draft.get("proposed_order"), dict):
            proposal = _fields(draft["proposed_order"])
        elif job.get("pending_edits"):
            try:
                proposal = _fields(apply_field_patch(
                    job.get("pending_base") or order, job["pending_edits"]))
            except (ValueError, TypeError, AttributeError):
                proposal = None
        anchor = job.get("anchor_id")
        source_url = ""
        # Only supergroup message IDs have an unambiguous Telegram source URL.
        if type(chat) is int and str(chat).startswith("-100") and type(anchor) is int and anchor > 0:
            source_url = f"https://t.me/c/{str(chat)[4:]}/{anchor}"
        base.update({
            "phone": _text(order.get("phone"), 32),
            "weight": _number(order.get("weight")),
            "description": _text(order.get("description"), 180),
            "delivery_type": _text(order.get("delivery_point_type"), 32),
            "fields": _fields(order),
            "pending_order": proposal,
            "correction_stale": bool(job.get("app_correction_stale")),
            "correction_version": _number(job.get("correction_version"), 0),
            "can_edit": job.get("state") not in {"processing", "uncertain"},
            "revision": _text(job.get("revision"), 64),
            "source_text": "\n\n".join(original)[:16000],
            "source_url": source_url,
            "error": SAFE_ERRORS.get(job.get("state"), ""),
            "notice": ("Зміни збережені як пропозиція. Чинна ТТН не змінена."
                       if proposal else ""),
            "history": [], "receipts": [],
            "can_print": bool(os.getenv("SAFAR_NP_PDF_PRINT") == "1" and _ttn(outcome.get("ttn"))),
        })
    return base


def _receipt(value, *, state, sender_profile="default", deleted_at=None):
    """Immutable receipt presentation excludes arbitrary NP response properties."""
    if not isinstance(value, dict):
        return None
    outcome = value.get("result") or value
    if not isinstance(outcome, dict) or not _ttn(outcome.get("ttn")):
        return None
    result = {"ttn": _ttn(outcome.get("ttn")), "state": state,
              "sender_profile": _text(value.get("sender_profile") or sender_profile, 32)}
    if isinstance(value.get("order"), dict):
        result["fields"] = _fields(value["order"])
    if deleted_at is not None:
        result["deleted_at"] = _number(deleted_at)
    return result


def create_safar_blueprint(*, telegram_token, webhook_secret, allowed, get_pipeline,
                           telegram_bot, sender_registry=None):
    bp = Blueprint("safar_app", __name__)
    auth = AuthService(telegram_token=telegram_token, webhook_secret=webhook_secret,
                       allowed=allowed, get_pipeline=get_pipeline)
    login_limit = RateLimiter(20, 60)
    pairing_start_limit = RateLimiter(5, 60)
    pairing_poll_limit = RateLimiter(40, 60)
    mutation_limit = RateLimiter(30, 60)

    @bp.before_request
    def throttle():
        if request.method != "POST":
            return None
        if request.path == "/api/safar/session":
            login_limit.check(request.remote_addr)
        elif request.path == "/api/safar/pairing/start":
            pairing_start_limit.check(request.remote_addr)
        elif request.path == "/api/safar/pairing/complete":
            pairing_poll_limit.check(request.remote_addr)
        elif request.path.startswith("/api/safar/"):
            mutation_limit.check(request.cookies.get(COOKIE_NAME, "") or request.remote_addr)
        return None

    @bp.record_once
    def attach_auth(state):
        state.app.extensions["safar_auth"] = auth

    def no_store(response):
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        return response

    @bp.after_request
    def secure_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.path.startswith("/api/safar/"):
            response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://telegram.org; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "font-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'self' https://web.telegram.org")
        if request.path.startswith("/api/safar/"):
            no_store(response)
        return response

    @bp.errorhandler(AuthError)
    def auth_error(exc):
        return jsonify(error=exc.error), exc.status_code

    @bp.errorhandler(HTTPException)
    def http_error(exc):
        messages = {400: "Invalid request", 401: "Sign-in required", 403: "Access denied",
                    404: "Record not found", 409: "Order changed. Refresh before editing",
                    413: "Image exceeds the size limit", 415: "JSON required",
                    422: "Check the proposed order fields", 429: "Try again later",
                    502: "Upstream service temporarily unavailable", 503: "Service temporarily unavailable"}
        return jsonify(error=messages.get(exc.code, "Request failed")), exc.code

    @bp.errorhandler(Exception)
    def unexpected_error(_exc):
        # Remote exceptions can contain bot tokens/addresses. Never reflect or log
        # the upstream exception in this browser-facing surface.
        return jsonify(error="Service temporarily unavailable"), 503

    def pipeline():
        result = get_pipeline()
        if not result:
            abort(503)
        return result

    def current_session():
        return auth.current_session(request.cookies.get(COOKIE_NAME, ""))

    def json_body():
        if not request.is_json:
            abort(415)
        if request.content_length and request.content_length > 24000:
            abort(413)
        result = request.get_json(silent=True)
        if not isinstance(result, dict):
            abort(400)
        return result

    def scopes(uid):
        pipe = pipeline()
        with pipe.lock:
            rows = pipe.db.execute(
                "SELECT chat_id,COUNT(*) AS quantity,MAX(updated) AS last_updated "
                "FROM jobs WHERE owner_id=? GROUP BY chat_id ORDER BY last_updated DESC,chat_id",
                (uid,),
            ).fetchall()
        result = [{"chat_id": row["chat_id"],
                   "label": "Приватний чат" if row["chat_id"] == uid else f"Група {row['chat_id']}",
                   "order_count": int(row["quantity"])}
                  for row in rows if allowed(row["chat_id"], uid)]
        if allowed(uid, uid) and not any(item["chat_id"] == uid for item in result):
            result.append({"chat_id": uid, "label": "Приватний чат", "order_count": 0})
        return result

    def default_scope(uid):
        available = scopes(uid)
        private = next((item for item in available if item["chat_id"] == uid and item["order_count"]), None)
        if not private and not available:
            abort(403)
        return (private or available[0])["chat_id"]

    def current_scope(session, body=None):
        supplied = (body or {}).get("chat_id") if body is not None else request.args.get("chat_id")
        if supplied is None:
            return default_scope(session.user_id)
        if isinstance(supplied, bool) or not re.fullmatch(r"-?[0-9]{1,17}", str(supplied)):
            abort(400)
        chat_id = int(supplied)
        if not allowed(chat_id, session.user_id):
            abort(403)
        return chat_id

    def safe_job(session, chat, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 170:
            abort(404)
        result = pipeline().order_for_key(chat, session.user_id, key)
        if result is None:
            abort(404)
        return result

    def detail_result(job):
        result = public_order(job, include_detail=True)
        identity = job.get("identity")
        if identity:
            pipe = pipeline()
            with pipe.lock:
                # The only receipt identity accepted originates in an already
                # owner/chat scoped journal record, never a request parameter.
                row = pipe.db.execute("SELECT result,state FROM receipts WHERE identity=?", (identity,)).fetchone()
            if row and row["result"]:
                import json
                receipt = json.loads(row["result"])
                if isinstance(receipt, dict):
                    profile = receipt.get("sender_profile") or job.get("sender_profile") or "default"
                    for item in receipt.get("history") or []:
                        if not isinstance(item, dict):
                            continue
                        entry = _receipt(item, state="deleted", sender_profile=profile,
                                         deleted_at=item.get("deleted_at"))
                        if entry:
                            result["history"].append(entry)
                    result["receipts"] = list(result["history"])
                    current = _receipt(receipt, state=row["state"], sender_profile=profile)
                    if current:
                        result["receipts"].append(current)
        if not result["receipts"]:
            current = _receipt({"result": job.get("result"), "order": job.get("order")},
                               state=job.get("state"), sender_profile=job.get("sender_profile", "default"))
            if current:
                result["receipts"].append(current)
        return result

    def session_response(session):
        response = make_response(jsonify(ok=True, user_id=session.user_id,
                                         csrf_token=session.csrf_token, expires_at=session.expires_at,
                                         chat_id=default_scope(session.user_id),
                                         auto_intake_enabled=os.getenv('SAFAR_APP_AUTO_CREATE') == '1'))
        response.set_cookie(COOKIE_NAME, session.token, max_age=SESSION_AGE, secure=True,
                            httponly=True, samesite="Lax", path="/api/safar")
        return no_store(response)

    @bp.get("/safar")
    @bp.get("/safar/")
    def page():
        return no_store(send_from_directory(STATIC_ROOT, "index.html"))

    @bp.get("/safar/<path:filename>")
    def asset(filename):
        if filename not in {"app.js", "style.css", "icon.svg", "manifest.webmanifest", "sw.js", "offline.html",
                            "icon-192.png", "icon-512.png", "icon-maskable-512.png"}:
            abort(404)
        result = send_from_directory(STATIC_ROOT, filename)
        if filename.endswith("webmanifest"):
            result.mimetype = "application/manifest+json"
        if filename == "sw.js":
            result.headers["Service-Worker-Allowed"] = "/safar"
            result.headers["Cache-Control"] = "no-cache"
        else:
            result.headers["Cache-Control"] = "public, max-age=300"
        return result

    @bp.post("/api/safar/session")
    def login():
        require_same_origin(request)
        body = json_body()
        issued = auth.authenticate_init_data(body.get("initData", ""),
                                             previous_token=request.cookies.get(COOKIE_NAME, ""))
        return session_response(issued)

    @bp.get("/api/safar/session")
    def session_status():
        session = current_session()
        return jsonify(ok=True, user_id=session.user_id, csrf_token=session.csrf_token,
                       expires_at=session.expires_at, chat_id=default_scope(session.user_id),
                       auto_intake_enabled=os.getenv("SAFAR_APP_AUTO_CREATE") == "1")

    @bp.post("/api/safar/logout")
    def logout():
        session = current_session()
        require_same_origin(request)
        require_csrf(request, session)
        auth.revoke(request.cookies.get(COOKIE_NAME, ""))
        response = jsonify(ok=True)
        response.delete_cookie(COOKIE_NAME, path="/api/safar", secure=True, httponly=True, samesite="Lax")
        return no_store(response)

    @bp.post("/api/safar/pairing/start")
    def pairing_start():
        require_same_origin(request)
        json_body()
        pair = auth.start_pairing()
        return jsonify(device_token=pair.device_token, code=pair.code, expires_at=pair.expires_at, poll_after=3)

    @bp.post("/api/safar/pairing/complete")
    def pairing_complete():
        require_same_origin(request)
        body = json_body()
        issued = auth.complete_pairing(body.get("device_token", ""),
                                       previous_token=request.cookies.get(COOKIE_NAME, ""))
        if issued is None:
            return jsonify(status="pending"), 202
        return session_response(issued)

    @bp.get("/api/safar/scopes")
    def list_scopes():
        session = current_session()
        return jsonify(scopes=scopes(session.user_id), selected_chat_id=default_scope(session.user_id))

    @bp.post("/api/safar/orders/intake")
    def intake_text():
        """Use the existing durable carrier queue, never create a TTN in HTTP."""
        session = current_session()
        require_same_origin(request)
        require_csrf(request, session)
        body = json_body()
        if os.getenv("SAFAR_APP_AUTO_CREATE") != "1":
            abort(503)
        chat = current_scope(session, body)
        try:
            job, is_new = pipeline().ingest_app_text(chat, session.user_id,
                                                      body.get("text"), body.get("request_id"))
        except OrderCorrectionConflict:
            abort(409)
        except (ValueError, TypeError):
            abort(422)
        return jsonify(order=public_order(job), queued=job["state"] == "collecting",
                       accepted=is_new, source="app"), 202 if is_new else 200

    @bp.get("/api/safar/returns")
    def return_cases():
        session = current_session()
        chat = current_scope(session)
        return jsonify(cases=list_cases(pipeline(), chat, session.user_id), chat_id=chat)

    @bp.post("/api/safar/returns")
    def open_return_case():
        session = current_session()
        require_same_origin(request)
        require_csrf(request, session)
        body = json_body()
        chat = current_scope(session, body)
        try:
            case, created = create_case(pipeline(), chat, session.user_id,
                                        body.get("order_id"), body.get("reason"))
        except (ValueError, TypeError):
            abort(422)
        return jsonify(case=case, created=created), 201 if created else 200

    @bp.get("/api/safar/returns/<case_id>")
    def return_case_detail(case_id):
        session = current_session()
        chat = current_scope(session)
        case = get_case(pipeline(), chat, session.user_id, case_id)
        if case is None:
            abort(404)
        return jsonify(case=case)

    @bp.post("/api/safar/returns/<case_id>/link-easy-return")
    def link_easy_return(case_id):
        """Evidence-only association: does not order a return or change cash."""
        session = current_session()
        require_same_origin(request)
        require_csrf(request, session)
        body = json_body()
        chat = current_scope(session, body)
        try:
            case, linked = link_verified_easy_return(pipeline(), chat, session.user_id,
                                                     case_id, body.get("reverse_ttn"))
        except (ValueError, TypeError):
            abort(422)
        except NovaPoshtaError:
            abort(502)
        if case is None:
            abort(404)
        return jsonify(case=case, linked=linked)

    @bp.get("/api/safar/returns/<case_id>/tracking")
    def return_case_tracking(case_id):
        """Read carrier truth for the immutable outbound TTN and sender."""
        session = current_session()
        chat = current_scope(session)
        try:
            snapshot = carrier_snapshot(pipeline(), chat, session.user_id, case_id)
        except ValueError:
            abort(409)
        except NovaPoshtaError:
            abort(502)
        if snapshot is None:
            abort(404)
        return jsonify(tracking=snapshot)

    @bp.get("/api/safar/orders")
    def orders():
        session = current_session()
        chat = current_scope(session)
        pipe = pipeline()
        try:
            limit = int(request.args.get("limit", "20"))
            offset = int(request.args.get("offset", "0"))
            date_from = float(request.args["date_from"]) if request.args.get("date_from") else None
            date_to = float(request.args["date_to"]) if request.args.get("date_to") else None
            has_ttn = request.args.get("has_ttn")
            if has_ttn is not None and has_ttn not in {"true", "false"}:
                abort(400)
            filters = {"status": request.args.get("status", ""), "search": request.args.get("search", ""),
                       "sender_profile": request.args.get("sender_profile", ""),
                       "date_from": date_from, "date_to": date_to,
                       "has_ttn": None if has_ttn is None else has_ttn == "true"}
            jobs = pipe.list_orders_page(chat, session.user_id, limit=limit, offset=offset,
                                         sort=request.args.get("sort", "updated_desc"), **filters)
            total = pipe.order_page_count(chat, session.user_id, **filters)
        except (TypeError, ValueError, OverflowError):
            abort(400)
        next_offset = offset + len(jobs)
        return jsonify(orders=[public_order(job) for job in jobs],
                       counts=pipe.order_state_counts(chat, session.user_id), chat_id=chat,
                       pagination={"offset": offset, "limit": limit, "total": total,
                                   "has_more": next_offset < total,
                                   "next_offset": next_offset if next_offset < total else None})

    @bp.get("/api/safar/orders/<path:key>")
    def detail(key):
        session = current_session()
        job = safe_job(session, current_scope(session), key)
        return jsonify(order=detail_result(job))

    @bp.post("/api/safar/orders/<path:key>/corrections")
    def corrections(key):
        session = current_session()
        require_same_origin(request)
        require_csrf(request, session)
        body = json_body()
        chat = current_scope(session, body)
        fields = body.get("fields")
        if not isinstance(fields, dict) or not fields or set(fields) - EDIT_FIELDS:
            abort(422)
        if body.get("acknowledge_creation"):
            abort(422)
        try:
            result = pipeline().stage_order_correction(chat, session.user_id, key, fields,
                                                       expected_revision=body.get("expected_revision"))
        except OrderCorrectionConflict:
            abort(409)
        except (ValueError, TypeError, NovaPoshtaError):
            abort(422)
        if result is None:
            abort(404)
        return jsonify(order=detail_result(result["job"]), staged=True, enqueued=False, ttn_modified=False)

    @bp.get("/api/safar/orders/<path:key>/tracking")
    def order_tracking(key):
        session = current_session()
        job = safe_job(session, current_scope(session), key)
        try:
            return jsonify(tracking=tracking(pipeline(), job))
        except ValueError:
            abort(422)
        except NovaPoshtaError:
            abort(502)

    def sender_result(value, chat):
        return {"profiles": value["profiles"], "selected_profile": value["selected"],
                "primary_profile": value["primary"], "fop_pending": value["fop_pending"], "chat_id": chat}

    @bp.get("/api/safar/senders")
    def senders():
        session = current_session()
        chat = current_scope(session)
        return jsonify(**sender_result(sender_summary(pipeline(), sender_registry, chat, session.user_id), chat))

    @bp.post("/api/safar/senders/select")
    def sender_selection():
        session = current_session()
        require_same_origin(request)
        require_csrf(request, session)
        body = json_body()
        chat = current_scope(session, body)
        try:
            result = select_sender(pipeline(), sender_registry, chat, session.user_id, body.get("profile_id"))
        except (ValueError, NovaPoshtaError):
            abort(422)
        return jsonify(**sender_result(result, chat))

    @bp.get("/api/safar/analytics")
    def analytics():
        session = current_session()
        chat = current_scope(session)
        try:
            days = int(request.args.get("days", "7"))
            date_from = float(request.args["date_from"]) if request.args.get("date_from") else None
            date_to = float(request.args["date_to"]) if request.args.get("date_to") else None
            result = pipeline().order_analytics(chat, session.user_id, days=days,
                                                 date_from=date_from, date_to=date_to)
        except (ValueError, TypeError, OverflowError):
            abort(400)
        return jsonify(analytics=result, chat_id=chat)

    @bp.get("/api/safar/orders/<path:key>/pdf")
    def issued_ttn_pdf(key):
        session = current_session()
        if os.getenv("SAFAR_NP_PDF_PRINT") != "1":
            abort(404)
        chat = current_scope(session)
        job = safe_job(session, chat, key)
        if job.get("state") != "created":
            abort(404)
        receipt = job.get("result") if isinstance(job.get("result"), dict) else {}
        number = _ttn(receipt.get("ttn"))
        if not number:
            abort(404)
        raw_ref = receipt.get("ref")
        doc_ref = raw_ref if isinstance(raw_ref, str) and re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", raw_ref) else ""
        try:
            carrier = pipeline()._sender_client(job.get("sender_profile") or "default")
            content = carrier.fetch_ttn_pdf(number, doc_ref=doc_ref)
        except NovaPoshtaError:
            abort(502)
        if not isinstance(content, bytes) or not content.startswith(b"%PDF-"):
            abort(502)
        return no_store(send_file(io.BytesIO(content), mimetype="application/pdf",
                                  as_attachment=True, download_name=f"SAFAR-TTN-{number}.pdf"))

    @bp.get("/api/safar/orders/<path:key>/photo/<int:index>")
    def photo(key, index):
        session = current_session()
        job = safe_job(session, current_scope(session), key)
        files = _photos(job)
        if index < 0 or index >= len(files) or not telegram_bot or not telegram_token:
            abort(404)
        item = files[index]
        if _number(item.get("file_size"), 0) > MAX_PHOTO_BYTES:
            abort(413)
        try:
            remote_file = telegram_bot.get_file(item["file_id"], timeout=10)
        except Exception:
            abort(502)
        path = getattr(remote_file, "file_path", None)
        if _number(getattr(remote_file, "file_size", 0), 0) > MAX_PHOTO_BYTES:
            abort(413)
        if (not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_/-]{1,240}\.(?:jpg|jpeg|png|webp)", path)
                or ".." in path or path.startswith("/")):
            abort(502)
        url = "https://api.telegram.org/file/bot" + telegram_token + "/" + path
        try:
            with requests.get(url, timeout=(5, 12), stream=True, allow_redirects=False) as upstream:
                if upstream.status_code != 200:
                    abort(502)
                length = upstream.headers.get("Content-Length", "")
                if length and (not re.fullmatch(r"[0-9]+", length) or int(length) > MAX_PHOTO_BYTES):
                    abort(413)
                content_type = upstream.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
                if content_type not in {"image/jpeg", "image/png", "image/webp", "application/octet-stream"}:
                    abort(502)
                contents = bytearray()
                for chunk in upstream.iter_content(chunk_size=32768):
                    if not chunk:
                        continue
                    if not isinstance(chunk, bytes):
                        abort(502)
                    if len(contents) + len(chunk) > MAX_PHOTO_BYTES:
                        abort(413)
                    contents.extend(chunk)
        except requests.RequestException:
            abort(502)
        data = bytes(contents)
        kind = None
        if len(data) >= 4 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
            kind = "image/jpeg"
        elif len(data) >= 33 and data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
            kind = "image/png"
        elif len(data) >= 20 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            kind = "image/webp"
        extension_kind = "image/png" if path.endswith(".png") else "image/webp" if path.endswith(".webp") else "image/jpeg"
        if kind is None or kind != extension_kind or content_type not in {kind, "application/octet-stream"}:
            abort(502)
        return no_store(send_file(io.BytesIO(data), mimetype=kind, etag=False, conditional=False))

    return bp
