"""Build the single-file app for the current operating system."""

import os
import subprocess
import sys

subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name",
        "BoardBench",
        "--add-data",
        "web" + os.pathsep + "web",
        "--collect-all",
        "esptool",
        "--collect-submodules",
        "serial.tools",
        "app.py",
    ],
    check=True,
)
