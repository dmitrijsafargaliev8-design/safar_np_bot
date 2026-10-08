"""No live API calls: FOP primary selection must never borrow old sender refs."""
import json
import os
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from np_client import NovaPoshtaError
from sender_profiles import SenderProfiles
from order_pipeline import OrderPipeline
from bot import parse_order
from test_order_pipeline import ORDER, message


class FopPrimaryTests(unittest.TestCase):
    def setUp(self):
        self.legacy = Mock()
        self.legacy.api_key = "legacy-test-key"
        self.legacy.create_ttn.return_value = {"ttn": "20400000000001"}
        self.fop_secret = "another-account-test-key"

    def test_missing_fop_secret_cannot_activate_primary(self):
        with self.assertRaisesRegex(NovaPoshtaError, "не настроен"):
            SenderProfiles(self.legacy, raw="", environ={"NP_PRIMARY_SENDER_PROFILE": "fop"})

    def test_fop_client_isolated_and_routed_primary(self):
        with patch.dict(os.environ, {
            "NP_SENDER_REF": "legacy-sender",
            "NP_SENDER_CONTACT_REF": "legacy-contact",
            "NP_SENDER_ADDRESS_REF": "legacy-address",
            "NP_SENDER_CITY_REF": "legacy-city",
            "NP_SENDER_PHONE": "0500000001",
        }):
            p = SenderProfiles(self.legacy, raw="", environ={
                "NP_FOP_API_KEY": self.fop_secret,
                "NP_PRIMARY_SENDER_PROFILE": "fop",
            })
        self.assertEqual(p.primary_profile, "fop")
        self.assertIs(p.get("default"), self.legacy)
        client = p.get("fop")
        self.assertEqual(client.api_key, self.fop_secret)
        for name in ("sender_ref", "contact_ref", "address_ref", "sender_city_ref", "sender_phone"):
            self.assertEqual(getattr(client, name), "")

        def lookup(model, method, props):
            if method == "getCounterparties":
                return [{"Ref": "fop-sender"}]
            if method == "getCounterpartyContactPersons":
                return [{"Ref": "fop-contact", "Phones": "+380501112233"}]
            if method == "getCounterpartyAddresses":
                return [{"Ref": "fop-warehouse", "CityRef": "fop-city"}]
            self.fail("Unexpected lookup")
        client._call = Mock(side_effect=lookup)
        sender = client.get_sender_info()
        self.assertEqual(sender["sender_ref"], "fop-sender")
        self.assertEqual(sender["contact_ref"], "fop-contact")
        self.assertEqual(sender["address_ref"], "fop-warehouse")
        self.assertEqual(sender["phone"], "380501112233")

        client.create_ttn = Mock(return_value={"ttn": "20400000000002"})
        client.is_ttn_deleted = Mock(return_value=False)
        bot = Mock()
        sent_id = [1000]
        def sent(**kw):
            sent_id[0] += 1
            return SimpleNamespace(message_id=sent_id[0])
        for name in ("send_photo", "send_video", "send_document", "send_animation", "send_message"):
            getattr(bot, name).side_effect = sent
        bot.send_media_group.side_effect = lambda **kw: [sent() for _ in kw["media"]]
        now = [1791400000.0]
        pipe = OrderPipeline(":memory:", parse_order, self.legacy, bot, clock=lambda: now[0],
                             sender_clients=p.clients,
                             default_sender_profile=p.primary_profile)
        self.addCleanup(pipe.close)
        self.assertEqual(pipe.sender_preference(100, 99), "fop")
        pipe.ingest(message(1, caption=ORDER), 1)
        now[0] += 5
        self.assertTrue(pipe.tick())
        client.create_ttn.assert_called_once()
        self.legacy.create_ttn.assert_not_called()
        self.assertEqual(pipe.list_orders(100,99)[0]["sender_profile"], "fop")
        pipe.set_sender_preference(100, 99, "default")
        self.assertEqual(pipe.sender_preference(100, 99), "default")
        self.assertEqual(pipe.list_orders(100,99)[0]["sender_profile"], "fop")

    def test_explicit_fop_refs_use_her_key_only(self):
        refs = {
            "sender_ref": "11111111-1111-4111-8111-111111111111",
            "contact_ref": "22222222-2222-4222-8222-222222222222",
            "address_ref": "33333333-3333-4333-8333-333333333333",
            "city_ref": "44444444-4444-4444-8444-444444444444",
            "phone": "+380501112233",
        }
        p = SenderProfiles(self.legacy, raw=json.dumps({"fop": refs}),
                           environ={"NP_FOP_API_KEY": self.fop_secret,
                                    "NP_PRIMARY_SENDER_PROFILE": "fop"})
        self.assertEqual(p.get("fop").api_key, self.fop_secret)
        self.assertEqual(p.get("fop").get_sender_info()["sender_ref"], refs["sender_ref"])

    def test_ambiguous_fop_sender_fails_closed(self):
        p = SenderProfiles(self.legacy, raw="", environ={"NP_FOP_API_KEY": self.fop_secret,
                                                         "NP_PRIMARY_SENDER_PROFILE": "fop"})
        client = p.get("fop")
        client._call = Mock(return_value=[{"Ref": "person-a"}, {"Ref": "person-b"}])
        with self.assertRaises(NovaPoshtaError):
            client.get_sender_info()
        self.legacy.create_ttn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
