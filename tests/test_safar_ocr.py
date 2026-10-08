"""Local-only screenshot extraction; no external API or real customer media."""
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from safar_ocr import OcrUnavailable, extract_text


class SafarOcrTests(unittest.TestCase):
    def test_disabled_and_missing_engine_fail_closed(self):
        with patch("safar_ocr.shutil.which", return_value="/usr/bin/tesseract"):
            with self.assertRaises(OcrUnavailable):
                extract_text(b"fake image bytes", enabled=False)
        with patch("safar_ocr.shutil.which", return_value=None):
            with self.assertRaises(OcrUnavailable):
                extract_text(b"fake image bytes", enabled=True)

    def test_local_binary_extracts_order_text_with_strict_timeout(self):
        output = "Прізвище: Тестова\nМісто: Одеса".encode()
        with patch("safar_ocr.shutil.which", return_value="/usr/bin/tesseract"), \
             patch("safar_ocr.subprocess.run",
                   return_value=SimpleNamespace(returncode=0, stdout=output)) as run:
            self.assertIn("Одеса", extract_text(b"fake image", enabled=True))
        self.assertIn("ukr+rus+eng", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["timeout"], 12)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_unavailable_or_garbled_engine_never_returns_fake_order(self):
        with patch("safar_ocr.shutil.which", return_value="/usr/bin/tesseract"), \
             patch("safar_ocr.subprocess.run", side_effect=subprocess.TimeoutExpired("tesseract", 12)):
            with self.assertRaises(OcrUnavailable):
                extract_text(b"invalid", enabled=True)
        with patch("safar_ocr.shutil.which", return_value="/usr/bin/tesseract"), \
             patch("safar_ocr.subprocess.run",
                   return_value=SimpleNamespace(returncode=1, stdout=b"")):
            with self.assertRaises(OcrUnavailable):
                extract_text(b"invalid", enabled=True)


if __name__ == "__main__":
    unittest.main()
