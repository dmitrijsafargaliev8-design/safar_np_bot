"""End-to-end local tests: staged edits, safe retries and sender preference."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bot import parse_order
from order_pipeline import OrderPipeline
from order_edits import parse_field_patch, apply_field_patch
from test_order_pipeline import ORDER, message


class SmartOrderTests(unittest.TestCase):
    def setUp(self):
        self.now = 1791400000.0
        self.default = Mock()
        self.default.create_ttn.return_value = {"ttn": "20400000000001"}
        self.default.is_ttn_deleted.return_value = False
        self.alternate = Mock()
        self.alternate.create_ttn.return_value = {"ttn": "20400000000002"}
        self.alternate.is_ttn_deleted.return_value = False
        self.telegram = Mock()
        self.mid = 900
        def sent(**kw):
            self.mid += 1
            return SimpleNamespace(message_id=self.mid)
        for name in ("send_message", "send_photo", "send_video", "send_document", "send_animation"):
            getattr(self.telegram, name).side_effect = sent
        self.telegram.send_media_group.side_effect = lambda **kw: [sent() for _ in kw["media"]]
        self.pipe = OrderPipeline(":memory:", parse_order, self.default, self.telegram,
                                  clock=lambda: self.now,
                                  sender_clients={"default": self.default, "other": self.alternate})
        self.addCleanup(self.pipe.close)

    def drain(self):
        self.now += 5
        for _ in range(30):
            if not self.pipe.tick():
                return
        self.fail("queue stuck")

    def current(self):
        return self.pipe.list_orders(100, 99)[0]

    def test_single_field_edit_staged_until_old_ttn_deleted(self):
        self.pipe.ingest(message(1, caption=ORDER), 1)
        self.drain()
        card = self.mid
        self.assertEqual(self.current()["state"], "created")
        updated = message(2, text="Телефон: 0500000002", photo=False)
        updated["reply_to_message"] = {"message_id": card}
        self.pipe.ingest(updated, 2)
        self.drain()
        self.assertEqual(self.default.create_ttn.call_count, 1)
        self.assertEqual(self.current()["order"]["phone"], "380500000001")
        self.assertEqual(self.current()["pending_edits"]["phone"], "0500000002")

        retry = message(3, text="/retry", photo=False)
        retry["reply_to_message"] = {"message_id": self.mid}
        self.pipe.ingest(retry, 3, retry=True)
        self.drain()
        self.assertEqual(self.default.create_ttn.call_count, 1)
        self.assertEqual(self.current()["order"]["phone"], "380500000001")
        self.assertEqual(self.current()["pending_edits"]["phone"], "0500000002")

        self.default.is_ttn_deleted.return_value = True
        retry2 = message(4, text="/retry", photo=False)
        retry2["reply_to_message"] = {"message_id": self.mid}
        self.pipe.ingest(retry2, 4, retry=True)
        self.drain()
        self.assertEqual(self.default.create_ttn.call_count, 2)
        self.assertEqual(self.default.create_ttn.call_args.kwargs["phone"], "380500000002")
        self.assertNotIn("pending_edits", self.current())

    def test_two_field_patch_without_discarding_photos(self):
        self.pipe.ingest(message(1, caption=ORDER), 1)
        self.drain()
        new = message(2, text="Оценка: 2100\nНаложка: 0", photo=False)
        new["reply_to_message"] = {"message_id": self.mid}
        self.pipe.ingest(new, 2)
        self.drain()
        self.assertEqual(self.current()["pending_edits"],
                         {"cost": "2100", "cod_amount": "0"})
        self.assertEqual(self.pipe.attachments(self.current())[0]["file_id"], "file-1")
        self.assertEqual(self.default.create_ttn.call_count, 1)

    def test_preference_is_scoped_and_snapshotted_before_creation(self):
        self.pipe.set_sender_preference(100, 99, "other")
        self.assertEqual(self.pipe.sender_preference(100, 99), "other")
        self.assertEqual(self.pipe.sender_preference(100, 45), "default")
        self.pipe.ingest(message(1, caption=ORDER), 1)
        self.pipe.set_sender_preference(100, 99, "default")
        self.drain()
        self.alternate.create_ttn.assert_called_once()
        self.default.create_ttn.assert_not_called()
        self.assertEqual(self.current()["sender_profile"], "other")

    def test_rejected_unsupported_sender_never_saves(self):
        with self.assertRaisesRegex(Exception, "Профиль"):
            self.pipe.set_sender_preference(100, 99, "unknown")
        self.assertEqual(self.pipe.sender_preference(100, 99), "default")
        self.default.create_ttn.assert_not_called()

    def test_edit_field_validation(self):
        order = parse_order(ORDER)
        with self.assertRaises(ValueError):
            apply_field_patch(order, {"cost": "-12"})
        with self.assertRaises(ValueError):
            apply_field_patch(order, {"warehouse": "not found"})
        self.assertEqual(parse_field_patch("Телефон: 0500000002"), {"phone": "0500000002"})
        self.assertIsNone(parse_field_patch(ORDER))
        self.assertEqual(apply_field_patch(order, {"cod_amount": "без наложки"})["cod_amount"], 0)


if __name__ == "__main__":
    unittest.main()
