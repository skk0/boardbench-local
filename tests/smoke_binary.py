"""Smoke test the packaged desktop distribution, including its bundled esptool worker."""

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

binary = str(
    Path("dist", "BoardBench.exe" if os.name == "nt" else "BoardBench").resolve()
)
subprocess.run([binary, "--esp-worker", "version"], check=True, timeout=60)
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
with tempfile.TemporaryDirectory() as temp:
    proc = subprocess.Popen(
        [binary, "--port", str(port), "--data-dir", temp, "--no-browser"]
    )
    try:
        for i in range(100):
            if proc.poll() is not None:
                raise RuntimeError(f"launcher exited: {proc.returncode}")
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1) as r:
                    assert json.load(r)["connected"] is False
                with urlopen(
                    f"http://127.0.0.1:{port}/static/panel.js", timeout=2
                ) as r:
                    assert b"async function" in r.read()
                with urlopen(
                    f"http://127.0.0.1:{port}/api/ai/settings", timeout=2
                ) as r:
                    assert json.load(r)["has_key"] is False
                with urlopen(
                    f"http://127.0.0.1:{port}/static/analysis.js", timeout=2
                ) as r:
                    assert b"loadRunResults" in r.read()
                break
            except OSError:
                time.sleep(0.3)
        else:
            raise RuntimeError("packaged web UI did not start")
    finally:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True
            )
        else:
            proc.terminate()
        proc.wait(timeout=20)
