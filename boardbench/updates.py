"""Update the packaged desktop application from its own stable GitHub Releases."""

import asyncio
import hashlib
import os
import platform
import plistlib
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import zipfile
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from pathlib import Path

import aiohttp
import certifi

from boardbench.version import VERSION
REPO = "skk0/boardbench-local"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
MAX_SIZE = 240 * 1024 * 1024
HELPERS = Path(__file__).resolve().parents[1] / "desktop-assets"


def installation():
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin" and exe.parts[-3:] == ("Contents", "MacOS", "BoardBench"):
        arch = {"arm64": "arm64", "x86_64": "x64"}.get(platform.machine())
        if arch:
            return exe.parents[2], f"macOS-{arch}"
    if sys.platform == "win32" and exe.suffix.lower() == ".exe" and sys.maxsize > 2**32:
        return exe, "Windows-x64"
    return None


def select_release(data, platform_name):
    tag = data.get("tag_name", "")
    if data.get("draft") or data.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("发布信息缺少有效的稳定版本号。")
    if tuple(map(int, tag[1:].split("."))) <= tuple(map(int, VERSION.split("."))):
        return None
    name = f"BoardBench-{platform_name}.zip"
    url = f"https://github.com/{REPO}/releases/download/{tag}/{name}"
    for item in data.get("assets", []):
        if item.get("name") == name and item.get("browser_download_url") == url:
            digest = item.get("digest") or ""
            size = item.get("size", 0)
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not 0 < size <= MAX_SIZE:
                raise ValueError("安装包缺少有效的 SHA-256 摘要或大小异常。")
            return dict(tag=tag, url=url, size=size, digest=digest[7:], notes=(data.get("body") or "")[:4000])
    raise ValueError(f"{tag} 尚未提供 {platform_name} 安装包。")


@asynccontextmanager
async def open_asset(client, url):
    hosts = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
    for _ in range(5):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.port:
            raise ValueError("安装包下载地址未通过验证。")
        async with client.get(url, allow_redirects=False) as response:
            if response.status in {301, 302, 303, 307, 308}:
                url = response.headers["Location"]
                continue
            response.raise_for_status()
            yield response
            return
    raise ValueError("安装包重定向次数异常。")


def prepare(archive, stage, target, mac):
    stage = stage.resolve()
    with zipfile.ZipFile(archive) as bundle:
        if sum(e.file_size for e in bundle.infolist()) > MAX_SIZE * 3:
            raise ValueError("安装包解压尺寸异常。")
        if not mac:
            if bundle.namelist() != ["BoardBench.exe"]:
                raise ValueError("Windows 安装包结构异常。")
            bundle.extract("BoardBench.exe", stage)
            exe = stage / "BoardBench.exe"
            with exe.open("rb") as stream:
                if stream.read(2) != b"MZ":
                    raise ValueError("安装包未包含 Windows 程序。")
            if sys.platform == "win32":
                shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
                verify = """
                    $ErrorActionPreference = 'Stop'
                    $old = Get-AuthenticodeSignature -LiteralPath $env:BB_TARGET
                    if ($old.SignerCertificate) {
                        $new = Get-AuthenticodeSignature -LiteralPath $env:BB_NEXT
                        if ($new.Status -ne 'Valid' -or
                            $new.SignerCertificate.Subject -ne $old.SignerCertificate.Subject) { exit 1 }
                    }
                """
                subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-Command", verify],
                               env={**os.environ, "BB_TARGET": str(target), "BB_NEXT": str(exe)},
                               creationflags=subprocess.CREATE_NO_WINDOW, check=True)
            return
        # ditto restores the bundle's framework symlinks and extended attributes.
        for entry in bundle.infolist():
            parts = Path(entry.filename).parts
            if not parts or parts[0] not in {"BoardBench.app", "__MACOSX"} or ".." in parts:
                raise ValueError("Mac 安装包结构异常。")
            if entry.external_attr >> 28 == 10:
                link = Path(bundle.read(entry).decode())
                dest = (stage / entry.filename).parent / link
                if link.is_absolute() or not dest.resolve().is_relative_to(stage / "BoardBench.app"):
                    raise ValueError("应用内链接超出范围。")
    subprocess.run(["/usr/bin/ditto", "-x", "-k", archive, stage], check=True)
    app = stage / "BoardBench.app"
    with (app / "Contents/Info.plist").open("rb") as stream:
        if plistlib.load(stream).get("CFBundleIdentifier") != "io.github.skk0.boardbench":
            raise ValueError("应用标识不匹配。")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app], check=True)
    info = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=4", target], capture_output=True)
    team = re.search(rb"^TeamIdentifier=([A-Z0-9]{10})$", info.stderr, re.M)
    if team:
        requirement = 'anchor apple generic and certificate leaf[subject.OU] = "' + team[1].decode() + '"'
        subprocess.run(["/usr/bin/codesign", "--verify", "-R", requirement, app], check=True)


