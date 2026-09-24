"""Redirect every app-level data path to a throwaway root for the duration of a test.

``config`` resolves its paths once, at import, and most modules then hold their own
copy of those constants (``desktop_update.DB_PATH``, ``desktop_diagnostics._DIAGNOSTICS_DIR``,
``BACKUP_DIR`` …). Setting ``TH_MEDIA_*`` after import therefore changes nothing, and patching
one module at a time leaks on the next module that was missed -- which is how ~20k rows of test
events ended up in the real acceptance database. So instead of env vars we rebind every
already-imported ``app.*`` attribute that points into the production data root, and restore
all of them on exit.

``db.connect()`` refuses to open anything but the database named in ``TH_MEDIA_ISOLATED_DB``
while ``TH_MEDIA_REQUIRE_DB_ISOLATION=1``, so a module that resolves its path in some way this
harness did not foresee fails loudly rather than writing to the real store.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config

#: config attributes that define the production data root and everything under it.
CONFIG_ATTRS = ("DATA_DIR", "MEDIA_DIR", "TEMP_DIR", "DB_PATH", "KEY_PATH")

#: Set by the harness to the isolated database; read by db.connect()'s guard.
ISOLATED_DB_ENV = "TH_MEDIA_ISOLATED_DB"

#: Opt-out for keeping the temp tree around to inspect failing artifacts.
KEEP_ENV = "TH_MEDIA_KEEP_ISOLATED_DATA"

_ENV_FOR_ATTR = {
    "DATA_DIR": "TH_MEDIA_DATA_DIR",
    "MEDIA_DIR": "TH_MEDIA_MEDIA_DIR",
    "TEMP_DIR": "TH_MEDIA_TEMP_DIR",
    "DB_PATH": "TH_MEDIA_DB_PATH",
    "KEY_PATH": "TH_MEDIA_KEY_PATH",
}


def _relocate(value: Path, mappings: list[tuple[Path, Path]]) -> Path | None:
    for old, new in mappings:
        if value == old:
            return new
        try:
            relative = value.relative_to(old)
        except ValueError:
            continue
        return new / relative
    return None


def _rebindable(value, mappings):
    """Return the replacement for a module attribute, or None to leave it alone."""
    if isinstance(value, Path):
        return _relocate(value, mappings)
    if isinstance(value, (tuple, list)) and value and all(isinstance(item, Path) for item in value):
        relocated = [_relocate(item, mappings) for item in value]
        if all(item is not None for item in relocated):
            return type(value)(relocated)
    return None


@contextmanager
def isolated_data_root(cleanup: bool = True) -> Iterator[dict]:
    """Swap every app data path for a temporary root; restore on exit.

    Yields the new paths, e.g. ``paths["DB_PATH"]``, ``paths["root"]``.
    """
    root = Path(tempfile.mkdtemp(prefix="thmedia-isolated-")).resolve()
    data = root / ".data"
    layout = {
        "DATA_DIR": data,
        "MEDIA_DIR": data,
        "TEMP_DIR": data / "Temp",
        "DB_PATH": data / "aihub.db",
        "KEY_PATH": data / "master.key",
    }
    for key in ("DATA_DIR", "MEDIA_DIR", "TEMP_DIR"):
        layout[key].mkdir(parents=True, exist_ok=True)

    # Most specific prefixes first, so .data/aihub.db maps as a database and not as a child of DATA_DIR.
    mappings = sorted(
        ((getattr(config, key), layout[key]) for key in CONFIG_ATTRS if hasattr(config, key)),
        key=lambda pair: len(pair[0].parts),
        reverse=True,
    )

    saved: list[tuple[object, str, object]] = []
    touched: set[int] = set()
    for name, module in list(sys.modules.items()):
        if module is None or not (name == "app" or name.startswith("app.")):
            continue
        for attr in dir(module):
            if attr.startswith("__"):
                continue
            try:
                current = getattr(module, attr)
            except AttributeError:  # pragma: no cover - exotic descriptors
                continue
            replacement = _rebindable(current, mappings)
            if replacement is None or replacement == current:
                continue
            saved.append((module, attr, current))
            setattr(module, attr, replacement)
        touched.add(id(module))

    saved_env: dict[str, str | None] = {}
    for key, env_name in _ENV_FOR_ATTR.items():
        saved_env[env_name] = os.environ.get(env_name)
        os.environ[env_name] = str(layout[key])
    saved_env[ISOLATED_DB_ENV] = os.environ.get(ISOLATED_DB_ENV)
    os.environ[ISOLATED_DB_ENV] = str(layout["DB_PATH"])

    try:
        yield {**layout, "root": root}
    finally:
        for module, attr, original in reversed(saved):
            setattr(module, attr, original)
        for env_name, original in saved_env.items():
            if original is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = original
        if cleanup and os.getenv(KEEP_ENV) != "1":
            shutil.rmtree(root, ignore_errors=True)


class IsolatedDataMixin:
    """Mixin form, so async and other specialised TestCases can isolate too.

    Put it first in the bases so the data root is installed before any other fixture runs.
    """

    isolate_data = True
    keep_isolated_data = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._data_isolation = None
        if not cls.isolate_data:
            return
        from .db import init_db

        cls._data_isolation = isolated_data_root(cleanup=not cls.keep_isolated_data)
        cls.paths = cls._data_isolation.__enter__()
        cls.data_dir = cls.paths["DATA_DIR"]
        cls.db_path = cls.paths["DB_PATH"]
        init_db()

    @classmethod
    def tearDownClass(cls):
        try:
            if getattr(cls, "_data_isolation", None) is not None:
                cls._data_isolation.__exit__(None, None, None)
                cls._data_isolation = None
        finally:
            super().tearDownClass()


class IsolatedDataTestCase(IsolatedDataMixin, unittest.TestCase):
    """Base class for any test that can reach SQLite or the media tree."""
