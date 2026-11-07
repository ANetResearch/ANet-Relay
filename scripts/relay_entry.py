"""Backend entry used by the desktop shell (development and the frozen build).

Runs the `relay` package exactly like `python -m relay`. When RELAY_LIFELINE=stdin
is set (the Electron main process sets it), a daemon thread watches stdin: the
shell keeps the pipe open for its whole life, so EOF means the shell is gone
(crash, SIGKILL, lost display) and the backend shuts down instead of lingering
on the HTTP port and UDP 14550.
"""

from __future__ import annotations

import os
import runpy
import signal
import sys
import threading
import time

GRACE_S = 3.0


def _watch_stdin() -> None:
    try:
        while sys.stdin.buffer.read(4096):
            pass
    except (OSError, ValueError):
        pass
    sys.stderr.write("relay_entry: shell went away (stdin closed); shutting down\n")
    sys.stderr.flush()
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except OSError:
        pass
    time.sleep(GRACE_S)
    os._exit(0)


def main() -> None:
    if not getattr(sys, "frozen", False):
        # Development: import the package from backend/ like `python -m relay` would.
        backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
        sys.path[0:1] = [backend]
    if os.environ.get("RELAY_LIFELINE") == "stdin" and sys.stdin is not None:
        threading.Thread(target=_watch_stdin, name="relay-lifeline", daemon=True).start()
    runpy.run_module("relay", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
