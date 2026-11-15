"""Load config/course_config.yaml and refuse it if any limit is above its ceiling."""

import math
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "course_config.yaml"
SO101_MOTORS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
# Syllabus rule, kept in code so editing the YAML cannot raise it.
SYLLABUS_MAX_TEMPERATURE_C = 60

# INSTRUCTOR ONLY. The authoritative ceilings live here, in code, so that editing the YAML (or
# pointing --config at another file) can never raise them. The YAML `ceilings:` section may only
# repeat or lower these. INSTRUCTOR: set after December pilot (and update the YAML to match).
INSTRUCTOR_CEILINGS = {
    "torque_limit": 500,          # torque units (50 %), the course ceiling once a load calibration exists
    "max_torque_limit": 500,
    "protection_current": 250,
    "overload_torque": 25,
    "protective_torque": 20,
    "protection_time": 200,
    "max_temperature_c": SYLLABUS_MAX_TEMPERATURE_C,
    "drive_time_s": 90,
    "wheel_speed": 2400,
    "move_speed": 300,
    "move_steps": 400,
    "acceleration": 50,
}
# The twist cap before a servo has a load calibration. limits.torque_limit may not exceed it.
# INSTRUCTOR: set after December pilot.
PROVISIONAL_TORQUE_LIMIT = 150
# Longest sleep between screw-test safety checks (temperature, time, stall).
MAX_SAMPLE_PERIOD_S = 0.1


class ConfigError(ValueError):
    """The course config is missing a value or breaks a safety ceiling."""


@dataclass(frozen=True)
class Limits:
    """Used twice: once for the hard ceilings and once for the values scripts use."""

    torque_limit: int
    max_torque_limit: int
    protection_current: int
    overload_torque: int
    protective_torque: int
    protection_time: int
    max_temperature_c: int
    drive_time_s: float
    wheel_speed: int
    move_speed: int
    move_steps: int
    acceleration: int


@dataclass(frozen=True)
class ScrewTuning:
    stall_velocity: int
    stall_time_s: float
    grace_s: float
    seated_fraction: float
    sample_period_s: float


@dataclass(frozen=True)
class CourseConfig:
    port: str | None
    model: str
    model_number: int
    motor_ids: dict[str, int]
    single_servo_name: str
    single_servo_id: int
    ceilings: Limits
    limits: Limits
    screw: ScrewTuning
    voltage_min: float
    voltage_max: float
    position_tolerance: int
    max_relative_target: float


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> CourseConfig:
    import yaml  # imported here so the tests run without PyYAML

    try:
        text = Path(path).read_text()
    except OSError as error:
        raise ConfigError(f"Cannot read the course config {path}: {error}") from error
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} is not valid YAML: {error}") from error
    return parse_config(raw)


def parse_config(raw: Any) -> CourseConfig:
    if not isinstance(raw, dict):
        raise ConfigError("The course config must be a YAML mapping (key: value lines).")
    hardware = _section(raw, "hardware")
    ceilings = _limits(_section(raw, "ceilings"), "ceilings")
    limits = _limits(_section(raw, "limits"), "limits")
    _check_against_ceilings(limits, ceilings)

    screw_raw = _section(raw, "screw")
    screw = ScrewTuning(**_typed_numbers(screw_raw, ScrewTuning, "screw"))
    if not 0 < screw.seated_fraction <= 1:
        raise ConfigError("screw.seated_fraction must be between 0 and 1.")
    if screw.sample_period_s > MAX_SAMPLE_PERIOD_S:
        raise ConfigError(f"screw.sample_period_s may not be above {MAX_SAMPLE_PERIOD_S} s: "
                          "the safety checks run once per sample.")
    if screw.grace_s + screw.stall_time_s >= limits.drive_time_s:
        raise ConfigError("screw.grace_s + screw.stall_time_s must be shorter than limits.drive_time_s, "
                          "or stall detection never gets a chance to run.")

    voltage = _section(raw, "voltage")
    v_min, v_max = _number(voltage, "min_v", "voltage"), _number(voltage, "max_v", "voltage")
    if not 6.0 <= v_min < v_max <= 8.4:
        raise ConfigError("voltage.min_v and voltage.max_v must satisfy 6.0 <= min_v < max_v <= 8.4.")

    port = hardware.get("port")
    if port is not None and not isinstance(port, str):
        raise ConfigError("hardware.port must be text (e.g. /dev/tty.usbmodem123) or null.")

    return CourseConfig(
        port=port,
        model=str(hardware.get("model", "sts3215")),
        model_number=_whole_number(hardware, "model_number", "hardware"),
        motor_ids=_motor_ids(hardware.get("motor_ids")),
        single_servo_name=str(hardware.get("single_servo_name", "gripper")),
        single_servo_id=_servo_id(hardware.get("single_servo_id"), "hardware.single_servo_id"),
        ceilings=ceilings,
        limits=limits,
        screw=screw,
        voltage_min=v_min,
        voltage_max=v_max,
        position_tolerance=_whole_number(raw, "position_tolerance", "top level"),
        max_relative_target=_number(_section(raw, "teleop"), "max_relative_target", "teleop"),
    )


