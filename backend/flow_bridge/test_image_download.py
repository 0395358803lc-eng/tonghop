import io
import unittest
from PIL import Image

from .browser import (
    FlowBrowserError,
    classify_flow_image_url,
    validate_downloaded_image,
)


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (12, 80, 160)).save(buf, "JPEG")
    return buf.getvalue()


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (200, 40, 40)).save(buf, "PNG")
    return buf.getvalue()


class FlowImageDownloadTests(unittest.TestCase):
    def test_classify_urls(self):
        self.assertEqual(classify_flow_image_url("https://flow.google.com/asb/abc"), "protected")
        self.assertEqual(
            classify_flow_image_url("https://flow-content.google/image/xyz"),
            "signed_cdn",
        )
        self.assertEqual(classify_flow_image_url("blob:https://flow.google.com/1"), "browser")
        self.assertEqual(classify_flow_image_url("not-a-url"), "invalid")

    def test_html_login_rejected(self):
        html = b"<!doctype html><html lang=\"en\"><head><base href=\"https://accounts.google.com/v3/signin/\"></head><body>Sign in</body></html>"
        with self.assertRaises(FlowBrowserError) as ctx:
            validate_downloaded_image(html, "text/html", "https://accounts.google.com")
        self.assertIn("FLOW_IMAGE_DOWNLOAD_AUTH_REQUIRED", str(ctx.exception))

    def test_valid_jpeg_and_png(self):
        jpeg = validate_downloaded_image(_jpeg_bytes(), "image/jpeg")
        self.assertEqual(jpeg["mime"], "image/jpeg")
        self.assertEqual(jpeg["suffix"], ".jpg")
        png = validate_downloaded_image(_png_bytes(), "image/png")
        self.assertEqual(png["mime"], "image/png")
        self.assertEqual(png["suffix"], ".png")

    def test_empty_and_unknown_rejected(self):
        with self.assertRaises(FlowBrowserError):
            validate_downloaded_image(b"", "image/jpeg")
        with self.assertRaises(FlowBrowserError):
            validate_downloaded_image(b"PK\x03\x04notanimage", "application/zip")


if __name__ == "__main__":
    unittest.main()
