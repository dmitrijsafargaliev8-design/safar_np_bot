"""Historical account tracking and future-only sender selection, never TTN writes."""
import copy
import threading
import unittest
from unittest.mock import Mock

from np_client import NovaPoshtaTemporaryError
from safar_operations import select_sender, sender_summary, tracking


class AppOperationsTests(unittest.TestCase):
    def setUp(self):
        self.old = Mock()
        self.new = Mock()
        self.pipe = Mock()
        self.pipe.lock = threading.RLock()
        self.pipe.sender_clients = {"default": self.old, "fop": self.new}
        self.pipe.default_sender_profile = "fop"
        self.pipe.sender_preference.return_value = "fop"
        self.pipe._sender_client.side_effect = lambda profile: self.pipe.sender_clients[profile]
        self.pipe._safar_tracking_cache = {}
        self.registry = Mock()
        self.registry.available.return_value = {"default": "Основний", "fop": "ФОП"}
        self.job = {"key": "synthetic", "chat_id": 100, "owner_id": 100,
                    "sender_profile": "default", "order": {"phone": ""},
                    "result": {"ttn": "20400000000001"}}
        self.old.get_ttn_status.return_value = {"Number": "20400000000001", "StatusCode": "1",
                                               "Status": "Очікує надходження", "PhoneRecipient": "PRIVATE"}

    def test_tracking_uses_historical_account_and_does_not_mark_issued_as_delivered(self):
        original = copy.deepcopy(self.job)
        status = tracking(self.pipe, self.job, clock=lambda: 1000)
        self.old.get_ttn_status.assert_called_once_with("20400000000001", phone="")
        self.new.get_ttn_status.assert_not_called()
        self.assertFalse(status["delivered"])
        self.assertEqual(status["phase"], "issued")
        self.assertNotIn("PhoneRecipient", status)
        self.assertEqual(self.job, original)
        self.pipe._write.assert_not_called()
        self.old.create_ttn.assert_not_called()

    def test_cache_expiration_and_exact_document_mismatch(self):
        self.assertFalse(tracking(self.pipe, self.job, clock=lambda: 1000)["cached"])
        self.assertTrue(tracking(self.pipe, self.job, force=True, clock=lambda: 1020)["cached"])
        self.assertEqual(self.old.get_ttn_status.call_count, 1)
        self.old.get_ttn_status.return_value = {"Number": "20400000000002", "StatusCode": "9"}
        with self.assertRaises(NovaPoshtaTemporaryError):
            tracking(self.pipe, self.job, clock=lambda: 1061)
        self.old.create_ttn.assert_not_called()

    def test_delivered_requires_carrier_code_not_job_state(self):
        self.job["state"] = "created"
        self.old.get_ttn_status.return_value.update(StatusCode="9", DateReceived="08.10.2026")
        result = tracking(self.pipe, self.job)
        self.assertTrue(result["delivered"])
        self.assertEqual(result["received_at"], "08.10.2026")

    def test_sender_selection_only_updates_preference(self):
        summary = select_sender(self.pipe, self.registry, 100, 100, "fop")
        self.pipe.set_sender_preference.assert_called_once_with(100, 100, "fop")
        self.assertEqual(summary["selected"], "fop")
        self.assertFalse(summary["fop_pending"])
        self.pipe._write.assert_not_called()
        self.new.get_sender_info.assert_not_called()
        self.new.create_ttn.assert_not_called()

    def test_unconfigured_fop_and_invalid_selection_fail_honestly(self):
        self.registry.available.return_value = {"default": "Основний"}
        self.assertTrue(sender_summary(self.pipe, self.registry, 100, 100)["fop_pending"])
        for profile in ("fop", "unknown", "../bad", 100):
            with self.assertRaises(ValueError):
                select_sender(self.pipe, self.registry, 100, 100, profile)
        self.pipe.set_sender_preference.assert_not_called()


if __name__ == "__main__":
    unittest.main()
