"""Keep the Photos app out of the way while a script drives it (macOS only).

Adding to an album has to go through Photos itself (AppleScript), which
launches the app and usually brings it to the front. launch_hidden() starts it
in the background without taking focus, and hide() puts the window away again.
Neither can fully stop macOS from raising a window, so this is best effort.
Both do nothing off macOS or if the calls fail."""
import subprocess
import sys


def _run(cmd: list[str]) -> None:
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(cmd, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def launch_hidden() -> None:
    _run(["open", "-g", "-j", "-a", "Photos"])


def hide() -> None:
    _run(["osascript", "-e",
          'tell application "System Events" to set visible of process "Photos" to false'])
