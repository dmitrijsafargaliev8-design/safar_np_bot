import json
import unittest
from unittest.mock import Mock

from np_client import NovaPoshtaError
from sender_profiles import SenderProfiles


VALID = {"label": "Второй отправитель", "sender_ref": "11111111-1111-4111-8111-111111111111",
         "contact_ref": "22222222-2222-4222-8222-222222222222",
         "address_ref": "33333333-3333-4333-8333-333333333333",
         "city_ref": "44444444-4444-4444-8444-444444444444",
         "phone": "+380500000002", "api_key_env": "NP_SECONDARY_API_KEY"}


class SenderProfileTests(unittest.TestCase):
    def setUp(self):
        self.main = Mock(api_key="default-test-token")

    def test_default_stays_without_optional_config(self):
        p = SenderProfiles(self.main, raw="", environ={})
        self.assertIs(p.get("default"), self.main)
        self.assertEqual(list(p.available()), ["default"])

    def test_secondary_is_isolated_and_has_explicit_refs(self):
        p = SenderProfiles(self.main, raw=json.dumps({"other": VALID}),
                           environ={"NP_SECONDARY_API_KEY": "safe-test-token"})
        c = p.get("other")
        self.assertIsNot(c, self.main)
        self.assertEqual(c.api_key, "safe-test-token")
        self.assertEqual(c.get_sender_info()["phone"], "380500000002")
        self.assertEqual(c.get_sender_info()["sender_ref"], VALID["sender_ref"])

    def test_missing_api_key_and_partial_refs_fail_closed(self):
        with self.assertRaises(NovaPoshtaError):
            SenderProfiles(self.main, raw=json.dumps({"other": VALID}), environ={})
        with self.assertRaises(NovaPoshtaError):
            SenderProfiles(self.main, raw=json.dumps({"other": {"label": "Incomplete"}}), environ={})

    def test_unknown_profile_never_falls_back_to_primary(self):
        p = SenderProfiles(self.main, raw="")
        with self.assertRaises(NovaPoshtaError):
            p.get("other")


if __name__ == "__main__":
    unittest.main()
