"""Guards that keep the real acceptance databases out of reach of the test suite.

They exist because ~95% of film_pipeline_events turned out to be test residue: config
resolves paths at import time, so a test module that never isolated anything still wrote
to the production store and still reported OK.
"""

from __future__ import annotations

import ast
import glob
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from . import config, db
from .data_isolation import IsolatedDataTestCase
from .db import production_db_paths

ISOLATED_BASES = ("IsolatedDataTestCase", "IsolatedDataMixin")
DB_SYMBOLS = {"init_db", "create_film_project", "delete_film_project", "emit_event", "connect"}
TEST_GLOBS = ("app/test_*.py", "flow_bridge/test_*.py")


def _class_names(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def _base_names(node: ast.ClassDef) -> list[str]:
    names = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _resolved_bases(node: ast.ClassDef, classes: dict[str, ast.ClassDef]) -> list[str]:
    """Base names, following same-module superclass chains (e.g. ObservabilityTests(Batch4Case))."""
    seen: set[str] = set()
    out: list[str] = []
    stack = list(_base_names(node))
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
        if name in classes:
            stack.extend(_base_names(classes[name]))
    return out


class IsolationGuardTests(IsolatedDataTestCase):
    def test_gate_blocks_every_production_store_path(self):
        """connect() must refuse the dev and installed stores while the gate is on.

        Inside this class config.DB_PATH has already been moved to a temp root, so the
        protected paths are named by production_db_paths() rather than read from config.
        """
        protected = sorted(production_db_paths())
        self.assertTrue(protected, "no production store path was recognised - the guard would be vacuous")
        for stored in protected:
            with patch.dict(os.environ, {db.ISOLATION_ENV: "1"}, clear=False), patch.object(config, "DB_PATH", stored):
                with self.assertRaises(RuntimeError) as ctx:
                    with db.connect():
                        self.fail(f"production store opened while the isolation gate was on: {stored}")
                self.assertIn("DB_ISOLATION_REQUIRED", str(ctx.exception))

    def test_gate_lets_a_harness_isolated_store_through(self):
        """The other half: isolation is not merely making every test fail."""
        with patch.dict(os.environ, {db.ISOLATION_ENV: "1"}, clear=False):
            with db.connect() as conn:
                self.assertEqual(1, conn.execute("SELECT 1").fetchone()[0])

    def test_subprocess_style_isolation_is_still_accepted(self):
        """test_desktop_security_acceptance isolates by handing a child TH_MEDIA_DB_PATH."""
        child_store = self.data_dir / "child" / "aihub.db"
        child_store.parent.mkdir(parents=True, exist_ok=True)
        with patch.dict(os.environ, {db.ISOLATION_ENV: "1"}, clear=False):
            db._guard_isolation(child_store)  # must not raise

    def test_every_db_reaching_test_module_is_isolated(self):
        """Anti-regression net for tests written from now on.

        A module that reaches SQLite through one of the DB symbols must derive its test
        classes from the isolation harness, or isolate a child process by env.
        """
        offenders = []
        root = Path(__file__).resolve().parents[1]
        for pattern in TEST_GLOBS:
            for path in sorted(root.glob(pattern)):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                classes = _class_names(tree)
                subprocess_env = "TH_MEDIA_DB_PATH" in (module_text := path.read_text(encoding="utf-8"))
                for cls in classes.values():
                    used = {
                        node.id
                        for node in ast.walk(cls)
                        if isinstance(node, ast.Name) and node.id in DB_SYMBOLS
                    }
                    if not used:
                        continue
                    if any(base in ISOLATED_BASES for base in _resolved_bases(cls, classes)):
                        continue
                    if subprocess_env and "subprocess" in module_text:
                        continue
                    offenders.append(f"{path.name}:{cls.name} uses {sorted(used)}")
        self.assertEqual([], offenders, "unisolated test classes would write to the real database")

    def test_deleting_a_project_leaves_no_orphan_events(self):
        """The invariant that 20k orphan rows prove was never enforced."""
        from .film_event_store import (
            SYSTEM_SCOPE,
            count_events_without_project,
            emit_event,
        )
        from .film_store import create_film_project, delete_film_project

        emit_event(SYSTEM_SCOPE, "CAPABILITY_REFRESHED")
        project = create_film_project("__guard_orphan__", "x" * 60, "xkiro", "m", {})
        for _ in range(3):
            emit_event(project["id"], "SCENE_QUEUED")
        self.assertEqual(0, count_events_without_project())

        result = delete_film_project(project["id"])

        self.assertEqual(3, result["events_purged"])
        self.assertEqual(0, count_events_without_project())
        with db.connect() as conn:
            self.assertEqual(0, conn.execute(
                "SELECT COUNT(*) FROM film_pipeline_events WHERE project_id=?", (project["id"],)
            ).fetchone()[0])
            self.assertEqual(1, conn.execute(
                "SELECT COUNT(*) FROM film_pipeline_events WHERE project_id=?", (SYSTEM_SCOPE,)
            ).fetchone()[0], "system diagnostics must survive a project delete")

    def test_purge_refuses_the_system_scope(self):
        from .film_event_store import SYSTEM_SCOPE, purge_project_events

        with db.connect() as conn:
            self.assertEqual(0, purge_project_events(conn, SYSTEM_SCOPE))
            self.assertEqual(0, purge_project_events(conn, ""))


if __name__ == "__main__":
    unittest.main()
