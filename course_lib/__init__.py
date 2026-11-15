"""Shared code for the INF 385T scripts: config, servo bus, safety checks, logging, calibration.

Nothing here imports LeRobot when it is imported, so the tests run without LeRobot installed.
"""

import sys

# Checked here, before any other course_lib module loads, because those modules use syntax that
# crashes older Pythons with a confusing traceback. Plain string formatting on purpose.
if sys.version_info < (3, 12):
    sys.exit(
        "This is Python %d.%d; the course needs Python 3.12. Activate the course environment "
        "(Week 6 setup video), then run the same command again." % sys.version_info[:2]
    )
