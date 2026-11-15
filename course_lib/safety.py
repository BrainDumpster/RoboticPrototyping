"""Safety helpers every powered script uses.

The inline STOP switch is the real emergency stop. These helpers make sure the software
side never leaves torque on, never trusts a write without reading it back (Feetech servos
silently ignore some writes), and never goes above the course ceilings.
"""

import math
import signal
from collections.abc import Callable, Sequence
from types import TracebackType

from .bus import Bus, read_raw
from .config import Limits

OPERATING_MODES = {"position": 0, "velocity": 1}

# (servo register, field in Limits). These live in the servo's EEPROM.
PROTECTION_REGISTERS = (
    ("Max_Torque_Limit", "max_torque_limit"),
    ("Protection_Current", "protection_current"),
    ("Overload_Torque", "overload_torque"),
    ("Protective_Torque", "protective_torque"),
    ("Protection_Time", "protection_time"),
    ("Max_Temperature_Limit", "max_temperature_c"),
)

# Status (65) and Unloading_Condition (19) alarm bits, from the Feetech SMS/STS manual (not from
# LeRobot or rustypot source). INSTRUCTOR: confirm on the drive stand in the December pilot.
STATUS_BITS = {0x01: "voltage", 0x02: "angle sensor", 0x04: "overheat", 0x08: "overcurrent",
               0x10: "angle", 0x20: "overload"}
PROTECTION_ALARMS = 0x04 | 0x08 | 0x20  # overheat, overcurrent, overload
# Unloading_Condition: the servo cuts its own torque on these alarms, even if the USB link is gone.
UNLOADING_CONDITION = PROTECTION_ALARMS  # 44, the STS3215 factory value per the manual


class SafetyError(RuntimeError):
    """A safety check failed. The script stops and switches torque off."""


class OverTemperatureError(SafetyError):
    pass


def check_requested(name: str, value: float, ceiling: float, minimum: float = 1) -> float:
    """Refuse a value you chose on the command line if it is above the course ceiling."""
    if not math.isfinite(value):  # NaN passes every comparison below
        raise SafetyError(f"{name} must be a finite number.")
    if value > ceiling:
        raise SafetyError(f"{name} = {value} is above the course ceiling of {ceiling}. Use {ceiling} or less.")
    if value < minimum:
        raise SafetyError(f"{name} = {value} is too small. Use at least {minimum}.")
    return value


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def write_verified(bus: Bus, register: str, motor: str, value: int) -> None:
    bus.write(register, motor, int(value), normalize=False)
    actual = read_raw(bus, register, motor)
    if actual != int(value):
        raise SafetyError(
            f"{motor}: wrote {register} = {int(value)} but read back {actual}. "
            "The servo did not take the setting, so the script stops."
        )


def set_mode_verified(bus: Bus, motor: str, mode: str) -> None:
    """Switch operating mode (needs torque off, because the mode lives in EEPROM) and read it back."""
    bus.disable_torque(motor)
    bus.set_operating_mode(mode, motor)
    actual = read_raw(bus, "Operating_Mode", motor)
    if actual != OPERATING_MODES[mode]:
        raise SafetyError(f"{motor}: asked for {mode} mode but Operating_Mode reads {actual}.")


def apply_protection_settings(bus: Bus, motor: str, limits: Limits) -> dict[str, int]:
    """Write the course protection settings to one servo and read each one back."""
    bus.disable_torque(motor)  # EEPROM only accepts writes with torque off
    applied = {}
    for register, field in PROTECTION_REGISTERS:
        value = int(getattr(limits, field))
        write_verified(bus, register, motor, value)
        applied[register] = value
    write_verified(bus, "Unloading_Condition", motor, UNLOADING_CONDITION)
    applied["Unloading_Condition"] = UNLOADING_CONDITION
    return applied


