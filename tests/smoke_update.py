"""Exercise the shipped update helper on an isolated desktop install and data directory."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from boardbench.updates import Updater, VERSION, prepare


def exercise():
    mac = sys.platform == "darwin"
    name = "BoardBench.app" if mac else "BoardBench.exe"
    with tempfile.TemporaryDirectory(prefix="BoardBench update QA ") as tmp:
        directory = Path(tmp).resolve()
        data = directory / "data"
        data.mkdir()
        target = directory / name
        stage = Path(tempfile.mkdtemp(prefix=".BoardBench-update-", dir=directory))
        if mac:
            subprocess.run(["ditto", ROOT / "dist" / name, target], check=True)
            archive = next((ROOT / "dist").glob("BoardBench-macOS-*.zip"))
        else:
            import zipfile
            shutil.copy2(ROOT / "dist" / name, target)
            archive = directory / "update.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(target, name)
        prepare(archive, stage, target, mac)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        updater = Updater(target, "macOS-arm64" if mac else "Windows-x64")
        updater.stage, updater.phase = stage, "ready"
        old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"])
        with patch("boardbench.updates.os.getpid", return_value=old.pid):
            updater.handoff(port, data)
        old.wait()
        try:
            for _ in range(120):
                if not stage.exists():
                    break
                time.sleep(1)
            else:
                log = data / "update-install.log"
                raise RuntimeError(log.read_text(encoding="utf-8", errors="replace") if log.exists() else "helper never launched")
            with urlopen(f"http://127.0.0.1:{port}/api/setup", timeout=3) as response:
                assert json.load(response)["desktop_update"]["version"] == VERSION
        finally:
            # Match only the executable in our unique temporary installation.
            if mac:
                subprocess.run(["pkill", "-f", str(target / "Contents/MacOS/BoardBench")], check=False)
            else:
                subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-Process BoardBench -ErrorAction SilentlyContinue | "
                                "Where-Object { $_.Path -eq $env:BB_QA_BINARY } | Stop-Process -Force"],
                               env={**os.environ, "BB_QA_BINARY": str(target)}, check=False)
            time.sleep(1)


if __name__ == "__main__":
    exercise()
