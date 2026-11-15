"""One slow position move, with a time limit and a temperature check."""

import time
from collections.abc import Callable

from .bus import Bus, read_raw
from .safety import check_temperature, write_verified


def move_to(
    bus: Bus,
    motor: str,
    target: int,
    *,
    tolerance: int,
    timeout_s: float,
    max_temperature_c: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    period_s: float = 0.05,
) -> bool:
    """Send Goal_Position and wait. True if it arrives, False on time-out.

    Speed is capped beforehand by Goal_Velocity (see commission.py).
    """
    write_verified(bus, "Goal_Position", motor, target)
    start = clock()
    while clock() - start < timeout_s:
        check_temperature(bus, motor, max_temperature_c)
        if abs(read_raw(bus, "Present_Position", motor) - target) <= tolerance:
            return True
        sleep(period_s)
    return False
