# INF 385T Human-Centered Robotics Prototyping: Course Repository

Spring 2027 · UT Austin School of Information · Mondays 6:00–9:00 pm

This repository holds the code you use with your SO-101 follower arm. It has the servo check, load calibration, screw-test and commissioning scripts and the course configuration file. The strike scripts come later in the term.

You don't need to write this code from scratch. You run it, change named parameters (often with Claude Code's help), and explain what your changes make the hardware do.

## Before Sun Feb 21: set up your environment

1. **Install Git** and sign in to GitHub. You made your GitHub account in Week 3.
2. **Clone this repository:**

   ```bash
   git clone https://github.com/BrainDumpster/RoboticPrototyping.git
   cd RoboticPrototyping
   ```

3. **Create the course Python environment.** Follow the setup video in the Week 6 module: it creates a Python 3.12 environment and activates it. Then, in the `RoboticPrototyping` folder, install the pinned packages:

   ```bash
   pip install -r requirements.txt
   ```

   If `python` is not found, or the check below says Python 3.9 or 3.11, your course environment is not active. Activate it and run the command again. **Intel Mac:** talk to the TA before installing (PyTorch has no package for it).
4. **Check the install:**

   ```bash
   python scripts/check_install.py
   ```

   You should see `install OK`. If you don't, post the full error to the TA. Do not paste it into an AI tool with anyone else's data in it.

5. **Do not power any servo yet.** The servo check script runs for the first time in class on Mon Feb 22, after the powered-screw safety check.
6. Bring an orange to class on Mon Feb 22 and place it on the table at the front of the room.

## Find your adapter's port (Mon Feb 22)

Every script that talks to a servo needs `--port`.

1. Plug in the adapter's USB cable and run `lerobot-find-port`.
2. When it asks, unplug the USB cable and press Enter.
3. Copy the port it prints. On a Mac it looks like `/dev/tty.usbmodem58760431551`; on Windows it looks like `COM3`.
4. Add it to every command, for example `python scripts/servo_check.py --port COM3`.

Pass `--port` on the command line rather than editing `config/course_config.yaml`: a changed config file makes `git pull` fail.

## What's in here

| Path | What it is | When you use it |
|---|---|---|
| `requirements.txt` | Pinned package versions for the course environment (Python 3.12) | Before Feb 21 |
| `scripts/check_install.py` | Confirms your Python environment | Before Feb 21 |
| `scripts/servo_check.py` | Reads position, load, temperature, voltage and limits from one servo, torque off | Feb 22, in class |
| `scripts/calibrate_load.py` | Converts the servo's load reading to N·m using known masses on a lever; saves `calibration/load_calibration_<servo>.json` | Feb 22, in class |
| `scripts/screw_test.py` | Sets the torque limit, drives the screw on a drive stand and logs the screw test to `logs/` | Feb 22 – Mar 8 |
| `config/course_config.yaml` | Speed cap, torque and current limits | All term. Never edit the safety limits. |
| `scripts/commission.py` | Commissioning checklist, limit read-back and one slow test move. Run `lerobot-setup-motors` and `lerobot-calibrate` first | Mar 22 – 25 |
| `course_lib/` | Shared code the scripts use: config checks, safety checks, logging, calibration | You don't run it directly |
| `tests/` | Tests that run without hardware: `python -m unittest discover -s tests` | Optional |

Scripts and settings are added or updated before the week you need them. Before each session, run `git pull` in the `RoboticPrototyping` folder.

## Safety rules that apply to everything here

- Never raise the torque, current or speed limits in `course_config.yaml`.
- Read the limit values back before every powered run. The scripts print them.
- Keep the inline STOP switch within reach whenever the supply is plugged in.
- Never drive screws with your servo at home. Screw testing happens only on department drive stands.

## Working with Claude Code

Claude Code reads this README and the scripts when you ask it for help. You are still responsible for every change it makes:
- read the diff before you run anything;
- never let an agent change a safety limit;
- never run a command you don't understand on powered hardware.

## Getting help

Ask me or the TA. Contact details are in the syllabus. Help-session times are posted in Canvas.
