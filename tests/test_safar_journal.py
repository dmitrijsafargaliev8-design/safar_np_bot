"""Private journal filtering, analytics and app drafts; all carrier APIs mocked."""
import json
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from bot import parse_order
from order_pipeline import OrderCorrectionConflict, OrderPipeline
from test_order_pipeline import ORDER, message


class SafarJournalTests(unittest.TestCase):
    database_url = None

    def setUp(self):
        self.now = datetime(2026, 10, 8, 12, tzinfo=ZoneInfo("Europe/Kyiv")).timestamp()
        if self.database_url:
            import psycopg
            with psycopg.connect(self.database_url) as connection:
                for table in ("messages", "receipts", "jobs", "updates"):
                    connection.execute("DELETE FROM safar_orders." + table)
                if connection.execute("SELECT to_regclass('safar_orders.sender_preferences')").fetchone()[0]:
                    connection.execute("DELETE FROM safar_orders.sender_preferences")
        self.client = Mock()
        self.client.create_ttn.return_value = {"ttn": "20400000000001"}
        self.client.is_ttn_deleted.return_value = False
        self.other_client = Mock()
        self.other_client.create_ttn.return_value = {"ttn": "20400000000002"}
        self.other_client.is_ttn_deleted.return_value = False
        self.bot = Mock()
        self.sent_id = 10000
        def sent(**kwargs):
            self.sent_id += 1
            return SimpleNamespace(message_id=self.sent_id)
        for name in ("send_photo", "send_video", "send_document", "send_animation", "send_message"):
            getattr(self.bot, name).side_effect = sent
        self.pipeline = OrderPipeline(
            ":memory:", parse_order, self.client, self.bot, clock=lambda: self.now,
            database_url=self.database_url,
            sender_clients={"default": self.client, "other": self.other_client},
        )
        self.addCleanup(self.pipeline.close)

    def drain(self):
        self.now += 4
        for _ in range(20):
            if not self.pipeline.tick():
                return
        self.fail("Mock queue did not drain")

    def seed(self, key, *, chat=100, owner=99, state="created", city="Київ", name="Тестова Марія",
             warehouse="8", sender="default", ttn="20400000000001", updated=None, attempts=0):
        job = dict(key=key, chat_id=chat, owner_id=owner, state=state, due=self.now,
                   created_at=self.now, attempts=attempts, notified=True, sender_profile=sender,
                   messages=[message(1, caption=ORDER)], order={**parse_order(ORDER), "city": city,
                   "full_name": name, "warehouse": warehouse}, result={"ttn": ttn} if ttn else None,
                   identity=None, anchor_id=1)
        with self.pipeline.lock, self.pipeline.db:
            self.pipeline._write(job)
            if updated is not None:
                self.pipeline.db.execute("UPDATE jobs SET updated=? WHERE key=?", (updated, key))
        return job

    def issued(self):
        self.pipeline.ingest(message(1, caption=ORDER), 1)
        self.drain()
        return self.pipeline.list_orders_page(100, 99)[0]

    def stage(self, job, fields):
        return self.pipeline.stage_order_correction(100, 99, job["key"], fields,
                                                    expected_revision=job["revision"])

    def retry(self, mid=2):
        incoming = message(mid, text="/retry", photo=False)
        incoming["reply_to_message"] = {"message_id": 1}
        return self.pipeline.ingest(incoming, mid, retry=True)

    def test_filters_query_whole_journal_before_pagination_and_keep_scope(self):
        self.seed("old-target", city="Львів", name="Іваненко Марія", warehouse="142",
                  sender="other", ttn="20400000000999", updated=self.now - 100)
        for index in range(95):
            self.seed("recent-" + str(index))
        self.seed("foreign-chat", chat=200, city="Львів", sender="other")
        self.seed("foreign-owner", owner=42, city="Львів", sender="other")
        for search in ("льВІВ", "ІВАНЕНКО", "20400000000999", "142"):
            filters = dict(search=search, sender_profile="other")
            page = self.pipeline.list_orders_page(100, 99, limit=1, **filters)
            self.assertEqual([job["key"] for job in page], ["old-target"])
            self.assertEqual(self.pipeline.order_page_count(100, 99, **filters), 1)
        self.assertEqual(self.pipeline.order_page_count(100, 99), 96)

    def test_stable_tied_timestamp_pages_and_sort_validation(self):
        for key in ("c", "a", "b"):
            self.seed(key)
        pages = [self.pipeline.list_orders_page(100, 99, limit=1, offset=index)[0]["key"] for index in range(3)]
        self.assertEqual(pages, ["c", "b", "a"])
        self.assertEqual([job["key"] for job in self.pipeline.list_orders_page(100, 99, sort="updated_asc")], ["a", "b", "c"])
        with self.assertRaises(ValueError):
            self.pipeline.list_orders_page(100, 99, sort="updated; DROP TABLE jobs")
        with self.assertRaises(ValueError):
            self.pipeline.list_orders_page(100, 99, offset=True)

    def test_attention_ttn_sender_and_half_open_activity_dates(self):
        self.seed("created", updated=self.now - 10)
        self.seed("invalid", state="invalid", ttn="", sender="other", updated=self.now)
        self.seed("uncertain", state="uncertain", ttn="", updated=self.now + 10)
        self.assertEqual(self.pipeline.order_page_count(100, 99, status="attention"), 2)
        self.assertEqual(self.pipeline.order_page_count(100, 99, has_ttn=True), 1)
        self.assertEqual(self.pipeline.order_page_count(100, 99, has_ttn=False, sender_profile="other"), 1)
        self.assertEqual([job["key"] for job in self.pipeline.list_orders_page(100, 99, date_from=self.now, date_to=self.now + 10)], ["invalid"])
        for kwargs in ({"date_from": float("nan")}, {"date_from": True},
                       {"date_from": 10, "date_to": 5}, {"status": "delivered"}, {"has_ttn": "true"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.pipeline.order_page_count(100, 99, **kwargs)

    def test_search_treats_sql_like_characters_as_literal(self):
        literal = "%_' OR 1=1"
        self.seed("literal", city=literal)
        self.seed("normal")
        self.seed("foreign", owner=42, city=literal)
        self.assertEqual([job["key"] for job in self.pipeline.list_orders_page(100, 99, search=literal)], ["literal"])

    def test_analytics_use_full_scope_and_kyiv_day_boundaries(self):
        midnight = datetime(2026, 10, 8, tzinfo=ZoneInfo("Europe/Kyiv")).timestamp()
        self.seed("today", updated=midnight, attempts=2)
        self.seed("yesterday", state="uncertain", ttn="", updated=midnight - 1, attempts=1)
        self.seed("old", state="invalid", ttn="", updated=midnight - 86400 * 20)
        self.seed("foreign", chat=200)
        stats = self.pipeline.order_analytics(100, 99)
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["counts"], {"created": 1, "uncertain": 1, "invalid": 1})
        self.assertEqual(stats["attention"], 2)
        self.assertEqual(stats["with_ttn"], 1)
        self.assertEqual(stats["current_attempts"], 3)
        self.assertEqual(stats["daily_activity"][-2:], [{"date": "2026-10-07", "count": 1}, {"date": "2026-10-08", "count": 1}])
        self.assertEqual(self.pipeline.order_analytics(100, 99, date_from=midnight)["total"], 1)
        empty = self.pipeline.order_analytics(100, 123)
        self.assertEqual((empty["total"], empty["attention"], empty["with_ttn"]), (0, 0, 0))

    def test_created_draft_preserves_receipt_photos_sender_and_worker_state(self):
        original = self.issued()
        receipt = self.pipeline.db.execute("SELECT result,state FROM receipts").fetchone()
        receipt_snapshot = dict(receipt)
        self.pipeline.wakeup.clear()
        result = self.stage(original, {"phone": "0500000002", "cost": 1900})
        updated = result["job"]
        self.assertEqual((result["staged"], result["enqueued"], result["ttn_modified"]), (True, False, False))
        for field in ("order", "result", "identity", "messages", "sender_profile", "state", "due", "notified"):
            self.assertEqual(updated[field], original[field], field)
        self.assertFalse(self.pipeline.wakeup.is_set())
        self.assertEqual(dict(self.pipeline.db.execute("SELECT result,state FROM receipts").fetchone()), receipt_snapshot)
        self.assertEqual(updated["app_correction"]["proposed_order"]["phone"], "380500000002")
        self.assertEqual(updated["app_correction"]["proposed_order"]["cost"], 1900)
        self.assertEqual(updated["app_correction"]["proposed_order"]["cod_amount"], 0)
        self.assertNotIn("pending_edits", updated)
        self.assertFalse(updated["app_correction_stale"])
        self.assertEqual(self.pipeline.order_revision(updated), updated["revision"])
        self.assertFalse(self.pipeline.tick())
        self.client.create_ttn.assert_called_once()

    def test_scheduled_collecting_order_does_not_consume_app_draft(self):
        self.pipeline.ingest(message(1, caption=ORDER), 1)
        job = self.pipeline.list_orders_page(100, 99)[0]
        self.stage(job, {"cost": "1900"})
        self.drain()
        self.assertEqual(self.client.create_ttn.call_args.kwargs["cost"], 1600)
        current = self.pipeline.order_for_key(100, 99, job["key"])
        self.assertTrue(current["app_correction_stale"])
        with self.assertRaises(OrderCorrectionConflict):
            self.retry()
        self.client.create_ttn.assert_called_once()

    def test_explicit_retry_adopts_draft_but_active_ttn_stays_immutable(self):
        original = self.issued()
        self.stage(original, {"phone": "0500000002", "cost": "1900"})
        self.retry()
        self.drain()
        current = self.pipeline.order_for_key(100, 99, original["key"])
        self.client.create_ttn.assert_called_once()
        self.assertEqual(current["order"], original["order"])
        self.assertNotIn("app_correction", current)
        self.assertEqual(current["pending_edits"]["cost"], "1900")
        self.client.is_ttn_deleted.return_value = True
        self.client.create_ttn.return_value = {"ttn": "20400000000002"}
        self.retry(3)
        self.drain()
        self.assertEqual(self.client.create_ttn.call_count, 2)
        self.assertEqual(self.client.create_ttn.call_args.kwargs["cost"], 1900)
        self.assertEqual(self.client.create_ttn.call_args.kwargs["phone"], "380500000002")
        self.assertEqual(self.pipeline.attachments(self.pipeline.list_orders(100, 99)[0]), self.pipeline.attachments(original))

    def test_stale_editor_and_foreign_scope_cannot_stage(self):
        original = self.issued()
        self.stage(original, {"cost": "1900"})
        with self.assertRaises(OrderCorrectionConflict):
            self.stage(original, {"cost": "2000"})
        for chat, owner in ((200, 99), (100, 42)):
            self.assertIsNone(self.pipeline.stage_order_correction(chat, owner, original["key"], {"cost": "2000"}, expected_revision=original["revision"]))
        self.assertEqual(self.pipeline.order_for_key(100, 99, original["key"])["app_correction"]["fields"], {"cost": "1900"})

    def test_processing_uncertain_and_invalid_values_fail_without_writes(self):
        for state in ("processing", "uncertain"):
            self.seed(state, state=state)
            job = self.pipeline.order_for_key(100, 99, state)
            with self.assertRaises(OrderCorrectionConflict):
                self.stage(job, {"cost": "2000"})
            self.assertNotIn("app_correction", self.pipeline.order_for_key(100, 99, state))
        original = self.issued()
        for fields in ({"sender_profile": "other"}, {"cost": True}, {"cost": "0"},
                       {"cod_amount": float("inf")}, {"full_name": "<script>"}, {"city": "Київ\nЛьвів"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.stage(original, fields)
        self.assertEqual(self.pipeline.order_for_key(100, 99, original["key"])["revision"], original["revision"])

    def test_complete_invalid_repair_waits_for_explicit_retry(self):
        self.pipeline.ingest(message(1, caption=ORDER.replace("Оценка 1600", "")), 1)
        self.drain()
        job = self.pipeline.list_orders_page(100, 99)[0]
        with self.assertRaises(ValueError):
            self.stage(job, {"cost": "1700"})
        self.stage(job, {"full_name": "Іваненко Марія", "phone": "0500000001", "city": "Одесса",
                         "warehouse": "8", "cost": "1700", "cod_amount": "0"})
        self.assertFalse(self.pipeline.tick())
        self.client.create_ttn.assert_not_called()
        self.retry()
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.assertEqual(self.client.create_ttn.call_args.kwargs["cost"], 1700)

    def test_notification_race_retains_app_draft(self):
        sent = self.bot.send_photo.side_effect
        def edit_during_notification(**kwargs):
            self.bot.send_photo.side_effect = sent
            job = self.pipeline.list_orders_page(100, 99)[0]
            self.stage(job, {"cost": "1900"})
            return sent(**kwargs)
        self.bot.send_photo.side_effect = edit_during_notification
        self.issued()
        current = self.pipeline.list_orders_page(100, 99)[0]
        self.assertEqual(current["app_correction"]["fields"], {"cost": "1900"})
        self.assertFalse(current["app_correction_stale"])
        self.client.create_ttn.assert_called_once()

    def test_notification_race_retains_telegram_field_correction(self):
        sent = self.bot.send_photo.side_effect
        def edit_during_notification(**kwargs):
            self.bot.send_photo.side_effect = sent
            incoming = message(2, text="Оценка: 1900", photo=False)
            incoming["reply_to_message"] = {"message_id": 1}
            self.pipeline.ingest(incoming, 2)
            return sent(**kwargs)
        self.bot.send_photo.side_effect = edit_during_notification
        self.issued()
        self.assertEqual(self.pipeline.list_orders_page(100, 99)[0]["pending_edits"], {"cost": "1900"})
        self.client.create_ttn.assert_called_once()

    def test_replacement_keeps_original_sender_after_future_selection_changes(self):
        self.issued()
        self.pipeline.set_sender_preference(100, 99, "other")
        self.client.is_ttn_deleted.return_value = True
        self.client.create_ttn.return_value = {"ttn": "20400000000003"}
        self.pipeline.ingest(message(2, caption=ORDER, unique="unique-1"), 2)
        self.drain()
        self.assertEqual(self.client.create_ttn.call_count, 2)
        self.other_client.create_ttn.assert_not_called()
        self.client.is_ttn_deleted.assert_called_once_with("20400000000001", phone="380500000001")
        latest = self.pipeline.list_orders_page(100, 99)[0]
        self.assertEqual(latest["sender_profile"], "default")
        receipt = json.loads(self.pipeline.db.execute("SELECT result FROM receipts LIMIT 1").fetchone()["result"])
        self.assertEqual(receipt["history"][0]["sender_profile"], "default")

    def test_explicit_retry_during_notification_preserves_queue_and_draft(self):
        original = self.issued()
        self.stage(original, {"cost": "1900"})
        with self.pipeline.lock, self.pipeline.db:
            job = self.pipeline._job(original["key"])
            job.update(notified=False, due=self.now)
            self.pipeline._write(job)
        invoked = []
        def retry_during_notification(**kwargs):
            if not invoked:
                invoked.append(True)
                self.retry()
            return SimpleNamespace(message_id=20001)
        self.bot.send_photo.side_effect = retry_during_notification
        self.assertTrue(self.pipeline.tick())
        queued = self.pipeline.order_for_key(100, 99, original["key"])
        self.assertEqual(queued["state"], "collecting")
        self.assertEqual(queued["pending_edits"]["cost"], "1900")
        self.assertNotIn("app_correction", queued)
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.client.is_ttn_deleted.assert_called_once()

    def test_review_of_stale_draft_does_not_silently_merge_old_proposals(self):
        original = self.issued()
        self.stage(original, {"phone": "0500000002"})
        with self.pipeline.lock, self.pipeline.db:
            job = self.pipeline._job(original["key"])
            job["messages"].append(message(10, photo=False, text="new source"))
            self.pipeline._write(job)
        fresh = self.pipeline.order_for_key(100, 99, original["key"])
        self.assertTrue(fresh["app_correction_stale"])
        # The real issued order is still usable as a proposal base even when
        # another source message has changed; old draft fields aren't merged.
        reviewed = self.stage(fresh, {"cost": "1900"})["job"]
        self.assertEqual(reviewed["app_correction"]["fields"], {"cost": "1900"})
        self.assertEqual(reviewed["app_correction"]["proposed_order"]["phone"], original["order"]["phone"])


@unittest.skipUnless(os.getenv("SAFAR_TEST_DATABASE_URL"), "Disposable Postgres test database not configured")
class SafarPostgresJournalTests(SafarJournalTests):
    database_url = os.getenv("SAFAR_TEST_DATABASE_URL")


if __name__ == "__main__":
    unittest.main()
