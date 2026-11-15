"""Feb 22, in class: turn the servo's load reading into newton-metres (N·m).

Setup: the servo is clamped in the lever rig with the lever level, and nothing hangs on it.
The servo holds the lever still (torque capped at the course's provisional limit) while
you hang known masses at a known distance from the servo's axis. For each mass the script
averages the servo's Present_Load reading. At the end it fits a straight line and saves
calibration/load_calibration_<servo>.json, which screw_test.py uses for --limit-nm.

Tips: start with 0 g (the lever alone), then use at least two more masses. Measure the
radius from the centre of the servo shaft to where the mass hangs.

Torque stays on for at most the drive time limit in the course config. Every recorded mass
is saved straight away (calibration/load_points_<servo>.json), so nothing is lost if the
time runs out or the fit fails: use --more to add masses, or --refit to fit again.

Examples:
    python scripts/calibrate_load.py --port /dev/tty.usbmodem58760431551 --servo gripper
    python scripts/calibrate_load.py --port /dev/tty.usbmodem58760431551 --servo gripper --more
    python scripts/calibrate_load.py --servo gripper --refit      (no servo needed)
"""

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # lets this script find course_lib

from course_lib.bus import Bus, read_raw  # noqa: E402
from course_lib.calibration import (  # noqa: E402
    MIN_POINTS, CalPoint, average_load, calibration_path, check_hold_time, fit_calibration, load_points,
    points_path, save_calibration, save_points,
)
from course_lib.cli import add_common_args, connect_single_servo, run  # noqa: E402
from course_lib.config import CourseConfig, Limits, load_config  # noqa: E402
from course_lib.safety import (  # noqa: E402
    TorqueGuard, apply_protection_settings, check_temperature, check_voltage, confirm_go,
    enable_torque_checked, set_mode_verified, write_verified,
)

# A reading this close to the torque cap means the servo may be losing the fight with the mass.
NEAR_CAP_FRACTION = 0.9
MAX_SAMPLES = 200  # 200 readings is about 10 seconds per mass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser)
    parser.add_argument("--servo", default="gripper",
                        help="Label for this servo, used in the file name (default: gripper).")
    parser.add_argument("--samples", type=int, default=40,
                        help=f"Readings averaged for each mass, 1-{MAX_SAMPLES} (default 40, about 2 seconds).")
    saved = parser.add_mutually_exclusive_group()
    saved.add_argument("--more", action="store_true",
                       help="Keep the masses saved last time and add more.")
    saved.add_argument("--refit", action="store_true",
                       help="Fit again from the masses saved last time. No servo needed.")
    return parser


def ask_number(prompt: str, default: float | None = None) -> float | None:
    """Ask until the answer is an ordinary number. Pressing Enter returns `default`."""
    while True:
        text = input(prompt).strip()
        if not text:
            return default
        try:
            value = float(text)
        except ValueError:
            print("Please type a number, like 200 or 12.5.")
            continue
        if not math.isfinite(value):  # float() accepts "nan" and "inf"
            print("Please type an ordinary number, like 200 or 12.5.")
            continue
        return value


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(lambda: calibrate(args))


