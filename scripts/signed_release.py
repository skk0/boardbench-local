"""Build and validate one signed desktop archive on the publisher's machine."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"请先配置 {name}，见 docs/distribution.md。")
    return value


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="新的发行标签，如 v0.5.1")
    version = parser.parse_args().version
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        parser.error("发行标签须使用 v主版本.次版本.修订号")
    sys.path.insert(0, str(ROOT))
    from boardbench.version import VERSION
    if version != f"v{VERSION}":
        parser.error(f"发布标签须与桌面版本 v{VERSION} 一致")
    mac = sys.platform == "darwin"
    if mac:
        identity = required("BOARDBENCH_MAC_IDENTITY")
        profile = required("BOARDBENCH_NOTARY_PROFILE")
        identities = subprocess.check_output(["security", "find-identity", "-v", "-p", "codesigning"], text=True)
        if not identity.startswith("Developer ID Application: ") or f'"{identity}"' not in identities:
            raise SystemExit("钥匙串中找不到所选 Developer ID Application 身份及其私钥。")
        arch = {"arm64": "arm64", "x86_64": "x64"}[os.uname().machine]
        platform_name = "macOS-" + arch
    elif sys.platform == "win32":
        thumbprint = required("BOARDBENCH_WIN_CERT_SHA1").replace(" ", "")
        if not re.fullmatch(r"[A-Fa-f0-9]{40}", thumbprint):
            raise SystemExit("请填写 Windows 签名证书的 40 位指纹。")
        timestamp_url = required("BOARDBENCH_TIMESTAMP_URL")
        signtool = shutil.which("signtool.exe")
        if not signtool:
            raise SystemExit("请在装有 Windows SDK、可运行 signtool.exe 的开发者终端中执行。")
        if sys.version_info[:2] != (3, 12) or sysconfig.get_platform() != "win-amd64":
            raise SystemExit("Windows 发布包使用 x64 Python 3.12。")
        platform_name = "Windows-x64"
    else:
        raise SystemExit("请在目标 macOS 或 Windows 发布机执行。")

    parent = ROOT / "dist" / "signed" / version
    final = parent / platform_name
    if final.exists():
        raise SystemExit(f"产物已存在，请先核对 {final}")
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as work:
        tmp = Path(work)
        output = tmp / "verified"
        output.mkdir()
        archive = output / f"BoardBench-{platform_name}.zip"
        build = [sys.executable, "package_app.py"]
        if mac:
            run(*build, "--mac-identity", identity)
            app = ROOT / "dist" / "BoardBench.app"
            run("codesign", "--verify", "--deep", "--strict", "--verbose=2", app)
            submission = tmp / "submit.zip"
            run("ditto", "-c", "-k", "--keepParent", app, submission)
            raw = subprocess.check_output(["xcrun", "notarytool", "submit", submission,
                                          "--keychain-profile", profile, "--wait", "--output-format", "json"], text=True)
            receipt = json.loads(raw)
            print("Apple 公证结果", receipt.get("status"), receipt.get("id"), flush=True)
            if receipt.get("status") != "Accepted":
                raise SystemExit("Apple 未接受提交，可使用提交编号查询 notarytool log。")
            run("xcrun", "stapler", "staple", app)
            run("xcrun", "stapler", "validate", app)
            run("spctl", "--assess", "--type", "execute", "--verbose=4", app)
            run(sys.executable, "tests/smoke_binary.py")
            run("ditto", "-c", "-k", "--keepParent", app, archive)
        else:
            run(*build)
            exe = ROOT / "dist" / "BoardBench.exe"
            run(signtool, "sign", "/s", "My", "/sha1", thumbprint, "/fd", "SHA256",
                "/tr", timestamp_url, "/td", "SHA256", exe)
            run(signtool, "verify", "/pa", "/all", "/tw", "/v", exe)
            run(sys.executable, "tests/smoke_binary.py")
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as packaged:
                packaged.write(exe, exe.name)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (output / (archive.name + ".sha256")).write_text(f"{digest}  {archive.name}\n", encoding="ascii")
        output.rename(final)
        print("已验证的发布目录", final)


if __name__ == "__main__":
    main()
