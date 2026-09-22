import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from . import crypto


class DesktopCryptoTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_desktop_mode_migrates_plain_master_key_to_dpapi(self):
        with tempfile.TemporaryDirectory() as temp:
            key_path = Path(temp) / "master.key"
            original = crypto.KEY_PATH
            plain_key = Fernet.generate_key()
            key_path.write_bytes(plain_key)
            try:
                crypto.KEY_PATH = key_path
                with patch.dict(os.environ, {"TH_MEDIA_DESKTOP_MODE": "1"}, clear=False):
                    os.environ.pop("SECRETS_ENCRYPTION_KEY", None)
                    loaded = crypto._load_master_key()
                    self.assertEqual(loaded, plain_key)
                    self.assertFalse(key_path.exists())

                    protected = key_path.with_name("master.key.dpapi")
                    self.assertTrue(protected.exists())
                    self.assertNotEqual(protected.read_bytes(), plain_key)

                    loaded_again = crypto._load_master_key()
                    self.assertEqual(loaded_again, plain_key)
            finally:
                crypto.KEY_PATH = original

    def test_web_mode_keeps_existing_plain_key_compatible(self):
        with tempfile.TemporaryDirectory() as temp:
            key_path = Path(temp) / "master.key"
            original = crypto.KEY_PATH
            plain_key = Fernet.generate_key()
            key_path.write_bytes(plain_key)
            try:
                crypto.KEY_PATH = key_path
                with patch.dict(os.environ, {"TH_MEDIA_DESKTOP_MODE": "0"}, clear=False):
                    os.environ.pop("SECRETS_ENCRYPTION_KEY", None)
                    loaded = crypto._load_master_key()
                    self.assertEqual(loaded, plain_key)
                    self.assertTrue(key_path.exists())
                    self.assertFalse(key_path.with_name("master.key.dpapi").exists())
            finally:
                crypto.KEY_PATH = original


if __name__ == "__main__":
    unittest.main()