def calibrate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    calibration_path(args.servo)  # checks the label before anything is powered
    if args.refit:
        return fit_and_save(args.servo, load_points(args.servo), cfg.limits)
    if not 1 <= args.samples <= MAX_SAMPLES:
        raise ValueError(f"--samples must be from 1 to {MAX_SAMPLES}.")
    points = load_points(args.servo) if args.more else []
    if points:
        print(f"Continuing with {len(points)} saved mass(es) from {points_path(args.servo)}.")

    limits = cfg.limits
    bus, motor = connect_single_servo(args.port, args.id, cfg)
    with TorqueGuard(bus, [motor]):
        set_mode_verified(bus, motor, "position")
        apply_protection_settings(bus, motor, limits)
        check_temperature(bus, motor, limits.max_temperature_c)
        volts = check_voltage(bus, motor, cfg.voltage_min, cfg.voltage_max)
        print(f"Settings read back OK. Supply {volts:.1f} V. Torque cap {limits.torque_limit} (of 1000).")

        hold = read_raw(bus, "Present_Position", motor)
        write_verified(bus, "Acceleration", motor, limits.acceleration)
        write_verified(bus, "Goal_Velocity", motor, limits.move_speed)  # speed cap; 0 would mean "maximum"
        write_verified(bus, "Goal_Position", motor, hold)  # hold where it is now: no jump at switch-on
        if not confirm_go("\nLever level, no mass hanging, hands clear, STOP switch in reach. "
                          f"The servo will switch torque on and hold the lever still for up to "
                          f"{limits.drive_time_s:g} s in total."):
            print("Cancelled. Torque stayed off.")
            return 1
        enable_torque_checked(bus, motor, limits.torque_limit)
        deadline = time.monotonic() + limits.drive_time_s
        record_points(bus, motor, cfg, args, hold, deadline, points)
    # Torque is off from here on.
    return fit_and_save(args.servo, points, limits)


def record_points(bus: Bus, motor: str, cfg: CourseConfig, args: argparse.Namespace, hold: int,
                  deadline: float, points: list[CalPoint]) -> None:
    """Ask for masses until the student finishes. Every point is saved as soon as it is recorded."""
    limits = cfg.limits
    radius_mm = points[-1].radius_mm if points else None

    def still_safe() -> None:  # after every wait at the keyboard
        check_hold_time(deadline)
        check_temperature(bus, motor, limits.max_temperature_c)

    while True:
        mass_g = ask_number("\nMass in grams (press Enter with nothing typed to finish): ")
        still_safe()
        if mass_g is None:
            if len(points) >= MIN_POINTS:
                return
            print(f"Need at least {MIN_POINTS} recorded masses before finishing ({len(points)} so far).")
            continue
        default = f" [Enter = {radius_mm}]" if radius_mm else ""
        radius_mm = ask_number(f"Radius in mm from the shaft centre to the mass{default}: ", radius_mm)
        still_safe()
        if mass_g < 0 or not radius_mm or radius_mm <= 0:
            print("Mass must be 0 or more and the radius more than 0. Try again.")
            continue
        go = confirm_go("Hang the mass, let it stop swinging, and take your hands away.")
        still_safe()
        if not go:
            print("Skipped.")
            continue
        load = average_load(bus, motor, args.samples, limits.max_temperature_c, deadline=deadline)
        drift = abs(read_raw(bus, "Present_Position", motor) - hold)
        if load >= NEAR_CAP_FRACTION * limits.torque_limit or drift > cfg.position_tolerance:
            print(f"Not used: load {load:.0f} is near the cap or the lever sagged {drift} steps. "
                  "Use a lighter mass or a shorter radius.")
            continue
        point = CalPoint(mass_g, radius_mm, load)
        points.append(point)
        save_points(args.servo, points)
        print(f"Recorded: {point.torque_nm:.4f} N·m -> load {load:.1f}  ({len(points)} point(s) so far)")


def fit_and_save(servo: str, points: list[CalPoint], limits: Limits) -> int:
    try:
        cal = fit_calibration(servo, points)  # refuses too few points, a backwards fit or R² below 0.95
    except ValueError as error:
        raise ValueError(f"{error}\nYour masses are saved in {points_path(servo)}. Add more with --more, "
                         "or ask the TA to correct an entry there and run with --refit.") from error
    out_path = save_calibration(cal)
    print(f"\nFit: torque_Nm = {cal.slope_nm_per_unit:.6f} x load + {cal.intercept_nm:.4f}  (R² = {cal.r_squared:.3f})")
    print(f"The provisional cap of {limits.torque_limit} units is about {cal.to_nm(limits.torque_limit):.3f} N·m.")
    print(f"Saved {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
