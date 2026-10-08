"""Regression tests for forwarded orders. No live TTNs or API calls."""
import unittest
from unittest.mock import Mock

from bot import parse_order
from np_client import NovaPoshtaClient


class ForwardedOrderParsingTests(unittest.TestCase):
    def test_ukrainian_warehouse_number_with_street(self):
        order = parse_order(
            "Отримувач\n"
            "Журавльова Інна Олександрівна\n"
            "м. Одеса відділення номер 8,\n"
            "вул. Геннадія Афанасьєва, 3/5\n"
            "+380 (63) 376 95 04\n\n"
            "Оценка 3250"
        )
        self.assertEqual(order["full_name"], "Журавльова Інна Олександрівна")
        self.assertEqual(order["city"], "Одеса")
        self.assertEqual(order["warehouse"], "8")
        self.assertEqual(order["street"], "")
        self.assertEqual(order["house"], "")
        self.assertEqual(order["cost"], 3250)
        self.assertEqual(order["cod_amount"], 0)

    def test_warehouse_and_city_on_separate_lines(self):
        order = parse_order(
            "На отделение:\nОдесса\nНП 142\n"
            "Погорельцева Наталья\n+380 95 947 7703\nОценка 1600"
        )
        self.assertEqual(order["city"], "Одесса")
        self.assertEqual(order["warehouse"], "142")
        self.assertEqual(order["full_name"], "Погорельцева Наталья")
        self.assertEqual(order["cost"], 1600)

    def test_address_delivery_remains_supported(self):
        order = parse_order(
            "Одеська область\nБерезівський район\nСело Петровірівка\n"
            "Вул. Шклярука 15\nЖелязкова Лариса\n0972218934\nОценка 1800"
        )
        self.assertEqual(order["city"], "Петровірівка")
        self.assertEqual(order["warehouse"], "")
        self.assertEqual(order["street"], "Шклярука")
        self.assertEqual(order["house"], "15")
        self.assertEqual(order["area"], "Одеська")
        self.assertEqual(order["region"], "Березівський")

    def test_other_warehouse_number_variants(self):
        for token in ("відділення №8", "Відділення номер 8", "НП 8", "отделение No. 8"):
            with self.subTest(token=token):
                order = parse_order(
                    "м. Одеса " + token + "\nЖуравльова Інна\n0633769504\nОценка 3250"
                )
                self.assertEqual(order["city"], "Одеса")
                self.assertEqual(order["warehouse"], "8")


