"""Built-in inspection plans. A plan describes firmware capabilities, never guessed pins."""

import re
from replies import ReplyMatch, try_sample


def manual(name, step):
    return {"name": name, "step": step, "kind": "manual"}


def command(name, step, tx, expect, timeout=5):
    return dict(
        name=name, step=step, kind="serial", tx=tx, expect=expect, timeout=timeout
    )


generic = [
    manual(
        "供电与启动", "插入数据线，确认板载电源指示或启动输出。记录电源方式和启动现象。"
    ),
    manual(
        "复位与启动日志", "打开串口，按一次实物 RESET，核对新的启动信息及应用就绪状态。"
    ),
]
box = generic + [
    manual(
        "LCD 显示",
        "查看开机画面与对话文字，观察缺行、花屏和方向。纯色坏点检测需要厂商屏幕例程。",
    ),
    manual(
        "K0 / K1 / K2",
        "依次短按三个功能键，逐键记录屏幕、音量或日志反馈。按键功能由当前固件定义。",
    ),
    manual(
        "麦克风采集",
        "启动面板的小智回声服务并连接板子，对着板载麦克风报一组随机数字，核对上传帧和回放内容。",
    ),
    manual(
        "扬声器播放",
        "听刚才数字的回放，确认内容、清晰度和爆音。收到数字音频帧只证明传输成功。",
    ),
    manual(
        "Wi-Fi 联网",
        "查看板端连接日志。启用回声服务后，用设备签到和对话握手核对局域网数据通路。",
    ),
    manual(
        "双色 LED", "运行板型匹配的 LED 例程，分别点亮两种颜色，记录颜色和熄灭情况。"
    ),
    manual("蜂鸣器", "运行蜂鸣器例程，听短鸣和停鸣。请与扬声器回放分别验收。"),
    manual(
        "姿态传感器",
        "打开传感器例程的串口输出，平放、侧放、翻面，记录每次变化的轴和数值。",
    ),
    manual("环境光", "运行光照例程，用手遮挡后移开，记录遮挡前后的两次读数。"),
    manual(
        "TF 卡",
        "插入测试卡，通过板端文件例程新建一个测试文件并读回同一内容，记录文件名和结果。",
    ),
    manual(
        "EEPROM", "在厂商例程的测试区写入测试值，重启后读回。先核实地址为空闲测试区。"
    ),
    manual("PSRAM", "运行板端 PSRAM 测试例程，记录识别容量、分配大小及写入读回结果。"),
    manual(
        "触摸（选装 / 需驱动）", "打开触摸画点例程，点四角和中心。记录坐标方向与响应。"
    ),
    manual(
        "摄像头（选装）", "装好匹配摄像头并运行预览例程，移动镜头检查是否连续更新。"
    ),
    manual(
        "蓝牙 BLE",
        "启动板端 BLE 广播例程，用第二台设备扫描并连接，记录广播名和读回值。",
    ),
    manual(
        "USB Slave / OTG",
        "使用具备数据传输能力的线和对应端口，运行 USB Device 例程，在主机检查预期设备是否枚举。",
    ),
    manual(
        "PH2.0 扩展接口",
        "按实物版本原理图核对线序，连接匹配外设，通过对应例程核对读数或回环。",
    ),
]
mp = generic + [
    command(
        "解释器与机器信息",
        "适用于已运行 MicroPython 且停在 >>> 的板子。",
        "import os; print('BOARD'+'_OK', os.uname())",
        "BOARD_OK",
    ),
    command(
        "可用堆内存",
        "读取当前空闲堆空间，结果单位为字节。",
        "import gc; print('HEAP'+'_OK', gc.mem_free())",
        "HEAP_OK",
    ),
    command(
        "CPU 频率",
        "读取当前机器频率，单位为 Hz。",
        "import machine; print('FREQ'+'_OK', machine.freq())",
        "FREQ_OK",
    ),
    command(
        "设备标识",
        "读取固件公开的唯一标识。",
        "import machine, binascii; print('UID'+'_OK', binascii.hexlify(machine.unique_id()))",
        "UID_OK",
    ),
    command(
        "挂载文件系统",
        "读取当前目录和可用存储统计。",
        "import os; print('FS'+'_OK', os.listdir(), os.statvfs('/'))",
        "FS_OK",
    ),
]
probe = [
    command(
        "串口回环应答",
        "需要配套 BoardBenchProbe 固件，确认命令确实由板端处理。",
        "PING",
        "OK PONG",
    ),
    command(
        "芯片与内存信息", "获取型号、核心数和 Flash / PSRAM 容量。", "INFO", "OK INFO"
    ),
    command(
        "片内 SRAM 读写", "申请 16 KiB 内存并校验两种写入模式。", "SRAM", "OK SRAM"
    ),
    command(
        "PSRAM 读写",
        "需板上有 PSRAM，编译时选择匹配的 PSRAM 类型。",
        "PSRAM",
        "OK PSRAM",
    ),
    command(
        "Wi-Fi 扫描",
        "板端扫描并返回 AP 数量，记录首个 RSSI；0 个 AP 仍表示扫描结束。",
        "WIFI",
        "OK WIFI",
        20,
    ),
    command(
        "连续遥测",
        "输出 30 组 JSON 数值，可在数据曲线页观察。",
        "SAMPLE",
        "OK SAMPLE",
        10,
    ),
]
for item in probe:
    item["reject"] = "ERR "

