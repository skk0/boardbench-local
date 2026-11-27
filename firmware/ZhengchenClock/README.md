# 征辰时钟固件 r4

适用征辰 AI Camera Wi-Fi 和使用官方 UART 接线的 ML307 双网络版（ESP32-S3、16 MB Flash）。横屏提供时钟、天气、最近对话和只读 Wi-Fi 投屏。默认保留小智官方云配置。双网络版 4G 模式支持定位和天气，进入 Wi-Fi 模式后可与同一局域网的 BoardBench 投屏。

## 从源码编译

1. 准备 ESP-IDF 编译环境，取小智上游 `78/xiaozhi-esp32` 的 `v2.6.0`（提交 `4de00aed740595a8713b01f8332b9b2cf30685bc`）。本次使用 ESP-IDF 6.1。
2. 在这个干净的小智源码目录执行 `git apply /路径/ZhengchenClock/integration.patch`，把本目录 `overlay/` 下的 `main/` 合并复制到源码的 `main/`。补丁接入板级入口，overlay 提供新增源码、字体和 RGB565 背景。
3. 在已加载 ESP-IDF 环境的终端执行对应板型的命令：

   - Wi-Fi：`python scripts/build.py zhengchen/cam --language zh-CN --zip`
   - Wi-Fi＋ML307：`python scripts/build.py zhengchen/cam-ml307 --language zh-CN --zip`

编译包位于小智工程的 `releases/`。选包时核对板型；外接 ML307 须核对 ESP32 TX GPIO43、RX GPIO44、共地以及模块的供电和电平。投屏操作和通用 JPEG 接口见本仓库 README 的「屏幕投影」。

上游代码使用 MIT 许可证，数字字体的 OFL 许可证随 overlay 提供。背景为生成的插画。
