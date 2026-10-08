"""SAFAR mobile operations PWA — secure, read-only first production slice.

All real records remain in the existing OrderPipeline journal. A Telegram Web
App signature is the only accepted entry point for private data. The anonymous
preview contains no real data and has no write access.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote

import requests
from flask import Blueprint, abort, jsonify, make_response, request, send_file, send_from_directory
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


STATIC_ROOT = Path(__file__).resolve().parent / "safar_web"
MAX_AUTH_AGE = 600
SESSION_AGE = 8 * 60 * 60
MAX_PHOTO_BYTES = 6 * 1024 * 1024
STATES = {"collecting", "processing", "invalid", "failed", "uncertain", "created", "deleted"}


def verify_telegram_init_data(raw, bot_token, *, clock=time.time):
    """Verify canonical Telegram WebApp HMAC; never trust client-supplied IDs."""
    if not isinstance(raw, str) or not 20 <= len(raw) <= 10000 or not bot_token:
        raise ValueError("Telegram authorization unavailable")
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    data = {}
    for k, v in pairs:
        if k in data or not k or k == "signature":
            # Signature is not part of Telegram's HMAC data-check-string.
            if k == "signature":
                continue
            raise ValueError("Duplicate Telegram authentication field")
        data[k] = v
    submitted = data.pop("hash", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", submitted):
        raise ValueError("Missing Telegram signature")
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    canonical = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    calculated = hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, submitted.lower()):
        raise ValueError("Invalid Telegram signature")
    try:
        stamp = int(data.get("auth_date", "0"))
        user = json.loads(data["user"])
        uid = user["id"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Missing Telegram identity") from exc
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 1:
        raise ValueError("Invalid Telegram user ID")
    age = int(clock()) - stamp
    if not -30 <= age <= MAX_AUTH_AGE:
        raise ValueError("Telegram authorization expired")
    return uid


def public_order(job, *, include_detail=False):
    """Whitelist fields; never leak original event payloads or NP/API tokens."""
    order = job.get("order") or {}
    outcome = job.get("result") or {}
    attachments = []
    for m in job.get("messages") or []:
        if m.get("photo"):
            fid = max(m["photo"], key=lambda f: f.get("width", 0) * f.get("height", 0))
            uniq = fid.get("file_unique_id") or fid.get("file_id")
            if uniq and uniq not in [x["_unique"] for x in attachments] and fid.get("file_id"):
                attachments.append({"_unique": uniq, "kind": "photo"})
    base = {
        "id": str(job.get("key", "")),
        "state": job.get("state") if job.get("state") in STATES else "invalid",
        "created_at": job.get("created_at") or job.get("created") or job.get("updated") or 0,
        "recipient": str(order.get("full_name") or "Неразобранный заказ")[:140],
        "city": str(order.get("city") or "")[:120],
        "warehouse": str(order.get("warehouse") or "")[:40],
        "area": str(order.get("area") or "")[:90],
        "declared": order.get("cost") if isinstance(order.get("cost"), (int, float)) else None,
        "cod": order.get("cod_amount") if isinstance(order.get("cod_amount"), (int, float)) else 0,
        "ttn": str(outcome.get("ttn") or "")[:24],
        "sender_profile": str(job.get("sender_profile") or "default")[:32],
        "photos": [{"index": i, "url": f"/api/safar/orders/{quote(str(job.get('key', '')), safe='')}/photo/{i}"} for i in range(len(attachments))],
    }
    if include_detail:
        base.update({
            "phone": str(order.get("phone") or "")[:32],
            "weight": order.get("weight"),
            "description": str(order.get("description") or "")[:180],
            "delivery_type": str(order.get("delivery_point_type") or "")[:32],
            "error": str(job.get("error") or "")[:300],
            "notice": str(job.get("notice") or "")[:350],
            "history": [{"ttn": str(x.get("ttn") or "")[:24]} for x in (job.get("history") or []) if isinstance(x, dict)][:12],
        })
    return base


def create_safar_blueprint(*, telegram_token, webhook_secret, allowed, get_pipeline, telegram_bot):
    bp = Blueprint("safar_app", __name__)
    # Domain-separated key. Bot credentials remain server-only.
    if webhook_secret and telegram_token:
        key = hmac.new(webhook_secret.encode(), ("safar-app-session-v1:" + telegram_token).encode(), hashlib.sha256).hexdigest()
        serializer = URLSafeTimedSerializer(key, salt="safar-app-auth-v1")
    else:
        serializer = None

    def no_store(response):
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    def current_user():
        if serializer is None:
            abort(503)
        token = request.cookies.get("safar_app_session", "")
        try:
            values = serializer.loads(token, max_age=SESSION_AGE)
            user_id = values.get("uid")
        except (BadSignature, SignatureExpired, TypeError, AttributeError):
            abort(401)
        if type(user_id) is not int or user_id < 1 or not allowed(user_id, user_id):
            abort(403)
        return user_id

    def safe_job(owner, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 170:
            abort(404)
        pipe = get_pipeline()
        if not pipe:
            abort(503)
        # A backend lookup ALWAYS binds chat + owner, even if the key is known.
        with pipe.lock:
            row = pipe.db.execute(
                "SELECT body FROM jobs WHERE key=? AND chat_id=? AND owner_id=?",
                (key, owner, owner),
            ).fetchone()
        if row is None:
            abort(404)
        return json.loads(row["body"])

    @bp.get("/safar")
    @bp.get("/safar/")
    def page():
        return no_store(send_from_directory(STATIC_ROOT, "index.html"))

    @bp.get("/safar/<path:filename>")
    def asset(filename):
        if filename not in {"app.js", "style.css", "icon.svg", "manifest.webmanifest"}:
            abort(404)
        result = send_from_directory(STATIC_ROOT, filename)
        if filename.endswith("webmanifest"):
            result.mimetype = "application/manifest+json"
        # Unversioned assets should be updated promptly.
        result.headers["Cache-Control"] = "public, max-age=300"
        return result

    @bp.post("/api/safar/session")
    def session():
        if serializer is None:
            return jsonify(error="App sign-in is not configured"), 503
        if not request.is_json:
            return jsonify(error="JSON required"), 415
        # Origin is present in same-origin browser fetch. Never allow a foreign
        # page to establish a login cookie via a cross-origin request.
        origin = request.headers.get("Origin", "")
        if origin and origin not in {"https://" + request.host, "http://" + request.host}:
            abort(403)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        try:
            uid = verify_telegram_init_data(data.get("initData", ""), telegram_token)
        except ValueError:
            abort(401)
        if not allowed(uid, uid):
            abort(403)
        response = make_response(jsonify(ok=True))
        response.set_cookie(
            "safar_app_session", serializer.dumps({"uid": uid}), max_age=SESSION_AGE,
            secure=True, httponly=True, samesite="Lax", path="/api/safar",
        )
        return no_store(response)

    @bp.post("/api/safar/logout")
    def logout():
        response = jsonify(ok=True)
        response.delete_cookie("safar_app_session", path="/api/safar", secure=True, httponly=True, samesite="Lax")
        return no_store(response)

    @bp.get("/api/safar/orders")
    def orders():
        uid = current_user()
        pipe = get_pipeline()
        if not pipe:
            abort(503)
        try:
            limit = max(1, min(60, int(request.args.get("limit", "40"))))
        except ValueError:
            abort(400)
        jobs = pipe.list_orders(uid, uid, limit=limit)
        states = pipe.order_state_counts(uid, uid)
        return no_store(jsonify(orders=[public_order(j) for j in jobs], counts=states, limit=limit))

    @bp.get("/api/safar/orders/<path:key>")
    def detail(key):
        uid = current_user()
        return no_store(jsonify(order=public_order(safe_job(uid, key), include_detail=True)))

    @bp.get("/api/safar/orders/<path:key>/photo/<int:index>")
    def photo(key, index):
        uid = current_user()
        job = safe_job(uid, key)
        if index < 0:
            abort(404)
        files = []
        for msg in job.get("messages") or []:
            if msg.get("photo"):
                photo = max(msg["photo"], key=lambda f: f.get("width", 0) * f.get("height", 0))
                if photo.get("file_id") and (photo.get("file_unique_id") or photo.get("file_id")) not in [
                    p.get("file_unique_id") or p["file_id"] for p in files
                ]:
                    files.append(photo)
        if index >= len(files) or not telegram_bot:
            abort(404)
        item = files[index]
        if item.get("file_size") and item["file_size"] > MAX_PHOTO_BYTES:
            abort(413)
        try:
            remote_file = telegram_bot.get_file(item["file_id"])
            path = remote_file.file_path
            if (not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,280}", path)
                    or ".." in path or not path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))):
                abort(502)
            url = "https://api.telegram.org/file/bot" + telegram_token + "/" + path
            with requests.get(url, timeout=(5, 12), stream=True, allow_redirects=False) as res:
                res.raise_for_status()
                contents = bytearray()
                for chunk in res.iter_content(chunk_size=32768):
                    contents.extend(chunk)
                    if len(contents) > MAX_PHOTO_BYTES:
                        abort(413)
            kind = ("image/png" if path.lower().endswith(".png") else
                    "image/webp" if path.lower().endswith(".webp") else "image/jpeg")
            return no_store(send_file(io.BytesIO(contents), mimetype=kind))
        except requests.RequestException:
            abort(502)

    return bp
