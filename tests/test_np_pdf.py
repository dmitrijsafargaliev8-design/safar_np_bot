"""Carrier PDF fetch regression tests. No external network or live NP documents."""
import unittest
from unittest.mock import Mock, patch

import requests
from np_client import NovaPoshtaClient, NovaPoshtaError, NovaPoshtaTemporaryError


def upstream(payload=b"%PDF-1.4\nmock-document\n%%EOF", *, mime="application/pdf",
             length=None, status=200):
    result = Mock()
    result.status_code = status
    result.headers = {"Content-Type": mime}
    if length is not None:
        result.headers["Content-Length"] = str(length)
    result.iter_content.return_value = [payload]
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class CarrierPdfTests(unittest.TestCase):
    def setUp(self):
        self.client = NovaPoshtaClient("SYNTHETIC-KEY-ONLY")
        self.number = "20400000000001"

    def test_pdf_uses_fixed_https_carrier_host_stream_and_no_redirect(self):
        with patch("np_client.requests.get", return_value=upstream()) as get:
            pdf = self.client.fetch_ttn_pdf(self.number)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        (url,), kwargs = get.call_args
        self.assertTrue(url.startswith("https://my.novaposhta.ua/orders/printDocument/"))
        self.assertIn("/orders[]/" + self.number + "/type/pdf/apiKey/", url)
        self.assertTrue(kwargs["stream"])
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], (5, 12))

    def test_reject_invalid_ttn_and_document_ref_without_network(self):
        with patch("np_client.requests.get") as get:
            for ttn in ["", "../20400000000001", "204123", "2040000000000X"]:
                with self.assertRaises(NovaPoshtaError):
                    self.client.fetch_ttn_pdf(ttn)
            with self.assertRaises(NovaPoshtaError):
                self.client.fetch_ttn_pdf(self.number, doc_ref="../../apiKey")
            get.assert_not_called()

    def test_reject_non_pdf_redirect_errors_and_oversized_content(self):
        cases = [
            upstream(b"<html>not-a-document</html>", mime="text/html"),
            upstream(b"bad-signature"),
            upstream(status=302),
            upstream(length=7 * 1024 * 1024),
            upstream(b"X" * 4 + b"%PDF-"),
        ]
        for response in cases:
            with self.subTest(response=response.status_code), patch("np_client.requests.get", return_value=response):
                with self.assertRaises(NovaPoshtaTemporaryError):
                    self.client.fetch_ttn_pdf(self.number)

    def test_network_failure_is_sanitized(self):
        with patch("np_client.requests.get", side_effect=requests.Timeout("secret-request-url-apiKey")):
            with self.assertRaises(NovaPoshtaTemporaryError) as exc:
                self.client.fetch_ttn_pdf(self.number)
        self.assertNotIn("secret-request-url", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
