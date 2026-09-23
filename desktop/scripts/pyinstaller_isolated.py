from __future__ import annotations

import sys
import sysconfig
from pathlib import Path

site_packages = Path(sysconfig.get_paths()["purelib"]).resolve()
controlled_paths = [
    site_packages,
    site_packages / "win32",
    site_packages / "win32" / "lib",
    site_packages / "pythonwin",
]
for path in controlled_paths:
    if path.exists():
        sys.path.append(str(path))

for path in sys.path:
    lowered = str(path).lower()
    if "tool-phiim" in lowered or "\\phim\\tools-phiim" in lowered:
        raise SystemExit(f"RELEASE_PATH_CONTAMINATION: {path}")

import PyInstaller.__main__

PyInstaller.__main__.run(sys.argv[1:])
