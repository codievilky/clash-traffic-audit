"""Installer filesystem behavior; launchctl is always mocked."""
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import install as installer


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.source = root / "source"
        self.source.mkdir()
        for n in installer.FILES:
            (self.source / n).write_text("new program")
        self.base = root / "Application Support/audit"
        self.agent = root / "LaunchAgents/test.plist"
        self.job = installer.build_job(self.base, Path(sys.executable), 0, root / "mihomo.sock")

    def tearDown(self):
        self.tmp.cleanup()

    def existing(self):
        self.base.mkdir(parents=True)
        (self.base / "data").mkdir()
        (self.base / "data/audit.sqlite3").write_bytes(b"keep history")
        for n in installer.FILES:
            (self.base / n).write_text("old program")
        self.agent.parent.mkdir()
        self.agent.write_bytes(plistlib.dumps(self.job))

    def test_plist_arguments_preserve_spaces(self):
        loaded = plistlib.loads(plistlib.dumps(self.job))
        self.assertEqual(loaded["ProgramArguments"][3], str(self.base))
        self.assertTrue(loaded["RunAtLoad"])
        self.assertTrue(loaded["KeepAlive"])
        self.assertEqual(loaded["ThrottleInterval"], 10)

    @patch.object(installer, "run", return_value=SimpleNamespace(returncode=1))
    def test_fresh_install_has_no_proxy_configuration_side_effects(self, mocked):
        installer.install(self.base, self.agent, self.job, self.source)
        self.assertEqual(installer.installed_base(self.agent), self.base)
        self.assertEqual((self.base / "clash_audit.py").read_text(), "new program")
        mocked.assert_any_call("bootstrap", "gui/%d" % installer.os.getuid(), str(self.agent))

    def test_failed_update_restores_previous_program_and_data(self):
        self.existing()
        bootstrap_calls = []

        def fake(*args, **kwargs):
            if args[0] == "bootstrap":
                bootstrap_calls.append(args)
                if len(bootstrap_calls) == 1:
                    raise subprocess.CalledProcessError(1, args, stderr="simulated failure")
            return SimpleNamespace(returncode=0)

        with patch.object(installer, "run", side_effect=fake):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.install(self.base, self.agent, self.job, self.source)
        self.assertEqual((self.base / "clash_audit.py").read_text(), "old program")
        self.assertEqual((self.base / "data/audit.sqlite3").read_bytes(), b"keep history")
        self.assertEqual(len(bootstrap_calls), 2)

    @patch.object(installer, "run", return_value=SimpleNamespace(returncode=0))
    def test_uninstall_preserves_history(self, mocked):
        self.existing()
        installer.uninstall(self.base, self.agent)
        self.assertFalse(self.agent.exists())
        self.assertFalse((self.base / "clash_audit.py").exists())
        self.assertEqual((self.base / "data/audit.sqlite3").read_bytes(), b"keep history")

    @patch.object(installer, "run")
    def test_unmanaged_files_are_not_overwritten(self, mocked):
        self.base.mkdir(parents=True)
        p = self.base / "clash_audit.py"
        p.write_text("unrelated file")
        with self.assertRaises(ValueError):
            installer.install(self.base, self.agent, self.job, self.source)
        self.assertEqual(p.read_text(), "unrelated file")
        mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
