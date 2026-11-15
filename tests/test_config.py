import importlib.util
import unittest

from helpers import sample_config

from course_lib.config import (
    DEFAULT_CONFIG_PATH, INSTRUCTOR_CEILINGS, PROVISIONAL_TORQUE_LIMIT, ConfigError, load_config, parse_config,
)

NAN = float("nan")
# Largest value each register can hold (Feetech STS3215 datasheet ranges).
REGISTER_MAX = {"torque_limit": 1000, "max_torque_limit": 1000, "protection_current": 2047, "overload_torque": 100,
                "protective_torque": 100, "protection_time": 254, "max_temperature_c": 100, "wheel_speed": 32767,
                "move_speed": 32767, "move_steps": 4095, "acceleration": 254}
from course_lib.safety import SafetyError, check_requested, clamp


class ConfigValidationTest(unittest.TestCase):
    def test_sample_config_is_valid(self):
        cfg = parse_config(sample_config())
        self.assertEqual(cfg.limits.torque_limit, 150)
        self.assertEqual(cfg.motor_ids["wrist_flex"], 4)

    def test_limit_above_ceiling_is_refused(self):
        raw = sample_config()
        raw["limits"]["torque_limit"] = 600
        with self.assertRaisesRegex(ConfigError, "above its ceiling"):
            parse_config(raw)

    def test_temperature_ceiling_cannot_pass_syllabus_rule(self):
        raw = sample_config()
        raw["ceilings"]["max_temperature_c"] = 70
        with self.assertRaisesRegex(ConfigError, "60"):
            parse_config(raw)

    def test_runtime_torque_cannot_exceed_eeprom_max(self):
        raw = sample_config()
        raw["limits"]["max_torque_limit"] = 100
        with self.assertRaises(ConfigError):
            parse_config(raw)

    def test_missing_section_and_bad_values(self):
        for change in (
            lambda r: r.pop("limits"),
            lambda r: r["limits"].update(acceleration=0),        # 0 means "no ramp": never allowed
            lambda r: r["limits"].update(wheel_speed="fast"),
            lambda r: r["limits"].update(move_speed=True),
            lambda r: r["hardware"]["motor_ids"].update(gripper=5),  # duplicate ID
            lambda r: r["hardware"]["motor_ids"].pop("gripper"),
            lambda r: r["voltage"].update(min_v=8.5),
        ):
            raw = sample_config()
            change(raw)
            with self.assertRaises(ConfigError):
                parse_config(raw)

    def test_not_a_number_refused(self):
        """NaN passes every comparison, so it would switch off the drive time limit or the 60 deg C abort."""
        for section, key in (("limits", "drive_time_s"), ("limits", "max_temperature_c"),
                             ("ceilings", "drive_time_s"), ("screw", "stall_time_s"), ("voltage", "max_v")):
            raw = sample_config()
            raw[section][key] = NAN
            with self.subTest(key=f"{section}.{key}"), self.assertRaisesRegex(ConfigError, "finite"):
                parse_config(raw)
        raw = sample_config()
        raw["limits"]["drive_time_s"] = float("inf")
        with self.assertRaises(ConfigError):
            parse_config(raw)

    def test_fractional_register_values_refused(self):
        """int(0.5) is 0, and Acceleration or Goal_Velocity 0 means no ramp / full speed."""
        for section, key, value in (("limits", "acceleration", 0.5), ("limits", "move_speed", 0.4),
                                    ("limits", "torque_limit", 149.5), ("screw", "stall_velocity", 0.5)):
            raw = sample_config()
            raw[section][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ConfigError, "whole number"):
                parse_config(raw)
        raw = sample_config()
        raw["position_tolerance"] = 0.5
        with self.assertRaisesRegex(ConfigError, "whole number"):
            parse_config(raw)

    def test_ceilings_cannot_be_raised_in_the_yaml(self):
        raw = sample_config()
        raw["ceilings"]["torque_limit"] = 5000
        raw["limits"]["max_torque_limit"] = 1000
        with self.assertRaisesRegex(ConfigError, "instructor's ceiling"):
            parse_config(raw)
        for key, hard in INSTRUCTOR_CEILINGS.items():
            raw = sample_config()
            raw["ceilings"][key] = hard + 1
            with self.subTest(key=key), self.assertRaises(ConfigError):
                parse_config(raw)

    def test_ceilings_can_be_lowered(self):
        raw = sample_config()
        raw["ceilings"]["wheel_speed"] = 1500
        self.assertEqual(parse_config(raw).ceilings.wheel_speed, 1500)

    def test_provisional_cap_cannot_be_raised(self):
        raw = sample_config()
        raw["limits"]["torque_limit"] = PROVISIONAL_TORQUE_LIMIT + 1  # still under ceilings.torque_limit
        with self.assertRaisesRegex(ConfigError, "provisional cap"):
            parse_config(raw)

    def test_instructor_ceilings_fit_the_registers(self):
        for key, register_max in REGISTER_MAX.items():
            self.assertLessEqual(INSTRUCTOR_CEILINGS[key], register_max, key)
        self.assertLessEqual(INSTRUCTOR_CEILINGS["max_temperature_c"], 60)

    def test_screw_loop_tuning_bounds(self):
        raw = sample_config()
        raw["screw"]["sample_period_s"] = 30  # would sleep 30 s between safety checks
        with self.assertRaisesRegex(ConfigError, "sample_period_s"):
            parse_config(raw)
        raw = sample_config()
        raw["screw"]["grace_s"] = 60  # stall detection would never run
        with self.assertRaisesRegex(ConfigError, "grace_s"):
            parse_config(raw)

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML not installed")
    def test_shipped_config_file_is_valid(self):
        cfg = load_config(DEFAULT_CONFIG_PATH)
        self.assertLessEqual(cfg.limits.max_temperature_c, 60)


class RequestedValueTest(unittest.TestCase):
    def test_value_above_ceiling_is_refused(self):
        with self.assertRaisesRegex(SafetyError, "ceiling"):
            check_requested("--speed", 3000, 2400)

    def test_value_at_or_below_ceiling_passes(self):
        self.assertEqual(check_requested("--speed", 2400, 2400), 2400)

    def test_zero_or_negative_is_refused(self):
        with self.assertRaises(SafetyError):
            check_requested("--speed", 0, 2400)

    def test_not_a_number_is_refused(self):
        # `--time-limit nan` would otherwise pass and the drive would never time out.
        for value in (NAN, float("inf")):
            with self.assertRaisesRegex(SafetyError, "finite"):
                check_requested("--time-limit", value, 90)

    def test_clamp(self):
        self.assertEqual(clamp(5000, 0, 4095), 4095)
        self.assertEqual(clamp(-10, 0, 4095), 0)
        self.assertEqual(clamp(100, 0, 4095), 100)


if __name__ == "__main__":
    unittest.main()
