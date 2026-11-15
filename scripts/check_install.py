"""Check that your course Python environment is ready. No servo or adapter needed.

Run from the repository folder:
    python scripts/check_install.py

It prints "install OK" when everything is in place. Otherwise it lists each problem
and what to do about it.
"""

import argparse
import importlib
import importlib.metadata
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # lets this script find course_lib

from course_lib.config import DEFAULT_CONFIG_PATH, parse_config  # noqa: E402

PYTHON_OK = ((3, 12), (3, 13))
STS3215_MODEL_NUMBER = 777
# Must match requirements.txt.
PINNED = {"lerobot": "0.7.0", "rustypot": "1.13.1"}
REINSTALL = "Run: pip install -r requirements.txt"


def find_problems(
    import_module: Callable = importlib.import_module,
    version_of: Callable[[str], str] = importlib.metadata.version,
    python_version: tuple[int, int] = sys.version_info[:2],
) -> list[str]:
    problems = []
    if python_version not in PYTHON_OK:
        problems.append(
            f"This is Python {python_version[0]}.{python_version[1]}; the course needs Python 3.12. "
            "Activate the course environment from the Week 6 setup video, then run this again."
        )
        return problems  # every other check would fail too, with advice that does not help

    yaml_module = _import(import_module, "yaml", "PyYAML is not installed.", problems)
    _import(import_module, "serial", "pyserial (talks to the USB adapter) is not installed.", problems)

    for package, wanted in PINNED.items():
        try:
            found = version_of(package)
        except importlib.metadata.PackageNotFoundError:
            problems.append(f"{package} is not installed. {REINSTALL}")
            continue
        if found != wanted:
            problems.append(f"{package} {found} is installed but the course needs {wanted}. {REINSTALL}")

    motors = _import(import_module, "lerobot.motors", "LeRobot's motor code would not load.", problems)
    if motors is not None:
        _check_servo_driver(import_module, motors, problems)

    if yaml_module is not None:
        try:
            parse_config(yaml_module.safe_load(DEFAULT_CONFIG_PATH.read_text()))
        except Exception as error:  # report any config problem; never crash the checker
            problems.append(f"The course config {DEFAULT_CONFIG_PATH} has a problem: {error}")
    return problems


def _check_servo_driver(import_module: Callable, motors, problems: list[str]) -> None:
    """Run the code path the servo scripts use on Feb 22, without opening any port."""
    try:
        rustypot = import_module("rustypot")
        found = rustypot.find_model("sts3215")
        if found is None or found[0] != STS3215_MODEL_NUMBER:
            problems.append(f"The servo driver (rustypot) does not know the STS3215 servo. {REINSTALL}")
            return
        motor = motors.Motor(1, "sts3215", motors.MotorNormMode.RANGE_M100_100)
        motors.SerialMotorsBus(port="unused", motors={"check": motor})  # builds it; never connects
    except Exception as error:  # a broken install can raise almost anything here
        problems.append(f"LeRobot's servo bus could not be set up ({type(error).__name__}: {error}) {REINSTALL}")


def _import(import_module: Callable, name: str, message: str, problems: list[str]):
    try:
        return import_module(name)
    except Exception as error:  # a broken install can raise more than ImportError
        problems.append(f"{message} ({type(error).__name__}: {error}) {REINSTALL}")
        return None


def report(problems: list[str]) -> int:
    if not problems:
        print("install OK")
        return 0
    print(f"The install check found {len(problems)} problem(s):\n")
    for number, problem in enumerate(problems, start=1):
        print(f"{number}. {problem}\n")
    print("Fix these, then run `python scripts/check_install.py` again. Still stuck? Send the TA this whole message.")
    return 1


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    return report(find_problems())


if __name__ == "__main__":
    sys.exit(main())
