"""Regression: a Polish forwarding must not create a Ukrainian domestic TTN."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bot import parse_order
from international_orders import detect_country, international_missing, normalize_international_phone
from order_pipeline import OrderPipeline


POLISH = (
    "Коваленко Марія Олександрівна\n"
    "Polska\n"
    "Nova Post.Oddział 1\n"
    "Gdańsk 80-171\n"
    "Rakoczego 30/U7 +48111222333\n"
    "sample@example.com"
)


class InternationalParsingTests(unittest.TestCase):
    def test_polish_forwarded_photo_caption_preserves_fields(self):
        order = parse_order(POLISH)
        self.assertEqual(order["shipment_scope"], "international")
        self.assertEqual(order["country_code"], "PL")
        self.assertEqual(order["full_name"], "Коваленко Марія Олександрівна")
        self.assertEqual(order["phone"], "+48111222333")
        self.assertEqual(order["warehouse"], "1")
        self.assertEqual(order["city"], "Gdańsk")
        self.assertEqual(order["postal_code"], "80-171")
        self.assertEqual(order["branch_address"], "Rakoczego 30/U7")
        self.assertEqual(order["street_address"], "")
        self.assertEqual(order["email"], "sample@example.com")
        self.assertIsNone(order["cost"])
        self.assertEqual(order["cod_amount"], 0.0)
        self.assertIn("Оценка товара", international_missing(order))
        self.assertIn("Валюта оценки", international_missing(order))

    def test_explicit_country_not_accidental_description(self):
        self.assertEqual(detect_country("Страна: Poland\nЗаказ"), "PL")
        self.assertEqual(detect_country("Country: Deutschland\nOrder"), "DE")
        self.assertIsNone(detect_country("Одесса\nНП 8\nТовар: кроссовки Polska"))
        domestic = parse_order("Одесса\nНП 8\nІваненко Марія\n0500000001\nОценка 1600")
        self.assertNotEqual(domestic.get("shipment_scope"), "international")

    def test_international_phone_is_not_rewritten_to_380(self):
        self.assertEqual(normalize_international_phone("+48 111 222 333"), "+48111222333")
        with self.assertRaises(ValueError):
            normalize_international_phone("0500000001")

    def test_named_customs_fields_are_optional_for_intake_not_invented(self):
        order = parse_order(POLISH + "\nОценка: 80 EUR\nВес: 1.2 кг\n"
                            "Товар: взуття\nКоличество: 1\nСтрана происхождения: Украина")
        self.assertEqual(order["cost"], 80)
        self.assertEqual(order["currency"], "EUR")
        self.assertEqual(order["weight"], 1.2)
        self.assertEqual(order["quantity"], 1)
        self.assertEqual(order["origin_country"], "Украина")
        self.assertEqual(international_missing(order), [])


class InternationalJournalTests(unittest.TestCase):
    def setUp(self):
        self.now = 1791500000
        self.client = Mock()
        self.telegram = Mock()
        self.counter = 900
        def sent(**_kwargs):
            self.counter += 1
            return SimpleNamespace(message_id=self.counter)
        for name in ("send_photo", "send_video", "send_document", "send_animation", "send_message"):
            getattr(self.telegram, name).side_effect = sent
        self.telegram.send_media_group.side_effect = lambda **kw: [sent() for _ in kw["media"]]
        self.pipeline = OrderPipeline(":memory:", parse_order, self.client, self.telegram,
                                      clock=lambda: self.now)
        self.addCleanup(self.pipeline.close)

    def message(self, mid, *, caption=None, text=None, reply=None, photo=True):
        msg = {"message_id": mid, "date": self.now, "chat": {"id": 100, "type": "private"},
               "from": {"id": 99, "is_bot": False}}
        if caption:
            msg["caption"] = caption
        if text:
            msg["text"] = text
        if photo:
            msg["photo"] = [{"file_id": "test-photo-"+str(mid),
                             "file_unique_id": "test-unique-"+str(mid),
                             "width": 800, "height": 1200}]
        if reply is not None:
            msg["reply_to_message"] = {"message_id": reply}
        return msg

    def drain(self):
        self.now += 5
        for _ in range(10):
            if not self.pipeline.tick():
                return
        self.fail("Non-idle pipeline")

    def test_international_photo_is_journalled_no_carrier_write(self):
        self.pipeline.ingest(self.message(1, caption=POLISH), 1)
        self.drain()
        job = self.pipeline.list_orders(100, 99)[0]
        self.assertEqual(job["state"], "international_review")
        self.assertEqual(job["order"]["country_code"], "PL")
        self.assertIsNone(job["result"])
        self.assertEqual(self.pipeline.attachments(job)[0]["file_id"], "test-photo-1")
        self.assertIn("МЕЖДУНАРОДНЫЙ ЗАКАЗ", self.telegram.send_photo.call_args.kwargs["caption"])
        self.client.create_ttn.assert_not_called()
        self.assertEqual(self.pipeline.order_queue(100, 99)[0]["state"], "international_review")
        self.assertEqual(self.pipeline.order_state_counts(100, 99)["international_review"], 1)
        self.assertEqual(len(self.pipeline.list_orders_page(100, 99, status="attention")), 1)

    def test_reply_adds_fields_without_losing_original_photo(self):
        self.pipeline.ingest(self.message(1, caption=POLISH), 1)
        self.drain()
        reply_to = self.counter
        self.pipeline.ingest(self.message(2, text="Оценка: 80 EUR\nВес: 1.2 кг\n"
                                                 "Товар: взуття\nКоличество: 1\n"
                                                 "Страна происхождения: Украина",
                                          reply=reply_to, photo=False), 2)
        self.drain()
        job = self.pipeline.list_orders(100, 99)[0]
        self.assertEqual(job["state"], "international_review")
        self.assertEqual(job["order"]["cost"], 80.0)
        self.assertEqual(job["order"]["currency"], "EUR")
        self.assertEqual(job["order"]["phone"], "+48111222333")
        self.assertEqual(self.pipeline.attachments(job)[0]["file_id"], "test-photo-1")
        self.assertEqual(job["international_missing"], [])
        self.client.create_ttn.assert_not_called()

    def test_retry_does_not_create_domestic_ttn(self):
        self.pipeline.ingest(self.message(1, caption=POLISH), 1)
        self.drain()
        self.pipeline.ingest(self.message(2, text="/retry", reply=1, photo=False), 2, retry=True)
        self.drain()
        self.assertEqual(self.pipeline.list_orders(100, 99)[0]["state"], "international_review")
        self.client.create_ttn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
