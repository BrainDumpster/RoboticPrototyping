"""Test helpers: a FakeBus that behaves like LeRobot's SerialMotorsBus, a fake clock, a sample config."""

import copy
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODES = {"position": 0, "velocity": 1, "pwm": 2, "step": 3}
# Registers below address 40 live in EEPROM: a Feetech servo silently ignores writes to them while Lock = 1.
EEPROM = {"ID", "Min_Position_Limit", "Max_Position_Limit", "Max_Temperature_Limit", "Max_Torque_Limit",
          "Protection_Current", "Operating_Mode", "Overload_Torque", "Protective_Torque", "Protection_Time",
          "Min_Voltage_Limit", "Max_Voltage_Limit", "Unloading_Condition"}
DEFAULTS = {
    "ID": 1, "Model_Number": 777, "Operating_Mode": 0, "Torque_Enable": 0, "Lock": 1,
    "Min_Position_Limit": 0, "Max_Position_Limit": 4095, "Max_Torque_Limit": 1000, "Torque_Limit": 1000,
    "Protection_Current": 500, "Overload_Torque": 80, "Protective_Torque": 20, "Protection_Time": 200, "Unloading_Condition": 44,
    "Max_Temperature_Limit": 70, "Min_Voltage_Limit": 40, "Max_Voltage_Limit": 80,
    "Acceleration": 254, "Goal_Position": 2048, "Goal_Velocity": 0,
    "Present_Position": 2048, "Present_Velocity": 0, "Present_Load": 0, "Present_Current": 0,
    "Present_Temperature": 30, "Present_Voltage": 75, "Moving": 0, "Status": 0,
}


class FakeBus:
    """Implements the subset of SerialMotorsBus the course code uses."""

    def __init__(self, motors: dict[str, int]):
        self.motors = motors
        self.regs = {name: {**DEFAULTS, "ID": id_} for name, id_ in motors.items()}
        self.is_connected = False
        self.ignore_writes: set[str] = set()       # simulate a servo that silently refuses a register
        self.dynamic: dict[str, Callable[[str], int]] = {}  # register -> function(motor) for live values
        self.writes: list[tuple[str, str, int]] = []
        self.move_per_read = 0                      # >0: Present_Position walks toward Goal_Position
        self.missing: set[str] = set()              # motors that never answer (wrong ID, loose cable)

    def connect(self, handshake: bool = True) -> None:
        self.is_connected = True

    def disconnect(self, disable_torque: bool = True) -> None:
        if not self.is_connected:
            return
        try:  # like LeRobot: the port is closed even if switching torque off fails
            if disable_torque:
                self.disable_torque()
        finally:
            self.is_connected = False

    def read(self, data_name: str, motor: str, *, normalize: bool = True, num_retry: int = 0) -> int:
        self._check(data_name, normalize)
        self._answer(motor)
        regs = self.regs[motor]  # like LeRobot, read() takes a motor *name*
        if data_name == "Present_Position" and self.move_per_read and regs["Torque_Enable"]:
            gap = regs["Goal_Position"] - regs["Present_Position"]
            regs["Present_Position"] += max(-self.move_per_read, min(self.move_per_read, gap))
        if data_name in self.dynamic:
            return self.dynamic[data_name](motor)
        return regs[data_name]

    def write(self, data_name: str, motor: str, value: int, *, normalize: bool = True, num_retry: int = 0) -> None:
        self._check(data_name, normalize)
        self._answer(motor)
        regs = self.regs[motor]
        self.writes.append((data_name, motor, int(value)))
        if data_name in self.ignore_writes or (data_name in EEPROM and regs["Lock"] == 1):
            return  # Feetech never reports a refused write
        regs[data_name] = int(value)

    def enable_torque(self, motors=None, num_retry: int = 0) -> None:
        self._set_torque(motors, 1)

    def disable_torque(self, motors=None, num_retry: int = 0) -> None:
        self._set_torque(motors, 0)

    def _set_torque(self, motors, enabled: int) -> None:
        # Like LeRobot 0.7.0: every answering motor is set, then one ConnectionError names the rest.
        names = self._names(motors)
        for name in names:
            if name not in self.missing:
                self.regs[name].update(Torque_Enable=enabled, Lock=enabled)
        if failed := [name for name in names if name in self.missing]:
            raise ConnectionError(f"Failed to set torque on {', '.join(failed)}")

    def set_operating_mode(self, mode: str, motors=None) -> None:
        for name in self._names(motors):
            self.write("Operating_Mode", name, MODES[mode])

    def ping(self, motor: str, num_retry: int = 0) -> int | None:
        return None if motor in self.missing else self.regs[motor]["Model_Number"]

    def _answer(self, motor: str) -> None:
        if motor in self.missing:
            raise ConnectionError(f"no status packet from {motor}")

    def _check(self, data_name: str, normalize: bool) -> None:
        if not self.is_connected:
            raise ConnectionError("FakeBus is not connected")
        if normalize and data_name in ("Goal_Position", "Present_Position"):
            raise RuntimeError("FakeBus has no calibration registered.")

    def _names(self, motors) -> list[str]:
        if motors is None:
            return list(self.motors)
        return [motors] if isinstance(motors, str) else list(motors)


class FakeClock:
    """clock() and sleep() for loops: sleeping just moves the fake time forward."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


SAMPLE_CONFIG = {
    "hardware": {
        "port": None, "model": "sts3215", "model_number": 777,
        "motor_ids": {"shoulder_pan": 1, "shoulder_lift": 2, "elbow_flex": 3,
                      "wrist_flex": 4, "wrist_roll": 5, "gripper": 6},
        "single_servo_name": "gripper", "single_servo_id": 1,
    },
    "ceilings": {"torque_limit": 500, "max_torque_limit": 500, "protection_current": 250, "overload_torque": 25,
                 "protective_torque": 20, "protection_time": 200, "max_temperature_c": 60, "drive_time_s": 90, "wheel_speed": 2400, "move_speed": 300,
                 "move_steps": 400, "acceleration": 50},
    "limits": {"torque_limit": 150, "max_torque_limit": 500, "protection_current": 250, "overload_torque": 25,
               "protective_torque": 20, "protection_time": 200, "max_temperature_c": 60, "drive_time_s": 60, "wheel_speed": 1200, "move_speed": 150,
               "move_steps": 200, "acceleration": 10},
    "screw": {"stall_velocity": 20, "stall_time_s": 0.3, "grace_s": 0.5, "seated_fraction": 0.8,
              "sample_period_s": 0.02},
    "voltage": {"min_v": 7.0, "max_v": 8.0},
    "position_tolerance": 15,
    "teleop": {"max_relative_target": 10},
}


def sample_config() -> dict:
    return copy.deepcopy(SAMPLE_CONFIG)


def connected_bus(motors: dict[str, int] | None = None) -> FakeBus:
    bus = FakeBus(motors or {"gripper": 1})
    bus.connect()
    return bus


def load_script(name: str):
    """Import scripts/<name>.py as a module (the scripts folder is not a package)."""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
