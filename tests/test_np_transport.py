import unittest
from unittest.mock import Mock, patch

from requests.exceptions import Timeout

from np_client import NovaPoshtaClient, NovaPoshtaError, NovaPoshtaTemporaryError, NovaPoshtaUncertainError


class NovaPoshtaTransportTests(unittest.TestCase):
    def setUp(self):
        self.client = NovaPoshtaClient("fake-test-api-key")

    @patch("np_client.requests.post", side_effect=Timeout("timeout"))
    def test_document_save_timeout_is_uncertain(self, post):
        with self.assertRaises(NovaPoshtaUncertainError):
            self.client._call("InternetDocument", "save", {})
        self.assertEqual(post.call_count, 1)

    @patch("np_client.requests.post", side_effect=Timeout("timeout"))
    def test_directory_read_timeout_is_safely_retryable(self, post):
        with self.assertRaises(NovaPoshtaTemporaryError):
            self.client._call("Address", "getCities", {})

    @patch("np_client.requests.post")
    def test_success_flag_missing_from_save_response_is_uncertain(self, post):
        post.return_value = Mock(json=Mock(return_value={"data": []}))
        with self.assertRaises(NovaPoshtaUncertainError):
            self.client._call("InternetDocument", "save", {})

    @patch("np_client.requests.post")
    def test_explicit_api_rejection_is_a_known_failure(self, post):
        post.return_value = Mock(json=Mock(return_value={"success": False, "data": [], "errors": ["Invalid recipient"]}))
        with self.assertRaises(NovaPoshtaError) as caught:
            self.client._call("InternetDocument", "save", {})
        self.assertNotIsInstance(caught.exception, NovaPoshtaUncertainError)
