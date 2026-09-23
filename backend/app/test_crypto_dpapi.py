import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import crypto


@unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
class DesktopDpapiTests(unittest.TestCase):
    def test_dpapi_round_trip(self):
        payload = b"th-media-dpapi-smoke"
        protected = crypto._dpapi_protect(payload)
        self.assertNotEqual(protected, payload)
        self.assertEqual(crypto._dpapi_unprotect(protected), payload)

    def test_desktop_mode_migrates_plaintext_master_key(self):
        with tempfile.TemporaryDirectory() as temp:
            key_path = Path(temp) / "master.key"
            key = crypto.Fernet.generate_key()
            key_path.write_bytes(key)

            with (
                patch.object(crypto, "KEY_PATH", key_path),
                patch.dict(
                    os.environ,
                    {"TH_MEDIA_DESKTOP_MODE": "1"},
                    clear=False,
                ),
            ):
                loaded = crypto._load_master_key()
                protected_path = crypto._dpapi_key_path()

                self.assertEqual(loaded, key)
                self.assertFalse(key_path.exists())
                self.assertTrue(protected_path.exists())
                self.assertNotEqual(protected_path.read_bytes(), key)
                self.assertEqual(crypto._load_master_key(), key)


if __name__ == "__main__":
    unittest.main()
