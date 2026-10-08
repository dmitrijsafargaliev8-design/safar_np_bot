"""Private SAFAR authentication; durable opaque sessions and device approval.

This module never signs a client supplied identity into a cookie. Telegram is
verified first, then a random cookie is bound to a revocable private DB row.
The existing journal transaction lock makes approval and consumption atomic
across workers and overlapping deployments.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit


COOKIE_NAME = "safar_app_session"
SESSION_AGE = 30 * 60
MAX_AUTH_AGE = 10 * 60
PAIRING_AGE = 5 * 60
_OPAQUE_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_PAIR_CODE = re.compile(r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{12}\Z")


class AuthError(ValueError):
    """A safe client-facing error; causes and submitted credentials stay private."""

    def __init__(self, status_code=401, error="Authentication required"):
        super().__init__(error)
        self.status_code = status_code
        self.error = error


@dataclass(frozen=True)
class Session:
    user_id: int
    expires_at: float
    token: str = field(repr=False)
    csrf_token: str = field(repr=False)


@dataclass(frozen=True)
class Pairing:
    expires_at: float
    device_token: str = field(repr=False)
    code: str = field(repr=False)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate identity field")
        result[key] = value
    return result


def _telegram_identity(raw, bot_token, clock):
    if (not isinstance(raw, str) or not 20 <= len(raw) <= 10000
            or not isinstance(bot_token, str) or not bot_token
            or re.search(r"%(?![a-fA-F0-9]{2})", raw)):
        raise ValueError("Invalid Telegram authorization")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True,
                          encoding="utf-8", errors="strict", max_num_fields=32)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid Telegram authorization") from exc
    data = {}
    for key, value in pairs:
        if not key or key in data or "\n" in key or "=" in key:
            raise ValueError("Duplicate Telegram authentication field")
        data[key] = value
    submitted = data.pop("hash", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", submitted):
        raise ValueError("Invalid Telegram signature")
    # Bot-token HMAC includes every field except hash, including the optional
    # signature. Excluding signature applies only to Telegram's separate
    # third-party Ed25519 validation, which is not used here.
    canonical = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated = hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, submitted.lower()):
        raise ValueError("Invalid Telegram signature")
    try:
        stamp = int(data["auth_date"])
        user = json.loads(data["user"], object_pairs_hook=_unique_object)
        uid = user["id"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid Telegram identity") from exc
    if type(uid) is not int or not 1 <= uid <= 2**63 - 1:
        raise ValueError("Invalid Telegram identity")
    age = clock() - stamp
    if not math.isfinite(age) or not -30 <= age <= MAX_AUTH_AGE:
        raise ValueError("Telegram authorization expired")
    # Canonical verified HMAC, rather than URL encoding or query_id, identifies
    # a replay. Reordering or encoding the same launch cannot bypass one-use.
    return uid, submitted.lower()


def verify_telegram_init_data(raw, bot_token, *, clock=time.time):
    """Compatibility verifier; AuthService also persists one-use consumption."""
    return _telegram_identity(raw, bot_token, clock)[0]


def require_same_origin(incoming_request, expected_origin=None):
    """Require an exact browser origin on every unsafe app request.

    Render terminates TLS before Flask; the browser HTTPS origin is authoritative
    even when the internal WSGI scheme is HTTP. HTTP is accepted only on a local
    development host, never on an arbitrary internet host.
    """
    origin = incoming_request.headers.get("Origin", "")
    site = incoming_request.headers.get("Sec-Fetch-Site", "")
    if site and site != "same-origin":
        raise AuthError(403, "Same-origin request required")
    try:
        parsed = urlsplit(origin)
        if (not origin or parsed.scheme not in {"http", "https"} or not parsed.netloc
                or parsed.username or parsed.password or parsed.path or parsed.query
                or parsed.fragment):
            raise ValueError("Invalid origin")
        if expected_origin:
            expected = str(expected_origin).rstrip("/")
        else:
            host = incoming_request.host
            local = incoming_request.hostname if hasattr(incoming_request, "hostname") else urlsplit("//" + host).hostname
            scheme = "http" if local in {"localhost", "127.0.0.1", "::1"} and incoming_request.scheme == "http" else "https"
            expected = scheme + "://" + host
        if not hmac.compare_digest(origin, expected):
            raise ValueError("Origin mismatch")
    except (ValueError, TypeError):
        raise AuthError(403, "Same-origin request required") from None


def require_csrf(incoming_request, session):
    require_same_origin(incoming_request)
    supplied = incoming_request.headers.get("X-CSRF-Token", "")
    if (not isinstance(supplied, str) or not re.fullmatch(r"[a-f0-9]{64}", supplied)
            or not hmac.compare_digest(supplied, session.csrf_token)):
        raise AuthError(403, "CSRF validation failed")


class RateLimiter:
    """Bounded per-process abuse throttle; credentials still need verification."""

    def __init__(self, limit=20, period=60, *, max_keys=4096, clock=time.time):
        self.limit, self.period, self.max_keys, self.clock = limit, period, max_keys, clock
        self._entries = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key):
        # Do not retain IP addresses, actor IDs or submitted codes as map keys.
        digest = hashlib.sha256(str(key).encode("utf-8")).digest()
        now = self.clock()
        with self._lock:
            count, start = self._entries.pop(digest, (0, now))
            if now - start >= self.period or now < start:
                count, start = 0, now
            self._entries[digest] = (count + 1, start)
            while len(self._entries) > self.max_keys:
                self._entries.popitem(last=False)
            if count >= self.limit:
                raise AuthError(429, "Too many requests; try again shortly")


class AuthService:
    def __init__(self, *, telegram_token, webhook_secret, allowed, get_pipeline, clock=time.time):
        self.telegram_token = telegram_token
        self.allowed, self.get_pipeline, self.clock = allowed, get_pipeline, clock
        self._key = (hmac.new(webhook_secret.encode("utf-8"),
                     ("safar-app-auth-v2:" + telegram_token).encode("utf-8"),
                     hashlib.sha256).digest() if webhook_secret and telegram_token else None)
        self._approval_limiter = RateLimiter(10, 60, clock=clock)

    def _digest(self, purpose, token):
        if self._key is None:
            raise AuthError(503, "App sign-in is unavailable")
        return hmac.new(self._key, (purpose + ":" + token).encode("ascii"), hashlib.sha256).hexdigest()

    def _pipeline(self):
        if self._key is None:
            raise AuthError(503, "App sign-in is unavailable")
        try:
            pipe = self.get_pipeline()
            if pipe is None:
                raise ValueError("Journal unavailable")
            return pipe
        except Exception:
            raise AuthError(503, "App sign-in is unavailable") from None

    def _allowed(self, uid):
        if type(uid) is not int or not 1 <= uid <= 2**63 - 1:
            return False
        return bool(self.allowed(uid, uid))

    def _prune(self, db, now):
        db.execute("DELETE FROM app_auth_replays WHERE expires<=?", (now,))
        db.execute("DELETE FROM app_pairings WHERE expires<=?", (now,))
        db.execute("DELETE FROM app_sessions WHERE expires<=?", (now - 86400,))

    def _new_session(self, db, uid, now):
        token = secrets.token_urlsafe(32)
        csrf = self._digest("csrf", token)
        db.execute("INSERT INTO app_sessions "
                   "(token_hash,user_id,csrf_hash,created,expires,revoked) VALUES (?,?,?,?,?,NULL)",
                   (self._digest("session", token), uid, self._digest("csrf-store", csrf), now, now + SESSION_AGE))
        return Session(user_id=uid, expires_at=now + SESSION_AGE, token=token, csrf_token=csrf)

    def authenticate_init_data(self, raw, previous_token=None):
        if self._key is None:
            raise AuthError(503, "App sign-in is unavailable")
        try:
            uid, signature = _telegram_identity(raw, self.telegram_token, self.clock)
        except ValueError:
            raise AuthError() from None
        if not self._allowed(uid):
            raise AuthError(403, "Access denied")
        pipe, now = self._pipeline(), self.clock()
        try:
            with pipe.lock, pipe.db:
                self._prune(pipe.db, now)
                consumed = pipe.db.execute(
                    "INSERT INTO app_auth_replays (replay_hash,expires) VALUES (?,?) "
                    "ON CONFLICT(replay_hash) DO NOTHING",
                    (self._digest("telegram", signature), now + MAX_AUTH_AGE + 31),
                )
                if consumed.rowcount != 1:
                    raise AuthError(401, "Telegram authorization already used")
                if previous_token and _OPAQUE_TOKEN.fullmatch(previous_token):
                    pipe.db.execute("UPDATE app_sessions SET revoked=? WHERE token_hash=? AND revoked IS NULL",
                                    (now, self._digest("session", previous_token)))
                return self._new_session(pipe.db, uid, now)
        except AuthError:
            raise
        except Exception:
            raise AuthError(503, "App sign-in is unavailable") from None

    def current_session(self, token):
        if not isinstance(token, str) or not _OPAQUE_TOKEN.fullmatch(token):
            raise AuthError()
        pipe = self._pipeline()
        try:
            with pipe.lock:
                row = pipe.db.execute("SELECT user_id,csrf_hash,expires,revoked FROM app_sessions WHERE token_hash=?",
                                      (self._digest("session", token),)).fetchone()
        except Exception:
            raise AuthError(503, "App sign-in is unavailable") from None
        if row is None or row["revoked"] is not None:
            raise AuthError()
        expires = row["expires"]
        if (type(expires) not in {int, float} or not math.isfinite(expires)
                or self.clock() >= expires or not self._allowed(row["user_id"])):
            raise AuthError()
        csrf = self._digest("csrf", token)
        if not hmac.compare_digest(str(row["csrf_hash"]), self._digest("csrf-store", csrf)):
            raise AuthError()
        return Session(user_id=row["user_id"], expires_at=expires, token=token, csrf_token=csrf)

    def revoke(self, token):
        if not isinstance(token, str) or not _OPAQUE_TOKEN.fullmatch(token):
            raise AuthError()
        pipe = self._pipeline()
        try:
            with pipe.lock, pipe.db:
                pipe.db.execute("UPDATE app_sessions SET revoked=? WHERE token_hash=? AND revoked IS NULL",
                                (self.clock(), self._digest("session", token)))
        except Exception:
            raise AuthError(503, "App sign-in is unavailable") from None

    def start_pairing(self):
        pipe, now = self._pipeline(), self.clock()
        device_token = secrets.token_urlsafe(32)
        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        code = "".join(secrets.choice(alphabet) for _ in range(12))
        try:
            with pipe.lock, pipe.db:
                self._prune(pipe.db, now)
                pipe.db.execute("INSERT INTO app_pairings "
                                "(pairing_hash,code_hash,user_id,approved_chat_id,created,expires,approved,consumed) "
                                "VALUES (?,?,NULL,NULL,?,?,NULL,NULL)",
                                (self._digest("device", device_token), self._digest("pair-code", code),
                                 now, now + PAIRING_AGE))
        except Exception:
            raise AuthError(503, "App sign-in is unavailable") from None
        formatted = "-".join(code[index:index + 4] for index in range(0, 12, 4))
        return Pairing(expires_at=now + PAIRING_AGE, device_token=device_token, code=formatted)

    def approve_pairing(self, code, chat_id, user_id):
        """Called only from the existing signed webhook, never a public API."""
        if type(chat_id) is not int or chat_id != user_id or not self._allowed(user_id):
            return False
        self._approval_limiter.check(user_id)
        if not isinstance(code, str):
            return False
        code = code.strip().upper().replace("-", "")
        if not _PAIR_CODE.fullmatch(code):
            return False
        pipe, now = self._pipeline(), self.clock()
        try:
            with pipe.lock, pipe.db:
                result = pipe.db.execute(
                    "UPDATE app_pairings SET user_id=?,approved_chat_id=?,approved=? "
                    "WHERE code_hash=? AND expires>? AND approved IS NULL AND consumed IS NULL",
                    (user_id, chat_id, now, self._digest("pair-code", code), now),
                )
                return result.rowcount == 1
        except Exception:
            raise AuthError(503, "App sign-in is unavailable") from None

    def complete_pairing(self, device_token, previous_token=None):
        if not isinstance(device_token, str) or not _OPAQUE_TOKEN.fullmatch(device_token):
            raise AuthError()
        pipe, now = self._pipeline(), self.clock()
        try:
            with pipe.lock, pipe.db:
                key = self._digest("device", device_token)
                row = pipe.db.execute("SELECT user_id,approved_chat_id,expires,approved,consumed "
                                      "FROM app_pairings WHERE pairing_hash=?", (key,)).fetchone()
                if not row or row["expires"] <= now or row["consumed"] is not None:
                    raise AuthError(401, "Device approval expired or already used")
                if row["approved"] is None:
                    return None
                uid = row["user_id"]
                if row["approved_chat_id"] != uid or not self._allowed(uid):
                    raise AuthError(403, "Access denied")
                consumed = pipe.db.execute("UPDATE app_pairings SET consumed=? WHERE pairing_hash=? "
                                           "AND consumed IS NULL AND expires>?", (now, key, now))
                if consumed.rowcount != 1:
                    raise AuthError()
                if previous_token and _OPAQUE_TOKEN.fullmatch(previous_token):
                    pipe.db.execute("UPDATE app_sessions SET revoked=? WHERE token_hash=? AND revoked IS NULL",
                                    (now, self._digest("session", previous_token)))
                return self._new_session(pipe.db, uid, now)
        except AuthError:
            raise
        except Exception:
            raise AuthError(503, "App sign-in is unavailable") from None
