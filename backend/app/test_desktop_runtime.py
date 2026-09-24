import os
import unittest
from unittest.mock import patch

from .main import DEV_ALLOWED_ORIGINS, DESKTOP_ALLOWED_ORIGINS, _configured_allowed_origins, _desktop_token_valid


class DesktopRuntimeAuthTests(unittest.TestCase):
    def test_web_mode_without_token_requirement_stays_compatible(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TH_MEDIA_AUTH_TOKEN", None)
            os.environ.pop("TH_MEDIA_DESKTOP_MODE", None)
            self.assertTrue(_desktop_token_valid(None))
            self.assertTrue(_desktop_token_valid("anything"))

    def test_desktop_mode_without_runtime_token_fails_closed(self):
        with patch.dict(os.environ, {"TH_MEDIA_DESKTOP_MODE": "1"}, clear=False):
            os.environ.pop("TH_MEDIA_AUTH_TOKEN", None)
            self.assertFalse(_desktop_token_valid(None))
            self.assertFalse(_desktop_token_valid("anything"))

    def test_desktop_mode_requires_exact_runtime_token(self):
        with patch.dict(os.environ, {"TH_MEDIA_DESKTOP_MODE": "1", "TH_MEDIA_AUTH_TOKEN": "desktop-secret-token"}, clear=False):
            self.assertFalse(_desktop_token_valid(None))
            self.assertFalse(_desktop_token_valid("wrong"))
            self.assertTrue(_desktop_token_valid("desktop-secret-token"))

    def test_desktop_cors_ignores_dev_or_external_origins(self):
        with patch.dict(os.environ, {"TH_MEDIA_DESKTOP_MODE": "1", "TH_MEDIA_DEV_SERVER": "0", "TH_MEDIA_ALLOWED_ORIGINS": "https://evil.example,http://localhost:5173"}, clear=False):
            self.assertEqual(_configured_allowed_origins(), DESKTOP_ALLOWED_ORIGINS)

    def test_desktop_debug_mode_uses_fixed_dev_origins(self):
        with patch.dict(os.environ, {"TH_MEDIA_DESKTOP_MODE": "1", "TH_MEDIA_DEV_SERVER": "1", "TH_MEDIA_ALLOWED_ORIGINS": "https://evil.example"}, clear=False):
            self.assertEqual(_configured_allowed_origins(), DEV_ALLOWED_ORIGINS)

    def test_web_mode_can_use_explicit_development_origins(self):
        with patch.dict(os.environ, {"TH_MEDIA_DESKTOP_MODE": "0", "TH_MEDIA_ALLOWED_ORIGINS": "http://127.0.0.1:5173"}, clear=False):
            self.assertEqual(_configured_allowed_origins(), ["http://127.0.0.1:5173"])


if __name__ == "__main__":
    unittest.main()
