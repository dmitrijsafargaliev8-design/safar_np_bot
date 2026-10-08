"""SAFAR PWA signed Telegram login, scoped API and read-only media tests."""
import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode
from unittest.mock import Mock, patch

from flask import Flask
from safar_app import create_safar_blueprint, verify_telegram_init_data, public_order

TOKEN = "123456:test-only-do-not-use"
WEBHOOK = "test-webhook-secret-only"
BASE = "https://safar-np-bot.onrender.com"
USER = 100
SAMPLE = {
    "key": "job-alpha", "state": "created", "chat_id": USER, "owner_id": USER,
    "created": 1791400000, "sender_profile": "default",
    "order": {"full_name": "Тестова Людина", "city": "Одеса", "warehouse": "4",
              "cost": 2400.0, "cod_amount": 0.0, "phone": "380501111111"},
    "result": {"ttn": "20400000000001"},
    "messages": [{"photo": [{"file_id": "secret-photo-id",
                             "file_unique_id": "dedupe-file",
                             "file_size": 4500, "width": 600, "height": 600}]}],
}


def sign(uid=USER, auth_date=None):
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAE-demo",
        "user": json.dumps({"id": uid, "first_name": "Tester"}, separators=(",", ":")),
    }
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class FakeDB:
    def __init__(self):
        self.requests = []

    def execute(self, statement, args):
        self.requests.append((statement, args))
        if args != ("job-alpha", 100, 100):
            return Mock(fetchone=Mock(return_value=None))
        return Mock(fetchone=Mock(return_value={"body": json.dumps(SAMPLE)}))


class SafarAppTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.pipeline = Mock(db=self.db)
        self.pipeline.list_orders.return_value = [SAMPLE]
        self.pipeline.order_state_counts.return_value = {"created": 1}
        self.telegram = Mock()
        self.web = Flask(__name__)
        self.web.register_blueprint(create_safar_blueprint(
            telegram_token=TOKEN, webhook_secret=WEBHOOK,
            allowed=lambda chat, user: chat == user == USER,
            get_pipeline=lambda: self.pipeline, telegram_bot=self.telegram,
        ))
        self.client = self.web.test_client()

    def login(self, uid=USER, data=None):
        return self.client.post("/api/safar/session", base_url=BASE,
                                json={"initData": data or sign(uid)})

    def test_guest_can_view_ui_and_manifest_but_never_data(self):
        page = self.client.get("/safar?demo=1", base_url=BASE)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"SAFAR", page.data)
        self.assertEqual(self.client.get("/safar/manifest.webmanifest", base_url=BASE).status_code, 200)
        self.assertEqual(self.client.get("/api/safar/orders", base_url=BASE).status_code, 401)
        self.assertEqual(self.client.get("/api/safar/orders/job-alpha", base_url=BASE).status_code, 401)
        self.pipeline.list_orders.assert_not_called()

    def test_valid_signed_webapp_login_and_owner_scope(self):
        auth = self.login()
        self.assertEqual(auth.status_code, 200)
        self.assertIn("Secure", auth.headers["Set-Cookie"])
        self.assertIn("HttpOnly", auth.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", auth.headers["Set-Cookie"])
        self.assertNotIn(TOKEN, auth.data.decode())
        resp = self.client.get("/api/safar/orders", base_url=BASE)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json["counts"]["created"], 1)
        self.pipeline.list_orders.assert_called_once_with(USER, USER, limit=40)
        listed = resp.json["orders"][0]
        self.assertEqual(listed["recipient"], "Тестова Людина")
        self.assertEqual(listed["cod"], 0.0)
        self.assertNotIn("secret-photo-id", json.dumps(listed))
        self.assertEqual(listed["photos"][0]["url"], "/api/safar/orders/job-alpha/photo/0")

    def test_tampered_expired_and_wrong_actor_fail_closed(self):
        forged = sign()[:-1] + ("0" if sign()[-1] != "0" else "1")
        self.assertEqual(self.login(data=forged).status_code, 401)
        self.assertEqual(self.login(data=sign(auth_date=int(time.time())-1500)).status_code, 401)
        self.assertEqual(self.login(uid=101).status_code, 403)
        self.assertEqual(self.client.get("/api/safar/orders", base_url=BASE).status_code, 401)

    def test_foreign_origin_rejected(self):
        resp = self.client.post("/api/safar/session", base_url=BASE,
                                headers={"Origin": "https://evil.example"},
                                json={"initData": sign()})
        self.assertEqual(resp.status_code, 403)

    def test_owned_detail_and_unowned_job(self):
        self.assertEqual(self.login().status_code, 200)
        data = self.client.get("/api/safar/orders/job-alpha", base_url=BASE)
        self.assertEqual(data.status_code, 200)
        self.assertEqual(data.json["order"]["phone"], "380501111111")
        self.assertEqual(self.client.get("/api/safar/orders/job-other", base_url=BASE).status_code, 404)
        self.assertEqual(self.db.requests[-1][1], ("job-other", 100, 100))

    def test_photo_authorized_proxied_no_raw_file_id_or_tokens(self):
        self.assertEqual(self.login().status_code, 200)
        self.telegram.get_file.return_value = Mock(file_path="photos/file_1.jpg")
        response = Mock()
        response.iter_content.return_value = [b"\xff\xd8FAKE_JPEG"]
        response.raise_for_status.return_value = None
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("safar_app.requests.get", return_value=response) as remote:
            res = self.client.get("/api/safar/orders/job-alpha/photo/0", base_url=BASE)
        self.assertEqual(res.status_code, 200)
        self.assertIn("image/jpeg", res.content_type)
        self.assertEqual(res.data, b"\xff\xd8FAKE_JPEG")
        self.assertIn("no-store", res.headers["Cache-Control"])
        self.assertNotIn(TOKEN, res.data.decode("latin1"))
        self.assertNotIn(TOKEN, json.dumps(res.headers))
        self.telegram.get_file.assert_called_once_with("secret-photo-id")
        self.assertIn("api.telegram.org/file/bot", remote.call_args.args[0])
        self.assertEqual(self.client.get("/api/safar/orders/job-other/photo/0", base_url=BASE).status_code, 404)

    def test_logout_revokes_cookie(self):
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.post("/api/safar/logout", base_url=BASE).status_code, 200)
        self.assertEqual(self.client.get("/api/safar/orders", base_url=BASE).status_code, 401)

    def test_no_pii_leaked_in_summary_and_no_carrier_mutations(self):
        result = public_order(SAMPLE)
        self.assertNotIn("phone", result)
        self.assertEqual(result["cod"], 0.0)
        self.assertEqual(result["declared"], 2400.0)
        self.telegram.create_ttn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
