"""Read-only verdicts scoped to observations from the current connection."""

import re

# These outputs need optical or acoustic feedback even when their controllers reply.
OBSERVATIONS = {
    "lcd": "屏幕像素、方向和缺行需看实物画面。",
    "led": "双色 LED 的实际颜色和亮灭需看实物。",
    "speaker": "扬声器实际出声和清晰度需现场听音。",
    "buzzer": "蜂鸣器的实际鸣响需现场听音。",
}
KEYS = ("K0", "K1", "K2")
KEY_LEVELS = re.compile(r"K0=(UP|DOWN)\s+K1=(UP|DOWN)\s+K2=(UP|DOWN)")
NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])")

# Match concrete ESP-IDF reports. A tag or the word "OK" alone proves nothing.
IDF_REPORTS = {
    "wifi": (
        (
            r"wifi:\s*(?:state:\s*\w+\s*->\s*run\b|connected with .+, aid\s*=\s*\d+)",
            "本次连接中，固件报告 Wi-Fi 已与接入点关联。互联网可达性尚未测量。",
        ),
        (
            r"esp_netif_handlers:\s*sta ip:\s*",
            "本次连接中，固件报告 Wi-Fi 站点已获得 IPv4 地址。互联网可达性尚未测量。",
        ),
    ),
    "psram": (
        (
            r"(?:esp_psram|octal_psram):\s*Found [1-9]\d*\s*(?:KB|MB|KiB|MiB) PSRAM device\b",
            "启动日志给出了已识别的 PSRAM 容量。内存读写仍需单独验证。",
        ),
        (
            r"esp_psram:\s*Adding pool of [1-9]\d*K of PSRAM memory to heap allocator\b",
            "固件报告已将 PSRAM 加入可分配内存池。内存读写仍需单独验证。",
        ),
    ),
    "mcu": (
        (
            r"(?:boot|cpu_start):\s*(?:Loaded app from partition at offset 0x[0-9a-f]+|Starting scheduler on PRO CPU|(?:Pro|App|Multicore) cpu (?:up|start(?:ing)? user code))\b",
            "本次连接中收到主控启动阶段日志。",
        ),
        (r"app_init:\s*Project name:\s*\S+", "本次连接中收到应用初始化日志及项目名。"),
    ),
}
STA_ADDRESS = re.compile(r"\bsta ip:\s*(\d{1,3}(?:\.\d{1,3}){3})(?:,|\s|$)", re.I)


def key_cycles(previous, state, value, log_id=None):
    """Track press/release evidence per key; heartbeat rows must not replace an edge."""
    cycles = dict(previous.get("key_cycle_logs", {}))
    presses = {}
    levels = KEY_LEVELS.fullmatch(value.strip()) if state == "有读数" else None
    if levels and log_id is not None:
        pending = previous.get("key_press_logs", {})
        for key, level in zip(KEYS, levels.groups()):
            if level == "DOWN":
                presses[key] = pending.get(key, log_id)
            elif key in pending:
                cycles[key] = (pending[key], log_id)
    return {"key_press_logs": presses, "key_cycle_logs": cycles}


def factory_detail(part, value):
    for pattern, detail in IDF_REPORTS.get(part, ()):
        # Only accept an informational line authored by the expected component.
        if not re.match(r"^I\s*\(\d+\)\s+" + pattern, value, re.I):
            continue
        if part == "wifi" and "sta ip:" in value.lower():
            address = STA_ADDRESS.search(value)
            if not address:
                continue
            octets = list(map(int, address[1].split(".")))
            if not 0 < octets[0] < 224 or octets[0] == 127 or max(octets) > 255:
                continue
        return detail
    return ""


