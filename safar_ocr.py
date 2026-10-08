"""Optional local OCR for screenshots; no external AI endpoint or API key.

This module deliberately fails closed if Tesseract or Ukrainian/Russian
language data is unavailable. Enable only after checking Render resources.
"""
import shutil
import subprocess


class OcrUnavailable(ValueError):
    """Source image cannot be parsed safely without a configured OCR engine."""


def extract_text(image: bytes, *, enabled: bool) -> str:
    if not enabled:
        raise OcrUnavailable("Image-only orders require enabled OCR")
    exe = shutil.which("tesseract")
    if not exe:
        raise OcrUnavailable("The OCR executable is not installed")
    try:
        result = subprocess.run(
            [exe, "stdin", "stdout", "-l", "ukr+rus+eng", "--psm", "6"],
            input=image,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=12,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise OcrUnavailable("Image OCR is temporarily unavailable") from None
    if result.returncode != 0:
        raise OcrUnavailable("OCR could not read this screenshot")
    text = result.stdout[:24000].decode("utf-8", errors="replace").strip()
    if not 1 <= len(text) <= 8000:
        raise OcrUnavailable("OCR found no usable order text")
    # The deterministic SAFAR parser and NP field validation remain mandatory.
    return text