class InlineOblastAndCityTests(unittest.TestCase):
    """Regression for screenshots where warehouse orders mention city + oblast."""

    def test_real_smila_cherkasy_order_fields_do_not_bleed(self):
        raw = (
            "Кушнір Катерина Василівна\n"
            "м. Сміла, Черкаська обл\n"
            "Відділення 4\n"
            "0630451047\n\n"
            "Оценка 3850"
        )
        order = parse_order(raw)
        self.assertEqual(order["full_name"], "Кушнір Катерина Василівна")
        self.assertEqual(order["city"], "Сміла")
        self.assertEqual(order["area"], "Черкаська")
        self.assertEqual(order["warehouse"], "4")
        self.assertEqual(order["phone"], "380630451047")
        self.assertEqual(order["cost"], 3850)
        self.assertEqual(order["cod_amount"], 0)
        self.assertEqual(order["settlement_type"], "місто")

    def test_inline_oblast_city_reversed_and_no_comma(self):
        for location in (
            "м. Сміла, Черкаська обл",
            "м.Сміла, Черкаська обл.",
            "Черкаська обл, м. Сміла",
            "Черкаська область, місто Сміла",
            "м. Сміла Черкаська обл",
        ):
            with self.subTest(location=location):
                raw = (
                    "Кушнір Катерина Василівна\n"
                    + location + "\nВідділення 4\n0630451047\nОценка 3850"
                )
                parsed = parse_order(raw)
                self.assertEqual(parsed["city"], "Сміла")
                self.assertEqual(parsed["area"], "Черкаська")
                self.assertEqual(parsed["warehouse"], "4")

    def test_separate_city_and_oblast_lines_still_work(self):
        order = parse_order(
            "Кушнір Катерина Василівна\n"
            "Черкаська область\nм. Сміла\n"
            "Відділення 4\n0630451047\nОценка 3850"
        )
        self.assertEqual(order["city"], "Сміла")
        self.assertEqual(order["area"], "Черкаська")

    def test_geo_directory_confirms_exact_city_area_and_warehouse(self):
        order = parse_order(
            "Кушнір Катерина Василівна\n"
            "м. Сміла, Черкаська обл\n"
            "Відділення 4\n0630451047\nОценка 3850"
        )
        client = NovaPoshtaClient("test-key-not-real")
        def fake_api(model, method, props):
            self.assertEqual(model, "Address")
            if method == "getCities":
                self.assertEqual(props["FindByString"], "Сміла")
                return [{"Ref": "smila-ref", "Description": "Сміла",
                         "DescriptionRu": "Смела", "Area": "cherkasy-ref"}]
            if method == "getAreas":
                return [{"Ref": "cherkasy-ref", "Description": "Черкаська",
                         "DescriptionRu": "Черкасская"}]
            if method == "getWarehouses":
                self.assertEqual(props["CityRef"], "smila-ref")
                self.assertEqual(props["FindByString"], "4")
                return [{"Ref": "branch-4", "Number": "4",
                         "Description": "Відділення №4"}]
            self.fail("Unexpected API operation: " + method)
        client._call = Mock(side_effect=fake_api)
        city_ref = client.get_city_ref(order["city"], area=order["area"], warehouse=order["warehouse"])
        branch_ref = client.get_warehouse_ref(city_ref, order["warehouse"])
        self.assertEqual((city_ref, branch_ref), ("smila-ref", "branch-4"))
        self.assertNotIn("InternetDocument", [c.args[0] for c in client._call.call_args_list])


class DeliverySelectionTests(unittest.TestCase):
    def _client(self):
        client = NovaPoshtaClient("fake-test-api-key")
        client.get_sender_info = Mock(return_value={
            "city_ref": "sender-city",
            "sender_ref": "sender",
            "address_ref": "sender-wh",
            "contact_ref": "sender-contact",
            "phone": "380959477703",
        })
        client.get_city_ref = Mock(return_value="recipient-city")
        client.get_warehouse_ref = Mock(return_value="recipient-wh")
        client.get_settlement_ref = Mock(return_value="recipient-settlement")
        client.get_or_create_recipient = Mock(
            return_value=("recipient", "recipient-contact", "380633769504")
        )
        client._call = Mock(return_value=[{"IntDocNumber": "20400000000000"}])
        return client

    def test_warehouse_has_priority_over_a_street(self):
        client = self._client()
        result = client.create_ttn(
            full_name="Журавльова Інна Олександрівна",
            phone="0633769504",
            city="Одеса", warehouse="8",
            street="Геннадія Афанасьєва", house="3/5",
            cost=3250,
        )
        self.assertEqual(result["ttn"], "20400000000000")
        client.get_settlement_ref.assert_not_called()
        method, operation, props = client._call.call_args.args
        self.assertEqual((method, operation), ("InternetDocument", "save"))
        self.assertEqual(props["ServiceType"], "WarehouseWarehouse")
        self.assertNotIn("NewAddress", props)
        self.assertNotIn("BackwardDeliveryData", props)

    def test_door_delivery_when_no_warehouse(self):
        client = self._client()
        client.create_ttn(
            full_name="Желязкова Лариса",
            phone="0972218934",
            city="Петровірівка", street="Шклярука", house="15", cost=1800,
        )
        client.get_warehouse_ref.assert_not_called()
        method, operation, props = client._call.call_args.args
        self.assertEqual(props["ServiceType"], "WarehouseDoors")
        self.assertEqual(props["RecipientHouse"], "15")


if __name__ == "__main__":
    unittest.main()
