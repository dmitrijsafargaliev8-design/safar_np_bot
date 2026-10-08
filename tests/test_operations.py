"""Read-only queue/statistics regressions; never call the real carrier API."""
import unittest

from order_pipeline import OrderPipeline


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = OrderPipeline(":memory:", lambda text: {}, None, None, clock=lambda: 100.0)

    def tearDown(self):
        self.pipeline.close()

    def add(self, key, state, *, chat=100, owner=42):
        job = dict(key=key, chat_id=chat, owner_id=owner, state=state,
                   due=100.0, order={"full_name": "Test User"})
        with self.pipeline.lock, self.pipeline.db:
            self.pipeline._write(job)

    def test_counts_are_owner_scoped_and_include_all_states(self):
        for idx, state in enumerate(["created", "created", "invalid", "failed", "deleted"]):
            self.add(str(idx), state)
        self.add("other-owner", "created", owner=43)
        self.add("other-chat", "failed", chat=101)
        self.assertEqual(self.pipeline.order_state_counts(100, 42),
                         {"created": 2, "invalid": 1, "failed": 1, "deleted": 1})

    def test_queue_excludes_completed_and_other_users(self):
        for idx, state in enumerate(["created", "deleted", "collecting",
                                     "processing", "invalid", "failed", "uncertain"]):
            self.add(str(idx), state)
        self.add("other", "failed", owner=43)
        rows = self.pipeline.order_queue(100, 42)
        self.assertEqual({row["state"] for row in rows},
                         {"collecting", "processing", "invalid", "failed", "uncertain"})
        self.assertEqual(len(self.pipeline.order_queue(100, 42, limit=2)), 2)


if __name__ == "__main__":
    unittest.main()
