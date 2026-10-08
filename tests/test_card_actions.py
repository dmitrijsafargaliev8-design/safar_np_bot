"""Photo-first Telegram cards, copy button, webhook callbacks and access control."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import telebot

import bot as app_module
from order_card import build_card_keyboard, format_order_card
from order_pipeline import OrderPipeline
from test_order_pipeline import ORDER, message


class PhotoCardTests(unittest.TestCase):
    def test_native_two_row_copy_keyboard(self):
        kb = build_card_keyboard("20400000000001")
        self.assertEqual(len(kb.keyboard), 2)
        self.assertEqual([len(row) for row in kb.keyboard], [2, 2])
        self.assertEqual(kb.keyboard[0][0].callback_data, "safar:track")
        self.assertEqual(kb.keyboard[0][1].callback_data, "safar:edit")
        self.assertEqual(kb.keyboard[1][0].copy_text.text, "20400000000001")
        self.assertEqual(kb.keyboard[1][1].callback_data, "safar:history")

    def test_caption_looks_like_photo_first_example_without_cod(self):
        order = app_module.parse_order(ORDER)
        card = format_order_card(order, {"ttn": "20400000000001"}, 3)
        for text in ("✅ ЗАКАЗ ОБРАБОТАН", "Фото товара · 3 шт.",
                     "🔖 ТТН: 20400000000001", "Получатель", "Оценка: 1600 грн",
                     "Без наложки"):
            self.assertIn(text, card)
        self.assertNotIn("Наложка: 1600 грн", card)


class CardCallbackTests(unittest.TestCase):
    def setUp(self):
        self.now = 1791400000.0
        self.telegram = telebot.TeleBot("123456:TEST_ONLY", threaded=False)
        self.telegram.send_photo = Mock(return_value=SimpleNamespace(message_id=1000))
        self.telegram.send_message = Mock(return_value=SimpleNamespace(message_id=1001))
        self.telegram.answer_callback_query = Mock()
        self.np = Mock()
        self.np.create_ttn.return_value = {"ttn": "20400000000001"}
        self.np.get_ttn_status.return_value = {
            "Number": "20400000000001", "Status": "У відділенні",
            "StatusCode": "7", "ScheduledDeliveryDate": "09.10.2026",
        }
        self.np.is_ttn_deleted.return_value = False
        self.pipeline = OrderPipeline(":memory:", app_module.parse_order, self.np, self.telegram,
                                      clock=lambda: self.now)
        self.addCleanup(self.pipeline.close)
        for name, value in (("bot", self.telegram), ("np_client", self.np),
                            ("_pipeline", self.pipeline), ("WEBHOOK_SECRET", "test-secret"),
                            ("ALLOWED_CHAT_IDS", {100})):
            override = patch.object(app_module, name, value)
            override.start()
            self.addCleanup(override.stop)
        env = patch.dict(os.environ, {"SAFAR_DISABLE_BACKGROUND": "1"})
        env.start()
        self.addCleanup(env.stop)
        app_module.register_command_handlers(self.telegram)
        self.http = app_module.app.test_client()
        self.pipeline.ingest(message(1, caption=ORDER), 1)
        self.now += 4
        self.pipeline.tick()
        self.np.create_ttn.reset_mock()
        self.telegram.send_message.reset_mock()

    def press(self, action, *, owner=99, chat=100, card=1000, update=10):
        callback = {
            "id": "callback-123", "from": {"id": owner, "first_name": "Test",
                                         "is_bot": False},
            "message": {"message_id": card, "date": 1791400000,
                        "chat": {"id": chat, "type": "private"},
                        "text": "Saved order card"},
            "chat_instance": "test", "data": f"safar:{action}",
        }
        return self.http.post(
            "/webhook", json={"update_id": update, "callback_query": callback},
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
        )

    def test_track_button_gets_live_status_without_issuing_ttn(self):
        res = self.press("track")
        self.assertEqual(res.status_code, 200)
        self.np.get_ttn_status.assert_called_once_with("20400000000001", phone="380500000001")
        self.assertIn("У відділенні", self.telegram.send_message.call_args.kwargs["text"])
        self.np.create_ttn.assert_not_called()
        self.telegram.answer_callback_query.assert_called_once()

    def test_edit_button_does_not_change_existing_ttn(self):
        res = self.press("edit")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Сначала удали действующую ТТН",
                      self.telegram.send_message.call_args.kwargs["text"])
        self.np.create_ttn.assert_not_called()

    def test_history_button_reports_current_ttn(self):
        res = self.press("history")
        self.assertEqual(res.status_code, 200)
        text = self.telegram.send_message.call_args.kwargs["text"]
        self.assertIn("ИСТОРИЯ ЗАКАЗА", text)
        self.assertIn("20400000000001", text)
        self.np.create_ttn.assert_not_called()

    def test_other_user_or_chat_cannot_operate_buttons(self):
        self.assertEqual(self.press("track", owner=42, update=10).status_code, 200)
        self.assertEqual(self.press("history", chat=200, update=11).status_code, 200)
        self.np.get_ttn_status.assert_not_called()
        self.telegram.send_message.assert_not_called()
        self.np.create_ttn.assert_not_called()
        self.assertTrue(self.telegram.answer_callback_query.call_args.kwargs["show_alert"])

    def test_invalid_callback_requires_valid_webhook_signature(self):
        callback = {"update_id": 99, "callback_query": {"data": "safar:track"}}
        res = self.http.post("/webhook", json=callback, headers={})
        self.assertEqual(res.status_code, 403)
        self.np.get_ttn_status.assert_not_called()
