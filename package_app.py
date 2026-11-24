"""Build the macOS application bundle or single-file Windows desktop application."""

import argparse
import os
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--mac-identity", help="给应用和内嵌二进制签名的 Developer ID Application 身份")
args = parser.parse_args()
mac = sys.platform == "darwin"
options = ["--onedir", "--windowed", "--osx-bundle-identifier", "io.github.skk0.boardbench",
           "--icon", "desktop-assets/BoardBench.icns"] if mac else ["--onefile"]
if args.mac_identity:
    if not mac:
        parser.error("--mac-identity 仅适用于 macOS")
    options += ["--codesign-identity", args.mac_identity, "--clean"]
if sys.platform == "win32":
    options += ["--windowed", "--icon", "desktop-assets/BoardBench.ico"]

subprocess.run([
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--name", "BoardBench",
    *options, "--add-data", "web" + os.pathsep + "web",
    "--collect-all", "esptool", "--collect-submodules", "serial.tools",
    "--collect-all", "keyring", "app.py",
], check=True)
if mac and not args.mac_identity:
    # Preserve the bundle's symlinks, executable bits and signature during distribution.
    subprocess.run(["ditto", "-c", "-k", "--keepParent", "dist/BoardBench.app",
                    "dist/BoardBench-macOS-arm64.zip" if os.uname().machine == "arm64"
                    else "dist/BoardBench-macOS-x64.zip"], check=True)
