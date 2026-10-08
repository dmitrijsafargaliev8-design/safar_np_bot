"""SAFAR API regressions: real disposable journal, synthetic data, mock carriers."""
import copy
import hashlib
import hmac
import json
import os
import time
import unittest
from urllib.parse import urlencode
from unittest.mock import Mock, patch

from flask import Flask

from order_pipeline import OrderPipeline
from safar_app import MAX_PHOTO_BYTES, create_safar_blueprint, public_order, verify_telegram_init_data


TOKEN = "123456:test-only-do-not-use"
WEBHOOK = "test-webhook-secret-only"
BASE = "https://safar-np-bot.onrender.com"
USER = 100
GROUP = -100123456789
JPEG = b"\xff\xd8\xff\xe0synthetic-jpeg\xff\xd9"
SAMPLE = {
    "key": "job-alpha", "state": "created", "chat_id": USER, "owner_id": USER,
    "thread_id": 0, "anchor_id": 7, "due": 1791400000, "notified": True,
    "attempts": 0, "created": 1791400000, "sender_profile": "default",
    "order": {"full_name": "Тестова Людина", "city": "Одеса", "warehouse": "4",
              "cost": 2400.0, "cod_amount": 0.0, "phone": "380500000001", "weight": 1.0,
              "description": "Тестова посилка"},
    "result": {"ttn": "20400000000001", "ref": "private-carrier-ref"},
    "identity": "synthetic-receipt",
    "messages": [{"message_id": 7, "date": 1791400000, "caption": "Synthetic test order",
                  "photo": [{"file_id": "secret-photo-id", "file_unique_id": "dedupe-file",
                             "file_size": 4500, "width": 600, "height": 600}]}],
}


