"""Read the chip family from the Espressif ROM without starting a flash stub."""

import asyncio
import contextlib
import io
import sys

from esptool.cmds import detect_chip
from esptool.targets import ROM_LIST


def detect(port):
    # esptool also prints connection diagnostics. Only the validated result
    # crosses the worker boundary, never a device MAC or arbitrary serial data.
    with contextlib.redirect_stdout(io.StringIO()):
        chip = detect_chip(port, baud=115200, connect_attempts=2)
        try:
            name = chip.CHIP_NAME
            chip.hard_reset()
        finally:
            chip._port.close()
    return name


def main(port):
    try:
        print(detect(port), flush=True)
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 1
    return 0


async def read_chip(port):
    worker = ([sys.executable, "--identify-esp"] if getattr(sys, "frozen", False)
              else [sys.executable, "-m", "boardbench.devices.chip_probe"])
    process = await asyncio.create_subprocess_exec(*worker, port, stdout=asyncio.subprocess.PIPE,
                                                  stderr=asyncio.subprocess.DEVNULL)
    try:
        data, _ = await asyncio.wait_for(process.communicate(), 25)
        name = data.decode("ascii", "replace").strip()
        if process.returncode or name not in {target.CHIP_NAME for target in ROM_LIST}:
            raise ValueError("未取得有效的 ESP ROM 应答。请核对下载串口和板子的 BOOT 下载方式。")
        return name
    except asyncio.TimeoutError:
        raise ValueError("等待 ESP ROM 应答超时。请检查所选 USB 口是否支持下载模式。") from None
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
