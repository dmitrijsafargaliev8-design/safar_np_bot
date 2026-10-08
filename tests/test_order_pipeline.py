"""Message-to-shipment tests with real parsing and mocked external APIs."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bot import parse_order
from np_client import NovaPoshtaClient, NovaPoshtaError, NovaPoshtaTemporaryError, NovaPoshtaUncertainError
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
        self.client.is_ttn_deleted.return_value = False
        self.bot = Mock()
        self.sent_id = 10000
        def sent(**kwargs):
            self.sent_id += 1
            return SimpleNamespace(message_id=self.sent_id)
        for name in ("send_photo", "send_video", "send_document", "send_animation", "send_message"):
            getattr(self.bot, name).side_effect = sent
        self.bot.send_media_group.side_effect = lambda **kw: [sent() for _ in kw["media"]]
        self.pipeline = self.make_pipeline(":memory:")
        self.addCleanup(self.pipeline.close)

    def make_pipeline(self, path):
        return OrderPipeline(path, parse_order, self.client, self.bot, clock=lambda: self.now)

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

    def test_forwarded_photo_with_oblast_before_city_creates_correct_shipment(self):
        client = NovaPoshtaClient("fake-test-api-key")
        client.get_sender_info = Mock(return_value={"city_ref": "sender-city", "sender_ref": "sender",
            "address_ref": "sender-wh", "contact_ref": "sender-contact", "phone": "380500000002"})
        client.get_or_create_recipient = Mock(return_value=("recipient", "recipient-contact", "380500000001"))
        def api(model, method, properties):
            if (model, method) == ("Address", "getCities"):
                self.assertEqual(properties["FindByString"], "Васильков")
                return [{"Ref": "vasylkiv-city", "Description": "Васильків", "DescriptionRu": "Васильков", "Area": "kyiv-area"}]
            if (model, method) == ("Address", "getAreas"):
                return [{"Ref": "kyiv-area", "Description": "Київська", "DescriptionRu": "Киевская"}]
            if (model, method) == ("Address", "getWarehouses"):
                self.assertEqual(properties["CityRef"], "vasylkiv-city")
                return [{"Ref": "vasylkiv-wh4", "Number": "4", "Description": "Відділення №4"}]
            if (model, method) == ("InternetDocument", "save"):
                self.assertEqual(properties["CityRecipient"], "vasylkiv-city")
                self.assertEqual(properties["RecipientAddress"], "vasylkiv-wh4")
                self.assertNotIn("BackwardDeliveryData", properties)
                return [{"IntDocNumber": "20400000000001"}]
            if (model, method) == ("TrackingDocument", "getStatusDocuments"):
                return [{"Number": "20400000000001", "StatusCode": "1"}]
            self.fail("Unexpected API operation")
        client._call = Mock(side_effect=api)
        self.pipeline.client = client
        incoming = message(1, caption="Киевская обл, Васильков, нп 4, Іваненко Марія, 0500000001, Оценка 1600")
        incoming["forward_origin"] = {"type": "hidden_user", "sender_user_name": "Seller", "date": 1791300000}
        self.pipeline.ingest(incoming, 1)
        self.drain()
        self.assertEqual(self.job()["state"], "created")
        self.assertEqual(self.bot.send_photo.call_args.kwargs["photo"], "file-1")
        repeat = copy.deepcopy(incoming)
        repeat["message_id"] = 2
        self.pipeline.ingest(repeat, 2)
        self.drain()
        saves = [call for call in client._call.call_args_list if call.args[:2] == ("InternetDocument", "save")]
        self.assertEqual(len(saves), 1)

    def test_saved_inline_branch_name_retry_keeps_photo_and_creates_one_shipment(self):
        caption = "Великодолинське Маріїнський 5. Відділення 2. Іваненко Марія Петрівна.\n\n0500000001\n\nОценка 800"
        incoming = message(1, caption=caption)
        incoming["forward_origin"] = {"type": "hidden_user", "sender_user_name": "Seller", "date": 1791300000}
        # Reproduce a job already saved as invalid by the previous parser.
        self.pipeline.parser = Mock(side_effect=ValueError("Не хватает полей: ФИО"))
        self.pipeline.ingest(incoming, 1)
        self.drain()
        self.assertEqual(self.job()["state"], "invalid")
        self.client.create_ttn.assert_not_called()
        error_id = self.sent_id

        client = NovaPoshtaClient("fake-test-api-key")
        client.get_sender_info = Mock(return_value={"city_ref": "sender-city", "sender_ref": "sender",
            "address_ref": "sender-wh", "contact_ref": "sender-contact", "phone": "380500000002"})
        client.get_or_create_recipient = Mock(return_value=("recipient", "recipient-contact", "380500000001"))
        def api(model, method, properties):
            if (model, method) == ("Address", "getCities"):
                if properties["FindByString"] == "Великодолинське":
                    return [{"Ref": "velykodolynske-city", "Description": "Великодолинське"}]
                return []
            if (model, method) == ("Address", "getWarehouses"):
                self.assertEqual(properties["CityRef"], "velykodolynske-city")
                return [{"Ref": "wrong-wh12", "Number": "12"}, {"Ref": "correct-wh2", "Number": "2"}]
            if (model, method) == ("InternetDocument", "save"):
                self.assertEqual(properties["CityRecipient"], "velykodolynske-city")
                self.assertEqual(properties["RecipientAddress"], "correct-wh2")
                self.assertEqual(properties["ServiceType"], "WarehouseWarehouse")
                self.assertEqual(properties["Cost"], "800")
                self.assertNotIn("BackwardDeliveryData", properties)
                return [{"IntDocNumber": "20400000000001"}]
            if (model, method) == ("TrackingDocument", "getStatusDocuments"):
                return [{"Number": "20400000000001", "StatusCode": "1"}]
            self.fail("Unexpected API operation")
        client._call = Mock(side_effect=api)
        self.pipeline.client, self.pipeline.parser = client, parse_order
        retry = message(2, text="/retry", photo=False)
        retry["reply_to_message"] = {"message_id": error_id}
        self.pipeline.ingest(retry, 2, retry=True)
        self.drain()
        self.assertEqual(self.job()["state"], "created")
        self.assertTrue(self.job()["notified"])
        client.get_or_create_recipient.assert_called_once_with(
            "Іваненко Марія Петрівна", "380500000001", "velykodolynske-city", email="")
        sent = self.bot.send_photo.call_args.kwargs
        self.assertEqual(sent["photo"], "file-1")
        self.assertIn("20400000000001", sent["caption"])
        self.assertIn("Іваненко Марія Петрівна", sent["caption"])

        repeat = copy.deepcopy(incoming)
        repeat["message_id"] = 3
        self.pipeline.ingest(repeat, 3)
        self.drain()
        saves = [call for call in client._call.call_args_list if call.args[:2] == ("InternetDocument", "save")]
        self.assertEqual(len(saves), 1)

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
        self.client.is_ttn_deleted.assert_called_once_with("20400000000001", phone="380500000001")

    def test_deleted_ttn_can_be_recreated_repeatedly_with_same_photo(self):
        self.client.create_ttn.side_effect = [
            {"ttn": f"2040000000000{i}", "ref": f"ref-{i}"} for i in range(1, 5)
        ]
        self.client.is_ttn_deleted.return_value = True
        for mid in range(1, 5):
            self.pipeline.ingest(message(mid, caption=ORDER, unique="same-photo"), mid)
            self.drain()
            self.assertEqual(self.job()["result"]["ttn"], f"2040000000000{mid}")
            self.assertEqual(self.bot.send_photo.call_args.kwargs["photo"], f"file-{mid}")
        self.assertEqual(self.client.create_ttn.call_count, 4)
        self.assertIn("Предыдущая ТТН 20400000000003 удалена", self.bot.send_photo.call_args.kwargs["caption"])
        receipt = self.pipeline.db.execute("SELECT result FROM receipts").fetchone()
        self.assertEqual([r["result"]["ttn"] for r in json.loads(receipt["result"])["history"]],
                         ["20400000000001", "20400000000002", "20400000000003"])

    def test_retry_on_completed_card_checks_deletion_and_preserves_photo(self):
        self.pipeline.ingest(message(1, caption=ORDER), 1)
        self.drain()
        self.client.is_ttn_deleted.return_value = True
        self.client.create_ttn.return_value = {"ttn": "20400000000002"}
        retry = message(2, text="/retry", photo=False)
        retry["reply_to_message"] = {"message_id": self.sent_id}
        self.pipeline.ingest(retry, 2, retry=True)
        self.drain()
        self.assertEqual(self.client.create_ttn.call_count, 2)
        self.assertEqual(self.bot.send_photo.call_args.kwargs["photo"], "file-1")
        self.assertIn("20400000000002", self.bot.send_photo.call_args.kwargs["caption"])

    def test_tracking_timeout_preserves_receipt_and_allows_retry(self):
        self.pipeline.ingest(message(1, caption=ORDER, unique="same"), 1)
        self.drain()
        self.client.is_ttn_deleted.side_effect = NovaPoshtaTemporaryError("status unavailable")
        self.pipeline.ingest(message(2, caption=ORDER, unique="same"), 2)
        self.drain()
        for _ in range(2):
            self.now += 20
            self.pipeline.tick()
        self.client.create_ttn.assert_called_once()
        self.assertEqual(self.job()["state"], "failed")
        self.assertEqual(self.pipeline.db.execute("SELECT state FROM receipts").fetchone()["state"], "created")
        self.client.is_ttn_deleted.side_effect = None
        self.client.is_ttn_deleted.return_value = True
        self.client.create_ttn.return_value = {"ttn": "20400000000002"}
        retry = message(3, text="/retry", photo=False)
        retry["reply_to_message"] = {"message_id": self.sent_id}
        self.pipeline.ingest(retry, 3, retry=True)
        self.drain()
        self.assertEqual(self.client.create_ttn.call_count, 2)

    def test_unknown_check_result_never_unlocks_duplicate(self):
        self.pipeline.ingest(message(1, caption=ORDER, unique="same"), 1)
        self.drain()
        self.client.is_ttn_deleted.return_value = None
        self.pipeline.ingest(message(2, caption=ORDER, unique="same"), 2)
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.assertEqual(self.job()["state"], "collecting")

    def test_replacement_timeout_stays_uncertain_even_if_old_ttn_deleted(self):
        self.pipeline.ingest(message(1, caption=ORDER, unique="same"), 1)
        self.drain()
        self.client.is_ttn_deleted.return_value = True
        self.client.create_ttn.side_effect = NovaPoshtaUncertainError("save timeout")
        self.pipeline.ingest(message(2, caption=ORDER, unique="same"), 2)
        self.drain()
        self.pipeline.ingest(message(3, caption=ORDER, unique="same"), 3)
        self.drain()
        self.assertEqual(self.client.create_ttn.call_count, 2)
        self.assertEqual(self.job()["state"], "uncertain")
        self.assertEqual(self.client.is_ttn_deleted.call_count, 1)

    def test_restart_during_replacement_save_never_creates_third_ttn(self):
        class SimulatedCrash(BaseException):
            pass
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "orders.sqlite3")
            pipeline = self.make_pipeline(path)
            pipeline.ingest(message(1, caption=ORDER, unique="same"), 1)
            self.now += 4
            pipeline.tick()
            self.client.is_ttn_deleted.return_value = True
            self.client.create_ttn.side_effect = SimulatedCrash()
            pipeline.ingest(message(2, caption=ORDER, unique="same"), 2)
            self.now += 4
            with self.assertRaises(SimulatedCrash):
                pipeline.tick()
            pipeline.close()
            resumed = self.make_pipeline(path)
            try:
                resumed.tick()
                self.assertEqual(self.client.create_ttn.call_count, 2)
                self.assertEqual(resumed.list_orders(100, 99)[0]["state"], "uncertain")
                self.assertEqual(resumed.db.execute("SELECT state FROM receipts").fetchone()["state"], "uncertain")
            finally:
                resumed.close()

    def test_known_replacement_failure_can_retry_without_losing_deletion(self):
        self.pipeline.ingest(message(1, caption=ORDER, unique="same"), 1)
        self.drain()
        self.client.is_ttn_deleted.return_value = True
        self.client.create_ttn.side_effect = [NovaPoshtaError("rejected"), {"ttn": "20400000000002"}]
        self.pipeline.ingest(message(2, caption=ORDER, unique="same"), 2)
        self.drain()
        self.assertEqual(self.pipeline.db.execute("SELECT state FROM receipts").fetchone()["state"], "deleted")
        self.pipeline.ingest(message(3, caption=ORDER, unique="same"), 3)
        self.drain()
        self.assertEqual(self.job()["result"]["ttn"], "20400000000002")
        self.assertEqual(self.client.is_ttn_deleted.call_count, 1)

    def test_replacement_updates_all_completed_album_aliases(self):
        first = message(1, caption=ORDER, group="album", unique="first")
        self.pipeline.ingest(first, 1)
        self.drain()
        self.pipeline.ingest(message(2, group="album", unique="second"), 2)
        self.drain()
        self.client.is_ttn_deleted.return_value = True
        self.client.create_ttn.return_value = {"ttn": "20400000000002"}
        # Recreate from the shorter version of the same album.
        self.pipeline.ingest(message(3, caption=ORDER, unique="first"), 3)
        self.drain()
        self.client.is_ttn_deleted.return_value = False
        self.pipeline.ingest(message(4, caption=ORDER, group="repeat", unique="first"), 4)
        self.pipeline.ingest(message(5, group="repeat", unique="second"), 5)
        self.drain()
        self.assertEqual(self.client.create_ttn.call_count, 2)
        self.assertEqual(self.job()["result"]["ttn"], "20400000000002")
        self.client.is_ttn_deleted.assert_called_with("20400000000002", phone="380500000001")

    def test_late_photo_during_replacement_follows_new_receipt(self):
        self.pipeline.ingest(message(1, caption=ORDER, group="album", unique="first"), 1)
        self.drain()
        def replace(**kwargs):
            self.pipeline.ingest(message(2, group="album", unique="second"), 2)
            return {"ttn": "20400000000002"}
        self.client.is_ttn_deleted.return_value = True
        self.client.create_ttn.side_effect = replace
        self.pipeline.ingest(message(3, caption=ORDER, unique="first"), 3)
        self.drain()
        self.client.is_ttn_deleted.return_value = False
        self.now += 4
        self.pipeline.tick()
        self.assertEqual(self.client.create_ttn.call_count, 2)
        self.pipeline.ingest(message(4, caption=ORDER, group="repeat", unique="first"), 4)
        self.pipeline.ingest(message(5, group="repeat", unique="second"), 5)
        self.drain()
        self.assertEqual(self.client.create_ttn.call_count, 2)
        self.assertEqual(self.job()["result"]["ttn"], "20400000000002")

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
            pipeline = self.make_pipeline(path)
            pipeline.ingest(message(1, caption=ORDER), 1)
            self.now += 4
            pipeline.tick()
            pipeline.close()
            resumed = self.make_pipeline(path)
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
            pipeline = self.make_pipeline(path)
            pipeline.ingest(message(1, caption=ORDER), 1)
            with pipeline.lock, pipeline.db:
                job = pipeline.list_orders(100, 99)[0]
                job["state"] = "processing"
                order = parse_order(ORDER)
                identity = pipeline._identity(job, order)
                pipeline.db.execute("INSERT INTO receipts VALUES (?,?,?,?)", (identity, "creating", None, self.now))
                pipeline._write(job)
            pipeline.close()
            resumed = self.make_pipeline(path)
            try:
                self.now += 4
                resumed.tick()
                self.client.create_ttn.assert_not_called()
                self.assertEqual(resumed.list_orders(100, 99)[0]["state"], "uncertain")
            finally:
                resumed.close()

    def test_correction_received_during_error_notification_is_preserved(self):
        original = message(1, caption=ORDER.replace("Оценка 1600", ""))
        sent = self.bot.send_message.side_effect
        def send_with_correction(**kwargs):
            correction = message(2, text=ORDER, photo=False)
            correction["reply_to_message"] = original
            self.pipeline.ingest(correction, 2)
            self.bot.send_message.side_effect = sent
            return sent(**kwargs)
        self.bot.send_message.side_effect = send_with_correction
        self.pipeline.ingest(original, 1)
        self.drain()
        self.assertEqual(self.job()["state"], "collecting")
        self.now += 4
        self.pipeline.tick()
        self.client.create_ttn.assert_called_once()
        self.assertEqual(self.bot.send_photo.call_args.kwargs["photo"], "file-1")

    def test_photo_after_completed_notification_updates_card_without_new_ttn(self):
        self.pipeline.ingest(message(1, caption=ORDER, group="album"), 1)
        self.drain()
        self.assertTrue(self.job()["notified"])
        self.pipeline.ingest(message(2, group="album"), 2)
        self.assertFalse(self.job()["notified"])
        self.drain()
        self.client.create_ttn.assert_called_once()
        media = self.bot.send_media_group.call_args.kwargs["media"]
        self.assertEqual([item.media for item in media], ["file-1", "file-2"])

    def test_reforward_of_album_expanded_after_creation_reuses_ttn(self):
        origin = {"type": "hidden_user", "sender_user_name": "Seller", "date": 1791300000}
        first = message(1, caption=ORDER, group="first", unique="first-photo")
        first["forward_origin"] = origin
        self.pipeline.ingest(first, 1)
        self.drain()
        second = message(2, group="first", unique="second-photo")
        second["forward_origin"] = origin
        self.pipeline.ingest(second, 2)
        self.drain()
        repeat = copy.deepcopy(first)
        repeat.update(message_id=10, media_group_id="repeat")
        repeated_second = copy.deepcopy(second)
        repeated_second.update(message_id=11, media_group_id="repeat")
        self.pipeline.ingest(repeat, 10)
        self.pipeline.ingest(repeated_second, 11)
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.assertTrue(self.job()["duplicate"])
