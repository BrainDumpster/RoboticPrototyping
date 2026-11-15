"""Pieces every script shares: common arguments, connecting to one servo, friendly errors."""

import argparse
from collections.abc import Callable
from pathlib import Path

from .bus import Bus, BusSetupError, make_bus, open_bus, resolve_port
from .config import DEFAULT_CONFIG_PATH, ConfigError, CourseConfig
from .safety import SafetyError

PORT_HELP = (
    "USB port of your servo adapter, e.g. /dev/tty.usbmodem58760431551 (Mac) or COM5 (Windows). "
    "Find it with `lerobot-find-port`. If left out, the port in the course config is used."
)


def add_common_args(parser: argparse.ArgumentParser, single_servo: bool = True) -> None:
    parser.add_argument("--port", help=PORT_HELP)
    if single_servo:
        parser.add_argument(
            "--id", type=int, default=None,
            help="ID of the servo. A new servo is ID 1 (the default). "
                 "After `lerobot-setup-motors`, your gripper servo is ID 6.",
        )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help="Course config file. Leave this alone unless the TA tells you otherwise.",
    )


def connect_single_servo(port_arg: str | None, servo_id: int | None, cfg: CourseConfig) -> tuple[Bus, str]:
    """Open the bus with just the one Feb 22 servo on it. Returns (bus, motor name)."""
    servo_id = cfg.single_servo_id if servo_id is None else servo_id
    if not 1 <= servo_id <= 253:
        raise BusSetupError("--id must be a whole number from 1 to 253.")
    name = cfg.single_servo_name
    bus = make_bus(resolve_port(port_arg, cfg), {name: servo_id}, cfg.model)
    other = 6 if servo_id == 1 else 1
    open_bus(bus, missing_hint=(
        f"No STS3215 answered at ID {servo_id}. Is it the only servo on the bus, with power on? "
        f"If this servo has been through `lerobot-setup-motors`, try --id {other}."
    ))
    return bus, name


def run(body: Callable[[], int]) -> int:
    """Run a script body and turn the expected failures into a short message and exit code."""
    try:
        return body()
    except KeyboardInterrupt:
        print("\nStopped with Ctrl+C. If a WARNING appeared above, press STOP.")
        return 130
    except (ConfigError, BusSetupError, SafetyError, ValueError) as error:
        print(f"\nSTOPPED: {error}")
        return 1
    except ConnectionError as error:  # LeRobot raises this when a servo stops answering
        print(f"\nSTOPPED: lost contact with the servo ({error}).\n"
              "This is expected if you pressed STOP, switched the supply off or pulled the USB cable: "
              "leave the power off. If you did none of those, check the USB cable, the supply and --port, "
              "and tell the TA.")
        return 1
    except OSError as error:  # e.g. the logs/ or calibration/ folder cannot be written
        print(f"\nSTOPPED: a file or device error: {error}")
        return 1
