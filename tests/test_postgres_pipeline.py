"""The complete order flow against a real disposable Postgres database."""
import concurrent.futures
import os
import unittest
from unittest.mock import patch

import psycopg

import test_order_pipeline as fixtures
from order_pipeline import OrderPipeline


TEST_DATABASE_URL = os.getenv("SAFAR_TEST_DATABASE_URL", "")


@unittest.skipUnless(TEST_DATABASE_URL, "Disposable Postgres test database not configured")
class PostgresPipelineTests(fixtures.PipelineTests):
    def setUp(self):
        # CI provides a dedicated disposable database; never use STATE_DATABASE_URL.
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            for table in ("messages", "receipts", "jobs", "updates"):
                connection.execute("DELETE FROM safar_orders." + table)
        super().setUp()

    def make_pipeline(self, path):
        return OrderPipeline(path, fixtures.parse_order, self.client, self.bot,
                             clock=lambda: self.now, database_url=TEST_DATABASE_URL)

    def test_runtime_role_has_no_owner_or_api_privileges(self):
        row = self.pipeline.db.execute(
            "SELECT current_user AS name, rolsuper, rolbypassrls "
            "FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        self.assertEqual(row["name"], "safar_bot")
        self.assertFalse(row["rolsuper"])
        self.assertFalse(row["rolbypassrls"])
        enabled = self.pipeline.db.execute(
            "SELECT count(*) AS enabled FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='safar_orders' AND c.relrowsecurity"
        ).fetchone()["enabled"]
        self.assertEqual(enabled, 4)
        for role in ("anon", "authenticated"):
            access = self.pipeline.db.execute(
                "SELECT has_schema_privilege(?, 'safar_orders', 'USAGE') AS allowed", (role,)
            ).fetchone()["allowed"]
            self.assertFalse(access)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.pipeline.db.execute("CREATE TABLE safar_orders.forbidden (id INTEGER)")

    def test_standby_does_not_recover_a_shipment_while_it_is_being_created(self):
        standby = self.make_pipeline(":memory:")
        self.addCleanup(standby.close)
        def create(**kwargs):
            self.assertFalse(standby.tick())
            receipt = standby.db.execute("SELECT state FROM receipts").fetchone()
            self.assertEqual(receipt["state"], "creating")
            return {"ttn": "20400000000001"}
        self.client.create_ttn.side_effect = create
        self.pipeline.ingest(fixtures.message(1, caption=fixtures.ORDER), 1)
        self.drain()
        self.client.create_ttn.assert_called_once()
        self.assertEqual(self.job()["state"], "created")

    def test_standby_takes_over_queue_after_leader_exits(self):
        self.pipeline.ingest(fixtures.message(1, caption=fixtures.ORDER), 1)
        self.drain()
        standby = self.make_pipeline(":memory:")
        self.addCleanup(standby.close)
        self.assertFalse(standby.tick())
        standby.ingest(fixtures.message(2, caption=fixtures.ORDER), 2)
        self.pipeline.close()
        self.now += 4
        self.assertTrue(standby.tick())
        self.assertEqual(self.client.create_ttn.call_count, 2)
        self.assertTrue(standby.has_update(1))
        self.assertTrue(standby.has_update(2))

    def test_concurrent_webhook_instances_keep_all_album_photos(self):
        other = self.make_pipeline(":memory:")
        self.addCleanup(other.close)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.pipeline.ingest, fixtures.message(1, caption=fixtures.ORDER, group="same"), 1),
                executor.submit(other.ingest, fixtures.message(2, group="same"), 2),
            ]
            self.assertEqual([future.result(timeout=15) for future in futures], [True, True])
        self.drain()
        self.client.create_ttn.assert_called_once()
        media = self.bot.send_media_group.call_args.kwargs["media"]
        self.assertEqual([item.media for item in media], ["file-1", "file-2"])

    def test_concurrent_duplicate_webhook_is_acknowledged_only_once(self):
        other = self.make_pipeline(":memory:")
        self.addCleanup(other.close)
        incoming = fixtures.message(1, caption=fixtures.ORDER)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(pipeline.ingest, incoming, 1) for pipeline in (self.pipeline, other)]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(results), [False, True])
        self.drain()
        self.client.create_ttn.assert_called_once()

    def test_failed_ingest_rolls_back_ack_and_order(self):
        original = self.pipeline._write
        def fail_after_write(job):
            original(job)
            raise RuntimeError("simulated journal interruption")
        with patch.object(self.pipeline, "_write", side_effect=fail_after_write):
            with self.assertRaises(RuntimeError):
                self.pipeline.ingest(fixtures.message(1, caption=fixtures.ORDER), 1)
        self.assertFalse(self.pipeline.has_update(1))
        self.assertFalse(self.pipeline.list_orders(100, 99))
        self.pipeline.ingest(fixtures.message(1, caption=fixtures.ORDER), 1)
        self.drain()
        self.client.create_ttn.assert_called_once()

    def test_closed_data_connection_reconnects_without_replaying_shipment(self):
        self.pipeline.remember_update(55)
        self.pipeline.db._connection.close()
        self.assertTrue(self.pipeline.has_update(55))
        self.pipeline.ingest(fixtures.message(1, caption=fixtures.ORDER), 1)
        self.drain()
        self.client.create_ttn.assert_called_once()

    def test_lost_worker_connection_recovers_uncertain_save_without_second_ttn(self):
        self.pipeline.ingest(fixtures.message(1, caption=fixtures.ORDER), 1)
        self.assertFalse(self.pipeline.tick())
        with self.pipeline.lock, self.pipeline.db:
            job = self.job()
            job["state"] = "processing"
            self.pipeline._write(job)
            identity = self.pipeline._identity(job, fixtures.parse_order(fixtures.ORDER))
            self.pipeline.db.execute("INSERT INTO receipts VALUES (?,?,?,?)",
                                     (identity, "creating", None, self.now))
        self.pipeline.db._worker_connection.close()
        self.now += 4
        self.pipeline.tick()
        self.client.create_ttn.assert_not_called()
        self.assertEqual(self.job()["state"], "uncertain")

    def test_photo_arriving_during_notification_stays_linked_and_is_sent(self):
        other = self.make_pipeline(":memory:")
        self.addCleanup(other.close)
        original = self.bot.send_photo.side_effect
        def send_with_late_photo(**kwargs):
            other.ingest(fixtures.message(2, group="same"), 2)
            self.bot.send_photo.side_effect = original
            return original(**kwargs)
        self.bot.send_photo.side_effect = send_with_late_photo
        self.pipeline.ingest(fixtures.message(1, caption=fixtures.ORDER, group="same"), 1)
        self.drain()
        self.assertTrue(other.has_update(2))
        self.assertEqual(len(self.job()["messages"]), 2)
        self.assertFalse(self.job()["notified"])
        self.now += 4
        self.pipeline.tick()
        self.client.create_ttn.assert_called_once()
        media = self.bot.send_media_group.call_args.kwargs["media"]
        self.assertEqual([item.media for item in media], ["file-1", "file-2"])
        self.assertTrue(self.job()["notified"])
