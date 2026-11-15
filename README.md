# INF 385T Human-Centered Robotics Prototyping: Course Repository

Spring 2027 · UT Austin School of Information · Mondays 6:00–9:00 pm

This repository holds the code you use with your SO-101 follower arm. It has the servo check script, the screw-test script, the course configuration file, and later the commissioning and strike scripts.

You don't need to write this code from scratch. You run it, change named parameters (often with Claude Code's help), and explain what your changes make the hardware do.

## Before Sun Feb 21: set up your environment

1. **Install Git** and sign in to GitHub. You made your GitHub account in Week 3.
2. **Clone this repository:**

   ```bash
   git clone https://github.com/BrainDumpster/RoboticPrototyping.git
   cd RoboticPrototyping
   ```

3. **Create the course Python environment.** Follow the setup video in the Week 6 module. The pinned versions are listed in `requirements.txt` (added before Feb 8).
4. **Check the install:**

   ```bash
   python scripts/check_install.py
   ```

   You should see `install OK`. If you don't, post the full error to the TA. Do not paste it into an AI tool with anyone else's data in it.

5. **Do not power any servo yet.** The servo check script runs for the first time in class on Mon Feb 22, after the powered-screw safety check.
6. Bring an orange to class on Mon Feb 22 and place it on the table at the front of the room.

## What's in here

| Path | What it is | When you use it |
|---|---|---|
| `scripts/check_install.py` | Confirms your Python environment | Before Feb 21 |
| `scripts/servo_check.py` | Reads position, load and temperature from one servo | Feb 22, in class |
| `scripts/screw_test.py` | Sets the torque limit and logs the screw test | Feb 22 – Mar 8 |
| `config/course_config.yaml` | Speed cap, torque and current limits | All term. Never edit the safety limits. |
| `scripts/commission.py` | Commissioning checklist and read-back | Mar 22 – 25 |

The scripts are added before the week you need them.

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

Ask Eric or the TA. Contact details are in the syllabus. Help-session times are posted in Canvas.
