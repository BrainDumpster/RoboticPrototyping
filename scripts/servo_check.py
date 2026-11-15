"""Feb 22, in class: read one servo's sensors and settings with torque OFF.

Nothing moves. The script switches torque off, so you can turn the servo horn by hand
and watch the position change. Press Ctrl+C to stop early.

Before you run it: the servo is alone on the bus, the supply is on, and the STOP switch
is within reach.

Example:
    python scripts/servo_check.py --port /dev/tty.usbmodem58760431551
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # lets this script find course_lib

from course_lib.bus import read_raw  # noqa: E402
from course_lib.cli import add_common_args, connect_single_servo, run  # noqa: E402
from course_lib.config import load_config  # noqa: E402
from course_lib.safety import TorqueGuard, describe_status  # noqa: E402
from course_lib.screw import read_sample  # noqa: E402

SETTINGS = (
    "ID", "Model_Number", "Operating_Mode", "Min_Position_Limit", "Max_Position_Limit",
    "Max_Torque_Limit", "Torque_Limit", "Protection_Current", "Overload_Torque",
    "Protective_Torque", "Protection_Time", "Unloading_Condition", "Max_Temperature_Limit",
    "Min_Voltage_Limit", "Max_Voltage_Limit", "Status",
)
ROW = "{:>6} {:>8} {:>8} {:>6} {:>7} {:>6} {:>6}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    parser.add_argument("--seconds", type=float, default=30, help="How long to show live readings (default 30).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(lambda: check(args))


def check(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    bus, motor = connect_single_servo(args.port, args.id, cfg)
    with TorqueGuard(bus, [motor]):
        # The only writes (torque off, which also unlocks EEPROM): nothing else is written.
        # Guarantees the horn turns freely.
        bus.disable_torque(motor)
        print("Servo settings (raw register values):")
        for register in SETTINGS:
            print(f"  {register:<22} {read_raw(bus, register, motor)}")
        if read_raw(bus, "Operating_Mode", motor) != 0:
            print("  NOTE: this servo is not in position mode (0). Tell the TA before any powered script.")
        status = read_raw(bus, "Status", motor)
        if status:
            print(f"  NOTE: the servo reports an alarm (Status {status}: {describe_status(status)}). Tell the TA.")

        print("\nTorque is OFF. Turn the horn slowly by hand. Ctrl+C stops.\n")
        print(ROW.format("time", "position", "velocity", "load", "current", "temp C", "volts"))
        positions = []
        start = time.monotonic()
        while time.monotonic() - start < args.seconds:
            sample = read_sample(bus, motor)
            volts = read_raw(bus, "Present_Voltage", motor) / 10
            positions.append(sample["position"])
            print(ROW.format(f"{time.monotonic() - start:.1f}", sample["position"], sample["velocity"],
                             sample["load"], sample["current"], sample["temperature_c"], f"{volts:.1f}"))
            if sample["status"]:
                print(f"ALARM: Status {sample['status']} ({describe_status(sample['status'])}). Tell the TA.")
            if sample["temperature_c"] > cfg.limits.max_temperature_c:
                print(f"WARNING: above {cfg.limits.max_temperature_c} deg C. Switch the supply off and tell the TA.")
            time.sleep(0.25)
    if positions:
        print(f"\nPosition range seen: {min(positions)} to {max(positions)} (4096 steps = one turn).")
    print("Servo check finished. Torque is off.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
