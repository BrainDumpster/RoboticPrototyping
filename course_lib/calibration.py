"""Turn the servo's Present_Load reading into newton-metres (N·m).

You hang known masses on a lever at a known radius. Each mass gives a known torque
(mass x g x radius) and a load reading. A straight-line fit through those points gives
torque = slope x load + intercept. The intercept soaks up the lever's own weight.
"""

import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .bus import Bus, read_raw
from .config import REPO_ROOT, ConfigError
from .safety import SafetyError, check_temperature

G = 9.80665  # m/s^2
CALIBRATION_DIR = REPO_ROOT / "calibration"
# A calibration unlocks the course twist ceiling, so it has to be a real one.
MIN_POINTS = 3
MIN_R_SQUARED = 0.95


@dataclass(frozen=True)
class CalPoint:
    mass_g: float
    radius_mm: float
    load_units: float  # average |Present_Load| while the mass hung

    @property
    def torque_nm(self) -> float:
        return torque_from_mass(self.mass_g, self.radius_mm)


@dataclass(frozen=True)
class LoadCalibration:
    servo: str
    slope_nm_per_unit: float
    intercept_nm: float
    r_squared: float
    points: tuple[CalPoint, ...]

    def to_nm(self, load_units: float) -> float:
        return self.slope_nm_per_unit * abs(load_units) + self.intercept_nm

    def to_units(self, torque_nm: float) -> int:
        """Rounded down, so the servo never gets a higher limit than you asked for."""
        return math.floor((torque_nm - self.intercept_nm) / self.slope_nm_per_unit)


def torque_from_mass(mass_g: float, radius_mm: float) -> float:
    return (mass_g / 1000) * G * (radius_mm / 1000)


def fit_line(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Least-squares line y = slope*x + intercept. Returns (slope, intercept, r_squared)."""
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("All load readings are the same, so no line can be fitted. Use different masses.")
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_squared = 1.0 if ss_tot == 0 else 1 - ss_res / ss_tot
    return slope, intercept, r_squared


def fit_calibration(servo: str, points: list[CalPoint]) -> LoadCalibration:
    if len(points) < MIN_POINTS:
        raise ValueError(f"Need at least {MIN_POINTS} masses (0 g plus two more) to fit a calibration.")
    if not all(math.isfinite(v) for p in points for v in (p.mass_g, p.radius_mm, p.load_units)):
        raise ValueError("A mass, radius or load is not a finite number. Check your entries.")
    slope, intercept, r_squared = fit_line([p.load_units for p in points], [p.torque_nm for p in points])
    if not (math.isfinite(slope) and math.isfinite(intercept)) or slope <= 0:
        raise ValueError("More mass gave less load, which means a data-entry mistake. Check grams and millimetres.")
    if r_squared < MIN_R_SQUARED:
        raise ValueError(f"R² is {r_squared:.3f}, below {MIN_R_SQUARED}: the points do not sit on a straight line. "
                         "Check your entries, or redo the masses.")
    return LoadCalibration(servo, slope, intercept, r_squared, tuple(points))


def calibration_path(servo: str, directory: Path = CALIBRATION_DIR) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", servo):
        raise ValueError("Servo labels may only use letters, numbers, - and _.")
    return Path(directory) / f"load_calibration_{servo}.json"


def save_calibration(cal: LoadCalibration, directory: Path = CALIBRATION_DIR) -> Path:
    path = calibration_path(cal.servo, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(cal)
    data["points"] = [{**asdict(p), "torque_nm": p.torque_nm} for p in cal.points]
    data["created"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def load_calibration(servo: str, directory: Path = CALIBRATION_DIR) -> LoadCalibration | None:
    """None if this servo has no calibration yet. Raises ConfigError if the file is not a valid fit.

    The fit is recomputed from the stored points, so a hand-edited or broken file cannot unlock
    the course twist ceiling.
    """
    path = calibration_path(servo, directory)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        points = [CalPoint(float(p["mass_g"]), float(p["radius_mm"]), float(p["load_units"])) for p in data["points"]]
        stored = (float(data["slope_nm_per_unit"]), float(data["intercept_nm"]))
        label = data["servo"]
        cal = fit_calibration(servo, points)
    except (OSError, KeyError, TypeError, ValueError) as error:  # JSONDecodeError is a ValueError
        raise ConfigError(f"{path} is not a valid load calibration ({error}). "
                          "Run scripts/calibrate_load.py again.") from error
    matches = all(math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9)
                  for a, b in zip(stored, (cal.slope_nm_per_unit, cal.intercept_nm)))
    if label != servo or not matches:
        raise ConfigError(f"{path} does not match its own data points. Run scripts/calibrate_load.py again.")
    return cal


def points_path(servo: str, directory: Path = CALIBRATION_DIR) -> Path:
    return calibration_path(servo, directory).with_name(f"load_points_{servo}.json")


def save_points(servo: str, points: list[CalPoint], directory: Path = CALIBRATION_DIR) -> Path:
    """Keep the raw measurements, so a failed fit can be redone without hanging the masses again."""
    path = points_path(servo, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(p) for p in points], indent=2) + "\n")
    return path


def load_points(servo: str, directory: Path = CALIBRATION_DIR) -> list[CalPoint]:
    path = points_path(servo, directory)
    try:
        return [CalPoint(float(p["mass_g"]), float(p["radius_mm"]), float(p["load_units"]))
                for p in json.loads(path.read_text())]
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ConfigError(f"Could not read the saved points in {path} ({error}).") from error


def check_hold_time(deadline: float, clock: Callable[[], float] = time.monotonic) -> None:
    """Drive time limit for a static hold: raise once torque has been on too long."""
    if clock() > deadline:
        raise SafetyError("Hold time limit reached. Torque goes off now: take the mass off, "
                          "then run the script again (your recorded points are saved).")


def average_load(
    bus: Bus,
    motor: str,
    samples: int,
    max_temperature_c: float,
    deadline: float = math.inf,
    period_s: float = 0.05,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> float:
    """Average Present_Load over `samples` readings; returns its size (the sign is ignored).

    Checks temperature and the hold deadline (a `clock()` time) before every reading.
    """
    if samples < 1:
        raise ValueError("samples must be at least 1.")
    total = 0
    for _ in range(samples):
        check_hold_time(deadline, clock)
        check_temperature(bus, motor, max_temperature_c)
        total += read_raw(bus, "Present_Load", motor)
        sleep(period_s)
    return abs(total / samples)
