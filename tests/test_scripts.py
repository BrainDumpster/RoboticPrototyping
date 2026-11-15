"""Script-level safety behaviour, run against the FakeBus (no hardware, no LeRobot)."""

import argparse
import contextlib
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import FakeClock, connected_bus, load_script, sample_config

from course_lib.cli import run
from course_lib.config import parse_config
from course_lib.csvlog import CsvLog
from course_lib.safety import OverTemperatureError, SafetyError

CFG = parse_config(sample_config())
commission = load_script("commission")
screw_test = load_script("screw_test")
calibrate_load = load_script("calibrate_load")


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


def arm_bus():
    bus = connected_bus(dict(CFG.motor_ids))
    bus.move_per_read = 50
    return bus


class CommissionTest(unittest.TestCase):
    def test_missing_motor_prints_checklist_without_crashing(self):
        """The common March failure: a servo whose ID was never set does not answer."""
        bus = arm_bus()
        bus.missing.add("elbow_flex")
        for motor in ("shoulder_pan", "gripper"):
            bus.regs[motor]["Torque_Enable"] = 1
        checklist = commission.Checklist()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            commission.run_checklist(bus, CFG, checklist)
            checklist.print_final()
        self.assertFalse(checklist.all_passed)
        self.assertIn("elbow_flex (ID 3): no answer", out.getvalue())
        self.assertIn("Steps 2-4 not run", out.getvalue())
        self.assertIn("RESULT: FAIL", out.getvalue())
        self.assertTrue(all(bus.regs[m]["Torque_Enable"] == 0 for m in CFG.motor_ids if m != "elbow_flex"))
        self.assertFalse(bus.is_connected)
        self.assertNotIn("Goal_Position", {register for register, _, _ in bus.writes})

    def test_full_pass(self):
        bus = arm_bus()
        checklist = commission.Checklist()
        with quiet(), mock.patch("builtins.input", return_value="GO"):
            commission.run_checklist(bus, CFG, checklist)
        self.assertTrue(checklist.all_passed, checklist.items)
        self.assertTrue(all(bus.regs[m]["Torque_Enable"] == 0 for m in CFG.motor_ids))
        self.assertEqual(bus.regs["wrist_flex"]["Present_Position"], 2048)  # came back

    def _ready_wrist(self, position: int, low: int = 2000, high: int = 3000):
        bus = arm_bus()
        bus.regs["wrist_flex"].update(Present_Position=position, Min_Position_Limit=low, Max_Position_Limit=high)
        return bus

    def test_start_outside_limits_refused(self):
        """Clamping to the limits from outside them would turn 18 deg into a 176 deg move."""
        bus = self._ready_wrist(position=0)
        with quiet(), mock.patch("builtins.input", return_value="GO"):
            passed, text = commission._test_move(bus, CFG)
        self.assertFalse(passed)
        self.assertIn("outside its limits", text)
        self.assertEqual(bus.regs["wrist_flex"]["Torque_Enable"], 0)

    def test_one_time_limit_for_both_legs(self):
        bus = self._ready_wrist(position=2500)
        clock = FakeClock()
        timeouts = []

        def fake_move(bus_, motor, target, *, timeout_s, **kwargs):
            timeouts.append(timeout_s)
            clock.now += 50  # the first leg uses 50 of the 60 s
            return True

        with quiet(), mock.patch("builtins.input", return_value="GO"), \
                mock.patch.object(commission, "move_to", fake_move), \
                mock.patch.object(commission.time, "monotonic", clock):
            commission._test_move(bus, CFG)
        self.assertEqual(timeouts[0], CFG.limits.drive_time_s)
        self.assertAlmostEqual(timeouts[1], CFG.limits.drive_time_s - 50)

    def test_torque_off_when_move_raises(self):
        bus = self._ready_wrist(position=2500)

        def hot_move(*args, **kwargs):
            raise OverTemperatureError("hot")

        with quiet(), mock.patch("builtins.input", return_value="GO"), \
                mock.patch.object(commission, "move_to", hot_move), self.assertRaises(OverTemperatureError):
            commission._test_move(bus, CFG)
        self.assertEqual(bus.regs["wrist_flex"]["Torque_Enable"], 0)


