import unittest

from access_policy import AccessPolicy


class AccessPolicyTests(unittest.TestCase):
    def test_legacy_mode_keeps_existing_chat_allowlist(self):
        policy = AccessPolicy(chats="-100123,55")
        self.assertTrue(policy.permits(-100123, 42))
        self.assertFalse(policy.permits(10, 42))

    def test_user_allowlist_blocks_other_group_members(self):
        policy = AccessPolicy(chats="-100123", users="42")
        self.assertTrue(policy.permits(-100123, 42))
        self.assertFalse(policy.permits(-100123, 43))

    def test_strict_mode_denies_when_missing_either_allowlist(self):
        for policy in (AccessPolicy(strict=True),
                       AccessPolicy(chats="123", strict=True),
                       AccessPolicy(users="42", strict=True)):
            self.assertFalse(policy.permits(123, 42))

    def test_strict_mode_allows_only_configured_actor_and_chat(self):
        policy = AccessPolicy(chats="-100123,99", users="42,43", strict=True)
        self.assertTrue(policy.permits(-100123, 42))
        self.assertTrue(policy.permits(99, 43))
        self.assertFalse(policy.permits(-100222, 42))
        self.assertFalse(policy.permits(-100123, 44))

    def test_rejects_anonymous_and_malformed_id(self):
        policy = AccessPolicy(chats="99", users="42")
        self.assertFalse(policy.permits(99, None))
        self.assertFalse(policy.permits(None, 42))
        self.assertFalse(policy.permits(99, -2))
        with self.assertRaises(ValueError):
            AccessPolicy(chats="99,oops")
        with self.assertRaises(ValueError):
            AccessPolicy(users="-10")

    def test_status_does_not_leak_identifiers(self):
        self.assertEqual(AccessPolicy("99", "42", strict=True).status(),
                         {"strict": True, "chats_configured": True, "users_configured": True})


if __name__ == "__main__":
    unittest.main()
