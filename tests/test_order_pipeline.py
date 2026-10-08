"""Message-to-shipment tests with real parsing and mocked external APIs."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bot import parse_order
from np_client import NovaPoshtaTemporaryError, NovaPoshtaUncertainError
from order_pipeline import OrderPipeline


ORDER = "Одесса\nНП 8\nІваненко Марія\n0500000001\nОценка 1600"


def message(mid, *, caption=None, text=None, group=None, photo=True, chat=100, unique=None):
    data = {"message_id": mid, "date": 1791400000, "chat": {"id": chat, "type": "private"},
            "from": {"id": 99, "first_name": "Test", "is_bot": False}}
    if photo:
        data["photo"] = [{"file_id": f"file-{mid}", "file_unique_id": unique or f"unique-{mid}",
                          "width": 1200, "height": 1600}]
    if caption is not None:
        data["caption"] = caption
    if text is not None:
        data["text"] = text
    if group is not None:
        data["media_group_id"] = group
    return data


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.now = 1791400000.0
        self.client = Mock()
        self.client.create_ttn.return_value = {"ttn": "20400000000001", "ref": "test-ref"}
        self.bot = Mock()
        self.sent_id = 10000
        def sent(**kwargs):
            self.sent_id += 1
            return SimpleNamespace(message_id=self.sent_id)
        for name in ("send_photo", "send_video", "send_document", "send_animation", "send_message"):
            getattr(self.bot, name).side_effect = sent
        self.bot.send_media_group.side_effect = lambda **kw: [sent() for _ in kw["media"]]
        self.pipeline = OrderPipeline(":memory:", parse_order, self.client, self.bot, clock=lambda: self.now)
        self.addCleanup(self.pipeline.close)

    def drain(self):
        self.now += 4
        count = 0
        while self.pipeline.tick():
            count += 1
            self.assertLess(count, 100)

    def job(self, chat=100):
        return self.pipeline.list_orders(chat, 99)[0]

    def test_single_photo_with_caption_returns_photo_and_ttn(self):
        self.pipeline.ingest(message(1, caption=ORDER), 1)
        self.drain()
        self.client.create_ttn.assert_called_once()
        sent = self.bot.send_photo.call_args.kwargs
        self.assertEqual(sent["photo"], "file-1")
        self.assertIn("20400000000001", sent["caption"])
        self.assertIn("Без наложки", sent["caption"])
        self.assertEqual(sent["reply_to_message_id"], 1)

    def test_album_caption_on_second_photo_one_shipment_all_photos(self):
        self.pipeline.ingest(message(3, group="album"), 3)
        self.pipeline.ingest(message(2, caption=ORDER, group="album"), 2)
        self.pipeline.ingest(message(1, group="album"), 1)
        self.assertFalse(self.pipeline.tick())
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.bot.send_media_group.assert_called_once()
        group = self.bot.send_media_group.call_args.kwargs["media"]
        self.assertEqual([m.media for m in group], ["file-1", "file-2", "file-3"])
        self.assertIn("20400000000001", group[0].caption)
        self.assertEqual(self.job()["anchor_id"], 2)

    def test_duplicate_update_not_reprocessed(self):
        self.assertTrue(self.pipeline.ingest(message(1, caption=ORDER), 1))
        self.assertFalse(self.pipeline.ingest(message(1, caption=ORDER), 1))
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.bot.send_photo.assert_called_once()

    def test_repeated_caption_within_album_one_ttn(self):
        for mid in (1, 2, 3):
            self.pipeline.ingest(message(mid, caption=ORDER, group="album"), mid)
        self.drain()
        self.client.create_ttn.assert_called_once()

    def test_two_recipients_in_album_no_shipment(self):
        self.pipeline.ingest(message(1, caption=ORDER, group="album"), 1)
        self.pipeline.ingest(message(2, caption=ORDER.replace("0500000001", "0500000002"), group="album"), 2)
        self.drain()
        self.client.create_ttn.assert_not_called()
        self.assertEqual(self.job()["state"], "invalid")

    def test_same_album_id_in_two_chats_keeps_orders_separate(self):
        self.pipeline.ingest(message(1, caption=ORDER, group="album", chat=100), 1)
        self.pipeline.ingest(message(2, caption=ORDER, group="album", chat=200), 2)
        self.drain()
        self.assertEqual(self.client.create_ttn.call_count, 2)

    def test_photo_without_caption_kept_for_reply_correction(self):
        original = message(1)
        self.pipeline.ingest(original, 1)
        self.drain()
        self.client.create_ttn.assert_not_called()
        error_id = self.sent_id
        correction = message(2, text=ORDER, photo=False)
        correction["reply_to_message"] = {"message_id": error_id}
        self.pipeline.ingest(correction, 2)
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.assertEqual(self.bot.send_photo.call_args.kwargs["photo"], "file-1")

    def test_reply_to_original_photo_attaches_text(self):
        original = message(1)
        self.pipeline.ingest(original, 1)
        correction = message(2, text=ORDER, photo=False)
        correction["reply_to_message"] = original
        self.pipeline.ingest(correction, 2)
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.assertEqual(len(self.pipeline.list_orders(100, 99)), 1)

    def test_reforwarded_same_order_returns_previous_ttn(self):
        original = message(1, caption=ORDER, unique="same-photo")
        original["forward_origin"] = {"type": "hidden_user", "sender_user_name": "Seller", "date": 1791300000}
        self.pipeline.ingest(original, 1)
        self.drain()
        repeat = copy.deepcopy(original)
        repeat["message_id"] = 9
        self.pipeline.ingest(repeat, 9)
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.assertIn("повторная ТТН не создавалась", self.bot.send_photo.call_args.kwargs["caption"])

    def test_different_product_photo_is_a_different_order(self):
        self.pipeline.ingest(message(1, caption=ORDER), 1)
        self.pipeline.ingest(message(2, caption=ORDER), 2)
        self.drain()
        self.assertEqual(self.client.create_ttn.call_count, 2)

    def test_updated_channel_source_never_mislabels_existing_ttn(self):
        original = message(1, caption=ORDER)
        original["forward_origin"] = {"type": "channel", "chat": {"id": -100123}, "message_id": 500, "date": 1791300000}
        self.pipeline.ingest(original, 1)
        self.drain()
        changed = copy.deepcopy(original)
        changed["message_id"] = 2
        changed["caption"] = ORDER.replace("1600", "1800")
        self.pipeline.ingest(changed, 2)
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.assertIn("Оценка: 1600", self.bot.send_photo.call_args.kwargs["caption"])

    def test_different_product_texts_are_distinct_orders(self):
        self.pipeline.ingest(message(1, text="Артикул: 1234\n" + ORDER, photo=False), 1)
        self.pipeline.ingest(message(2, text="Артикул: 5678\n" + ORDER, photo=False), 2)
        self.drain()
        self.assertEqual(self.client.create_ttn.call_count, 2)

    def test_telegram_notification_failure_does_not_repeat_shipment(self):
        self.bot.send_photo.side_effect = RuntimeError("network")
        self.pipeline.ingest(message(1, caption=ORDER), 1)
        self.drain()
        self.assertEqual(self.job()["state"], "created")
        self.assertFalse(self.job()["notified"])
        self.bot.send_photo.side_effect = lambda **kw: SimpleNamespace(message_id=20000)
        self.now += 20
        self.pipeline.tick()
        self.client.create_ttn.assert_called_once()
        self.assertTrue(self.job()["notified"])

    def test_save_timeout_never_retries_create(self):
        self.client.create_ttn.side_effect = NovaPoshtaUncertainError("Проверь накладные")
        self.pipeline.ingest(message(1, caption=ORDER), 1)
        self.drain()
        retry = message(2, text="/retry", photo=False)
        retry["reply_to_message"] = {"message_id": self.sent_id}
        self.pipeline.ingest(retry, 2, retry=True)
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.assertEqual(self.job()["state"], "uncertain")

    def test_read_timeout_is_retried_before_creating_ttn(self):
        self.client.create_ttn.side_effect = [NovaPoshtaTemporaryError("retry"), {"ttn": "20400000000001"}]
        self.pipeline.ingest(message(1, caption=ORDER), 1)
        self.drain()
        self.assertEqual(self.job()["state"], "collecting")
        self.now += 10
        self.pipeline.tick()
        self.assertEqual(self.job()["state"], "created")

    def test_edited_invalid_caption_uses_original_photo(self):
        self.pipeline.ingest(message(1, caption=ORDER.replace("Оценка 1600", "")), 1)
        self.drain()
        self.client.create_ttn.assert_not_called()
        self.pipeline.ingest(message(1, caption=ORDER), 2, edited=True)
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.assertEqual(self.bot.send_photo.call_args.kwargs["photo"], "file-1")

    def test_edit_of_created_order_does_not_create_new_ttn(self):
        self.pipeline.ingest(message(1, caption=ORDER), 1)
        self.drain()
        self.pipeline.ingest(message(1, caption=ORDER.replace("1600", "1800")), 2, edited=True)
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.assertIn("Изменение текста не меняет", self.bot.send_photo.call_args.kwargs["caption"])

    def test_completed_jobs_do_not_starve_later_queue_entries(self):
        for mid in range(1, 62):
            self.pipeline.ingest(message(mid, caption=ORDER), mid)
            self.drain()
        self.assertEqual(self.client.create_ttn.call_count, 61)

    def test_journal_survives_worker_restart_without_second_ttn(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "orders.sqlite3")
            pipeline = OrderPipeline(path, parse_order, self.client, self.bot, clock=lambda: self.now)
            pipeline.ingest(message(1, caption=ORDER), 1)
            self.now += 4
            pipeline.tick()
            pipeline.close()
            resumed = OrderPipeline(path, parse_order, self.client, self.bot, clock=lambda: self.now)
            try:
                self.assertTrue(resumed.has_update(1))
                self.assertEqual(resumed.list_orders(100, 99)[0]["result"]["ttn"], "20400000000001")
                self.assertFalse(resumed.tick())
                self.client.create_ttn.assert_called_once()
            finally:
                resumed.close()

    def test_crash_during_save_recovers_to_uncertain(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "orders.sqlite3")
            pipeline = OrderPipeline(path, parse_order, self.client, self.bot, clock=lambda: self.now)
            pipeline.ingest(message(1, caption=ORDER), 1)
            with pipeline.lock, pipeline.db:
                job = pipeline.list_orders(100, 99)[0]
                job["state"] = "processing"
                order = parse_order(ORDER)
                identity = pipeline._identity(job, order)
                pipeline.db.execute("INSERT INTO receipts VALUES (?,?,?,?)", (identity, "creating", None, self.now))
                pipeline._write(job)
            pipeline.close()
            resumed = OrderPipeline(path, parse_order, self.client, self.bot, clock=lambda: self.now)
            try:
                self.now += 4
                resumed.tick()
                self.client.create_ttn.assert_not_called()
                self.assertEqual(resumed.list_orders(100, 99)[0]["state"], "uncertain")
            finally:
                resumed.close()
