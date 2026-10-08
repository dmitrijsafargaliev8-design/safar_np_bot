"""Select the named city in the named oblast before saving a shipment."""
import unittest
from unittest.mock import Mock

from np_client import NovaPoshtaClient, NovaPoshtaError


AREAS = [
    {"Ref": "kyiv-area", "Description": "Київська", "DescriptionRu": "Киевская"},
    {"Ref": "dnipro-area", "Description": "Дніпропетровська", "DescriptionRu": "Днепропетровская"},
]
CITY = {"Ref": "vasylkiv-city", "Description": "Васильків", "DescriptionRu": "Васильков", "Area": "kyiv-area"}


class GeographyTests(unittest.TestCase):
    def client(self, cities=None):
        client = NovaPoshtaClient("fake-test-api-key")
        def directory(model, method, properties):
            self.assertEqual(model, "Address")
            if method == "getCities":
                return cities if cities is not None else [CITY]
            if method == "getAreas":
                return AREAS
            self.fail("Unexpected directory operation: " + method)
        client._call = Mock(side_effect=directory)
        return client

    def test_oblast_before_or_after_city_in_russian_and_ukrainian(self):
        for location in ("Киевская обл, Васильков", "Васильков, Киевская обл.",
                         "Київська область; м. Васильків", "м.Васильків, Київська обл",
                         "Киевская обл Васильков", "Киевская обл, Васильковский р-н, г. Васильков"):
            with self.subTest(location=location):
                client = self.client()
                self.assertEqual(client.get_city_ref(location), "vasylkiv-city")
                query = client._call.call_args_list[0].args[2]["FindByString"]
                self.assertIn(query, {"Васильков", "Васильків"})

    def test_same_name_in_two_oblasts_uses_area_ref_even_without_area_labels(self):
        other = dict(CITY, Ref="other-city", Area="dnipro-area")
        client = self.client([other, CITY])
        self.assertEqual(client.get_city_ref("Васильков", area="Киевская"), "vasylkiv-city")

    def test_area_hint_is_enforced_even_when_only_one_city_was_returned(self):
        with self.assertRaisesRegex(NovaPoshtaError, "не найден в области"):
            self.client().get_city_ref("Днепропетровская обл, Васильков")

    def test_parenthesized_area_and_district_in_actual_directory_city_names(self):
        labeled = dict(CITY, Description="Васильків (Київська обл., Обухівський р-н)",
                       DescriptionRu="Васильков (Киевская обл., Обуховский р-н)")
        other = dict(CITY, Ref="other-city", Area="dnipro-area", Description="Васильків (Дніпропетровська обл.)",
                     DescriptionRu="Васильков (Днепропетровская обл.)")
        self.assertEqual(self.client([other, labeled]).get_city_ref("Киевская обл, Васильков"), "vasylkiv-city")

    def test_partial_name_cannot_select_a_different_city(self):
        other = dict(CITY, Ref="vasylkivka-city", Description="Васильківка", DescriptionRu="Васильковка")
        with self.assertRaisesRegex(NovaPoshtaError, "не найден точно"):
            self.client([other]).get_city_ref("Киевская обл, Васильков")

    def test_same_named_cities_without_oblast_require_clarification(self):
        other = dict(CITY, Ref="other-city", Area="dnipro-area")
        with self.assertRaisesRegex(NovaPoshtaError, "неоднозначно"):
            self.client([CITY, other]).get_city_ref("Васильков")

    def test_conflicting_inline_and_separate_oblast_are_rejected(self):
        with self.assertRaisesRegex(NovaPoshtaError, "разные области"):
            self.client().get_city_ref("Киевская обл, Васильков", area="Днепропетровская")

    def test_ukrainian_and_russian_aliases_of_same_oblast_are_allowed(self):
        self.assertEqual(self.client().get_city_ref("Киевская обл, Васильков", area="Київська"), "vasylkiv-city")

    def test_door_delivery_also_uses_inline_oblast_and_bilingual_aliases(self):
        client = NovaPoshtaClient("fake-test-api-key")
        def directory(model, method, properties):
            if method == "getAreas":
                return AREAS
            self.assertEqual(method, "searchSettlements")
            self.assertEqual(properties["CityName"], "Васильків")
            return [{"Addresses": [
                {"Ref": "other-settlement", "MainDescription": "Васильків", "Area": "Дніпропетровська"},
                {"Ref": "correct-settlement", "MainDescription": "Васильків", "Area": "Київська"},
            ]}]
        client._call = Mock(side_effect=directory)
        self.assertEqual(client.get_settlement_ref("Киевская обл, м. Васильків"), "correct-settlement")

    def test_door_delivery_does_not_ignore_wrong_explicit_oblast(self):
        client = NovaPoshtaClient("fake-test-api-key")
        client._call = Mock(side_effect=lambda model, method, props: AREAS if method == "getAreas" else
                            [{"Addresses": [{"Ref": "settlement", "MainDescription": "Васильків", "Area": "Київська"}]}])
        with self.assertRaisesRegex(NovaPoshtaError, "не найден в области"):
            client.get_settlement_ref("Васильків", area="Днепропетровская")
