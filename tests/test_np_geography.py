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

    def test_street_and_house_before_branch_number_use_exact_city_prefix(self):
        city = {"Ref": "velykodolynske-city", "Description": "Великодолинське", "DescriptionRu": "Великодолинское"}
        for location in ("Великодолинське Маріїнський 5.", "Великодолинське, Маріїнська, 5",
                         "Великодолинське вул. Маріїнська 5"):
            with self.subTest(location=location):
                client = self.client([city])
                self.assertEqual(client.get_city_ref(location, warehouse="2"), "velykodolynske-city")
                self.assertEqual(client._call.call_args.args[2]["FindByString"], "Великодолинське")

    def test_multiword_city_with_multiword_street_keeps_whole_city(self):
        city = {"Ref": "bila-tserkva", "Description": "Біла Церква", "DescriptionRu": "Белая Церковь"}
        client = self.client([city])
        self.assertEqual(client.get_city_ref("Біла Церква Героїв Небесної Сотні 12", warehouse="2"), "bila-tserkva")
        self.assertEqual(client._call.call_args.args[2]["FindByString"], "Біла Церква")

    def test_branch_address_does_not_remove_or_override_oblast(self):
        location = "Киевская обл, Васильков, Центральная, 5"
        self.assertEqual(self.client().get_city_ref(location, warehouse="2"), "vasylkiv-city")
        with self.assertRaisesRegex(NovaPoshtaError, "разные области"):
            self.client().get_city_ref(location, area="Днепропетровская", warehouse="2")
        with self.assertRaisesRegex(NovaPoshtaError, "не найден в области"):
            self.client().get_city_ref("Днепропетровская обл, Васильков Центральная 5", warehouse="2")

    def test_city_prefix_requires_a_street_house_and_explicit_branch(self):
        for location, warehouse in (("Васильков Центральная 5", ""), ("Васильков Центральная", "2"),
                                    ("Васильковка Центральная 5", "2")):
            with self.subTest(location=location, warehouse=warehouse):
                with self.assertRaises(NovaPoshtaError):
                    self.client().get_city_ref(location, warehouse=warehouse)

    def test_longest_exact_city_name_wins_over_shorter_name(self):
        cities = [{"Ref": "nova-kakhovka", "Description": "Нова Каховка"},
                  {"Ref": "nova-village", "Description": "Нова"}]
        self.assertEqual(self.client(cities).get_city_ref("Нова Каховка Шевченка 5", warehouse="2"), "nova-kakhovka")

    def test_ambiguous_city_with_branch_address_still_requires_oblast(self):
        other = dict(CITY, Ref="other-city", Area="dnipro-area")
        with self.assertRaisesRegex(NovaPoshtaError, "неоднозначно"):
            self.client([CITY, other]).get_city_ref("Васильков Центральная 5", warehouse="2")

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
