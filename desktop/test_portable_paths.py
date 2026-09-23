import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = [ROOT / "backend", ROOT / "frontend" / "src", ROOT / "desktop"]
TEXT_SUFFIXES = {".py", ".ps1", ".ts", ".tsx", ".js", ".jsx", ".rs", ".toml", ".json"}
SKIP_PARTS = {"node_modules", "target", "sidecars", ".build", "gen"}

FORBIDDEN = [
    re.compile(r"[A-Za-z]:\\Users\\[^\\]+\\Desktop\\tonghop-main", re.I),
    re.compile(r"[A-Za-z]:\\Users\\Admin\\Desktop", re.I),
    re.compile(r"tonghop-main\\tonghop-main", re.I),
]


class PortablePathTests(unittest.TestCase):
    def test_source_has_no_machine_specific_project_paths(self):
        hits = []
        for base in SOURCE_DIRS:
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                if any(part in SKIP_PARTS for part in path.parts):
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for pattern in FORBIDDEN:
                    if pattern.search(text):
                        hits.append(f"{path.relative_to(ROOT)} -> {pattern.pattern}")
        self.assertEqual(hits, [], "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
