"""Native desktop window around the local BoardBench service."""

import asyncio
import concurrent.futures
import os
import socket
import sys
import threading
import time

from aiohttp import web


class Picker:
    """Native open dialogs for the flashing wizard (window.pywebview.api.pick_firmware)."""

    def __init__(self):
        self._window = None

    def pick_firmware(self, folder=False):
        import webview

        kind = webview.FileDialog.FOLDER if folder else webview.FileDialog.OPEN
        chosen = self._window.create_file_dialog(kind) if self._window else None
        return chosen[0] if chosen else ""


def run_window(port, make_service, smoke=False, receipt=None):
    import webview

    # Claim the address before opening the session database or enumerating devices.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if sys.platform == "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"本机端口 {port} 已被占用。请先关闭正在运行的测试台，或用 --port 指定另一个端口。") from exc
        listener.setblocking(False)
        ready = concurrent.futures.Future()

        async def serve():
            runner = web.AppRunner(make_service(lambda: window.destroy()), access_log=None)
            try:
                await runner.setup()
                await web.SockSite(runner, listener).start()
                stop = asyncio.Event()
                ready.set_result((asyncio.get_running_loop(), stop))
                await stop.wait()
            finally:
                await runner.cleanup()

        def worker():
            try:
                asyncio.run(serve())
            except Exception as exc:
                if not ready.done():
                    ready.set_exception(exc)
                else:
                    raise

        server = threading.Thread(target=worker, name="BoardBench local service")
        server.start()
        loop, stop = ready.result()
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True
            picker = Picker()
            window = webview.create_window(
                "BoardBench · 板子测试台",
                f"http://127.0.0.1:{listener.getsockname()[1]}",
                width=1360, height=900, min_size=(700, 520),
                text_select=True, background_color="#f5f5f7", js_api=picker,
            )
            if receipt is not None:
                from boardbench.updates import installation
                installed = installation()
                if (installed and receipt.name == "started" and receipt.parent.name.startswith(".BoardBench-update-")
                        and receipt.parent.resolve().parent == installed[0].parent and receipt.parent.is_dir()):
                    def acknowledge():
                        receipt.write_text(str(os.getpid()), encoding="ascii")
                    window.events.loaded += acknowledge
            picker._window = window
            checked = concurrent.futures.Future()

            def verify_window():
                try:
                    if not window.events.loaded.wait(50):
                        raise RuntimeError("桌面窗口未载入")
                    for _ in range(100):
                        if window.evaluate_js(
                            "document.querySelector('#emptyParts')?.hidden === false && "
                            "document.querySelectorAll('#componentRows tr').length === 0 && "
                            "document.querySelector('#monitorInfo').textContent.includes('0 个部件有报告')"
                        ):
                            interactions_ok = window.evaluate_js("""
                                (() => {
                                    const byId = id => document.getElementById(id);
                                    const mode = byId('transport');
                                    mode.value = 'TCP';
                                    mode.dispatchEvent(new Event('change'));
                                    if (!byId('connect').disabled) return false;
                                    byId('pause').click();
                                    const search = byId('search');
                                    search.value = 'boardbench-smoke-empty-filter';
                                    search.dispatchEvent(new Event('input'));
                                    if (byId('clearLogFilters').hidden ||
                                        byId('pause').getAttribute('aria-pressed') !== 'true') return false;
                                    byId('clearLogFilters').click();
                                    byId('pause').click();
                                    return search.value === '' && byId('clearLogFilters').hidden &&
                                           byId('pause').getAttribute('aria-pressed') === 'false';
                                })()
                            """)
                            if not interactions_ok:
                                raise RuntimeError("桌面窗口连接校验或日志交互异常")
                            window.evaluate_js("document.querySelector('[data-tab=overview]').click()")
                            if not window.evaluate_js(
                                "document.querySelector('#ovParts p')?.textContent === '没有符合条件的部件。' && "
                                "document.querySelector('#ovModel')?.textContent === '未连接' && "
                                "document.querySelector('#ovBoardInfo').textContent.includes('测试清单') && "
                                "document.querySelectorAll('#ovParts button').length === 0 && "
                                "document.querySelector('#ovCoverage strong').textContent === '—'"
                            ):
                                raise RuntimeError("未显示板卡总览")
                            window.evaluate_js("document.querySelector('#ovPowerTab').click()")
                            if not window.evaluate_js("!document.querySelector('#ovPower').hidden && document.querySelectorAll('#ovPowerCards canvas').length === 3"):
                                raise RuntimeError("未显示能源遥测面板")
                            window.evaluate_js("document.querySelector('#ovRuntimeTab').click()")
                            if not window.evaluate_js("!document.querySelector('#ovRuntime').hidden && document.querySelector('#ovRuntimeInfo').textContent.includes('SRAM')"):
                                raise RuntimeError("未显示板端运行监控")
                            window.evaluate_js("document.querySelector('[data-tab=hardware]').click()")
                            visible = window.evaluate_js(
                                "!document.querySelector('#hardware').hidden && "
                                "!document.querySelector('#manualSuite').open && "
                                "document.querySelector('#smartHelp').textContent.length > 0"
                            )
                            if not visible:
                                raise RuntimeError("桌面窗口未显示智能检测")
                            window.evaluate_js("document.querySelector('#screenDock > summary').click()")
                            if not window.evaluate_js("document.querySelector('#screenDock[open] #screenForm').offsetWidth > 0"):
                                raise RuntimeError("体检页未显示投屏连接表单")
                            window.evaluate_js("document.querySelector('[data-tab=screen]').click()")
                            if not window.evaluate_js("document.querySelector('#screenHome #screenForm').offsetWidth > 0"):
                                raise RuntimeError("独立投影页未接回投屏表单")
                            window.evaluate_js("document.querySelector('[data-tab=signal]').click()")
                            if not window.evaluate_js("!!document.querySelector('#motionView .motion-dial')"):
                                raise RuntimeError("桌面窗口未显示水平仪")
                            window.evaluate_js("document.querySelector('[data-tab=flash]').click()")
                            for _ in range(60):
                                if window.evaluate_js("document.querySelector('#flashBody')?.textContent.includes('第 1 步：选固件')"):
                                    break
                                time.sleep(.1)
                            else:
                                raise RuntimeError("桌面窗口未显示刷固件向导")
                            if not window.evaluate_js("!!window.pywebview?.api?.pick_firmware"):
                                raise RuntimeError("桌面窗口缺少选择文件的接口")
                            window.evaluate_js("document.querySelector('[data-tab=replay]').click()")
                            for _ in range(60):
                                if window.evaluate_js("document.querySelector('#rpPlayer')?.hidden === false"):
                                    break
                                time.sleep(.1)
                            else:
                                raise RuntimeError("桌面窗口未加载历史会话")
                            window.evaluate_js("""
                                const query = document.getElementById('rpQuery');
                                query.value = 'L1';
                                query.dispatchEvent(new Event('input'));
                                document.getElementById('rpFind').click();
                            """)
                            for _ in range(60):
                                if window.evaluate_js("!!document.querySelector('#rpMatches .rp-issue-hit')"):
                                    break
                                time.sleep(.1)
                            else:
                                raise RuntimeError("回放未能定位首条日志")
                            window.evaluate_js("document.getElementById('appUpdate').click()")
                            if not window.evaluate_js("document.querySelector('#dialog[open] .app-update strong')?.textContent.includes('BoardBench  v')"):
                                raise RuntimeError("桌面版更新入口未打开")
                            checked.set_result(True)
                            break
                        time.sleep(0.15)
                    else:
                        raise RuntimeError("桌面窗口未显示通用设备的零上报状态")
                except Exception as exc:
                    checked.set_exception(exc)
                finally:
                    window.destroy()

            webview.start(
                verify_window if smoke else None,
                gui="edgechromium" if sys.platform == "win32" else "cocoa",
                private_mode=False,
            )
            if smoke:
                checked.result(timeout=5)
        finally:
            loop.call_soon_threadsafe(stop.set)
            server.join()


def startup_error(message):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "BoardBench", 0x10)
    elif sys.platform == "darwin":
        import subprocess
        subprocess.run(["osascript", "-e", 'on run argv', "-e",
                        'display alert "BoardBench" message (item 1 of argv) as critical',
                        "-e", 'end run', message], check=False)
