"""Feb 22 - Mar 8, department drive stand ONLY: drive an M3 screw and log the test.

The servo spins like a wheel (wheel mode) at your approach speed, with its torque capped
at your twist limit. The test stops by itself when the screw stops turning, when the time
limit runs out, if the servo gets hotter than 60 deg C, or when you press Ctrl+C. Then the
servo goes back to normal position mode with torque off.

Every reading goes into a CSV file in logs/. At the end the script reports one outcome:
  seated            stopped turning at (or near) your twist limit
  stalled           stopped turning well below your limit (jammed? cross-threaded?)
  time_out          still turning when the time limit ran out
  over_temperature  stopped because the servo got too hot
  protection_tripped  the servo's own overload/overcurrent/overheat protection switched on

Your twist limit can never go above the instructor's provisional cap until your servo has a
load calibration (scripts/calibrate_load.py). After that, the course ceiling applies.

Examples:
    python scripts/screw_test.py --drive-stand --port /dev/tty.usbmodem58760431551 --limit-raw 120
    python scripts/screw_test.py --drive-stand --port /dev/tty.usbmodem58760431551 --limit-nm 0.25 --speed 800
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # lets this script find course_lib

from course_lib.calibration import calibration_path, load_calibration  # noqa: E402
from course_lib.cli import add_common_args, connect_single_servo, run  # noqa: E402
from course_lib.config import load_config  # noqa: E402
from course_lib.csvlog import CsvLog  # noqa: E402
from course_lib.safety import (  # noqa: E402
    SafetyError, TorqueGuard, apply_protection_settings, check_requested, check_temperature, check_voltage,
    confirm_go, enable_torque_checked, set_mode_verified, write_verified,
)
from course_lib.screw import LOG_FIELDS, DriveSettings, choose_twist_limit, drive_until_stop  # noqa: E402

OUTCOME_TEXT = {
    "seated": "SEATED: the screw stopped turning at your twist limit.",
    "stalled": "STALLED: the screw stopped turning well below your limit. Check for jamming or cross-threading.",
    "time_out": "TIME OUT: still turning when the time limit ran out.",
    "over_temperature": "OVER TEMPERATURE: stopped because the servo got too hot. Let it cool 10 minutes.",
    "protection_tripped": "PROTECTION TRIPPED: the servo's own overload/overcurrent/overheat alarm came on "
                          "(see the status column in the log). Let it cool 10 minutes and tell the TA.",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drive-stand", action="store_true",
                        help="Required. Confirms the servo is clamped in a department drive stand. "
                             "Never drive screws anywhere else.")
    add_common_args(parser)
    parser.add_argument("--servo", default="gripper",
                        help="Servo label used for its load calibration file (default: gripper).")
    limit = parser.add_mutually_exclusive_group()
    limit.add_argument("--limit-nm", type=float,
                       help="Twist limit in N·m. Needs your load calibration file.")
    limit.add_argument("--limit-raw", type=int,
                       help="Twist limit in torque units (0-1000 = 0-100%% of stall torque). "
                            "Default: the provisional cap in the course config.")
    parser.add_argument("--speed", type=int,
                        help="Approach speed in steps/s (4096 steps = one turn). Default: from the course config.")
    parser.add_argument("--time-limit", type=float,
                        help="Seconds before the test stops on its own. Default: from the course config.")
    parser.add_argument("--reverse", action="store_true",
                        help="Turn the other way (use this if the screw backs out instead of going in).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.drive_stand:
        print("Refusing to run: screw tests happen only on a department drive stand.\n"
              "If the servo is clamped in a drive stand, run again with --drive-stand.")
        return 2
    return run(lambda: screw_test(args))


def screw_test(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    limits, ceilings = cfg.limits, cfg.ceilings
    cal = load_calibration(args.servo)
    if cal is None and args.limit_nm is not None:
        raise SafetyError(f"No load calibration at {calibration_path(args.servo)}. Use the same --servo label "
                          "you used with calibrate_load.py, or give the limit with --limit-raw.")
    limit_raw = choose_twist_limit(args.limit_nm, args.limit_raw, cal, limits, ceilings)
    if args.speed is not None and args.speed < 1:
        raise SafetyError("--speed must be at least 1. To turn the other way, use --reverse.")
    speed = int(check_requested("--speed", limits.wheel_speed if args.speed is None else args.speed,
                                ceilings.wheel_speed))
    time_limit = check_requested("--time-limit", limits.drive_time_s if args.time_limit is None else args.time_limit,
                                 ceilings.drive_time_s)
    settings = DriveSettings(
        limit_raw=limit_raw,
        speed=-speed if args.reverse else speed,
        time_limit_s=time_limit,
        max_temperature_c=limits.max_temperature_c,
        stall_velocity=cfg.screw.stall_velocity,
        stall_time_s=cfg.screw.stall_time_s,
        grace_s=cfg.screw.grace_s,
        seated_fraction=cfg.screw.seated_fraction,
        sample_period_s=cfg.screw.sample_period_s,
    )

    bus, motor = connect_single_servo(args.port, args.id, cfg)
    with TorqueGuard(bus, [motor], restore_position_mode=True):
        applied = apply_protection_settings(bus, motor, limits)
        temperature = check_temperature(bus, motor, limits.max_temperature_c)
        volts = check_voltage(bus, motor, cfg.voltage_min, cfg.voltage_max)
        set_mode_verified(bus, motor, "velocity")
        write_verified(bus, "Acceleration", motor, limits.acceleration)
        write_verified(bus, "Goal_Velocity", motor, 0)  # no motion when torque comes on

        limit_text = f"{limit_raw} units" + (f" (about {cal.to_nm(limit_raw):.3f} N·m)" if cal else "")
        print("Read back OK: " + ", ".join(f"{register}={value}" for register, value in applied.items()))
        print(f"Supply {volts:.1f} V, servo {temperature} deg C. "
              + (f"Load calibration: {calibration_path(args.servo)}" if cal else "No load calibration found: "
                 "the provisional cap applies."))
        print(f"Twist limit {limit_text}, speed {settings.speed} steps/s, time limit {time_limit} s.")
        if not confirm_go("\nServo clamped in the drive stand, block clamped, screw started by hand, "
                          "hands clear, STOP switch in reach."):
            print("Cancelled. Torque stayed off.")
            return 1
        enable_torque_checked(bus, motor, limit_raw)
        with CsvLog(f"screw_test_{args.servo}", LOG_FIELDS) as log:
            print("Driving... (Ctrl+C stops)")
            result = drive_until_stop(bus, motor, settings, log.write)

    print(f"\n{OUTCOME_TEXT[result.outcome]}")
    peak = f"{result.peak_load} units" + (f" (about {cal.to_nm(result.peak_load):.3f} N·m)" if cal else "")
    print(f"Time {result.elapsed_s:.2f} s, peak load {peak}.")
    print("Torque is off and the servo is back in position mode.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