def _check_against_ceilings(limits: Limits, ceilings: Limits) -> None:
    for f in fields(Limits):
        value, ceiling = getattr(limits, f.name), getattr(ceilings, f.name)
        hard = INSTRUCTOR_CEILINGS[f.name]
        if ceiling > hard:
            raise ConfigError(
                f"ceilings.{f.name} = {ceiling} is above the instructor's ceiling of {hard}. "
                "Only the instructor sets ceilings. Put it back to the value from the course repository."
            )
        if value > ceiling:
            raise ConfigError(
                f"limits.{f.name} = {value} is above its ceiling of {ceiling}. "
                "Safety limits can only go down. Put the value back at or below the ceiling."
            )
    if limits.torque_limit > PROVISIONAL_TORQUE_LIMIT:
        raise ConfigError(
            f"limits.torque_limit = {limits.torque_limit} is above the instructor's provisional cap of "
            f"{PROVISIONAL_TORQUE_LIMIT}. Put it back at or below {PROVISIONAL_TORQUE_LIMIT}."
        )
    if limits.torque_limit > limits.max_torque_limit:
        raise ConfigError("limits.torque_limit may not be above limits.max_torque_limit.")


def _section(raw: dict, name: str) -> dict:
    section = raw.get(name)
    if not isinstance(section, dict):
        raise ConfigError(f"The course config is missing its '{name}:' section.")
    return section


def _number(section: dict, key: str, where: str) -> float:
    value = section.get(key)
    # bool is a subclass of int in Python, so rule it out explicitly.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}.{key} must be a number (found {value!r}).")
    # NaN passes every < and > comparison, so it would slip past each limit check below.
    if not math.isfinite(value):
        raise ConfigError(f"{where}.{key} must be a finite number (found {value}).")
    if value <= 0:
        raise ConfigError(f"{where}.{key} must be greater than 0 (found {value}).")
    return value


def _whole_number(section: dict, key: str, where: str) -> int:
    """Register values must be whole: int(0.5) would quietly write 0, which means "no limit"."""
    value = _number(section, key, where)
    if not isinstance(value, int):
        raise ConfigError(f"{where}.{key} must be a whole number (found {value}).")
    return value


def _typed_numbers(section: dict, cls: type, where: str) -> dict[str, float]:
    """Read every field of dataclass `cls`: int fields must be whole numbers, float fields any number."""
    return {
        f.name: _whole_number(section, f.name, where) if f.type is int else _number(section, f.name, where)
        for f in fields(cls)
    }


def _limits(section: dict, where: str) -> Limits:
    return Limits(**_typed_numbers(section, Limits, where))


def _servo_id(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 253:
        raise ConfigError(f"{where} must be a whole number from 1 to 253 (found {value!r}).")
    return value


def _motor_ids(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict) or set(raw) != set(SO101_MOTORS):
        raise ConfigError(f"hardware.motor_ids must list exactly these motors: {', '.join(SO101_MOTORS)}.")
    ids = {name: _servo_id(raw[name], f"hardware.motor_ids.{name}") for name in SO101_MOTORS}
    if len(set(ids.values())) != len(ids):
        raise ConfigError("hardware.motor_ids has the same ID twice. Every motor needs its own ID.")
    return ids
