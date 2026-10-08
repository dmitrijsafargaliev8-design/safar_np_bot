import unittest

from bot import parse_order


class ExtendedParserTests(unittest.TestCase):
    def test_one_line_order_with_name_before_phone(self):
        order = parse_order("Одесса НП 142 Іваненко Марія +380 50 000 00 01 Оценка 1600")
        self.assertEqual(order["city"], "Одесса")
        self.assertEqual(order["warehouse"], "142")
        self.assertEqual(order["full_name"], "Іваненко Марія")
        self.assertEqual(order["cost"], 1600)

    def test_name_after_phone_and_no_cod(self):
        order = parse_order("м. Коростень Нова пошта 7 0500000001 Синяк Віта Оценка 900")
        self.assertEqual(order["full_name"], "Синяк Віта")
        self.assertEqual(order["city"], "Коростень")
        self.assertEqual(order["cod_amount"], 0)

    def test_postomat_and_emoji_lines(self):
        order = parse_order("📍 Одесса\n📦 Почтомат №142\n👤 Іваненко Марія\n☎ +380 50 000 00 01\n💰 Оценка: 1 600 грн")
        self.assertEqual(order["city"], "Одесса")
        self.assertEqual(order["warehouse"], "142")
        self.assertEqual(order["cost"], 1600)

    def test_thousand_separators(self):
        for cost in ("1 600", "1.600", "1,600", "1.600,50", "1,600.50"):
            with self.subTest(cost=cost):
                order = parse_order("Одесса\nНП 8\nІваненко Марія\n0500000001\nОценка " + cost)
                self.assertEqual(order["cost"], 1600.5 if cost.endswith("50") else 1600)

    def test_cost_does_not_consume_phone_on_next_line(self):
        order = parse_order("Одесса\nНП 8\nІваненко Марія\nОценка 1600\n0500000001")
        self.assertEqual(order["cost"], 1600)

    def test_ukrainian_cod_label(self):
        order = parse_order("Одесса\nНП 8\nІваненко Марія\n0500000001\nОцінка: 1600\nПісляплата: 1400")
        self.assertEqual(order["cost"], 1600)
        self.assertEqual(order["cod_amount"], 1400)

    def test_missing_cost_requests_cost_without_guessing(self):
        with self.assertRaisesRegex(ValueError, "Оценка"):
            parse_order("Одесса\nНП 8\nІваненко Марія\n0500000001")

    def test_unknown_metadata_does_not_become_api_kwargs(self):
        order = parse_order("Артикул: 1234\nОдесса\nНП 8\nІваненко Марія\n0500000001\nОценка 1600")
        self.assertNotIn("артикул", order)

    def test_multiple_recipients_are_not_combined(self):
        with self.assertRaisesRegex(ValueError, "несколько телефонов"):
            parse_order("Одесса\nНП 8\nІваненко Марія\n0500000001\nОценка 1600\n0500000002")

    def test_name_starting_with_city_prefix_does_not_become_city(self):
        order = parse_order("Синяк Віта\n0500000001\nОдесса\nНП 8\nОценка 1600")
        self.assertEqual(order["city"], "Одесса")
        self.assertEqual(order["full_name"], "Синяк Віта")

    def test_cod_without_amount_is_not_silently_removed(self):
        with self.assertRaisesRegex(ValueError, "наложка без суммы"):
            parse_order("Одесса\nНП 8\nІваненко Марія\n0500000001\nОценка 1600\nНаложка")

    def test_lowercase_name_is_supported(self):
        order = parse_order("Одесса\nНП 8\nіваненко марія\n0500000001\nОценка 1600")
        self.assertEqual(order["full_name"], "іваненко марія")

    def test_cost_with_thousands_and_currency(self):
        order = parse_order("Одесса\nНП 8\nІваненко Марія\n0500000001\nОценка: 1.600 грн")
        self.assertEqual(order["cost"], 1600)

    def test_negative_cost_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_order("Одесса\nНП 8\nІваненко Марія\n0500000001\nОценка -1600")

    def test_conflicting_cod_instructions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Одновременно"):
            parse_order("Одесса\nНП 8\nІваненко Марія\n0500000001\nОценка 1600\nНаложка 1400\nБез наложки")
