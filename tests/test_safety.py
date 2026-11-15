import contextlib
import io
import unittest

from helpers import connected_bus, sample_config

from course_lib.config import parse_config
from course_lib.safety import (
    UNLOADING_CONDITION, OverTemperatureError, SafetyError, TorqueGuard, apply_protection_settings,
    check_temperature, check_voltage, confirm_go, describe_status, enable_torque_checked, set_mode_verified,
    write_verified,
)

LIMITS = parse_config(sample_config()).limits


class ReadBackTest(unittest.TestCase):
    def test_write_verified_passes_when_servo_accepts(self):
        bus = connected_bus()
        write_verified(bus, "Torque_Limit", "gripper", 150)
        self.assertEqual(bus.regs["gripper"]["Torque_Limit"], 150)

    def test_mismatch_raises(self):
        bus = connected_bus()
        bus.ignore_writes.add("Torque_Limit")
        with self.assertRaisesRegex(SafetyError, "read back 1000"):
            write_verified(bus, "Torque_Limit", "gripper", 150)

    def test_torque_never_enabled_when_limit_not_accepted(self):
        bus = connected_bus()
        bus.ignore_writes.add("Torque_Limit")
        with self.assertRaises(SafetyError):
            enable_torque_checked(bus, "gripper", 150)
        self.assertEqual(bus.regs["gripper"]["Torque_Enable"], 0)

    def test_enable_torque_checked_enables_after_read_back(self):
        bus = connected_bus()
        enable_torque_checked(bus, "gripper", 150)
        self.assertEqual(bus.regs["gripper"]["Torque_Enable"], 1)
        self.assertEqual(bus.regs["gripper"]["Torque_Limit"], 150)

    def test_protection_settings_written_with_torque_off(self):
        bus = connected_bus()
        bus.enable_torque("gripper")  # EEPROM locked; apply must unlock it by switching torque off
        applied = apply_protection_settings(bus, "gripper", LIMITS)
        self.assertEqual(applied["Max_Torque_Limit"], 500)
        self.assertEqual(bus.regs["gripper"]["Protection_Current"], 250)
        self.assertEqual(bus.regs["gripper"]["Max_Temperature_Limit"], 60)

    def test_servo_overload_protection_is_set_and_read_back(self):
        """The servo's own protection is the only software-independent stop if USB drops mid-drive."""
        bus = connected_bus()
        bus.regs["gripper"].update(Protective_Torque=90, Protection_Time=250, Unloading_Condition=0)
        applied = apply_protection_settings(bus, "gripper", LIMITS)
        regs = bus.regs["gripper"]
        self.assertEqual((regs["Protective_Torque"], regs["Protection_Time"]), (20, 200))
        self.assertEqual(regs["Unloading_Condition"], UNLOADING_CONDITION)
        self.assertEqual(applied["Unloading_Condition"], 44)

    def test_unloading_condition_refusal_is_caught(self):
        bus = connected_bus()
        bus.ignore_writes.add("Unloading_Condition")
        bus.regs["gripper"]["Unloading_Condition"] = 0
        with self.assertRaises(SafetyError):
            apply_protection_settings(bus, "gripper", LIMITS)

    def test_describe_status(self):
        self.assertEqual(describe_status(0), "none")
        self.assertEqual(describe_status(0x24), "overheat, overload")

    def test_eeprom_write_while_locked_is_caught(self):
        bus = connected_bus()
        bus.enable_torque("gripper")  # Lock = 1: the fake ignores EEPROM writes, like the real servo
        with self.assertRaises(SafetyError):
            write_verified(bus, "Protection_Current", "gripper", 250)

    def test_mode_change_is_read_back(self):
        bus = connected_bus()
        set_mode_verified(bus, "gripper", "velocity")
        self.assertEqual(bus.regs["gripper"]["Operating_Mode"], 1)


class ChecksTest(unittest.TestCase):
    def test_temperature_abort_above_limit(self):
        bus = connected_bus()
        bus.regs["gripper"]["Present_Temperature"] = 61
        with self.assertRaises(OverTemperatureError):
            check_temperature(bus, "gripper", 60)

    def test_temperature_at_limit_is_allowed(self):
        bus = connected_bus()
        bus.regs["gripper"]["Present_Temperature"] = 60
        self.assertEqual(check_temperature(bus, "gripper", 60), 60)

    def test_voltage_range(self):
        bus = connected_bus()
        self.assertAlmostEqual(check_voltage(bus, "gripper", 7.0, 8.0), 7.5)
        bus.regs["gripper"]["Present_Voltage"] = 120  # someone used a 12 V supply
        with self.assertRaises(SafetyError):
            check_voltage(bus, "gripper", 7.0, 8.0)

    def test_go_prompt(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self._check_go_answers()

    def _check_go_answers(self):
        self.assertTrue(confirm_go("msg", input_fn=lambda _: " go "))
        self.assertFalse(confirm_go("msg", input_fn=lambda _: "yes"))
        self.assertFalse(confirm_go("msg", input_fn=lambda _: ""))


class TorqueGuardTest(unittest.TestCase):
    def _powered_wheel_bus(self):
        bus = connected_bus()
        set_mode_verified(bus, "gripper", "velocity")
        bus.enable_torque("gripper")
        return bus

    def test_torque_off_and_position_mode_after_exception(self):
        bus = self._powered_wheel_bus()
        with self.assertRaises(ValueError):
            with TorqueGuard(bus, ["gripper"], restore_position_mode=True):
                raise ValueError("boom")
        self.assertEqual(bus.regs["gripper"]["Torque_Enable"], 0)
        self.assertEqual(bus.regs["gripper"]["Operating_Mode"], 0)
        self.assertFalse(bus.is_connected)

    def test_torque_off_after_ctrl_c(self):
        bus = self._powered_wheel_bus()
        with self.assertRaises(KeyboardInterrupt):
            with TorqueGuard(bus, ["gripper"], restore_position_mode=True):
                raise KeyboardInterrupt
        self.assertEqual(bus.regs["gripper"]["Torque_Enable"], 0)
        self.assertEqual(bus.regs["gripper"]["Operating_Mode"], 0)

    def test_torque_off_after_normal_exit(self):
        bus = self._powered_wheel_bus()
        with TorqueGuard(bus, ["gripper"]):
            pass
        self.assertEqual(bus.regs["gripper"]["Torque_Enable"], 0)
        self.assertFalse(bus.is_connected)

    def test_lost_servo_gets_the_stop_switch_message(self):
        bus = self._powered_wheel_bus()
        bus.missing.add("gripper")  # STOP pressed: the servo no longer answers
        out = io.StringIO()
        with self.assertRaises(ConnectionError), contextlib.redirect_stdout(out):
            with TorqueGuard(bus, ["gripper"]):
                raise ConnectionError("no status packet")
        self.assertIn("If you pressed STOP", out.getvalue())
        self.assertFalse(bus.is_connected)

    def test_failed_shutdown_step_does_not_skip_the_rest(self):
        bus = self._powered_wheel_bus()
        bus.ignore_writes.add("Operating_Mode")  # position mode cannot be restored
        with self.assertRaisesRegex(SafetyError, "position mode"), contextlib.redirect_stdout(io.StringIO()):
            with TorqueGuard(bus, ["gripper"], restore_position_mode=True):
                pass
        self.assertEqual(bus.regs["gripper"]["Torque_Enable"], 0)
        self.assertFalse(bus.is_connected)


if __name__ == "__main__":
    unittest.main()
