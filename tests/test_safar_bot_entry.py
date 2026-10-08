"""The app fails closed independently of the legacy bot rollout policy."""
import unittest
from unittest.mock import Mock, patch

import telebot
import bot as app_module
from access_policy import AccessPolicy
from order_pipeline import OrderCorrectionConflict


class AppBotEntryTests(unittest.TestCase):
    def test_browser_access_needs_both_explicit_allowlists(self):
        for chats, users in (("", ""), ("100", ""), ("", "100")):
            policy = AccessPolicy(chats=chats, users=users, strict=False)
            with patch.object(app_module, "ACCESS_POLICY", policy), patch.object(app_module, "ALLOWED_CHAT_IDS", policy.chats):
                self.assertFalse(app_module._app_access_permits(100, 100))
        policy = AccessPolicy(chats="100,-200", users="100", strict=False)
        with patch.object(app_module, "ACCESS_POLICY", policy), patch.object(app_module, "ALLOWED_CHAT_IDS", policy.chats):
            self.assertTrue(app_module._app_access_permits(100, 100))
            self.assertTrue(app_module._app_access_permits(-200, 100))
            self.assertFalse(app_module._app_access_permits(-200, 101))
            self.assertFalse(app_module._app_access_permits(-201, 100))

    def pairing_message(self, chat_type="private"):
        return telebot.types.Message.de_json({
            "message_id": 1, "date": 1000,
            "chat": {"id": 100, "type": chat_type},
            "from": {"id": 100, "is_bot": False, "first_name": "Test"},
            "text": "/app ABCD-EFGH-JKLM",
        })

    def test_pairing_command_only_approves_verified_private_actor(self):
        telegram = telebot.TeleBot("123456:TEST_ONLY", threaded=False)
        telegram.reply_to = Mock()
        auth = Mock()
        auth.approve_pairing.return_value = True
        policy = AccessPolicy(chats="100", users="100", strict=True)
        with patch.object(app_module, "ACCESS_POLICY", policy), patch.object(app_module, "ALLOWED_CHAT_IDS", policy.chats), patch.dict(app_module.app.extensions, {"safar_auth": auth}):
            app_module.register_command_handlers(telegram)
            telegram.process_new_messages([self.pairing_message()])
            auth.approve_pairing.assert_called_once_with("ABCD-EFGH-JKLM", 100, 100)
            auth.approve_pairing.reset_mock()
            telegram.process_new_messages([self.pairing_message("group")])
            auth.approve_pairing.assert_not_called()

    def test_stale_app_retry_is_acknowledged_without_scheduling_carrier_work(self):
        telegram, pipeline = Mock(), Mock()
        pipeline.has_update.return_value = False
        pipeline.ingest.side_effect = OrderCorrectionConflict("stale test draft")
        policy = AccessPolicy(chats="100", users="100", strict=True)
        incoming = {"message_id": 2, "date": 1000,
                    "chat": {"id": 100, "type": "private"},
                    "from": {"id": 100, "is_bot": False},
                    "text": "/retry", "reply_to_message": {"message_id": 1}}
        with patch.object(app_module, "ACCESS_POLICY", policy), patch.object(app_module, "ALLOWED_CHAT_IDS", policy.chats), patch.object(app_module, "bot", telegram), patch.object(app_module, "WEBHOOK_SECRET", "test-only"), patch.object(app_module, "get_pipeline", return_value=pipeline):
            response = app_module.app.test_client().post('/webhook',
                json={"update_id": 10, "message": incoming},
                headers={"X-Telegram-Bot-Api-Secret-Token": "test-only"})
        self.assertEqual(response.status_code, 200)
        pipeline.remember_update.assert_called_once_with(10)
        self.assertIn("устарела", telegram.send_message.call_args.args[1])
        self.assertIn("ТТН не изменена", telegram.send_message.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
