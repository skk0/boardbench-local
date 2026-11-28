"""Build the macOS application bundle or single-file Windows desktop application."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
ICONS = ROOT / "desktop-assets"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--mac-identity", help="给应用和内嵌二进制签名的 Developer ID Application 身份")
args = parser.parse_args()
mac = sys.platform == "darwin"
env = os.environ.copy()
env.pop("BOARDBENCH_BUILD_IDENTITY", None)
options = []
if args.mac_identity:
    if not mac:
        parser.error("--mac-identity 仅适用于 macOS")
    env["BOARDBENCH_BUILD_IDENTITY"] = args.mac_identity
    options.append("--clean")

subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", *options, ICONS / "BoardBench.spec"],
               cwd=ROOT, env=env, check=True)
if mac and not args.mac_identity:
    # Preserve the bundle's symlinks, executable bits and signature during distribution.
    subprocess.run(["ditto", "-c", "-k", "--keepParent", "dist/BoardBench.app",
                    "dist/BoardBench-macOS-arm64.zip" if os.uname().machine == "arm64"
                    else "dist/BoardBench-macOS-x64.zip"], cwd=ROOT, check=True)
