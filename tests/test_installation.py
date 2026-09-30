"""Exercise removal helpers only; never run provisioning or service commands."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("bash"), "Bash is required for installer helpers")
class UninstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_helper(self, install, config, command):
        environment = dict(os.environ, PYTHON=sys.executable,
                           INSTALL_DIR_PATH=str(install), CONFIG_FILE_DIR_PATH=str(config))
        result = subprocess.run(
            ["bash", "-c", 'set -e; source "$1"; ' + command,
             "uninstall-test", str(ROOT / "install_common.sh")],
            env=environment, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_default_removal_preserves_data_and_purge_leaves_unrelated_files(self):
        for location in ("custom", "same", "nested"):
            with self.subTest(location=location):
                stage = self.root / location
                install = stage / "install with spaces"
                config = (install if location == "same" else
                          install / ".env" / "user-data" if location == "nested" else
                          stage / "config $ with spaces%")
                protected = {
                    config / "dialpad_dev": b"[main]\nlayout = custom\n",
                    config / ".dialpad_dev.lock": b"",
                    config / "layouts" / "custom.json": b"user layout bytes",
                    config / ".layout-state" / "last-successful.json": b"recovery bytes",
                    install / "layouts" / "proartp16.py": b"customized bundled layout",
                }
                unrelated = {install / "unrelated.txt": b"install user data",
                             config / "unrelated.txt": b"config user data",
                             install / "locales" / "custom.json": b"user-owned translation"}
                for path, data in (protected | unrelated).items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                programs = [install / name for name in ("dialpad.py", "dialpad_overlay.py",
                            "dialpad_help.py", "dialpad_i18n.py", "locales/help.zh_CN.json",
                            "locales/manager.zh_TW.json", "locales/en.json",
                            "locales/en_US.json", "locales/zh_CN.json", "locales/zh_TW.json")]
                for program in programs:
                    program.write_text("installed program")
                self.run_helper(install, config, "dialpad_remove_program_files 0")
                for program in programs:
                    self.assertFalse(program.exists())
                for path, data in (protected | unrelated).items():
                    self.assertEqual(path.read_bytes(), data)
                self.run_helper(install, config, "dialpad_remove_program_files 1")
                for path in protected:
                    self.assertFalse(path.exists(), str(path))
                for path, data in unrelated.items():
                    self.assertEqual(path.read_bytes(), data)

    def test_removal_only_deletes_unchanged_owned_launchers(self):
        install = self.root / "install"
        install.mkdir()
        owned = self.root / "owned-launcher"
        changed = self.root / "modified-launcher"
        unowned = self.root / "unowned-launcher"
        entries = {}
        for path in (owned, changed, unowned):
            path.write_bytes(b"original launcher")
            if path != unowned:
                entries[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        (install / ".installation.json").write_text(json.dumps({"launchers": entries}))
        changed.write_bytes(b"user replacement")
        self.run_helper(install, self.root / "config", "dialpad_remove_launchers")
        self.assertFalse(owned.exists())
        self.assertEqual(changed.read_bytes(), b"user replacement")
        self.assertEqual(unowned.read_bytes(), b"original launcher")


if __name__ == "__main__":
    unittest.main()
