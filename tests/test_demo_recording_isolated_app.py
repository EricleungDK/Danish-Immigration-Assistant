"""Isolation rules for the README demo recording app (recording/isolated_app.py)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from danish_rag.provider_setup import CapabilityTestResult
from recording.isolated_app import isolated_environment, prepare_workspace


def passing_tester(configuration):
    return CapabilityTestResult(
        ok=True,
        reason="passed",
        message="ok",
        provider_version="0.34.0",
        model_identity={"family": "gemma4"},
        capabilities=["completion"],
    )


def failing_tester(configuration):
    return CapabilityTestResult(ok=False, reason="structured_output_failed", message="timed out")


class IsolatedEnvironmentTest(unittest.TestCase):
    def test_config_and_data_live_under_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
            env = isolated_environment(Path(root), home=Path(home))
            self.assertEqual(env["XDG_CONFIG_HOME"], str(Path(root) / "config"))
            self.assertEqual(env["XDG_DATA_HOME"], str(Path(root) / "data"))

    def test_refuses_the_real_user_config_or_data_directories(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            for inside in (".config/demo", ".local/share/demo", ".local/share"):
                with self.subTest(inside=inside), self.assertRaises(ValueError):
                    isolated_environment(Path(home) / inside, home=Path(home))

    def test_refuses_a_workspace_with_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
            (Path(root) / "conversations.sqlite3").write_text("x")
            with self.assertRaises(ValueError):
                isolated_environment(Path(root), home=Path(home))


class PrepareWorkspaceTest(unittest.TestCase):
    def test_saves_verified_provider_and_installs_the_named_release(self) -> None:
        calls = []
        preloaded = []

        def installer(data_dir, **kwargs):
            calls.append((data_dir, kwargs))
            return {"knowledge_release_id": kwargs["expected_release_id"]}

        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
            env = isolated_environment(Path(root), home=Path(home))
            prepare_workspace(
                env,
                model="gemma4:12b",
                release_id="kr-2026-09-05.1",
                tester=passing_tester,
                installer=installer,
                preload=preloaded.append,
            )
            self.assertEqual(preloaded, ["gemma4:12b"])
            config = json.loads(
                (Path(root) / "config/danish-immigration-rag/provider-config.json").read_text()
            )
            self.assertEqual(config["model"], "gemma4:12b")
            self.assertEqual(config["endpoint"], "http://127.0.0.1:11434")
            data_dir, kwargs = calls[0]
            self.assertEqual(data_dir, Path(root) / "data/danish-immigration-rag")
            self.assertEqual(kwargs["expected_release_id"], "kr-2026-09-05.1")
            self.assertEqual(Path(kwargs["release_dir"]).name, "kr-2026-09-05.1")
            self.assertEqual(Path(kwargs["trust_root_path"]).name, "project-release-key-v2.json")

    def test_failed_capability_test_stops_before_saving_or_installing(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
            env = isolated_environment(Path(root), home=Path(home))
            with self.assertRaises(RuntimeError):
                prepare_workspace(
                    env,
                    model="gemma4:12b",
                    release_id="kr-2026-09-05.1",
                    tester=failing_tester,
                    installer=lambda *a, **k: self.fail("must not install"),
                    preload=lambda model: None,
                    attempts=2,
                )
            self.assertFalse((Path(root) / "config").exists())


if __name__ == "__main__":
    unittest.main()
