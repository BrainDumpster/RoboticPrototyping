import unittest
from dataclasses import replace

from helpers import FakeClock, connected_bus, sample_config

from course_lib.calibration import CalPoint, fit_calibration
from course_lib.config import parse_config
from course_lib.motion import move_to
from course_lib.safety import OverTemperatureError, SafetyError
from course_lib.screw import (
    OVER_TEMPERATURE, PROTECTION_TRIPPED, SEATED, STALLED, TIME_OUT, DriveSettings, choose_twist_limit, drive_until_stop,
)

CFG = parse_config(sample_config())
SETTINGS = DriveSettings(limit_raw=150, speed=1200, time_limit_s=5.0, max_temperature_c=60,
                         stall_velocity=20, stall_time_s=0.3, grace_s=0.5, seated_fraction=0.8,
                         sample_period_s=0.02)


def drive(bus, clock, settings=SETTINGS):
    rows = []
    result = drive_until_stop(bus, "gripper", settings, rows.append, clock=clock, sleep=clock.sleep)
    return result, rows


class DriveLoopTest(unittest.TestCase):
    def setUp(self):
        self.bus = connected_bus()
        self.clock = FakeClock()

    def _turning_until(self, stop_at: float, load_after: int):
        """Velocity 600 until `stop_at` seconds, then 0 with `load_after` load."""
        self.bus.dynamic["Present_Velocity"] = lambda m: 600 if self.clock.now < stop_at else 0
        self.bus.dynamic["Present_Load"] = lambda m: 40 if self.clock.now < stop_at else load_after

    def test_time_limit_stops_the_drive(self):
        self.bus.dynamic["Present_Velocity"] = lambda m: 600  # never stalls
        result, rows = drive(self.bus, self.clock)
        self.assertEqual(result.outcome, TIME_OUT)
        self.assertGreaterEqual(result.elapsed_s, 5.0)
        self.assertLess(result.elapsed_s, 5.1)
        self.assertEqual(self.bus.regs["gripper"]["Goal_Velocity"], 0)

    def test_stall_at_limit_is_seated(self):
        self._turning_until(2.0, load_after=-140)  # load sign does not matter
        result, rows = drive(self.bus, self.clock)
        self.assertEqual(result.outcome, SEATED)
        self.assertAlmostEqual(result.elapsed_s, 2.3, delta=0.05)
        self.assertEqual(result.peak_load, 140)
        self.assertEqual(self.bus.regs["gripper"]["Goal_Velocity"], 0)

    def test_stall_below_limit_is_stalled(self):
        self._turning_until(2.0, load_after=30)
        result, _ = drive(self.bus, self.clock)
        self.assertEqual(result.outcome, STALLED)

    def test_no_stall_during_spin_up_grace(self):
        self._turning_until(0.0, load_after=140)  # "stalled" from t=0
        result, _ = drive(self.bus, self.clock)
        self.assertGreaterEqual(result.elapsed_s, SETTINGS.grace_s + SETTINGS.stall_time_s - 0.01)

    def test_over_temperature_aborts(self):
        self.bus.dynamic["Present_Velocity"] = lambda m: 600
        self.bus.dynamic["Present_Temperature"] = lambda m: 30 if self.clock.now < 1.0 else 61
        result, _ = drive(self.bus, self.clock)
        self.assertEqual(result.outcome, OVER_TEMPERATURE)
        self.assertAlmostEqual(result.elapsed_s, 1.0, delta=0.05)
        self.assertEqual(self.bus.regs["gripper"]["Goal_Velocity"], 0)

    def test_speed_zeroed_on_error(self):
        def broken(m):
            raise ConnectionError("cable pulled")
        self.bus.dynamic["Present_Load"] = broken
        with self.assertRaises(ConnectionError):
            drive(self.bus, self.clock)
        self.assertEqual(self.bus.writes[-1], ("Goal_Velocity", "gripper", 0))

    def test_log_rows_have_every_field(self):
        self.bus.dynamic["Present_Velocity"] = lambda m: 600
        _, rows = drive(self.bus, self.clock)
        self.assertEqual(set(rows[0]), {"t_s", "load", "current", "temperature_c", "position", "velocity", "status"})

    def test_servo_protection_alarm_stops_the_drive(self):
        self.bus.dynamic["Present_Velocity"] = lambda m: 600
        self.bus.dynamic["Status"] = lambda m: 0x20 if self.clock.now >= 1.0 else 0  # overload bit
        result, rows = drive(self.bus, self.clock)
        self.assertEqual(result.outcome, PROTECTION_TRIPPED)
        self.assertAlmostEqual(result.elapsed_s, 1.0, delta=0.05)
        self.assertEqual(rows[-1]["status"], 0x20)
        self.assertEqual(self.bus.regs["gripper"]["Goal_Velocity"], 0)