def describe_status(status: int) -> str:
    """Names of the alarm bits set in the Status register, e.g. "overload, overheat"."""
    return ", ".join(name for bit, name in STATUS_BITS.items() if status & bit) or "none"


def check_temperature(bus: Bus, motor: str, max_c: float) -> int:
    temperature = read_raw(bus, "Present_Temperature", motor)
    if temperature > max_c:
        raise OverTemperatureError(
            f"{motor} is at {temperature} deg C, above the {max_c} deg C limit. "
            "Torque is off. Let it cool for at least 10 minutes."
        )
    return temperature


def check_voltage(bus: Bus, motor: str, v_min: float, v_max: float) -> float:
    volts = read_raw(bus, "Present_Voltage", motor) / 10  # the register counts 0.1 V
    if not v_min <= volts <= v_max:
        raise SafetyError(
            f"{motor} sees {volts:.1f} V; the course range is {v_min}-{v_max} V. "
            "Check that you are using the course 7.5 V supply and that its plug is fully in."
        )
    return volts


def enable_torque_checked(bus: Bus, motor: str, torque_limit: int) -> None:
    """Syllabus rule: read the torque limit back before every enable."""
    write_verified(bus, "Torque_Limit", motor, torque_limit)
    bus.enable_torque(motor)
    after = read_raw(bus, "Torque_Limit", motor)
    if after != torque_limit:
        bus.disable_torque(motor)
        raise SafetyError(f"{motor}: Torque_Limit changed to {after} when torque came on. Torque is off again.")


def confirm_go(message: str, input_fn: Callable[[str], str] | None = None) -> bool:
    """Hands-off call-and-response: nothing powered happens unless the student types GO."""
    print(message)
    answer = (input_fn or input)("Type GO and press Enter (anything else cancels): ")
    return answer.strip().upper() == "GO"


class TorqueGuard:
    """`with TorqueGuard(bus, motors):` -- however the block ends (normally, on an error or
    on Ctrl+C), switch torque off, optionally restore position mode, and close the port."""

    def __init__(self, bus: Bus, motors: Sequence[str], restore_position_mode: bool = False):
        self.bus = bus
        self.motors = list(motors)
        self.restore_position_mode = restore_position_mode

    def __enter__(self) -> "TorqueGuard":
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> bool:
        previous_handler = _ignore_ctrl_c()  # a second Ctrl+C must not cut the shutdown short
        problems: list[str] = []
        try:
            _attempt(problems, "switch torque off", lambda: self.bus.disable_torque(self.motors, num_retry=3))
            if self.restore_position_mode:
                for motor in self.motors:
                    _attempt(problems, f"put {motor} back in position mode",
                             lambda motor=motor: set_mode_verified(self.bus, motor, "position"))
            _attempt(problems, "close the port", lambda: self.bus.disconnect(disable_torque=True))
        finally:
            _restore_ctrl_c(previous_handler)
        if problems:
            if exc_type is not None and issubclass(exc_type, ConnectionError):
                print("\nWARNING: lost contact with the servo, so torque could not be switched off by software. "
                      "If you pressed STOP or pulled the USB cable, leave the power off. "
                      "Otherwise press the STOP switch now and tell the TA.")
            else:
                print("\nWARNING: the shutdown was not clean. Press the STOP switch now and tell the TA.")
            for problem in problems:
                print(f"  - {problem}")
            if exc_type is None:
                raise SafetyError("Shutdown failed: " + "; ".join(problems))
        return False  # never hide the original error


def _attempt(problems: list[str], what: str, action: Callable[[], object]) -> None:
    try:
        action()
    except Exception as error:  # keep going: every later shutdown step must still run
        problems.append(f"could not {what}: {error}")


def _ignore_ctrl_c():
    try:
        return signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:  # signal handlers can only be set from the main thread
        return None


def _restore_ctrl_c(previous_handler) -> None:
    if previous_handler is not None:
        signal.signal(signal.SIGINT, previous_handler)
