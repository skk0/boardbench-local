# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from boardbench.version import VERSION

mac = sys.platform == "darwin"
assets = root / "desktop-assets"
helper = assets / ("install-update.sh" if mac else "install-update.ps1")
datas = [(str(root / "web"), "web"), (str(helper), "desktop-assets")]
binaries = []
hidden = collect_submodules("serial.tools")
for package in ("esptool", "keyring"):
    data, libs, modules = collect_all(package)
    datas.extend(data)
    binaries.extend(libs)
    hidden.extend(modules)

a = Analysis([str(root / "app.py")], pathex=[str(root)], binaries=binaries, datas=datas, hiddenimports=hidden)
identity = os.environ.get("BOARDBENCH_BUILD_IDENTITY") if mac else None
icon = str(assets / ("BoardBench.icns" if mac else "BoardBench.ico"))
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, *([] if mac else [a.binaries, a.datas]), [],
          name="BoardBench", exclude_binaries=mac, console=False, icon=icon, codesign_identity=identity)
if mac:
    coll = COLLECT(exe, a.binaries, a.datas, name="BoardBench")
    app = BUNDLE(coll, name="BoardBench.app", icon=icon, version=VERSION,
                 bundle_identifier="io.github.skk0.boardbench", info_plist={"CFBundleVersion": VERSION})