class TwistLimitTest(unittest.TestCase):
    def test_default_is_provisional_cap(self):
        self.assertEqual(choose_twist_limit(None, None, None, CFG.limits, CFG.ceilings), 150)

    def test_raw_above_provisional_cap_refused_without_calibration(self):
        with self.assertRaisesRegex(SafetyError, "provisional cap"):
            choose_twist_limit(None, 200, None, CFG.limits, CFG.ceilings)

    def test_newton_metres_need_calibration(self):
        with self.assertRaisesRegex(SafetyError, "calibration"):
            choose_twist_limit(0.2, None, None, CFG.limits, CFG.ceilings)

    def test_with_calibration_course_ceiling_applies(self):
        cal = fit_calibration("gripper", [CalPoint(0, 100, 10), CalPoint(100, 100, 110), CalPoint(200, 100, 210)])
        self.assertEqual(choose_twist_limit(None, 300, cal, CFG.limits, CFG.ceilings), 300)
        with self.assertRaisesRegex(SafetyError, "course ceiling"):
            choose_twist_limit(None, 501, cal, CFG.limits, CFG.ceilings)

    def test_twist_limit_never_above_max_torque_limit(self):
        cal = fit_calibration("gripper", [CalPoint(0, 100, 10), CalPoint(100, 100, 110), CalPoint(200, 100, 210)])
        limits = replace(CFG.limits, max_torque_limit=300)
        with self.assertRaisesRegex(SafetyError, "Max_Torque_Limit"):
            choose_twist_limit(None, 400, cal, limits, CFG.ceilings)

    def test_newton_metres_converted_with_calibration(self):
        cal = fit_calibration("gripper", [CalPoint(0, 100, 10), CalPoint(100, 100, 110), CalPoint(200, 100, 210)])
        raw = choose_twist_limit(cal.to_nm(250), None, cal, CFG.limits, CFG.ceilings)
        self.assertIn(raw, (249, 250))  # rounded down, never up


class MoveTest(unittest.TestCase):
    def test_move_arrives(self):
        bus = connected_bus({"wrist_flex": 4})
        bus.move_per_read = 50
        bus.enable_torque("wrist_flex")
        clock = FakeClock()
        self.assertTrue(move_to(bus, "wrist_flex", 2248, tolerance=15, timeout_s=10, max_temperature_c=60,
                                clock=clock, sleep=clock.sleep))

    def test_move_times_out(self):
        bus = connected_bus({"wrist_flex": 4})  # move_per_read = 0: the joint is stuck
        bus.enable_torque("wrist_flex")
        clock = FakeClock()
        self.assertFalse(move_to(bus, "wrist_flex", 2248, tolerance=15, timeout_s=2, max_temperature_c=60,
                                 clock=clock, sleep=clock.sleep))
        self.assertGreaterEqual(clock.now, 2)

    def test_move_aborts_when_hot(self):
        bus = connected_bus({"wrist_flex": 4})
        bus.regs["wrist_flex"]["Present_Temperature"] = 65
        clock = FakeClock()
        with self.assertRaises(OverTemperatureError):
            move_to(bus, "wrist_flex", 2248, tolerance=15, timeout_s=2, max_temperature_c=60,
                    clock=clock, sleep=clock.sleep)


if __name__ == "__main__":
    unittest.main()