class RunTest(unittest.TestCase):
    def _run(self, error: BaseException) -> tuple[int, str]:
        def body():
            raise error
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run(body)
        return code, out.getvalue()

    def test_lost_servo_is_a_message_not_a_traceback(self):
        code, out = self._run(ConnectionError("Failed to read 'Present_Load' from motor ID 1"))
        self.assertEqual(code, 1)
        self.assertIn("lost contact with the servo", out)
        self.assertIn("pressed STOP", out)

    def test_file_error_is_a_message(self):
        code, out = self._run(PermissionError("logs/ is read-only"))
        self.assertEqual(code, 1)
        self.assertIn("file or device error", out)

    def test_ctrl_c_message_points_at_warnings(self):
        code, out = self._run(KeyboardInterrupt())
        self.assertEqual(code, 130)
        self.assertIn("If a WARNING appeared above, press STOP", out)


class ScrewTestArgsTest(unittest.TestCase):
    """Argument checks that must refuse before anything is powered."""

    def _args(self, **overrides) -> argparse.Namespace:
        args = screw_test.build_parser().parse_args(["--drive-stand", "--port", "unused"])
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def _refused(self, **overrides) -> str:
        with mock.patch.object(screw_test, "load_config", return_value=CFG), \
                mock.patch.object(screw_test, "load_calibration", return_value=None), \
                mock.patch.object(screw_test, "connect_single_servo", side_effect=AssertionError("powered")), \
                self.assertRaises(SafetyError) as caught:
            screw_test.screw_test(self._args(**overrides))
        return str(caught.exception)

    def test_zero_speed_is_refused_not_replaced(self):
        self.assertIn("--reverse", self._refused(speed=0))

    def test_not_a_number_time_limit_is_refused(self):
        self.assertIn("finite", self._refused(time_limit=float("nan")))

    def test_missing_calibration_names_the_file(self):
        self.assertIn("load_calibration_gripper.json", self._refused(limit_nm=0.2))

    def test_without_drive_stand_flag_nothing_runs(self):
        with quiet(), mock.patch.object(screw_test, "run", side_effect=AssertionError("ran")):
            self.assertEqual(screw_test.main(["--port", "unused"]), 2)


class CalibrateLoadTest(unittest.TestCase):
    def test_ask_number_rejects_nan_and_inf(self):
        with quiet(), mock.patch("builtins.input", side_effect=["nan", "inf", "12.5"]):
            self.assertEqual(calibrate_load.ask_number("? "), 12.5)

    def test_samples_out_of_range_refused_before_power(self):
        for samples in (0, 10_000):
            args = calibrate_load.build_parser().parse_args(["--port", "unused", "--samples", str(samples)])
            with mock.patch.object(calibrate_load, "load_config", return_value=CFG), \
                    mock.patch.object(calibrate_load, "connect_single_servo", side_effect=AssertionError("powered")), \
                    self.assertRaises(ValueError):
                calibrate_load.calibrate(args)

    def test_hold_time_limit_while_waiting_at_the_keyboard(self):
        bus = connected_bus()
        args = calibrate_load.build_parser().parse_args(["--port", "unused"])
        deadline = time.monotonic() - 1  # the student took too long typing
        with quiet(), mock.patch("builtins.input", return_value="100"), \
                self.assertRaisesRegex(SafetyError, "Hold time"):
            calibrate_load.record_points(bus, "gripper", CFG, args, 2048, deadline, [])

    def test_cannot_finish_with_too_few_masses(self):
        bus = connected_bus()
        args = calibrate_load.build_parser().parse_args(["--port", "unused", "--samples", "1"])
        loads = iter([20, 70, 120])  # under 90 % of the 150 cap
        bus.dynamic["Present_Load"] = lambda m: next(loads)
        answers = ["", "0", "100", "GO", "", "100", "", "GO", "200", "", "GO", ""]
        points = []
        with quiet() as out, mock.patch("builtins.input", side_effect=answers), \
                mock.patch.object(calibrate_load, "save_points") as save_points:
            calibrate_load.record_points(bus, "gripper", CFG, args, 2048, time.monotonic() + 60, points)
        self.assertEqual(len(points), 3)
        self.assertIn("Need at least 3", out.getvalue())
        self.assertEqual(save_points.call_count, 3)  # saved after every mass, not only at the end


class CsvLogTest(unittest.TestCase):
    def test_path_printed_even_on_error(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(out):
            with self.assertRaises(KeyboardInterrupt):
                with CsvLog("screw_test_gripper", ["t_s"], Path(tmp)) as log:
                    log.write({"t_s": 0})
                    raise KeyboardInterrupt
        self.assertIn("Log saved to", out.getvalue())


if __name__ == "__main__":
    unittest.main()
