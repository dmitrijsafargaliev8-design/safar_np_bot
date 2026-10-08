"""Slash commands through the signed webhook, without live Telegram sends."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import telebot
import bot as app_module
from np_client import NovaPoshtaTemporaryError
from order_pipeline import OrderPipeline
from test_order_pipeline import message, ORDER


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.now = 1791400000.0
        self.telegram = telebot.TeleBot("123456:TEST_ONLY", threaded=False)
        self.telegram.reply_to = Mock(return_value=SimpleNamespace(message_id=2000))
        self.telegram.send_message = Mock(return_value=SimpleNamespace(message_id=1001))
        self.telegram.send_photo = Mock(return_value=SimpleNamespace(message_id=1000))
        self.telegram.get_me = Mock(return_value=SimpleNamespace(id=123456))
        self.np = Mock()
        self.np.ping.return_value = True
        self.np.create_ttn.return_value = {"ttn": "20400000000001"}
        self.np.get_ttn_status.return_value = {"Number": "20400000000001", "StatusCode": "1",
                                               "Status": "Нова пошта очікує на надходження"}
        self.pipeline = OrderPipeline(":memory:", app_module.parse_order, self.np, self.telegram,
                                      clock=lambda: self.now)
        self.addCleanup(self.pipeline.close)
        for name, value in (("bot", self.telegram), ("np_client", self.np), ("_pipeline", self.pipeline),
                            ("WEBHOOK_SECRET", "test-secret"), ("ALLOWED_CHAT_IDS", {100})):
            override = patch.object(app_module, name, value)
            override.start()
            self.addCleanup(override.stop)
        env = patch.dict(os.environ, {"SAFAR_DISABLE_BACKGROUND": "1"})
        env.start()
        self.addCleanup(env.stop)
        app_module.register_command_handlers(self.telegram)
        self.http = app_module.app.test_client()

    def send_command(self, text, *, update=10, reply=None, chat=100):
        data = message(update, text=text, photo=False, chat=chat)
        if reply:
            data["reply_to_message"] = {"message_id": reply, "date": 1791400000,
                                         "chat": data["chat"], "text": "Saved order card"}
        result = self.http.post("/webhook", json={"update_id": update, "message": data},
                                headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"})
        self.assertEqual(result.status_code, 200)
        return result

    def saved_order(self):
        self.pipeline.ingest(message(1, caption=ORDER), 1)
        self.now += 4
        self.pipeline.tick()
        self.np.create_ttn.reset_mock()

    def test_menu_help_example_orders_status_and_unknown_commands(self):
        for update, (command, expected) in enumerate((
            ("/start", "/track"), ("/menu", "/retry"), ("/help", "Оценка — объявленная стоимость"),
            ("/example", app_module.ORDER_EXAMPLE), ("/orders", "Заказов пока нет"),
            ("/status", "Telegram: OK"), ("/retry", "Ответь командой /retry"),
            ("/unknown", "Эта команда не найдена"),
        ), 10):
            with self.subTest(command=command):
                self.send_command(command, update=update)
                actual = (self.telegram.send_message.call_args.args[1] if command == "/retry"
                          else self.telegram.reply_to.call_args.args[1])
                self.assertIn(expected, actual)
        self.np.create_ttn.assert_not_called()
        self.assertFalse(self.pipeline.list_orders(100, 99))

    def test_track_explicit_number_and_bot_mention_only_reads_status(self):
        self.send_command("/track@SafarTestBot 2040 0000 0000 01")
        self.np.get_ttn_status.assert_called_once_with("20400000000001", phone="")
        self.assertIn("Нова пошта очікує", self.telegram.reply_to.call_args.args[1])
        self.np.create_ttn.assert_not_called()

    def test_track_bad_number_or_missing_order_does_not_call_api(self):
        for update, text in enumerate(("/track", "/track 123", "/track abc20400000000001"), 10):
            self.send_command(text, update=update)
            self.assertIn("14 цифр", self.telegram.reply_to.call_args.args[1])
        self.np.get_ttn_status.assert_not_called()
        self.np.create_ttn.assert_not_called()

    def test_track_latest_order_and_reply_to_card_preserve_shipment(self):
        self.saved_order()
        for update, reply in ((10, None), (11, 1000), (12, 1)):
            self.send_command("/track", update=update, reply=reply)
            self.np.get_ttn_status.assert_called_with("20400000000001", phone="380500000001")
        self.np.create_ttn.assert_not_called()
        self.assertEqual(self.pipeline.list_orders(100, 99)[0]["state"], "created")

    def test_reply_lookup_cannot_read_another_owners_order(self):
        self.saved_order()
        self.assertIsNone(self.pipeline.order_for_message(100, 42, 1000))
        self.assertIsNone(self.pipeline.order_for_message(200, 99, 1000))
        self.send_command("/track", reply=9999)
        self.np.get_ttn_status.assert_not_called()

    def test_track_error_does_not_create_a_shipment(self):
        self.np.get_ttn_status.side_effect = NovaPoshtaTemporaryError("unavailable")
        self.send_command("/track 20400000000001")
        self.assertIn("Повтори /track позже", self.telegram.reply_to.call_args.args[1])
        self.np.create_ttn.assert_not_called()

    def test_deleted_tracking_does_not_show_a_future_delivery(self):
        self.np.get_ttn_status.return_value = {"Number": "20400000000001", "StatusCode": "2",
                                               "Status": "Видалено", "ScheduledDeliveryDate": "09.10.2026"}
        self.send_command("/track 20400000000001")
        reply = self.telegram.reply_to.call_args.args[1]
        self.assertIn("Видалено", reply)
        self.assertNotIn("09.10.2026", reply)

    def test_commands_in_disallowed_chat_do_not_reply_or_read_api(self):
        for update, (command, _) in enumerate(app_module.BOT_COMMANDS, 10):
            self.send_command("/" + command, update=update, chat=200)
        self.telegram.reply_to.assert_not_called()
        self.telegram.send_message.assert_not_called()
        self.np.get_ttn_status.assert_not_called()
        self.np.ping.assert_not_called()
        self.np.create_ttn.assert_not_called()

    def test_order_example_has_declared_value_without_cod(self):
        parsed = app_module.parse_order(app_module.ORDER_EXAMPLE)
        self.assertEqual(parsed["cost"], 1600)
        self.assertEqual(parsed["cod_amount"], 0)


class TelegramMenuTests(unittest.TestCase):
    def test_startup_publishes_and_verifies_real_command_descriptions(self):
        telegram = Mock()
        telegram.set_my_commands.return_value = True
        telegram.set_chat_menu_button.return_value = True
        telegram.get_my_commands.return_value = [telebot.types.BotCommand(c, d) for c, d in app_module.BOT_COMMANDS]
        with patch.object(app_module, "bot", telegram), patch.object(app_module, "_telegram_commands", []):
            self.assertTrue(app_module.ensure_command_menu())
            self.assertEqual(app_module._telegram_commands, [c for c, _ in app_module.BOT_COMMANDS])
            self.assertEqual([c.kwargs["language_code"] for c in telegram.set_my_commands.call_args_list], ["", "ru", "uk"])
            self.assertEqual(telegram.set_chat_menu_button.call_args.kwargs["menu_button"].type, "commands")

    def test_telegram_rejection_or_mismatched_menu_is_not_reported_as_ready(self):
        for reject in (True, False):
            with self.subTest(reject=reject):
                telegram = Mock()
                telegram.set_my_commands.return_value = not reject
                telegram.set_chat_menu_button.return_value = True
                telegram.get_my_commands.return_value = []
                with patch.object(app_module, "bot", telegram), patch.object(app_module, "_telegram_commands", []):
                    self.assertFalse(app_module.ensure_command_menu())
                    self.assertEqual(app_module._telegram_commands, [])
