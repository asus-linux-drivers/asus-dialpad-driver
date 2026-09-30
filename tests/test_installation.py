"""Exercise removal helpers and the uninstaller flow with sandboxed commands only."""

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


SUDO_SHIM = """#!/usr/bin/env bash
# Sandboxed sudo: records every privileged call and removes only files below the
# test root, so the tests can never touch a production path or run a command.
printf 'sudo %s\\n' "$*" >> "$DIALPAD_TEST_SHIM_LOG"
if [[ "$1" != rm ]]; then
    exit 0
fi
shift
PATHS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -f|--force) shift ;;
        --) shift; break ;;
        *) PATHS+=("$1"); shift ;;
    esac
done
PATHS+=("$@")
for ENTRY in "${PATHS[@]}"; do
    if [[ "$ENTRY" == "$DIALPAD_TEST_SANDBOX"/* ]]; then
        rm -f -- "$ENTRY"
    fi
done
exit 0
"""


def recorder_shim(command):
    return ("#!/usr/bin/env bash\n"
            f"printf '{command} %s\\n' \"$*\" >> \"$DIALPAD_TEST_SHIM_LOG\"\n")


@unittest.skipUnless(shutil.which("bash"), "Bash is required for installer helpers")
class UninstallScriptTests(unittest.TestCase):
    """Run the real uninstaller against sandboxed paths and command shims.

    Every service, udev and privileged command is intercepted, so no test starts a
    service, runs sudo, reloads udev or reads from a production path.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.install = self.root / "install"
        self.config = self.root / "config"
        self.service = self.root / "service"
        self.udev = self.root / "udev"
        self.rules = self.udev / "rules.d"
        self.modules_load = self.root / "modules-load.d"
        self.logs = self.root / "logs"
        self.shims = self.root / "shims"
        self.shim_log = self.root / "commands.log"
        for directory in (self.install, self.config, self.service, self.rules,
                          self.modules_load, self.shims):
            directory.mkdir(parents=True, exist_ok=True)
        self.add_shim("sudo", SUDO_SHIM)
        self.add_shim("systemctl", recorder_shim("systemctl"))
        self.add_shim("udevadm", recorder_shim("udevadm"))

    def add_shim(self, name, script):
        path = self.shims / name
        path.write_text(script)
        path.chmod(0o755)

    def write(self, path, data="artifact\n"):
        path.write_text(data)
        return path

    def run_uninstaller(self, responses=("\n", "yes\n")):
        """Answer the preserve/purge prompt with Enter and accept a reboot prompt."""
        environment = dict(
            os.environ,
            PATH=f"{self.shims}:{os.environ['PATH']}",
            PYTHON=sys.executable,
            HOME=str(self.root / "home"),
            XDG_STATE_HOME=str(self.root / "state"),
            INSTALL_DIR_PATH=str(self.install),
            CONFIG_FILE_DIR_PATH=str(self.config),
            SERVICE_INSTALL_DIR_PATH=str(self.service),
            INSTALL_UDEV_DIR_PATH=str(self.udev),
            MODULES_LOAD_DIR_PATH=str(self.modules_load),
            LOGS_DIR_PATH=str(self.logs),
            DIALPAD_TEST_SANDBOX=str(self.root),
            DIALPAD_TEST_SHIM_LOG=str(self.shim_log),
        )
        result = subprocess.run(["bash", str(ROOT / "uninstall.sh")], env=environment,
                                input="".join(responses), text=True, capture_output=True,
                                cwd=str(self.root))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        commands = self.shim_log.read_text() if self.shim_log.exists() else ""
        return result.stdout + result.stderr, commands

    def assert_no_reboot_question(self, commands):
        self.assertNotIn("/sbin/reboot", commands)

    def test_partial_install_without_driver_file_removes_owned_artifacts(self):
        owned_services = [self.write(self.service / name) for name in
                          ("asus_dialpad_driver@.service", "asus_dialpad_driver_ui@.service")]
        unrelated_service = self.write(self.service / "asus_numberpad_driver@.service", "other driver")
        owned_rules = [self.write(self.rules / name) for name in
                       ("99-asus-dialpad-driver-uinput.rules", "99-asus-dialpad-driver-i2c-dev.rules")]
        unrelated_rule = self.write(self.rules / "99-asusd.rules", "other rule")
        owned_confs = [self.write(self.modules_load / name) for name in
                       ("uinput-asus-dialpad-driver.conf", "i2c-dev-asus-dialpad-driver.conf")]
        unrelated_install_file = self.write(self.install / "notes.txt", "keep me")
        output, commands = self.run_uninstaller()
        for path in owned_services + owned_rules + owned_confs:
            self.assertFalse(path.exists(), f"{path} was left behind: {output}")
        self.assertEqual(unrelated_service.read_text(), "other driver")
        self.assertEqual(unrelated_rule.read_text(), "other rule")
        self.assertEqual(unrelated_install_file.read_text(), "keep me")
        self.assertIn("systemctl --user disable --now asus_dialpad_driver@", commands)
        self.assertIn("systemctl --user daemon-reload", commands)
        self.assertNotIn("asus_numberpad_driver", commands)
        for path in owned_rules + owned_confs:
            self.assertIn(str(path), commands)
        self.assertIn("udevadm control --reload-rules", commands)
        self.assert_no_reboot_question(commands)

    def test_service_template_only_install_skips_privileged_cleanup(self):
        service = self.write(self.service / "asus_dialpad_driver@.service")
        unrelated_rule = self.write(self.rules / "99-asusd.rules", "other rule")
        output, commands = self.run_uninstaller()
        self.assertFalse(service.exists(), output)
        self.assertIn("systemctl --user disable --now asus_dialpad_driver@", commands)
        self.assertNotIn("sudo", commands)
        self.assertEqual(unrelated_rule.read_text(), "other rule")
        self.assert_no_reboot_question(commands)

    def test_udev_rule_only_install_skips_service_cleanup(self):
        rule = self.write(self.rules / "99-asus-dialpad-driver-uinput.rules")
        unrelated_rule = self.write(self.rules / "99-asusd.rules", "other rule")
        output, commands = self.run_uninstaller()
        self.assertFalse(rule.exists(), output)
        self.assertIn("udevadm control --reload-rules", commands)
        self.assertNotIn("systemctl", commands)
        self.assertEqual(unrelated_rule.read_text(), "other rule")
        self.assert_no_reboot_question(commands)

    def test_modules_load_conf_only_install_skips_service_cleanup(self):
        conf = self.write(self.modules_load / "uinput-asus-dialpad-driver.conf")
        output, commands = self.run_uninstaller()
        self.assertFalse(conf.exists(), output)
        self.assertIn("udevadm control --reload-rules", commands)
        self.assertNotIn("systemctl", commands)
        self.assert_no_reboot_question(commands)

    def test_editor_only_install_skips_cleanup_and_reboot_question(self):
        unrelated = {
            self.service / "asus_numberpad_driver@.service": "other driver",
            self.rules / "99-asusd.rules": "other rule",
            self.modules_load / "i2c-dev.conf": "other module",
            self.install / "notes.txt": "keep me",
            self.config / "dialpad_dev": "user configuration",
        }
        for path, data in unrelated.items():
            self.write(path, data)
        output, commands = self.run_uninstaller()
        for path, data in unrelated.items():
            self.assertEqual(path.read_text(), data, str(path))
        self.assertEqual(commands, "", "editor-only uninstallation ran cleanup commands")
        self.assert_no_reboot_question(commands)
        self.assertIn("Uninstallation finished successfully.", output)

    def test_installed_driver_still_removes_program_files_and_asks_for_reboot(self):
        driver = self.write(self.install / "dialpad.py", "driver program")
        output, commands = self.run_uninstaller()
        self.assertFalse(driver.exists(), output)
        self.assertIn("/sbin/reboot", commands)


if __name__ == "__main__":
    unittest.main()