def _verdict(bench, row):
    part, state, value = row["part"], row["status"], row["value"].strip()
    if state in ("等待设备", "连接断开"):
        return "unknown", "等待设备连接并输出本次检测的证据。"
    if state == "上报超时":
        return "unknown", "周期上报已中断，等待新的板端读数。"
    if state in ("设备报错", "固件报错", "无应答"):
        return "fault", f"本次收到板端报告 · {state}。{value}"
    if part in OBSERVATIONS:
        return "observe", OBSERVATIONS[part] + (f" 板端记录 · {value}" if value else "")
    if state == "固件日志":
        detail = factory_detail(part, value)
        if detail:
            return "verified", detail + " " + value
        return "unknown", "收到固件日志，尚无可据以判定的测试结果。"
    if state in ("未上报", "未启用", "未提供读数"):
        return "unknown", state + (f" · {value}" if value else "。")
    if part == "usb":
        if (
            bench.transport == "串口"
            and state == "控制状态"
            and value.startswith("diagnostic serial heartbeat |")
        ):
            return (
                "verified",
                "本机串口收到了板端诊断心跳。USB OTG 功能另需对应固件和设备。",
            )
        return "unknown", "当前证据尚未验证 USB 接口功能。"
    if state == "有应答":
        if part in {"buttons", "wifi", "ble"}:
            return "unknown", "收到应答，尚无按键动作或无线连接的实测记录。"
        return "verified", "板端报告设备应答。此项验证总线通信。" + (
            f" {value}" if value else ""
        )
    if state == "控制状态":
        return "unknown", "收到控制器状态，尚无功能测试证据。" + (
            f" {value}" if value else ""
        )
    if state != "有读数":
        return "unknown", "等待可判定的板端读数。"
    if part == "buttons":
        if not KEY_LEVELS.fullmatch(value):
            return "unknown", "按键上报未包含完整的 K0、K1、K2 电平。"
        cycles = bench.components.last[part].get("key_cycle_logs", {})
        missing = [key for key in KEYS if key not in cycles]
        if not missing:
            links = "、".join(
                f"{key} L{cycles[key][0]}→L{cycles[key][1]}" for key in KEYS
            )
            return "verified", "已收到三个按键各自按下和释放的电平变化。 " + links
        return "observe", "依次按下并松开 " + "、".join(missing) + "，面板会自动记录。"
    if part == "wifi":
        associated = re.fullmatch(r"associated\s*\|\s*RSSI (-?\d+) dBm", value)
        if associated and -127 <= int(associated[1]) <= 0:
            return (
                "verified",
                "固件报告已关联接入点及信号强度。互联网可达性尚未测量。 " + value,
            )
        return "unknown", "等待固件提供明确的接入点关联记录。"
    if part == "ble":
        return "unknown", "等待蓝牙对端连接和数据收发的实测证据。"
    if not NUMBER.search(value) or re.search(
        r"\b(?:nan|inf|infinity|null|none)\b", value, re.I
    ):
        return "unknown", "上报未提供可用于验证的有效数值。"
    return "verified", "已收到板端数值上报 · " + value


def evaluate(bench):
    """Return one evidence-scoped result per monitored part without sending commands."""
    rows = []
    for component in bench.components.states(bench.profile, bool(bench.ser)):
        status, detail = _verdict(bench, component)
        log_id = component["log_id"]
        name = component["name"]
        if status == "verified" and component["status"] == "有应答":
            name += " · 总线应答"
        elif (
            status == "verified"
            and component["status"] == "有读数"
            and component["part"] != "buttons"
        ):
            name += " · 数值上报"
        if (
            component["part"] == "buttons"
            and status == "verified"
            and component["status"] == "有读数"
        ):
            cycles = bench.components.last["buttons"]["key_cycle_logs"]
            log_id = max(release for _, release in cycles.values())
        if component["part"] == "usb" and bench.ser and bench.transport == "串口":
            received = getattr(bench, "current_rx_log_id", None)
            if received and status != "fault":
                name = "串口接收"
                status = "verified"
                detail = "本次连接已通过串口收到数据。USB OTG 功能另需对应固件和设备。"
                log_id = received
        rows.append(
            dict(
                id=component["part"],
                name=name,
                status=status,
                detail=detail,
                log_id=log_id,
            )
        )
    return rows
