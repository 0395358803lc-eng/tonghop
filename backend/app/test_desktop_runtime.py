import os
import unittest
from unittest.mock import patch

from .main import _desktop_token_valid


class DesktopRuntimeAuthTests(unittest.TestCase):
    def test_web_mode_without_token_requirement_stays_compatible(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TH_MEDIA_AUTH_TOKEN", None)
            self.assertTrue(_desktop_token_valid(None))
            self.assertTrue(_desktop_token_valid("anything"))

    def test_desktop_mode_requires_exact_runtime_token(self):
        with patch.dict(os.environ, {"TH_MEDIA_AUTH_TOKEN": "desktop-secret-token"}, clear=False):
            self.assertFalse(_desktop_token_valid(None))
            self.assertFalse(_desktop_token_valid("wrong"))
            self.assertTrue(_desktop_token_valid("desktop-secret-token"))


if __name__ == "__main__":
    unittest.main()