def sign(uid=USER, auth_date=None, *, nonce=None):
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": str(nonce or time.time_ns()),
        "user": json.dumps({"id": uid, "first_name": "Synthetic"}, separators=(",", ":")),
    }
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class SafarAppTests(unittest.TestCase):
    def make_pipeline(self):
        return OrderPipeline(":memory:", lambda text: copy.deepcopy(SAMPLE["order"]),
                             self.carrier, self.telegram,
                             sender_clients={"default": self.carrier, "other": self.other_carrier})

    def setUp(self):
        self.authorized_chats = {USER, GROUP}
        self.authorized = True
        self.carrier, self.other_carrier, self.telegram = Mock(), Mock(), Mock()
        self.carrier.get_ttn_status.return_value = {
            "Number": SAMPLE["result"]["ttn"], "StatusCode": "1", "Status": "ТТН створено",
            "APIKey": "hidden-carrier-key", "RecipientAddress": "hidden-address"}
        self.pipeline = self.make_pipeline()
        self.addCleanup(self.pipeline.close)
        self.seed(SAMPLE)
        self.web = Flask(__name__)
        self.web.register_blueprint(create_safar_blueprint(
            telegram_token=TOKEN, webhook_secret=WEBHOOK,
            allowed=lambda chat, user: self.authorized and user == USER and chat in self.authorized_chats,
            get_pipeline=lambda: self.pipeline, telegram_bot=self.telegram,
        ))
        self.client = self.web.test_client()
        self.csrf = ""

    def seed(self, job):
        with self.pipeline.lock, self.pipeline.db:
            self.pipeline._write(copy.deepcopy(job))

    def get(self, path):
        return self.client.get(path, base_url=BASE)

    def post(self, path, body=None, *, csrf=True, origin=BASE):
        headers = {"Origin": origin} if origin is not None else {}
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        return self.client.post(path, base_url=BASE, headers=headers, json=body or {})

    def login(self, uid=USER, data=None):
        result = self.post("/api/safar/session", {"initData": data or sign(uid)})
        if result.status_code == 200:
            self.csrf = result.json["csrf_token"]
        return result

    def remote_response(self, contents=JPEG, *, kind="image/jpeg", status=200, headers=None):
        result = Mock(status_code=status, headers={"Content-Type": kind, **(headers or {})})
        result.iter_content.return_value = [contents]
        result.__enter__ = Mock(return_value=result)
        result.__exit__ = Mock(return_value=False)
        self.telegram.get_file.return_value = Mock(file_path="photos/file_1.jpg", file_size=len(contents))
        return result

    def test_guest_can_view_ui_and_manifest_but_never_private_data(self):
        self.assertEqual(self.get("/safar?demo=1").status_code, 200)
        self.assertIn(b"SAFAR", self.get("/safar").data)
        self.assertEqual(self.get("/safar/manifest.webmanifest").status_code, 200)
        for path in ("orders", "orders/job-alpha", "orders/job-alpha/photo/0", "scopes",
                     "orders/job-alpha/tracking", "analytics", "senders", "session"):
            result = self.get("/api/safar/" + path)
            self.assertEqual(result.status_code, 401, path)
            self.assertIn("no-store", result.headers["Cache-Control"])
        self.carrier.get_ttn_status.assert_not_called()

    def test_valid_signed_webapp_login_opaque_cookie_and_owner_scoped_list(self):
        result = self.login()
        self.assertEqual(result.status_code, 200)
        cookie = result.headers["Set-Cookie"]
        for policy in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/api/safar"):
            self.assertIn(policy, cookie)
        self.assertEqual(result.json["user_id"], USER)
        self.assertEqual(len(self.csrf), 64)
        listed = self.get("/api/safar/orders")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json["counts"]["created"], 1)
        order = listed.json["orders"][0]
        self.assertEqual(order["recipient"], "Тестова Людина")
        self.assertEqual(order["cod"], 0)
        self.assertNotIn("phone", order)
        self.assertNotIn("secret-photo-id", json.dumps(order))
        self.assertNotIn(TOKEN, result.data.decode())
        self.assertEqual(order["photos"][0]["url"], "/api/safar/orders/job-alpha/photo/0?chat_id=100")
        row = self.pipeline.db.execute("SELECT token_hash FROM app_sessions").fetchone()
        opaque = cookie.split("=", 1)[1].split(";", 1)[0]
        self.assertEqual(len(opaque), 43)
        self.assertNotEqual(row["token_hash"], opaque)

    def test_tampered_expired_unsigned_and_wrong_actor_fail_closed(self):
        signed = sign()
        forged = signed[:-1] + ("0" if signed[-1] != "0" else "1")
        for data in (forged, sign(auth_date=int(time.time()) - 1500), "user=%7B%22id%22:100%7D"):
            self.assertEqual(self.login(data=data).status_code, 401)
        self.assertEqual(self.login(uid=101).status_code, 403)
        self.assertEqual(self.get("/api/safar/orders").status_code, 401)
        self.assertEqual(verify_telegram_init_data(sign(), TOKEN), USER)

    def test_telegram_launch_is_one_use_without_invalidating_existing_session(self):
        data = sign()
        self.assertEqual(self.login(data=data).status_code, 200)
        self.assertEqual(self.login(data=data).status_code, 401)
        self.assertEqual(self.get("/api/safar/session").status_code, 200)

    def test_legacy_cookie_can_upgrade_and_new_sign_in_revokes_previous_session(self):
        self.client.set_cookie("safar_app_session", "legacy.signed.cookie", domain="safar-np-bot.onrender.com", path="/api/safar")
        self.assertEqual(self.login().status_code, 200)
        first = self.client.get_cookie("safar_app_session", domain="safar-np-bot.onrender.com", path="/api/safar").value
        self.assertEqual(self.login().status_code, 200)
        old = self.web.test_client()
        old.set_cookie("safar_app_session", first, domain="safar-np-bot.onrender.com", path="/api/safar")
        self.assertEqual(old.get("/api/safar/orders", base_url=BASE).status_code, 401)

    def test_login_and_pairing_require_exact_same_origin(self):
        for origin in (None, "https://evil.example", "http://safar-np-bot.onrender.com", BASE + "/"):
            self.assertEqual(self.post("/api/safar/session", {"initData": sign()}, origin=origin).status_code, 403)
            self.assertEqual(self.post("/api/safar/pairing/start", origin=origin).status_code, 403)

    def test_authorized_groups_remain_owner_scoped_and_allowlist_is_rechecked(self):
        owned = copy.deepcopy(SAMPLE)
        owned.update(key="group-owned", chat_id=GROUP)
        self.seed(owned)
        foreign = copy.deepcopy(owned)
        foreign.update(key="group-foreign", owner_id=101)
        self.seed(foreign)
        self.assertEqual(self.login().status_code, 200)
        scopes = self.get("/api/safar/scopes").json
        self.assertEqual({scope["chat_id"] for scope in scopes["scopes"]}, {USER, GROUP})
        self.assertEqual(next(scope["order_count"] for scope in scopes["scopes"] if scope["chat_id"] == GROUP), 1)
        listed = self.get(f"/api/safar/orders?chat_id={GROUP}")
        self.assertEqual([item["id"] for item in listed.json["orders"]], ["group-owned"])
        self.assertEqual(self.get(f"/api/safar/orders/group-foreign?chat_id={GROUP}").status_code, 404)
        detail = self.get(f"/api/safar/orders/group-owned?chat_id={GROUP}").json["order"]
        self.assertEqual(detail["source_url"], "https://t.me/c/123456789/7")
        self.assertIn(f"chat_id={GROUP}", detail["photos"][0]["url"])
        self.assertEqual(self.get("/api/safar/orders?chat_id=-100999").status_code, 403)
        self.authorized_chats.remove(GROUP)
        self.assertEqual(self.get(f"/api/safar/orders?chat_id={GROUP}").status_code, 403)
        self.authorized = False
        self.assertEqual(self.get("/api/safar/orders").status_code, 401)

    def test_default_scope_uses_owned_group_when_private_inbox_is_empty(self):
        self.pipeline.db.execute("DELETE FROM jobs WHERE chat_id=?", (USER,))
        self.pipeline.db._connection.commit()
        owned = copy.deepcopy(SAMPLE)
        owned.update(chat_id=GROUP, key="group-only")
        self.seed(owned)
        result = self.login()
        self.assertEqual(result.json["chat_id"], GROUP)
        self.assertEqual(self.get("/api/safar/orders").json["orders"][0]["id"], "group-only")

    def test_pagination_search_and_status_filters_apply_before_limit(self):
        for number in range(45):
            job = copy.deepcopy(SAMPLE)
            job.update(key=f"page-{number:02d}", identity=None)
            job["order"]["full_name"] = f"Синтетична Людина {number}"
            if number == 44:
                job.update(state="invalid", result=None)
                job["order"]["full_name"] = "Пошук Українською"
            self.seed(job)
        self.assertEqual(self.login().status_code, 200)
        first = self.get("/api/safar/orders?limit=20&sort=updated_asc").json
        self.assertEqual(first["pagination"]["total"], 46)
        self.assertEqual(first["pagination"]["next_offset"], 20)
        second = self.get("/api/safar/orders?limit=20&offset=20&sort=updated_asc").json
        self.assertFalse({item["id"] for item in first["orders"]} & {item["id"] for item in second["orders"]})
        filtered = self.get("/api/safar/orders?limit=1&search=українською&status=attention").json
        self.assertEqual(filtered["pagination"]["total"], 1)
        self.assertEqual(filtered["orders"][0]["id"], "page-44")
        self.assertFalse(filtered["pagination"]["has_more"])
        for query in ("limit=100", "offset=-1", "date_from=nan", "date_to=inf", "status=secret", "has_ttn=yes", "sort=sql"):
            self.assertEqual(self.get("/api/safar/orders?" + query).status_code, 400, query)

    def test_detail_and_receipt_history_whitelist_secrets_and_original_source(self):
        receipt = {"result": {**SAMPLE["result"], "api_key": "hidden-carrier-key"},
                   "order": SAMPLE["order"], "sender_profile": "default",
                   "history": [{"result": {"ttn": "20400000000002", "ref": "hidden-history-ref"},
                                "order": SAMPLE["order"], "deleted_at": 1791400000}]}
        with self.pipeline.db:
            self.pipeline.db.execute("INSERT INTO receipts VALUES (?,?,?,?)", (SAMPLE["identity"], "created", json.dumps(receipt), time.time()))
        job = copy.deepcopy(SAMPLE)
        job["error"] = "api_key hidden-error-credential"
        job["notice"] = "hidden-notice-credential"
        self.seed(job)
        self.login()
        response = self.get("/api/safar/orders/job-alpha")
        self.assertEqual(response.status_code, 200)
        order = response.json["order"]
        self.assertEqual(order["source_text"], "Synthetic test order")
        self.assertEqual(order["phone"], SAMPLE["order"]["phone"])
        self.assertEqual(len(order["revision"]), 64)
        self.assertEqual([item["ttn"] for item in order["receipts"]], ["20400000000002", "20400000000001"])
        self.assertEqual(order["history"][0]["state"], "deleted")
        for sentinel in ("hidden-carrier-key", "hidden-history-ref", "hidden-error-credential", "hidden-notice-credential", "private-carrier-ref", "secret-photo-id"):
            self.assertNotIn(sentinel, response.data.decode())
        self.assertEqual(self.get("/api/safar/orders/job-foreign").status_code, 404)

    def test_correction_is_revision_checked_staging_without_carrier_or_queue_changes(self):
        self.login()
        before = self.pipeline.order_for_key(USER, USER, "job-alpha")
        result = self.post("/api/safar/orders/job-alpha/corrections", {
            "fields": {"cost": 2700, "cod_amount": 0}, "expected_revision": before["revision"]})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.json["staged"])
        self.assertFalse(result.json["enqueued"])
        self.assertFalse(result.json["ttn_modified"])
        after = self.pipeline.order_for_key(USER, USER, "job-alpha")
        self.assertEqual(after["order"], before["order"])
        self.assertEqual(after["result"], before["result"])
        self.assertEqual((after["state"], after["due"], after["notified"]), (before["state"], before["due"], before["notified"]))
        self.assertFalse(self.pipeline.wakeup.is_set())
        self.assertNotIn("pending_edits", after)
        self.assertEqual(result.json["order"]["pending_order"]["cost"], 2700)
        self.assertEqual(result.json["order"]["fields"]["cost"], 2400)
        self.assertEqual(self.post("/api/safar/orders/job-alpha/corrections", {
            "fields": {"cost": 2900}, "expected_revision": before["revision"]}).status_code, 409)
        self.carrier.create_ttn.assert_not_called()
        self.carrier.is_ttn_deleted.assert_not_called()

    def test_mutation_csrf_and_field_allowlists_fail_closed(self):
        self.login()
        revision = self.get("/api/safar/orders/job-alpha").json["order"]["revision"]
        body = {"fields": {"cost": 2700}, "expected_revision": revision}
        self.assertEqual(self.post("/api/safar/orders/job-alpha/corrections", body, csrf=False).status_code, 403)
        self.assertEqual(self.post("/api/safar/logout", csrf=False).status_code, 403)
        self.assertEqual(self.post("/api/safar/senders/select", {"profile_id": "other"}, csrf=False).status_code, 403)
        for fields in ({"sender_profile": "other"}, {"phone": "abc"}, {"cost": True}, {"cost": -1}):
            self.assertEqual(self.post("/api/safar/orders/job-alpha/corrections", {**body, "fields": fields}).status_code, 422)
        self.assertEqual(self.post("/api/safar/orders/job-alpha/corrections", {**body, "acknowledge_creation": True}).status_code, 422)
        self.assertEqual(self.post("/api/safar/orders/job-alpha/corrections", body, origin="https://evil.example").status_code, 403)

    def test_uncertain_and_processing_corrections_stay_locked(self):
        self.login()
        for state in ("processing", "uncertain"):
            job = copy.deepcopy(SAMPLE)
            job["state"] = state
            self.seed(job)
            detail = self.get("/api/safar/orders/job-alpha").json["order"]
            self.assertFalse(detail["can_edit"])
            response = self.post("/api/safar/orders/job-alpha/corrections", {"fields": {"cost": 2800}, "expected_revision": detail["revision"]})
            self.assertEqual(response.status_code, 409)
        self.carrier.create_ttn.assert_not_called()

    def test_sender_changes_only_future_preferences_and_analytics_are_scoped(self):
        self.login()
        result = self.get("/api/safar/senders")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json["fop_pending"])
        selected = self.post("/api/safar/senders/select", {"profile_id": "other"})
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json["selected_profile"], "other")
        self.assertEqual(self.pipeline.order_for_key(USER, USER, "job-alpha")["sender_profile"], "default")
        self.assertEqual(self.post("/api/safar/senders/select", {"profile_id": "fop"}).status_code, 422)
        analytics = self.get("/api/safar/analytics").json["analytics"]
        self.assertEqual(analytics["counts"], {"created": 1})
        self.assertEqual(analytics["total"], 1)
        self.assertEqual(analytics["timezone"], "Europe/Kyiv")
        self.assertEqual(len(analytics["daily_activity"]), 7)
        self.assertNotIn("delivered", analytics)
        self.assertEqual(self.get("/api/safar/analytics?days=99").status_code, 400)

    def test_tracking_readonly_uses_immutable_sender_and_never_infers_delivery(self):
        self.login()
        self.post("/api/safar/senders/select", {"profile_id": "other"})
        result = self.get("/api/safar/orders/job-alpha/tracking")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["tracking"]["phase"], "issued")
        self.assertFalse(result.json["tracking"]["delivered"])
        self.assertTrue(self.get("/api/safar/orders/job-alpha/tracking").json["tracking"]["cached"])
        self.carrier.get_ttn_status.assert_called_once_with(SAMPLE["result"]["ttn"], phone=SAMPLE["order"]["phone"])
        self.other_carrier.get_ttn_status.assert_not_called()
        self.assertNotIn("hidden-address", result.data.decode())
        self.assertNotIn("hidden-carrier-key", result.data.decode())
        self.carrier.create_ttn.assert_not_called()

    def test_photo_proxy_checks_auth_timeout_mime_signature_and_keeps_credentials_private(self):
        self.login()
        response = self.remote_response()
        with patch("safar_app.requests.get", return_value=response) as remote:
            result = self.get("/api/safar/orders/job-alpha/photo/0")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data, JPEG)
        self.assertEqual(result.mimetype, "image/jpeg")
        self.assertIn("no-store", result.headers["Cache-Control"])
        self.assertNotIn(TOKEN, str(dict(result.headers)))
        self.telegram.get_file.assert_called_once_with("secret-photo-id", timeout=10)
        self.assertEqual(remote.call_args.kwargs, {"timeout": (5, 12), "stream": True, "allow_redirects": False})
        self.assertEqual(self.get("/api/safar/orders/job-foreign/photo/0").status_code, 404)

    def test_photo_upstream_failures_are_sanitized_and_byte_limit_is_enforced(self):
        self.login()
        self.telegram.get_file.side_effect = RuntimeError("https://api.telegram.org/bot" + TOKEN)
        failed = self.get("/api/safar/orders/job-alpha/photo/0")
        self.assertEqual(failed.status_code, 502)
        self.assertNotIn(TOKEN, failed.data.decode())
        self.telegram.get_file.side_effect = None
        for response, code in ((self.remote_response(b"<html>wrong image</html>", kind="image/jpeg"), 502),
                               (self.remote_response(kind="text/html"), 502),
                               (self.remote_response(status=302), 502),
                               (self.remote_response(headers={"Content-Length": str(MAX_PHOTO_BYTES + 1)}), 413)):
            with patch("safar_app.requests.get", return_value=response):
                self.assertEqual(self.get("/api/safar/orders/job-alpha/photo/0").status_code, code)
        response = self.remote_response()
        response.iter_content.return_value = [b"x" * (MAX_PHOTO_BYTES + 1)]
        with patch("safar_app.requests.get", return_value=response):
            self.assertEqual(self.get("/api/safar/orders/job-alpha/photo/0").status_code, 413)
        self.telegram.get_file.return_value = Mock(file_path="../secret.jpg", file_size=1)
        with patch("safar_app.requests.get") as remote:
            self.assertEqual(self.get("/api/safar/orders/job-alpha/photo/0").status_code, 502)
            remote.assert_not_called()

    def test_pairing_is_private_chat_approved_one_use_and_throttled(self):
        started = self.post("/api/safar/pairing/start")
        self.assertEqual(started.status_code, 200)
        token = started.json["device_token"]
        code = started.json["code"]
        self.assertEqual(self.post("/api/safar/pairing/complete", {"device_token": token}).status_code, 202)
        auth = self.web.extensions["safar_auth"]
        self.assertFalse(auth.approve_pairing(code, GROUP, USER))
        self.assertFalse(auth.approve_pairing(code, 101, 101))
        self.assertTrue(auth.approve_pairing(code, USER, USER))
        self.assertFalse(auth.approve_pairing(code, USER, USER))
        completed = self.post("/api/safar/pairing/complete", {"device_token": token})
        self.assertEqual(completed.status_code, 200)
        self.assertIn("csrf_token", completed.json)
        self.assertEqual(self.post("/api/safar/pairing/complete", {"device_token": token}).status_code, 401)
        self.assertEqual(self.get("/api/safar/orders").status_code, 200)
        for _ in range(4):
            self.assertEqual(self.post("/api/safar/pairing/start").status_code, 200)
        self.assertEqual(self.post("/api/safar/pairing/start").status_code, 429)

    def test_logout_revokes_copied_cookie_on_server(self):
        self.login()
        saved = self.client.get_cookie("safar_app_session", domain="safar-np-bot.onrender.com", path="/api/safar").value
        self.assertEqual(self.post("/api/safar/logout").status_code, 200)
        self.assertEqual(self.get("/api/safar/orders").status_code, 401)
        copied = self.web.test_client()
        copied.set_cookie("safar_app_session", saved, domain="safar-np-bot.onrender.com", path="/api/safar")
        self.assertEqual(copied.get("/api/safar/orders", base_url=BASE).status_code, 401)

    def test_public_numeric_fields_are_finite_cod_is_not_inferred_and_media_dedupes(self):
        job = copy.deepcopy(SAMPLE)
        job["order"].pop("cod_amount")
        job["order"]["weight"] = float("inf")
        job["messages"].append(copy.deepcopy(job["messages"][0]))
        result = public_order(job, include_detail=True)
        self.assertEqual(result["cod"], 0)
        self.assertEqual(result["declared"], 2400)
        self.assertIsNone(result["weight"])
        self.assertEqual(len(result["photos"]), 1)
        self.assertNotIn("secret-photo-id", json.dumps(result))

    def test_return_cases_are_distinct_from_deleted_receipts_and_scoped(self):
        self.login()
        self.assertEqual(self.get("/api/safar/returns").json["cases"], [])
        payload = {"order_id": "job-alpha", "reason": "refused_by_recipient", "chat_id": USER}
        opened = self.post("/api/safar/returns", payload)
        self.assertEqual(opened.status_code, 201)
        case = opened.json["case"]
        self.assertEqual(case["outbound_ttn"], SAMPLE["result"]["ttn"])
        self.assertEqual(case["sender_profile"], "default")
        self.assertEqual(case["carrier_state"], "unverified")
        self.assertEqual(case["warehouse_state"], "not_received")
        self.assertEqual(case["finance_state"], "unreviewed")
        self.assertEqual(self.post("/api/safar/returns", payload).status_code, 200)
        self.assertEqual(len(self.get("/api/safar/returns").json["cases"]), 1)
        details = self.get("/api/safar/returns/" + case["id"])
        self.assertEqual(details.status_code, 200)
        self.assertEqual(details.json["case"]["events"][0]["event_type"], "case_opened")
        self.assertEqual(self.get("/api/safar/returns/" + case["id"] + "?chat_id=" + str(GROUP)).status_code, 404)
        self.assertEqual(self.post("/api/safar/returns", {**payload, "chat_id": 43201}).status_code, 403)
        self.assertEqual(self.post("/api/safar/returns", {**payload, "reason": "deleted"}).status_code, 422)
        self.assertEqual(self.post("/api/safar/returns", {**payload, "order_id": "nonexistent"}).status_code, 422)
        self.assertEqual(self.post("/api/safar/returns", {**payload, "order_id": "job-alpha"}, csrf=False).status_code, 403)
        self.carrier.create_ttn.assert_not_called()
        self.carrier.get_ttn_status.assert_not_called()

    def test_cancelled_ttn_does_not_automatically_create_return(self):
        cancelled = copy.deepcopy(SAMPLE)
        cancelled["key"] = "job-cancelled"
        cancelled["state"] = "deleted"
        self.seed(cancelled)
        self.login()
        result = self.post("/api/safar/returns",
                           {"order_id": "job-cancelled", "reason": "unclaimed", "chat_id": USER})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(self.get("/api/safar/returns").json["cases"], [])


    def test_app_intake_disabled_by_default_and_rejects_wrong_actor_or_csrf(self):
        request_id = "fixed-client-idempotency-00001"
        payload = {"text": "ФИО: Іван Іваненко", "request_id": request_id, "chat_id": USER}
        self.assertEqual(self.post("/api/safar/orders/intake", payload).status_code, 401)
        self.login()
        with patch.dict(os.environ, {"SAFAR_APP_AUTO_CREATE": "0"}):
            self.assertEqual(self.post("/api/safar/orders/intake", payload).status_code, 503)
        with patch.dict(os.environ, {"SAFAR_APP_AUTO_CREATE": "1"}):
            self.assertEqual(self.post("/api/safar/orders/intake", payload, csrf=False).status_code, 403)
            self.assertEqual(self.post("/api/safar/orders/intake", payload, origin="https://hostile.example").status_code, 403)
            self.assertEqual(self.post("/api/safar/orders/intake", {**payload, "chat_id": 9876}).status_code, 403)
            self.assertEqual(self.post("/api/safar/orders/intake", {**payload, "request_id": "bad"}).status_code, 422)
            self.assertEqual(self.post("/api/safar/orders/intake", {**payload, "text": ""}).status_code, 422)
        self.carrier.create_ttn.assert_not_called()

    def test_app_intake_has_no_fake_telegram_ids_and_retries_are_idempotent(self):
        self.login()
        request_id = "fixed-client-idempotency-00002"
        payload = {"text": "ФИО: Іван Іваненко\nТелефон: 0500000001\nГород: Одеса\nОтделение: 4\nОценка: 1600",
                   "request_id": request_id, "chat_id": USER}
        self.carrier.create_ttn.return_value = {"ttn": "20400000000011"}
        with patch.dict(os.environ, {"SAFAR_APP_AUTO_CREATE": "1"}):
            created = self.post("/api/safar/orders/intake", payload)
            self.assertEqual(created.status_code, 202)
            self.assertTrue(created.json["accepted"])
            self.assertEqual(created.json["order"]["source"], "app")
            self.assertEqual(created.json["order"]["state"], "collecting")
            duplicate = self.post("/api/safar/orders/intake", payload)
            self.assertEqual(duplicate.status_code, 200)
            self.assertFalse(duplicate.json["accepted"])
            self.assertEqual(created.json["order"]["id"], duplicate.json["order"]["id"])
            self.assertEqual(self.post("/api/safar/orders/intake",
                                       {**payload, "text": payload["text"] + " changed"}).status_code, 409)
            key = created.json["order"]["id"]
            stored = self.pipeline._job(key)
            self.assertIsNone(stored["anchor_id"])
            self.assertTrue(all("message_id" not in message for message in stored["messages"]))
            self.assertTrue(self.pipeline.tick())
            self.assertEqual(self.pipeline._job(key)["state"], "created")
            self.assertEqual(self.pipeline._job(key)["result"]["ttn"], "20400000000011")
            self.assertEqual(self.carrier.create_ttn.call_count, 1)
            self.telegram.send_message.assert_not_called()
            self.telegram.send_photo.assert_not_called()
            self.assertEqual(self.post("/api/safar/orders/intake", payload).status_code, 200)
            self.assertEqual(self.carrier.create_ttn.call_count, 1)
            details = self.get("/api/safar/orders/" + key)
            self.assertEqual(details.status_code, 200)
            self.assertEqual(details.json["order"]["source"], "app")
            self.assertIn("Іван Іваненко", details.json["order"]["source_text"])

    def test_app_replay_guard_new_request_id_does_not_block_legitimate_repeat_customer(self):
        """Within two minutes exact text is one intake; phone/name never form global identity."""
        text = "ФИО: Іван Іваненко\nТелефон: 0500000001\nГород: Одеса\nОтделение: 4\nОценка: 1600"
        first, new = self.pipeline.ingest_app_text(USER, USER, text, "new-client-id-00000001")
        self.assertTrue(new)
        repeated, new = self.pipeline.ingest_app_text(
            USER, USER, text.replace("\n", "\n  "), "new-client-id-00000002")
        self.assertFalse(new)
        self.assertEqual(first["key"], repeated["key"])
        # Different item/order text from the same person is not a duplicate.
        different, new = self.pipeline.ingest_app_text(
            USER, USER, text + "\nОписание: другая пара", "new-client-id-00000003")
        self.assertTrue(new)
        self.assertNotEqual(first["key"], different["key"])
        # Identical order well after the short replay window remains possible.
        with patch.object(self.pipeline, "clock", return_value=first["created_at"] + 121):
            later, new = self.pipeline.ingest_app_text(USER, USER, text, "new-client-id-00000004")
        self.assertTrue(new)
        self.assertNotEqual(first["key"], later["key"])
        self.carrier.create_ttn.assert_not_called()

    def test_return_carrier_tracking_is_read_only_scoped_and_uses_original_sender(self):
        self.assertEqual(self.get("/api/safar/returns/not-found/tracking").status_code, 401)
        self.login()
        opened = self.post("/api/safar/returns",
                           {"order_id": "job-alpha", "reason": "unclaimed", "chat_id": USER})
        self.assertEqual(opened.status_code, 201)
        case = opened.json["case"]
        self.pipeline.set_sender_preference(USER, USER, "other")
        tracking = self.get("/api/safar/returns/" + case["id"] + "/tracking")
        self.assertEqual(tracking.status_code, 200)
        snapshot = tracking.json["tracking"]
        self.assertEqual(snapshot["outbound_ttn"], SAMPLE["result"]["ttn"])
        self.assertEqual(snapshot["source"], "nova_poshta")
        self.assertEqual(snapshot["status_code"], "1")
        self.assertEqual(snapshot["warehouse_state"], "not_received")
        self.assertEqual(snapshot["finance_state"], "unreviewed")
        self.assertEqual(snapshot["sender_profile"], "default")
        self.carrier.get_ttn_status.assert_called_once()
        self.other_carrier.get_ttn_status.assert_not_called()
        self.assertEqual(self.get("/api/safar/returns/" + case["id"] + "/tracking?chat_id=" + str(GROUP)).status_code, 404)
        self.assertEqual(len(self.get("/api/safar/returns").json["cases"]), 1)
        self.assertEqual(len(self.get("/api/safar/returns/" + case["id"]).json["case"]["events"]), 1)

    def test_pdf_proxy_uses_original_sender_and_never_exposes_key(self):
        self.login()
        self.assertEqual(self.get("/api/safar/orders/job-alpha/pdf").status_code, 404)
        self.carrier.fetch_ttn_pdf.return_value = b"%PDF-1.4\n%mock unit PDF\n%%EOF"
        with patch.dict(os.environ, {"SAFAR_NP_PDF_PRINT": "1"}):
            detail = self.get("/api/safar/orders/job-alpha")
            self.assertTrue(detail.json["order"]["can_print"])
            response = self.get("/api/safar/orders/job-alpha/pdf")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/pdf")
            self.assertTrue(response.data.startswith(b"%PDF-"))
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertNotIn("private-carrier-ref", str(response.headers))
            self.carrier.fetch_ttn_pdf.assert_called_once_with(SAMPLE["result"]["ttn"], doc_ref="")
            self.other_carrier.fetch_ttn_pdf.assert_not_called()
            self.assertEqual(self.get("/api/safar/orders/job-alpha/pdf?chat_id=" + str(GROUP)).status_code, 404)
            self.assertEqual(self.get("/api/safar/orders/no-such-job/pdf").status_code, 404)
        self.assertEqual(self.get("/api/safar/orders/job-alpha").json["order"]["can_print"], False)

    def test_easy_return_link_requires_exact_verified_original_and_never_orders_carrier(self):
        self.login()
        created = self.post("/api/safar/returns",
                            {"chat_id": USER, "order_id": "job-alpha",
                             "reason": "easy_return_after_delivery"})
        self.assertEqual(created.status_code, 201)
        case_id = created.json["case"]["id"]
        reverse = "20400000000123"
        route = f"/api/safar/returns/{case_id}/link-easy-return"
        self.pipeline.set_sender_preference(USER, USER, "other")
        self.carrier.get_ttn_status.return_value = {
            "Number": reverse, "StatusCode": "4", "Status": "In transit",
            "LightReturnNumber": "20400000000999"}
        self.assertEqual(self.post(route, {"reverse_ttn": reverse, "chat_id": USER}).status_code, 422)
        self.assertEqual(self.get("/api/safar/returns/" + case_id).json["case"]["reverse_ttn"], "")
        self.carrier.get_ttn_status.return_value["LightReturnNumber"] = SAMPLE["result"]["ttn"]
        response = self.post(route, {"reverse_ttn": reverse, "chat_id": USER})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["linked"])
        self.assertEqual(response.json["case"]["reverse_ttn"], reverse)
        details = self.get("/api/safar/returns/" + case_id)
        self.assertEqual(details.json["case"]["events"][-1]["event_type"], "verified_easy_return_link")
        self.assertEqual(self.post(route, {"reverse_ttn": reverse, "chat_id": USER}).json["linked"], False)
        self.assertEqual(self.post(route, {"reverse_ttn": reverse, "chat_id": GROUP}, csrf=False).status_code, 403)
        self.assertEqual(self.post(route, {"reverse_ttn": "1234", "chat_id": USER}).status_code, 422)
        self.other_carrier.get_ttn_status.assert_not_called()
        self.carrier.create_ttn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
