"""Regression guards: the requested delivery point and settlement must be exact.

All API calls are mocked. This suite never creates a real Nova Poshta TTN.
"""
import unittest
from unittest.mock import Mock

from bot import parse_order
from np_client import NovaPoshtaClient, NovaPoshtaError


ORDER_PARTS = "Одесса\n{point}\nІваненко Марія\n0500000001\nОценка 1600"


class RoutingSafetyTests(unittest.TestCase):
    def test_forwarded_postomat_keeps_delivery_point_kind(self):
        order = parse_order(ORDER_PARTS.format(point="📦 Поштомат №12"))
        self.assertEqual(order["city"], "Одесса")
        self.assertEqual(order["warehouse"], "12")
        self.assertEqual(order["delivery_point_type"], "postomat")
        self.assertEqual(order["cod_amount"], 0)

    def test_explicit_branch_keeps_default_kind(self):
        order = parse_order(ORDER_PARTS.format(point="Відділення №12"))
        self.assertEqual(order["delivery_point_type"], "")

    def test_same_number_branch_and_postomat_cannot_be_swapped(self):
        client = NovaPoshtaClient("fake-test-api-key")
        client._call = Mock(return_value=[
            {"Ref": "branch", "Number": "12", "Description": "Відділення №12"},
            {"Ref": "locker", "Number": "12", "Description": "Поштомат №12"},
        ])
        self.assertEqual(client.get_warehouse_ref("city", "12", delivery_point_type="postomat"), "locker")
        self.assertEqual(client.get_warehouse_ref("city", "12"), "branch")

    def test_missing_requested_postomat_does_not_fall_back_to_branch(self):
        client = NovaPoshtaClient("fake-test-api-key")
        client._call = Mock(return_value=[
            {"Ref": "branch", "Number": "12", "Description": "Відділення №12"},
        ])
        with self.assertRaisesRegex(NovaPoshtaError, "неоднозначно"):
            client.get_warehouse_ref("city", "12", delivery_point_type="postomat")

    def test_create_ttn_passes_postomat_kind_to_warehouse_lookup(self):
        order = parse_order(ORDER_PARTS.format(point="Почтомат №12"))
        client = NovaPoshtaClient("fake-test-api-key")
        client.get_sender_info = Mock(return_value={
            "city_ref": "sender-city", "sender_ref": "sender",
            "address_ref": "sender-wh", "contact_ref": "sender-contact", "phone": "380500000002",
        })
        client.get_city_ref = Mock(return_value="recipient-city")
        client.get_warehouse_ref = Mock(return_value="locker")
        client.get_or_create_recipient = Mock(return_value=("recipient", "recipient-contact", "380500000001"))
        client._call = Mock(return_value=[{"IntDocNumber": "20400000000001"}])
        client.create_ttn(**order)
        client.get_warehouse_ref.assert_called_once_with(
            "recipient-city", "12", delivery_point_type="postomat"
        )
        self.assertEqual(client._call.call_args.args[2]["RecipientAddress"], "locker")

    def test_similar_village_from_search_is_not_accepted(self):
        client = NovaPoshtaClient("fake-test-api-key")
        client._call = Mock(return_value=[{"Addresses": [
            {"Ref": "different-village", "MainDescription": "Петровірівка-Нова", "Area": "Одеська"}
        ]}])
        with self.assertRaisesRegex(NovaPoshtaError, "не найден точно"):
            client.get_settlement_ref("Петровірівка")

    def test_explicit_district_must_match(self):
        client = NovaPoshtaClient("fake-test-api-key")
        client._call = Mock(return_value=[{"Addresses": [
            {"Ref": "wrong-district", "MainDescription": "Петровірівка", "Area": "Одеська", "Region": "Подільський"}
        ]}])
        with self.assertRaisesRegex(NovaPoshtaError, "Район"):
            client.get_settlement_ref("Петровірівка", region="Березівський")

    def test_explicit_village_type_must_match(self):
        client = NovaPoshtaClient("fake-test-api-key")
        client._call = Mock(return_value=[{"Addresses": [
            {"Ref": "same-name-city", "MainDescription": "Петровірівка", "SettlementTypeCode": "м."}
        ]}])
        with self.assertRaisesRegex(NovaPoshtaError, "Тип населённого пункта"):
            client.get_settlement_ref("Петровірівка", settlement_type="село")

    def test_exact_village_is_accepted(self):
        client = NovaPoshtaClient("fake-test-api-key")
        client._call = Mock(return_value=[{"Addresses": [
            {"Ref": "correct-village", "MainDescription": "Петровірівка", "Area": "Одеська"}
        ]}])
        self.assertEqual(client.get_settlement_ref("Петровірівка"), "correct-village")


if __name__ == "__main__":
    unittest.main()
