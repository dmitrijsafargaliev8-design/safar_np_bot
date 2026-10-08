"""Exact, affirmative deletion checks; no live shipment operations."""
import unittest
from unittest.mock import Mock

from np_client import NovaPoshtaClient, NovaPoshtaError, NovaPoshtaTemporaryError


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.client = NovaPoshtaClient("fake-test-api-key")
        self.client._call = Mock()

    def test_explicit_deletion_of_exact_number(self):
        self.client._call.return_value = [{"Number": "20400000000001", "StatusCode": "2", "Status": "Видалено"}]
        self.assertTrue(self.client.is_ttn_deleted("20400000000001", phone="0500000001"))
        self.client._call.assert_called_once_with("TrackingDocument", "getStatusDocuments", {
            "Documents": [{"DocumentNumber": "20400000000001", "Phone": "380500000001"}]
        })

    def test_existing_created_transit_and_delivered_shipments_are_not_deleted(self):
        for code in ("1", "4", "9", "103"):
            with self.subTest(code=code):
                self.client._call.return_value = [{"Number": "20400000000001", "StatusCode": code}]
                self.assertFalse(self.client.is_ttn_deleted("20400000000001"))

    def test_absent_ambiguous_or_mismatched_status_is_not_proof_of_deletion(self):
        for rows in ([], [{}], [{"Number": "20400000000002", "StatusCode": "2"}],
                     [{"Number": "20400000000001", "StatusCode": "3"}],
                     [{"Number": "20400000000001", "StatusCode": "0"}],
                     [{"Number": "20400000000001"}],
                     [{"Number": "20400000000001", "StatusCode": None}],
                     [{"Number": "20400000000001", "StatusCode": "2"}] * 2):
            with self.subTest(rows=rows):
                self.client._call.return_value = rows
                with self.assertRaises(NovaPoshtaTemporaryError):
                    self.client.is_ttn_deleted("20400000000001")

    def test_invalid_saved_number_does_not_query_tracking(self):
        with self.assertRaises(NovaPoshtaError):
            self.client.is_ttn_deleted("invalid")
        self.client._call.assert_not_called()

    def test_number_not_found_is_visible_for_tracking_but_never_unlocks_recreation(self):
        row = {"Number": "20400000000001", "StatusCode": "3", "Status": "Номер не знайдено"}
        self.client._call.return_value = [row]
        self.assertEqual(self.client.get_ttn_status("20400000000001"), row)
        with self.assertRaises(NovaPoshtaTemporaryError):
            self.client.is_ttn_deleted("20400000000001")
