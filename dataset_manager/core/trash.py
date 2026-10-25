"""Move files to the desktop Trash (freedesktop spec) without Qt."""
from __future__ import annotations

import shutil
import subprocess


def move_to_trash(path: str) -> bool:
    for cmd in (["gio", "trash", path], ["trash-put", path], ["kioclient6", "move", path, "trash:/"],
                ["kioclient5", "move", path, "trash:/"]):
        if shutil.which(cmd[0]):
            try:
                if subprocess.run(cmd, capture_output=True, timeout=30).returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False