class Updater:
    def __init__(self, target, platform_name):
        self.target, self.platform = target, platform_name
        self.phase = "idle"
        self.error = ""
        self.received = 0
        self.release = self.stage = self.task = None

    def state(self):
        rel = self.release or {}
        return dict(version=VERSION, phase=self.phase, latest=rel.get("tag"), notes=rel.get("notes", ""),
                    size=rel.get("size", 0), received=self.received, error=self.error)

    def start(self, action):
        allowed = {"check": ("idle", "current", "available", "error"), "download": ("available",)}
        if self.phase not in allowed[action]:
            raise ValueError("更新状态已改变，请稍后再试。")
        self.error = ""
        self.phase = "checking" if action == "check" else "downloading"
        self.task = asyncio.create_task(self._work(action))

    async def _work(self, action):
        try:
            ctx = ssl.create_default_context()
            ctx.load_verify_locations(certifi.where())
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ctx), trust_env=True,
                    timeout=aiohttp.ClientTimeout(total=300, connect=12, sock_read=30),
                    headers={"User-Agent": f"BoardBench/{VERSION}"}) as client:
                if action == "check":
                    async with client.get(LATEST, allow_redirects=False) as response:
                        response.raise_for_status()
                        self.release = select_release(await response.json(), self.platform)
                    self.phase = "available" if self.release else "current"
                else:
                    await self._download(client)
                    self.phase = "ready"
        except Exception as exc:
            self.discard()
            detail = f"HTTP {exc.status}" if isinstance(exc, aiohttp.ClientResponseError) else str(exc) or type(exc).__name__
            self.error = f"{'检查' if action == 'check' else '下载或校验'}失败：{detail}"
            self.phase = "error" if action == "check" else "available"

    async def _download(self, client):
        if "AppTranslocation" in self.target.parts:
            raise ValueError("请先把 BoardBench 拖入“应用程序”并从那里打开。")
        self.stage = Path(tempfile.mkdtemp(prefix=".BoardBench-update-", dir=self.target.parent))
        archive = self.stage / "download.zip"
        self.received = 0
        digest = hashlib.sha256()
        rel = self.release
        async with open_asset(client, rel["url"]) as response:
            response.raise_for_status()
            with archive.open("xb") as output:
                async for block in response.content.iter_chunked(256 * 1024):
                    self.received += len(block)
                    if self.received > rel["size"]:
                        raise ValueError("安装包大小超过发布记录。")
                    output.write(block)
                    digest.update(block)
        if self.received != rel["size"] or digest.hexdigest() != rel["digest"]:
            raise ValueError("安装包的长度或 SHA-256 与发布记录不符。")
        self.phase = "verifying"
        await asyncio.to_thread(prepare, archive, self.stage, self.target, self.platform.startswith("macOS"))
        archive.unlink()

    def discard(self):
        if self.stage:
            shutil.rmtree(self.stage, ignore_errors=True)
            self.stage = None

    def handoff(self, port, directory):
        if self.phase != "ready":
            raise ValueError("新版尚未就绪。")
        env = os.environ.copy()
        env.update(BB_TARGET=str(self.target), BB_STAGE=str(self.stage), BB_PORT=str(port),
                   BB_DATA=str(directory.resolve()), BB_PID=str(os.getpid()), PYINSTALLER_RESET_ENVIRONMENT="1")
        if self.platform.startswith("macOS"):
            # Run from a copy independent of the bundle we are replacing.
            script = self.stage / "install.sh"
            shutil.copyfile(HELPERS / "install-update.sh", script)
            subprocess.Popen(["/bin/sh", script], cwd=self.stage.parent, env=env, start_new_session=True)
        else:
            shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            script = (HELPERS / "install-update.ps1").read_text(encoding="utf-8")
            subprocess.Popen([str(shell), "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                             cwd=self.stage.parent, creationflags=subprocess.CREATE_NO_WINDOW)
        self.phase = "restarting"

    async def stop(self):
        if self.task:
            # Let active extraction finish before deleting its temporary directory.
            if self.phase != "verifying":
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.phase != "restarting":
            self.discard()
