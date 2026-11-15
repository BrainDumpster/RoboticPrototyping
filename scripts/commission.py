"""Mar 22-25, assembled arm: check all six servos, write the course limits, and do one test move.

Run this AFTER `lerobot-setup-motors` (which gives each servo its ID) and `lerobot-calibrate`,
because LeRobot rewrites some gripper settings when it connects. Steps:
  1. every motor ID answers as an STS3215;
  2. the course limits are written to every servo and read back;
  3. supply voltage and servo temperatures are checked;
  4. after you type GO, wrist_flex alone makes ONE slow, small move and comes back.
Every other joint stays torque-off the whole time.

The PASS/FAIL checklist at the end is what you show in your 60-second video.

Example:
    python scripts/commission.py --port /dev/tty.usbmodem58760431551
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # lets this script find course_lib

from course_lib.bus import Bus, make_bus, open_bus, read_raw, resolve_port  # noqa: E402
from course_lib.cli import add_common_args, run  # noqa: E402
from course_lib.config import CourseConfig, load_config  # noqa: E402
from course_lib.motion import move_to  # noqa: E402
from course_lib.safety import (  # noqa: E402
    SafetyError, TorqueGuard, apply_protection_settings, check_temperature, check_voltage, clamp,
    confirm_go, enable_torque_checked, set_mode_verified, write_verified,
)

MOVE_JOINT = "wrist_flex"
STEPS_PER_DEGREE = 4096 / 360


class Checklist:
    def __init__(self) -> None:
        self.items: list[tuple[bool, str]] = []

    def add(self, passed: bool, text: str) -> bool:
        self.items.append((passed, text))
        print(f"  {'PASS' if passed else 'FAIL'}  {text}")
        return passed

    @property
    def all_passed(self) -> bool:
        return all(passed for passed, _ in self.items)

    def print_final(self) -> None:
        print("\n========== COMMISSIONING CHECKLIST ==========")
        for passed, text in self.items:
            print(f"  {'PASS' if passed else 'FAIL'}  {text}")
        print(f"RESULT: {'PASS' if self.all_passed else 'FAIL'}")
        print("=============================================")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(parser, single_servo=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(lambda: commission(args))


def commission(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    bus = make_bus(resolve_port(args.port, cfg), cfg.motor_ids, cfg.model)
    open_bus(bus, handshake=False)  # we check each ID ourselves so the checklist names it
    checklist = Checklist()
    run_checklist(bus, cfg, checklist)
    checklist.print_final()
    return 0 if checklist.all_passed else 1


def run_checklist(bus: Bus, cfg: CourseConfig, checklist: Checklist) -> None:
    motors = cfg.motor_ids
    print("1. Motor IDs")
    answering: list[str] = []
    try:
        _check_ids(bus, cfg, checklist, answering)
    except BaseException:  # e.g. Ctrl+C mid-check: still switch off what answered
        _release(bus, answering)
        raise
    if not checklist.all_passed:
        # TorqueGuard would fail on the missing motor, so switch off the ones that answered here.
        print("Steps 2-4 not run: fix the IDs above first.")
        _release(bus, answering)
        return

    with TorqueGuard(bus, list(motors)):
        bus.disable_torque(num_retry=3)
        print("2. Limits written and read back")
        for name in motors:
            checklist.add(*_apply_limits(bus, name, cfg))
        print("3. Voltage and temperature")
        for name in motors:
            checklist.add(*_check_power(bus, name, cfg))
        if checklist.all_passed:
            print("4. Test move")
            try:
                result = _test_move(bus, cfg)
            except SafetyError as error:  # e.g. over temperature mid-move; the guard switches torque off
                result = (False, f"Test move stopped: {error}")
            checklist.add(*result)


def _check_ids(bus: Bus, cfg: CourseConfig, checklist: Checklist, answering: list[str]) -> None:
    """Ping every motor. Fills `answering` with the names that replied."""
    for name, motor_id in cfg.motor_ids.items():
        model = bus.ping(name, num_retry=2)
        if model is None:
            checklist.add(False, f"{name} (ID {motor_id}): no answer. Check its cable and that "
                                 f"lerobot-setup-motors gave it ID {motor_id}")
            continue
        answering.append(name)
        checklist.add(model == cfg.model_number, f"{name} (ID {motor_id}) answers as STS3215 (model {model})")


def _release(bus: Bus, names: list[str]) -> None:
    """Torque off on the motors that answered, then close the port, without touching missing ones."""
    try:
        if names:
            bus.disable_torque(names, num_retry=3)
    except Exception as error:
        print(f"WARNING: could not switch torque off ({error}). Press the STOP switch and tell the TA.")
    finally:
        bus.disconnect(disable_torque=False)


def _apply_limits(bus: Bus, name: str, cfg: CourseConfig) -> tuple[bool, str]:
    try:
        set_mode_verified(bus, name, "position")
        applied = apply_protection_settings(bus, name, cfg.limits)
        write_verified(bus, "Torque_Limit", name, cfg.limits.torque_limit)
    except SafetyError as error:
        return False, f"{name}: {error}"
    values = ", ".join(f"{register}={value}" for register, value in applied.items())
    return True, f"{name}: position mode, {values}, Torque_Limit={cfg.limits.torque_limit}"


def _check_power(bus: Bus, name: str, cfg: CourseConfig) -> tuple[bool, str]:
    try:
        volts = check_voltage(bus, name, cfg.voltage_min, cfg.voltage_max)
        temperature = check_temperature(bus, name, cfg.limits.max_temperature_c)
    except SafetyError as error:
        return False, f"{name}: {error}"
    return True, f"{name}: {volts:.1f} V, {temperature} deg C"


def _test_move(bus: Bus, cfg: CourseConfig) -> tuple[bool, str]:
    limits = cfg.limits
    start = read_raw(bus, "Present_Position", MOVE_JOINT)
    low = read_raw(bus, "Min_Position_Limit", MOVE_JOINT)
    high = read_raw(bus, "Max_Position_Limit", MOVE_JOINT)
    if high <= low:
        return False, f"{MOVE_JOINT} has no valid position limits. Run lerobot-calibrate first."
    if not low <= start <= high:
        # Clamping the target to the limits would turn this into a long move (or a jump at switch-on).
        return False, (f"{MOVE_JOINT} is at {start}, outside its limits {low}-{high}. "
                       "Move it to mid-range by hand and run again.")
    # Move toward whichever limit is further away, and never past a limit.
    direction = 1 if high - start >= start - low else -1
    target = clamp(start + direction * int(limits.move_steps), low, high)
    if abs(target - start) < 2 * cfg.position_tolerance:
        return False, f"{MOVE_JOINT} is too close to its limits for the test move. Move it to mid-range by hand."

    degrees = abs(target - start) / STEPS_PER_DEGREE
    speed = limits.move_speed / STEPS_PER_DEGREE
    write_verified(bus, "Acceleration", MOVE_JOINT, limits.acceleration)
    write_verified(bus, "Goal_Velocity", MOVE_JOINT, limits.move_speed)  # speed cap; 0 would mean "maximum"
    write_verified(bus, "Goal_Position", MOVE_JOINT, start)  # no jump when torque comes on
    if not confirm_go(f"\n{MOVE_JOINT} will move about {degrees:.0f} deg at about {speed:.0f} deg/s and come back.\n"
                      "Arm resting safely, hands clear, STOP switch in reach, camera recording."):
        return False, "Test move cancelled (GO not typed)"

    timeout = limits.drive_time_s
    enable_torque_checked(bus, MOVE_JOINT, limits.torque_limit)
    try:
        # One drive time limit for the whole move, there and back.
        deadline = time.monotonic() + timeout
        common = dict(tolerance=cfg.position_tolerance, max_temperature_c=limits.max_temperature_c)
        went = move_to(bus, MOVE_JOINT, target, timeout_s=deadline - time.monotonic(), **common)
        came_back = went and move_to(bus, MOVE_JOINT, start, timeout_s=deadline - time.monotonic(), **common)
    finally:
        bus.disable_torque(MOVE_JOINT)
    if not (went and came_back):
        return False, f"{MOVE_JOINT} did not get there and back within {timeout} s. Torque off."
    return True, f"{MOVE_JOINT} moved {degrees:.0f} deg and back, then stopped (torque off)"


if __name__ == "__main__":
    sys.exit(main())
