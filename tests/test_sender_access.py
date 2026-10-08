"""Read-only existing credential discovery never issues a TTN or exposes keys."""
import unittest
from unittest.mock import Mock

from sender_access import read_only_sender_access
from np_client import NovaPoshtaError


class SenderAccessTests(unittest.TestCase):
    def test_single_authorized_sender_is_listed_without_secret_or_uuid(self):
        client = Mock()
        client.api_key = "do-not-print-secret"
        client.create_ttn = Mock()
        client._call.return_value = [{
            "Ref": "11111111-1111-1111-1111-111111111111",
            "Description": "Доступный отправитель",
        }]
        report = read_only_sender_access(client)
        self.assertIn("Доступный отправитель", report)
        self.assertNotIn("do-not-print-secret", report)
        self.assertNotIn("11111111-", report)
        self.assertIn("\n", report)
        client.create_ttn.assert_not_called()
        client._call.assert_called_once_with(
            "Counterparty", "getCounterparties",
            {"CounterpartyProperty": "Sender", "Page": "1"}
        )

    def test_no_senders_does_not_claim_fop_access(self):
        client = Mock()
        client._call.return_value = []
        report = read_only_sender_access(client)
        self.assertIn("не вернула доступных отправителей", report)
        self.assertIn("ТТН не создавалась", report)
        client.create_ttn.assert_not_called()

    def test_deduplicate_mask_ids_and_sanitize_untrusted_labels(self):
        client = Mock()
        client._call.return_value = [
            {"Ref": "ref-a", "Description": "Компания\nВторая строка"},
            {"Ref": "ref-a", "Description": "Компания"},
            {"Ref": "ref-b", "Description": "ФОП «Тест»"},
            {"Ref": None, "Description": "unknown"},
        ]
        report = read_only_sender_access(client)
        self.assertIn("Компания Вторая строка", report)
        self.assertIn("ФОП «Тест»", report)
        self.assertEqual(report.count("• Компания"), 1)
        self.assertNotIn("ref-a", report)
        self.assertNotIn("unknown", report)

    def test_fail_closed_if_api_fails(self):
        client = Mock()
        client._call.side_effect = NovaPoshtaError("read only blocked")
        with self.assertRaises(NovaPoshtaError):
            read_only_sender_access(client)
        client.create_ttn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
