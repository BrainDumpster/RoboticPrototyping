import json
import tempfile
import unittest
from pathlib import Path

from helpers import FakeClock, connected_bus

from course_lib.calibration import (
    CalPoint, average_load, calibration_path, fit_calibration, fit_line, load_calibration, load_points,
    save_calibration, save_points, torque_from_mass,
)
from course_lib.config import ConfigError
from course_lib.safety import OverTemperatureError, SafetyError

NAN = float("nan")
POINTS = (CalPoint(0, 100, 20), CalPoint(100, 100, 120), CalPoint(200, 100, 220))


class FitTest(unittest.TestCase):
    def test_torque_from_mass(self):
        # 1 kg at 100 mm = 0.980665 N·m
        self.assertAlmostEqual(torque_from_mass(1000, 100), 0.980665)

    def test_exact_line(self):
        slope, intercept, r2 = fit_line([0, 100, 200], [0.05, 0.15, 0.25])
        self.assertAlmostEqual(slope, 0.001)
        self.assertAlmostEqual(intercept, 0.05)
        self.assertAlmostEqual(r2, 1.0)

    def test_calibration_round_trip(self):
        points = [CalPoint(0, 100, 20), CalPoint(100, 100, 120), CalPoint(200, 100, 220)]
        cal = fit_calibration("gripper", points)
        self.assertAlmostEqual(cal.r_squared, 1.0)
        self.assertAlmostEqual(cal.to_nm(120), torque_from_mass(100, 100))
        self.assertAlmostEqual(cal.to_nm(-120), cal.to_nm(120))  # sign of Present_Load is ignored
        self.assertIn(cal.to_units(cal.to_nm(170)), (169, 170))

    def test_too_few_points(self):
        with self.assertRaises(ValueError):
            fit_calibration("gripper", [CalPoint(0, 100, 20), CalPoint(200, 100, 220)])

    def test_identical_loads(self):
        with self.assertRaises(ValueError):
            fit_calibration("gripper", [CalPoint(0, 100, 50), CalPoint(100, 100, 50), CalPoint(200, 100, 50)])

    def test_backwards_data_refused(self):
        with self.assertRaises(ValueError):
            fit_calibration("gripper", [CalPoint(0, 100, 200), CalPoint(100, 100, 110), CalPoint(200, 100, 20)])

    def test_not_a_number_refused(self):
        # NaN passes `slope <= 0`, so it has to be refused explicitly.
        with self.assertRaises(ValueError):
            fit_calibration("gripper", [CalPoint(0, 100, 20), CalPoint(100, 100, NAN), CalPoint(200, 100, 220)])

    def test_poor_fit_refused(self):
        with self.assertRaisesRegex(ValueError, "R²"):
            fit_calibration("gripper", [CalPoint(0, 100, 20), CalPoint(100, 100, 300), CalPoint(200, 100, 220),
                                        CalPoint(300, 100, 60)])

    def test_save_and_load(self):
        cal = fit_calibration("gripper", POINTS)
        with tempfile.TemporaryDirectory() as tmp:
            path = save_calibration(cal, Path(tmp))
            self.assertEqual(path.name, "load_calibration_gripper.json")
            loaded = load_calibration("gripper", Path(tmp))
            self.assertAlmostEqual(loaded.slope_nm_per_unit, cal.slope_nm_per_unit)
            self.assertEqual(len(loaded.points), 3)
            self.assertIsNone(load_calibration("other", Path(tmp)))

    def test_tampered_calibration_refused(self):
        """A hand-edited file would unlock the course twist ceiling, so it must not load."""
        cal = fit_calibration("gripper", POINTS)
        with tempfile.TemporaryDirectory() as tmp:
            path = save_calibration(cal, Path(tmp))
            data = json.loads(path.read_text())
            data["slope_nm_per_unit"] *= 3
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ConfigError, "does not match"):
                load_calibration("gripper", Path(tmp))

    def test_junk_calibration_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for junk in ("not json", '{"servo": "gripper"}', '{"servo": "gripper", "slope_nm_per_unit": 1, '
                         '"intercept_nm": 0, "r_squared": 1, "points": []}'):
                calibration_path("gripper", Path(tmp)).write_text(junk)
                with self.assertRaises(ConfigError):
                    load_calibration("gripper", Path(tmp))

    def test_calibration_saved_under_another_label_refused(self):
        cal = fit_calibration("gripper", POINTS)
        with tempfile.TemporaryDirectory() as tmp:
            path = save_calibration(cal, Path(tmp))
            path.rename(calibration_path("other", Path(tmp)))
            with self.assertRaises(ConfigError):
                load_calibration("other", Path(tmp))

    def test_points_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_points("gripper", list(POINTS), Path(tmp))
            self.assertEqual(load_points("gripper", Path(tmp)), list(POINTS))

    def test_bad_label_refused(self):
        with self.assertRaises(ValueError):
            calibration_path("../../etc")


class AverageLoadTest(unittest.TestCase):
    def test_average(self):
        bus = connected_bus()
        values = iter([-100, -110, -90, -100])
        bus.dynamic["Present_Load"] = lambda m: next(values)
        self.assertAlmostEqual(average_load(bus, "gripper", 4, 60, sleep=lambda s: None), 100)

    def test_hold_deadline_aborts(self):
        bus = connected_bus()
        clock = FakeClock()
        with self.assertRaisesRegex(SafetyError, "Hold time"):
            average_load(bus, "gripper", 100, 60, deadline=1.0, clock=clock, sleep=clock.sleep)
        self.assertLessEqual(clock.now, 1.1)

    def test_zero_samples_refused(self):
        with self.assertRaises(ValueError):
            average_load(connected_bus(), "gripper", 0, 60, sleep=lambda s: None)

    def test_hot_servo_aborts(self):
        bus = connected_bus()
        bus.regs["gripper"]["Present_Temperature"] = 61
        with self.assertRaises(OverTemperatureError):
            average_load(bus, "gripper", 4, 60, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
