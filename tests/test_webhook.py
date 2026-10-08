import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bot as app_module
from order_pipeline import OrderPipeline
from test_order_pipeline import message, ORDER


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.now = 1791400000.0
        self.bot = Mock()
        self.bot.send_photo.return_value = SimpleNamespace(message_id=1000)
        self.bot.send_message.return_value = SimpleNamespace(message_id=1001)
        self.np = Mock()
        self.np.create_ttn.return_value = {"ttn": "20400000000001"}
        self.pipeline = OrderPipeline(":memory:", app_module.parse_order, self.np, self.bot, clock=lambda: self.now)
        self.addCleanup(self.pipeline.close)
        for name, value in (("bot", self.bot), ("np_client", self.np), ("_pipeline", self.pipeline),
                            ("WEBHOOK_SECRET", "test-secret"), ("ALLOWED_CHAT_IDS", {100})):
            override = patch.object(app_module, name, value)
            override.start()
            self.addCleanup(override.stop)
        env = patch.dict(os.environ, {"SAFAR_DISABLE_BACKGROUND": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.http = app_module.app.test_client()

    def post(self, payload, headers=None):
        return self.http.post("/webhook", json=payload,
                              headers=headers if headers is not None else {"X-Telegram-Bot-Api-Secret-Token": "test-secret"})

    def test_signed_caption_is_saved_before_ack_and_processed_once(self):
        data = {"update_id": 1, "message": message(1, caption=ORDER)}
        self.assertEqual(self.post(data).status_code, 200)
        self.assertTrue(self.pipeline.has_update(1))
        self.np.create_ttn.assert_not_called()
        self.now += 4
        self.pipeline.tick()
        self.assertTrue(self.post(data).json["duplicate"])
        self.np.create_ttn.assert_called_once()

    def test_forged_webhook_does_not_save_order(self):
        self.assertEqual(self.post({"update_id": 1, "message": message(1, caption=ORDER)}, {}).status_code, 403)
        self.assertFalse(self.pipeline.has_update(1))
        self.np.create_ttn.assert_not_called()

    def test_disallowed_chat_does_not_create_order(self):
        result = self.post({"update_id": 1, "message": message(1, caption=ORDER, chat=200)})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json["ignored"])
        self.assertFalse(self.pipeline.list_orders(200, 99))

    def test_journal_write_failure_returns_retryable_http_status(self):
        with patch.object(self.pipeline, "ingest", side_effect=RuntimeError("database unavailable")):
            result = self.post({"update_id": 1, "message": message(1, caption=ORDER)})
        self.assertEqual(result.status_code, 503)
        self.assertFalse(self.pipeline.has_update(1))

    def test_malformed_payload_is_rejected(self):
        for payload in (None, [], {}, {"update_id": True}, {"update_id": "1"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 400)

    def test_edited_caption_reaches_pipeline(self):
        self.post({"update_id": 1, "message": message(1, caption=ORDER.replace("Оценка 1600", ""))})
        self.now += 4
        self.pipeline.tick()
        self.post({"update_id": 2, "edited_message": message(1, caption=ORDER)})
        self.now += 4
        self.pipeline.tick()
        self.np.create_ttn.assert_called_once()

    def test_retry_unknown_message_is_acknowledged_with_instruction(self):
        retry = message(1, text="/retry", photo=False)
        retry["reply_to_message"] = {"message_id": 90000}
        self.assertEqual(self.post({"update_id": 1, "message": retry}).status_code, 200)
        self.bot.send_message.assert_called_once()
        self.np.create_ttn.assert_not_called()

    def test_journal_startup_failure_returns_retryable_http_status(self):
        with patch.object(app_module, "get_pipeline", side_effect=RuntimeError("database unavailable")):
            result = self.post({"update_id": 1, "message": message(1, caption=ORDER)})
        self.assertEqual(result.status_code, 503)
        self.assertFalse(self.pipeline.has_update(1))
        self.np.create_ttn.assert_not_called()

    def test_required_database_missing_never_uses_sqlite(self):
        with patch.object(app_module, "_pipeline", None), patch.dict(
                os.environ, {"STATE_DATABASE_URL": "", "STATE_REQUIRE_PERSISTENT": "1"}):
            result = self.post({"update_id": 1, "message": message(1, caption=ORDER)})
        self.assertEqual(result.status_code, 503)
        self.np.create_ttn.assert_not_called()
