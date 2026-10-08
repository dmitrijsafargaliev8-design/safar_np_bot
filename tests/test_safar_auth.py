"""Negative auth/security regressions; no Telegram, carrier or production calls."""
import concurrent.futures
import hashlib
import hmac
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode
from unittest.mock import Mock, patch

from flask import Flask, request

from order_journal import OrderJournal
from safar_auth import (AuthError, AuthService, MAX_AUTH_AGE, PAIRING_AGE,
                        RateLimiter, SESSION_AGE, require_csrf,
                        require_same_origin, verify_telegram_init_data)

TOKEN = "123456:test-only-invalid-token"
WEBHOOK = "test-only-auth-secret"
USER = 100
BASE = "https://safar.invalid"
TEST_DATABASE_URL = os.environ.get("SAFAR_TEST_DATABASE_URL", "")


def signed_data(uid=USER, now=1791400000, **extra):
    fields = {"auth_date": str(now), "query_id": "test-launch",
              "user": json.dumps({"id": uid, "first_name": "Fixture"}, separators=(",", ":"))}
    fields.update(extra)
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    canonical = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class SafarAuthTests(unittest.TestCase):
    database_url = None

    def setUp(self):
        self.now = 1791400000
        self.enabled = True
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "private.sqlite3")
        self.pipe = self.make_pipeline()
        self.service = self.make_service(lambda: self.pipe)
        self.web = Flask(__name__)
        if self.database_url:
            with self.pipe.lock, self.pipe.db:
                for table in ("app_sessions", "app_auth_replays", "app_pairings"):
                    self.pipe.db.execute("DELETE FROM " + table)

    def make_pipeline(self):
        pipe = SimpleNamespace(db=OrderJournal(self.path, self.database_url), lock=threading.RLock())
        self.addCleanup(pipe.db.close)
        return pipe

    def make_service(self, get_pipeline):
        return AuthService(telegram_token=TOKEN, webhook_secret=WEBHOOK,
                           allowed=lambda chat, uid: self.enabled and chat == uid == USER,
                           get_pipeline=get_pipeline, clock=lambda: self.now)

    def login(self, query_id="test-launch", **extra):
        return self.service.authenticate_init_data(signed_data(now=self.now, query_id=query_id, **extra))

    def assertAuth(self, status, action):
        with self.assertRaises(AuthError) as caught:
            action()
        self.assertEqual(caught.exception.status_code, status)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(WEBHOOK, str(caught.exception))

    def test_signed_telegram_identity_is_validated_before_any_session(self):
        self.assertEqual(verify_telegram_init_data(signed_data(), TOKEN, clock=lambda: self.now), USER)
        session = self.login()
        self.assertEqual(self.service.current_session(session.token).user_id, USER)
        self.assertEqual(session.expires_at, self.now + SESSION_AGE)
        self.assertNotIn(session.token, repr(session))
        self.assertNotIn(session.csrf_token, repr(session))

    def test_telegram_third_party_signature_is_part_of_bot_hmac(self):
        valid = signed_data(signature="fixture-third-party-signature")
        self.assertEqual(verify_telegram_init_data(valid, TOKEN, clock=lambda: self.now), USER)
        forged = valid.replace("fixture-third-party-signature", "tampered-signature")
        self.assertAuth(401, lambda: self.service.authenticate_init_data(forged))

    def test_tampered_unsigned_expired_future_wrong_type_actors_fail_closed(self):
        valid = signed_data()
        tampered = valid.replace("Fixture", "Forged")
        invalid = [tampered, "user=%7B%22id%22%3A100%7D&auth_date=1791400000",
                   signed_data(now=self.now - MAX_AUTH_AGE - 1),
                   signed_data(now=self.now + 31), signed_data(uid=True),
                   signed_data(uid="100"), signed_data(uid=-100), signed_data(uid=2**64),
                   signed_data(user='{"id":100,"id":101}')]
        for raw in invalid:
            self.assertAuth(401, lambda raw=raw: self.service.authenticate_init_data(raw))
        self.assertAuth(403, lambda: self.service.authenticate_init_data(signed_data(uid=101)))
        self.assertEqual(self.pipe.db.execute("SELECT count(*) AS n FROM app_sessions").fetchone()["n"], 0)

    def test_duplicates_malformed_encoding_and_field_overflow_rejected(self):
        valid = signed_data()
        malformed = [valid + "&user=x", valid + "&hash=x", valid + "&signature=a&signature=b",
                     valid + "&broken=%ZZ", valid + "&broken=%FF", valid + "&=x",
                     valid + "&" + "&".join(f"extra{n}=x" for n in range(40))]
        for raw in malformed:
            self.assertAuth(401, lambda raw=raw: self.service.authenticate_init_data(raw))

    def test_auth_replay_reordering_and_encoding_are_not_new_authority(self):
        raw = signed_data()
        session = self.service.authenticate_init_data(raw)
        reordered = urlencode(list(reversed(parse_qsl(raw))))
        self.assertAuth(401, lambda: self.service.authenticate_init_data(raw))
        self.assertAuth(401, lambda: self.service.authenticate_init_data(reordered))
        other = self.make_service(lambda: self.make_pipeline())
        self.assertAuth(401, lambda: other.authenticate_init_data(raw))
        self.assertEqual(self.service.current_session(session.token).user_id, USER)

    def test_session_expiry_allowlist_removal_unknown_and_forged_cookies(self):
        session = self.login()
        for cookie in (None, "", "uid=100", "A" * 43, session.token + "x", "💥" * 43):
            self.assertAuth(401, lambda cookie=cookie: self.service.current_session(cookie))
        self.enabled = False
        self.assertAuth(401, lambda: self.service.current_session(session.token))
        self.enabled = True
        self.now = session.expires_at
        self.assertAuth(401, lambda: self.service.current_session(session.token))

    def test_logout_revokes_stolen_cookie_across_server_instances(self):
        session = self.login()
        other_pipe = self.make_pipeline()
        other = self.make_service(lambda: other_pipe)
        self.assertEqual(other.current_session(session.token).user_id, USER)
        self.service.revoke(session.token)
        self.assertAuth(401, lambda: other.current_session(session.token))
        self.service.revoke(session.token)  # revocation is idempotent

    def test_sign_in_rotates_previous_session_atomically(self):
        old = self.login()
        new = self.service.authenticate_init_data(signed_data(query_id="new-launch"), old.token)
        self.assertNotEqual(old.token, new.token)
        self.assertAuth(401, lambda: self.service.current_session(old.token))
        self.assertEqual(self.service.current_session(new.token).user_id, USER)

    def test_invalid_or_legacy_cookie_does_not_block_verified_login(self):
        for n, legacy in enumerate(("legacy.signed.uid.cookie", "", "💥" * 43, "A" * 43)):
            session = self.service.authenticate_init_data(signed_data(query_id="upgrade-" + str(n)), legacy)
            self.assertEqual(self.service.current_session(session.token).user_id, USER)
        pairing = self.service.start_pairing()
        self.assertTrue(self.service.approve_pairing(pairing.code, USER, USER))
        session = self.service.complete_pairing(pairing.device_token, "legacy.signed.uid.cookie")
        self.assertEqual(self.service.current_session(session.token).user_id, USER)

    def test_stored_session_and_pairing_states_contain_only_hashes(self):
        session = self.login()
        pairing = self.service.start_pairing()
        rows = self.pipe.db.execute("SELECT * FROM app_sessions").fetchall()
        rows += self.pipe.db.execute("SELECT * FROM app_pairings").fetchall()
        rows += self.pipe.db.execute("SELECT * FROM app_auth_replays").fetchall()
        serialized = json.dumps([dict(row) for row in rows])
        for secret in (session.token, session.csrf_token, pairing.device_token,
                       pairing.code, pairing.code.replace("-", ""), TOKEN, WEBHOOK):
            self.assertNotIn(secret, serialized)
        self.assertNotIn(pairing.device_token, repr(pairing))
        self.assertNotIn(pairing.code, repr(pairing))

    def test_pairing_requires_device_secret_private_approved_actor_and_one_use(self):
        pairing = self.service.start_pairing()
        self.assertIsNone(self.service.complete_pairing(pairing.device_token))
        self.assertFalse(self.service.approve_pairing(pairing.code, -100, USER))
        self.assertFalse(self.service.approve_pairing(pairing.code, 101, 101))
        self.assertFalse(self.service.approve_pairing(pairing.code, USER, True))
        self.assertFalse(self.service.approve_pairing("INVALID-CODE", USER, USER))
        self.assertTrue(self.service.approve_pairing(pairing.code, USER, USER))
        self.assertFalse(self.service.approve_pairing(pairing.code, USER, USER))
        self.assertAuth(401, lambda: self.service.complete_pairing(pairing.code))
        self.assertAuth(401, lambda: self.service.complete_pairing("A" * 43))
        session = self.service.complete_pairing(pairing.device_token)
        self.assertEqual(self.service.current_session(session.token).user_id, USER)
        self.assertAuth(401, lambda: self.service.complete_pairing(pairing.device_token))

    def test_pairing_expiry_and_allowlist_removal_cannot_issue_session(self):
        pairing = self.service.start_pairing()
        self.assertTrue(self.service.approve_pairing(pairing.code, USER, USER))
        self.enabled = False
        self.assertAuth(403, lambda: self.service.complete_pairing(pairing.device_token))
        self.enabled = True
        self.now += PAIRING_AGE
        self.assertFalse(self.service.approve_pairing(pairing.code, USER, USER))
        self.assertAuth(401, lambda: self.service.complete_pairing(pairing.device_token))

    def test_pairing_duplicate_consumption_across_workers_is_atomic(self):
        pairing = self.service.start_pairing()
        self.assertTrue(self.service.approve_pairing(pairing.code, USER, USER))
        other_pipe = self.make_pipeline()
        other = self.make_service(lambda: other_pipe)
        def complete(service):
            try:
                return service.complete_pairing(pairing.device_token).user_id
            except AuthError as exc:
                return exc.status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(complete, (self.service, other)))
        self.assertEqual(sorted(outcomes), [USER, 401])
        self.assertEqual(self.pipe.db.execute("SELECT count(*) AS n FROM app_sessions").fetchone()["n"], 1)

    def test_failed_session_write_rolls_back_replay_and_pairing_consumption(self):
        with patch.object(self.service, "_new_session", side_effect=RuntimeError("simulated failure")):
            self.assertAuth(503, lambda: self.login())
        self.assertEqual(self.login().user_id, USER)
        pairing = self.service.start_pairing()
        self.service.approve_pairing(pairing.code, USER, USER)
        with patch.object(self.service, "_new_session", side_effect=RuntimeError("simulated failure")):
            self.assertAuth(503, lambda: self.service.complete_pairing(pairing.device_token))
        self.assertEqual(self.service.complete_pairing(pairing.device_token).user_id, USER)

    def test_missing_credentials_journal_or_migration_fail_closed(self):
        unconfigured = AuthService(telegram_token=TOKEN, webhook_secret="", allowed=lambda *args: True,
                                   get_pipeline=lambda: self.pipe, clock=lambda: self.now)
        self.assertAuth(503, lambda: unconfigured.authenticate_init_data(signed_data()))
        unavailable = self.make_service(lambda: None)
        self.assertAuth(503, unavailable.start_pairing)
        missing_tables = SimpleNamespace(lock=threading.RLock(), db=Mock())
        missing_tables.db.__enter__ = Mock(return_value=missing_tables.db)
        missing_tables.db.__exit__ = Mock(return_value=False)
        missing_tables.db.execute.side_effect = RuntimeError("missing auth migration")
        broken = self.make_service(lambda: missing_tables)
        self.assertAuth(503, lambda: broken.authenticate_init_data(signed_data()))

    def test_origin_and_csrf_fail_closed_on_all_mutations(self):
        session = self.login()
        for origin in (None, "null", "http://safar.invalid", "https://foreign.invalid",
                       BASE + "/", BASE + "@foreign.invalid", BASE + "?x=1"):
            headers = {"X-CSRF-Token": session.csrf_token}
            if origin is not None:
                headers["Origin"] = origin
            with self.web.test_request_context("/api/safar/logout", base_url=BASE, method="POST", headers=headers):
                self.assertAuth(403, lambda: require_csrf(request, session))
        for csrf in ("", "f" * 64, "💥" * 64):
            with self.web.test_request_context("/api/safar/logout", base_url=BASE, method="POST",
                                               headers={"Origin": BASE, "X-CSRF-Token": csrf}):
                self.assertAuth(403, lambda: require_csrf(request, session))
        with self.web.test_request_context("/api/safar/logout", base_url=BASE, method="POST",
                                           headers={"Origin": BASE, "X-CSRF-Token": session.csrf_token}):
            require_csrf(request, session)
        for site in ("cross-site", "same-site", "none"):
            with self.web.test_request_context("/api/safar/logout", base_url=BASE, method="POST",
                                               headers={"Origin": BASE, "Sec-Fetch-Site": site}):
                self.assertAuth(403, lambda: require_same_origin(request))

    def test_rate_limits_are_bounded_and_expire(self):
        limiter = RateLimiter(2, 60, max_keys=3, clock=lambda: self.now)
        limiter.check("test-source")
        limiter.check("test-source")
        self.assertAuth(429, lambda: limiter.check("test-source"))
        self.now += 60
        limiter.check("test-source")
        for n in range(5):
            limiter.check("source-" + str(n))
        self.assertEqual(len(limiter._entries), 3)
        self.assertNotIn("test-source", repr(limiter._entries))


@unittest.skipUnless(TEST_DATABASE_URL, "Disposable Postgres test database not configured")
class PostgresSafarAuthTests(SafarAuthTests):
    database_url = TEST_DATABASE_URL


if __name__ == "__main__":
    unittest.main()
