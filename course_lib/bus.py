"""Open the servo bus (the USB adapter) with raw register access.

The course scripts always read and write raw numbers (`normalize=False`), so they work
before you have run `lerobot-calibrate`.
"""

from collections.abc import Sequence
from typing import Protocol

from .config import CourseConfig


class BusSetupError(RuntimeError):
    """The bus could not be created or opened. The message says what to check."""


class Bus(Protocol):
    """The part of LeRobot's SerialMotorsBus the course code uses (tests use a fake)."""

    is_connected: bool

    def connect(self, handshake: bool = True) -> None: ...
    def disconnect(self, disable_torque: bool = True) -> None: ...
    def read(self, data_name: str, motor: str, *, normalize: bool = True, num_retry: int = 0) -> int: ...
    def write(self, data_name: str, motor: str, value: int, *, normalize: bool = True, num_retry: int = 0) -> None: ...
    def enable_torque(self, motors: str | Sequence[str] | None = None, num_retry: int = 0) -> None: ...
    def disable_torque(self, motors: str | Sequence[str] | None = None, num_retry: int = 0) -> None: ...
    def set_operating_mode(self, mode: str, motors: str | Sequence[str] | None = None) -> None: ...
    def ping(self, motor: str, num_retry: int = 0) -> int | None: ...


def resolve_port(cli_port: str | None, cfg: CourseConfig) -> str:
    port = cli_port or cfg.port
    if not port:
        raise BusSetupError(
            "No port given. Run `lerobot-find-port` to find your adapter's port, "
            "then pass it like this: --port /dev/tty.usbmodem58760431551"
        )
    return port


def make_bus(port: str, motor_ids: dict[str, int], model: str) -> Bus:
    try:
        from lerobot.motors import Motor, MotorNormMode, SerialMotorsBus

        # The norm mode is required by Motor, but unused: course code never normalizes.
        motors = {name: Motor(id_, model, MotorNormMode.RANGE_M100_100) for name, id_ in motor_ids.items()}
        # The constructor loads rustypot, so a broken rustypot install fails here, not at import.
        return SerialMotorsBus(port=port, motors=motors)
    except (ImportError, ValueError) as error:
        raise BusSetupError(
            f"LeRobot's motor code could not be set up ({error}). "
            "Run `python scripts/check_install.py` and fix what it reports."
        ) from error


def open_bus(bus: Bus, *, handshake: bool = True, missing_hint: str = "") -> None:
    """Connect, turning LeRobot's errors into messages a beginner can act on."""
    try:
        bus.connect(handshake=handshake)
    except ConnectionError as error:
        raise BusSetupError(
            f"Could not talk to the adapter: {error}\n"
            "Check: USB cable plugged in, supply on, STOP switch not pressed (power on), and the right --port."
        ) from error
    except RuntimeError as error:
        raise BusSetupError(f"The servo check at connect failed: {error}\n{missing_hint}") from error


def read_raw(bus: Bus, register: str, motor: str) -> int:
    return int(bus.read(register, motor, normalize=False))
