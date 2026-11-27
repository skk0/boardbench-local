# 小智面包板局域网投屏

| 固件目标 | 屏幕 | 启停操作 |
|---|---|---|
| bread-compact-wifi-lcd | ESP32-S3、ST7789 240×320 SPI 彩屏，官方默认接线 | 运行时双击 BOOT |
| bread-compact-wifi-128x64 | ESP32-S3、SSD1306 128×64 I²C 单色屏，官方默认接线 | 运行时双击 BOOT |

两个预编译包均为 ESP32-S3、16 MB Flash、八线 OPI PSRAM 配置。它们使用对应的小智上游板级配置、显示界面和云连接设置。型号和 GPIO 以 v2.6.0 的 [LCD 配置](https://github.com/78/xiaozhi-esp32/blob/v2.6.0/main/boards/bread-compact-wifi-lcd/config.h) 和 [OLED 配置](https://github.com/78/xiaozhi-esp32/blob/v2.6.0/main/boards/bread-compact-wifi/config.h) 为准。烧录前核对屏幕驱动、分辨率、引脚、Flash 容量及 PSRAM 配置。单麦 Wi-Fi 描述本身不足以选包。

最新 USB 包：电脑接主调试串口（这两款构建为 UART0，115200、8N1），运行 BoardBench 后设备每 4 秒报告一次截图能力。程序识别后自动通过 USB 取帧。固件保留原有小智官方云连接。

旧包或需要局域网截图时：正常启动并连上 Wi-Fi 后双击 BOOT，屏上显示六位配对码，串口输出含 IP 的 `ScreenMirror: Screen address` 日志。在 BoardBench「板子屏幕」输入地址和码，路径用默认值。再次双击、断网或满十分钟会关闭共享。配对码不会写入串口日志。单击维持原有动作。

## 从源码构建

在已加载 ESP-IDF 6.1 的终端，进入干净的小智 v2.6.0 源码（提交 `4de00aed740595a8713b01f8332b9b2cf30685bc`），将下面的路径改为本机的 BoardBench 仓库路径。

```sh
bb_repo=/path/to/boardbench-local
git apply "$bb_repo/firmware/XiaozhiScreenMirror/breadboard.patch"
mkdir -p main/screen_mirror
cp "$bb_repo/firmware/ZhengchenClock/overlay/main/boards/zhengchen/cam/desk_mirror."{cc,h} main/screen_mirror/
git apply "$bb_repo/firmware/XiaozhiScreenMirror/usb/console.patch"
cp "$bb_repo/firmware/XiaozhiScreenMirror/usb/usb_screen.cc" main/usb_screen.cc
python scripts/build.py bread-compact-wifi-lcd --name bread-compact-wifi-lcd --language zh-CN --zip
python scripts/build.py bread-compact-wifi --name bread-compact-wifi-128x64 --language zh-CN --zip
```

补丁把已有的只读 JPEG 服务接入这两个板型的空闲双击动作，沿用原有显示和按键驱动。其他固件也可以直接提供当前帧的 HTTP GET 接口，返回 image/jpeg 或 image/png。接口应返回屏幕实际渲染画面，局域网配对信息由设备端提供。每种新板型需核对取帧能力、剩余内存、可用按键及实际屏幕效果。

## 给其他板型接 USB 投屏

确认主调试串口的收发专供电脑使用。板端显示驱动需支持 SnapshotToJpeg，LVGL 需启用 CONFIG_LV_USE_SNAPSHOT。复制 usb_screen.cc，在显示初始化后调用 StartUsbScreen() 并将源文件加入构建。检查本板的资源预算与串口复用情况。只有物理 USB 数据口和主控制台双向连通时才能取帧。

协议为换行分隔的 ASCII。板端周期发送 `BBS1 CAP JPEG` 或 `BBS1 CAP NO_CAPTURE`；主机收到能力声明后才发送 `BBS1 GET <8位十六进制请求号>`。板端逐行返回 `BBS1 DATA <请求号> <十进制字节偏移> <最多128字节的十六进制数据>`，最后返回 `BBS1 END <请求号> <十进制总字节数> <8位十六进制CRC32>`。单帧上限 64 KiB。设备只在收到取帧请求时截图。
