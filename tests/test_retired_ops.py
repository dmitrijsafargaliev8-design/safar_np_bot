"""The retired one-time endpoint must never create live documents."""
import unittest
from unittest.mock import Mock, patch

import bot as app_module


class RetiredOneTimeOpsTests(unittest.TestCase):
    def test_one_time_endpoint_is_gone_even_if_legacy_env_enabled(self):
        client = Mock()
        with patch.object(app_module, "np_client", client), patch.object(
            app_module, "get_pipeline", return_value=None
        ):
            http = app_module.app.test_client()
            for method in ("get", "post"):
                with self.subTest(method=method):
                    response = getattr(http, method)(
                        "/ops/create-one-time-ttn?secret=old-url-secret",
                    )
                    self.assertEqual(response.status_code, 410)
                    self.assertIn("retired", response.json["error"])
            client.create_ttn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
