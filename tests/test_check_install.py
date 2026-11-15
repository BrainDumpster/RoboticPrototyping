import contextlib
import importlib.metadata
import importlib.util
import io
import subprocess
import sys
import types
import unittest
from pathlib import Path

from helpers import sample_config

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_install", REPO / "scripts" / "check_install.py")
check_install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_install)

FAKE_YAML = types.SimpleNamespace(safe_load=lambda text: sample_config())
FAKE_MOTORS = types.SimpleNamespace(
    Motor=lambda id_, model, norm_mode: (id_, model),
    MotorNormMode=types.SimpleNamespace(RANGE_M100_100="range"),
    SerialMotorsBus=lambda port, motors: object(),
)
FAKE_RUSTYPOT = types.SimpleNamespace(find_model=lambda name: (777, object()) if name == "sts3215" else None)


def broken_bus(port, motors):
    # What LeRobot raises from the constructor when rustypot's compiled part is missing.
    raise ImportError("'rustypot' is required but not installed.")


def fake_import(missing: set[str], overrides: dict | None = None):
    modules = {"yaml": FAKE_YAML, "serial": types.SimpleNamespace(), "lerobot.motors": FAKE_MOTORS,
               "rustypot": FAKE_RUSTYPOT, **(overrides or {})}

    def import_module(name):
        if name in missing or name.split(".")[0] in missing:
            raise ModuleNotFoundError(f"No module named '{name}'")
        return modules[name]
    return import_module


def fake_versions(missing: set[str]):
    def version_of(name):
        if name in missing:
            raise importlib.metadata.PackageNotFoundError(name)
        return check_install.PINNED[name]
    return version_of


def run_check(missing=frozenset(), python=(3, 12), overrides=None):
    problems = check_install.find_problems(fake_import(set(missing), overrides), fake_versions(set(missing)), python)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = check_install.report(problems)
    return code, out.getvalue()


class CheckInstallTest(unittest.TestCase):
    def test_all_present_prints_install_ok(self):
        code, out = run_check()
        self.assertEqual(code, 0)
        self.assertEqual(out, "install OK\n")

    def test_lerobot_missing_fails(self):
        code, out = run_check(missing={"lerobot"})
        self.assertNotEqual(code, 0)
        self.assertNotIn("install OK", out)
        self.assertIn("lerobot is not installed", out)
        self.assertIn("pip install -r requirements.txt", out)

    def test_wrong_python_fails(self):
        code, out = run_check(python=(3, 11))
        self.assertEqual(code, 1)
        self.assertIn("Python 3.11", out)

    def test_wrong_python_gives_only_the_python_advice(self):
        # On 3.11 "pip install -r requirements.txt" would fail too, so it must not be suggested.
        code, out = run_check(missing={"lerobot"}, python=(3, 11))
        self.assertIn("1 problem", out)
        self.assertNotIn("requirements.txt", out)

    def test_bus_that_cannot_be_built_fails(self):
        """A broken rustypot passes the import but would crash the first servo script on Feb 22."""
        motors = types.SimpleNamespace(**{**vars(FAKE_MOTORS), "SerialMotorsBus": broken_bus})
        code, out = run_check(overrides={"lerobot.motors": motors})
        self.assertEqual(code, 1)
        self.assertIn("servo bus could not be set up", out)

    def test_driver_without_sts3215_fails(self):
        code, out = run_check(overrides={"rustypot": types.SimpleNamespace(find_model=lambda name: None)})
        self.assertEqual(code, 1)
        self.assertIn("STS3215", out)

    def test_wrong_version_fails(self):
        problems = check_install.find_problems(fake_import(set()), lambda name: "0.6.1", (3, 12))
        self.assertTrue(any("0.6.1 is installed" in p for p in problems))

    @unittest.skipIf(importlib.util.find_spec("lerobot"), "LeRobot is installed in this Python")
    def test_real_run_without_lerobot_exits_nonzero(self):
        result = subprocess.run([sys.executable, str(REPO / "scripts" / "check_install.py")],
                                capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("install OK", result.stdout)
        self.assertIn("lerobot", result.stdout)


if __name__ == "__main__":
    unittest.main()