BUILTINS = {
    "通用串口设备": generic,
    "ESP32 · BoardBenchProbe 固件": probe,
    "DNESP32S3B BOX · BoardBenchProbe 固件": probe
    + [
        command(
            "板载 I²C 总线",
            "仅用于已核对丝印的一代 BOX，SDA 48 / SCL 45。枚举应答地址，具体器件按原理图比对。",
            "I2C 48 45",
            "OK I2C",
            10,
        )
    ]
    + box,
    "ESP32 系列": generic
    + [
        manual(
            "无线与板载外设",
            "选择固件对应的测试配置，或导入包含 TX 命令和预期应答的配置。",
        )
    ],
    "正点原子 · DNESP32S3B BOX": box,
    "MicroPython REPL": mp,
}


# Test names are local to built-ins. Imported profiles declare their own component IDs.
BOX_PARTS = {
    "LCD 显示": "lcd", "K0 / K1 / K2": "buttons", "麦克风采集": "mic",
    "扬声器播放": "speaker", "Wi-Fi 联网": "wifi", "双色 LED": "led",
    "蜂鸣器": "buzzer", "姿态传感器": "imu", "环境光": "light", "TF 卡": "sd",
    "EEPROM": "eeprom", "PSRAM": "psram", "触摸（选装 / 需驱动）": "touch",
    "摄像头（选装）": "camera", "蓝牙 BLE": "ble", "PH2.0 扩展接口": "expansion",
    "PSRAM 读写": "psram", "Wi-Fi 扫描": "wifi", "板载 I²C 总线": "i2c",
}
for tests in BUILTINS.values():
    for test in tests:
        if test["name"] in BOX_PARTS:
            test["part"] = BOX_PARTS[test["name"]]


def validate(data):
    name = data.get("name", "")
    rows = data.get("tests")
    if (
        not isinstance(name, str)
        or not 1 <= len(name.strip()) <= 80
        or not isinstance(rows, list)
        or not 1 <= len(rows) <= 100
    ):
        raise ValueError("配置需要 name 和 1–100 个 tests 项目。")
    names = set()
    for r in rows:
        if not isinstance(r, dict) or r.get("kind") not in ("manual", "serial"):
            raise ValueError("每项 kind 应为 manual 或 serial。")
        for k in ["name", "step"] + (["tx", "expect"] if r["kind"] == "serial" else []):
            if not isinstance(r.get(k), str) or not 1 <= len(r[k].strip()) <= 2048:
                raise ValueError(f"项目缺少有效的 {k}。")
        if "part" in r and (not isinstance(r["part"], str)
                            or not re.fullmatch(r"[a-z0-9_.-]{1,40}", r["part"])):
            raise ValueError("part 应为 1–40 个小写字母、数字、点、下划线或连字符。")
        if "group" in r and (
            not isinstance(r["group"], str) or not 1 <= len(r["group"].strip()) <= 60
        ):
            raise ValueError("group 应为 1–60 个字符的分组名。")
        if "quick" in r and not isinstance(r["quick"], bool):
            raise ValueError("quick 应为 true 或 false。")
        if r["name"] in names:
            raise ValueError("项目名称重复。")
        names.add(r["name"])
        if (
            not isinstance(r.get("timeout", 5), int)
            or not 1 <= r.get("timeout", 5) <= 60
        ):
            raise ValueError("应答超时须为 1–60 秒。")
        if r["kind"] == "serial":
            if r.get("encoding", "text") not in ("text", "hex") or r.get(
                "ending", "CRLF"
            ) not in ("CRLF", "LF", "CR", "none"):
                raise ValueError(
                    "命令编码应为 text / hex，行尾支持 CRLF、LF、CR、none。"
                )
            if not isinstance(r.get("reject", ""), str):
                raise ValueError("reject 应为设备失败时的应答文本或 HEX 字节。")
            if r.get("encoding") == "hex":
                for key in ("tx", "expect", "reject"):
                    bytes.fromhex(r.get(key, ""))
            ReplyMatch(r)
            for field, desired in (("success_sample", "通过"), ("failure_sample", "非通过")):
                if field in r:
                    result = try_sample(r, r[field])
                    if (result == "通过") != (desired == "通过"):
                        raise ValueError(f"{r['name']} 的 {field} 试跑结果为{result}，请检查匹配规则。")
    return name.strip(), rows
