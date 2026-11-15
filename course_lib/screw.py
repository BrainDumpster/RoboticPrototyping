"""The screw-driving loop: turn in wheel mode until the screw seats, stalls or time runs out."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from .bus import Bus, read_raw
from .calibration import LoadCalibration
from .config import Limits
from .safety import PROTECTION_ALARMS, SafetyError

LOG_FIELDS = ("t_s", "load", "current", "temperature_c", "position", "velocity", "status")

SEATED = "seated"            # stopped turning at (or near) your twist limit
STALLED = "stalled"          # stopped turning well below your limit: jammed or cross-threaded?
TIME_OUT = "time_out"        # still turning when the time limit ran out
OVER_TEMPERATURE = "over_temperature"
PROTECTION_TRIPPED = "protection_tripped"  # the servo's own overload/overcurrent/overheat alarm

MIN_TWIST_LIMIT = 10  # torque units; anything lower cannot turn a screw


@dataclass(frozen=True)
class DriveSettings:
    limit_raw: int           # twist limit in torque units (also written to Torque_Limit)
    speed: int               # signed steps/s; the sign sets the turning direction
    time_limit_s: float
    max_temperature_c: float
    stall_velocity: int
    stall_time_s: float
    grace_s: float
    seated_fraction: float
    sample_period_s: float


@dataclass(frozen=True)
class DriveResult:
    outcome: str
    elapsed_s: float
    peak_load: int
    final_load: int


def read_sample(bus: Bus, motor: str) -> dict[str, int]:
    return {
        "load": read_raw(bus, "Present_Load", motor),
        # Masked in case the firmware sets a direction bit (bit 15) on this register.
        "current": read_raw(bus, "Present_Current", motor) & 0x7FFF,
        "temperature_c": read_raw(bus, "Present_Temperature", motor),
        "position": read_raw(bus, "Present_Position", motor),
        "velocity": read_raw(bus, "Present_Velocity", motor),
        # Alarm bits. LeRobot never raises on these, so they are read explicitly.
        "status": read_raw(bus, "Status", motor),
    }


def drive_until_stop(
    bus: Bus,
    motor: str,
    settings: DriveSettings,
    log_row: Callable[[dict], None],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> DriveResult:
    """Torque must already be on in velocity mode. Always writes speed 0 before returning."""
    bus.write("Goal_Velocity", motor, settings.speed, normalize=False)
    start = clock()
    stall_since: float | None = None
    peak_load = 0
    try:
        while True:
            t = clock() - start
            sample = read_sample(bus, motor)
            log_row({"t_s": round(t, 3), **sample})
            load = abs(sample["load"])
            peak_load = max(peak_load, load)

            outcome = None
            if sample["temperature_c"] > settings.max_temperature_c:
                outcome = OVER_TEMPERATURE
            elif sample["status"] & PROTECTION_ALARMS:
                outcome = PROTECTION_TRIPPED
            elif t >= settings.grace_s and abs(sample["velocity"]) <= settings.stall_velocity:
                stall_since = t if stall_since is None else stall_since
                if t - stall_since >= settings.stall_time_s:
                    outcome = SEATED if load >= settings.seated_fraction * settings.limit_raw else STALLED
            else:
                stall_since = None
            if outcome is None and t >= settings.time_limit_s:
                outcome = TIME_OUT
            if outcome:
                return DriveResult(outcome, round(t, 3), peak_load, load)
            sleep(settings.sample_period_s)
    finally:
        # Stop turning first; the TorqueGuard then switches torque off and restores position mode.
        try:
            bus.write("Goal_Velocity", motor, 0, normalize=False)
        except Exception as error:
            print(f"WARNING: could not write speed 0 ({error}); torque will be switched off next.")


def choose_twist_limit(
    limit_nm: float | None,
    limit_raw: int | None,
    cal: LoadCalibration | None,
    limits: Limits,
    ceilings: Limits,
) -> int:
    """Pick the twist limit in torque units and refuse it if it is above the allowed ceiling.

    Until this servo has a load calibration, the instructor's provisional cap
    (limits.torque_limit) is the ceiling. After that, the course ceiling applies, but never
    above the Max_Torque_Limit the scripts write to the servo.
    """
    if cal is None:
        ceiling, ceiling_name = limits.torque_limit, "provisional cap (this servo has no load calibration yet)"
    elif limits.max_torque_limit < ceilings.torque_limit:
        ceiling, ceiling_name = limits.max_torque_limit, "Max_Torque_Limit written to the servo"
    else:
        ceiling, ceiling_name = ceilings.torque_limit, "course ceiling"

    if limit_nm is not None:
        if cal is None:
            raise SafetyError("--limit-nm needs a load calibration. Run scripts/calibrate_load.py first, "
                              "or give the limit in raw units with --limit-raw.")
        raw = cal.to_units(limit_nm)
    elif limit_raw is not None:
        raw = limit_raw
    else:
        raw = limits.torque_limit

    if raw > ceiling:
        raise SafetyError(f"A twist limit of {raw} units is above the {ceiling_name} of {ceiling}.")
    if raw < MIN_TWIST_LIMIT:
        raise SafetyError(f"A twist limit of {raw} units is too small to turn a screw (minimum {MIN_TWIST_LIMIT}).")
    return raw
