# BoardBench 板卡测试台

在 macOS 或 Windows 上用浏览器控制本机串口，执行板卡验收、观察传感器曲线并导出带原始收发记录的报告。控制页默认在 `http://127.0.0.1:8765`。USB 和串口检测在本机完成，小智回声测试可以临时开启独立的局域网监听。

![BoardBench 本地控制面板，通过 TCP 回环夹具验证通信与日志](docs/workbench.png)

截图使用本机 TCP 回环夹具。右上角可切换明暗主题，字体、图标和设备示意图随软件离线提供。

## 启动

- 从 [Releases](../../releases) 下载对应系统的压缩包并解压，即可运行独立程序。
- 源码运行需要 Python 3.9 或以上。macOS 双击 `macOS.command`，Windows 双击 `Windows.cmd`。首次运行创建虚拟环境并下载依赖，以后可离线启动。
- 也可手动运行 `python -m pip install -r requirements.txt` 和 `python app.py`。端口冲突时使用 `python app.py --port 8766`。
- GitHub Actions 中成功的 **Desktop and firmware** 运行会提供对应系统、CPU 架构的独立可执行文件。下载并解压，macOS 如缺少执行位可执行 `chmod +x BoardBench`。构建产物为未签名的控制台应用。
- 用 USB **数据线**连接，选择端口、波特率、串口格式。Windows 如无法枚举，请检查设备管理器和板载 USB 转串口芯片的驱动。关闭其他占用端口的串口监视器。

面板支持 USB 串口、TCP 客户端和 UDP 对端。网络板填设备地址和固件实际监听的端口，三种连接方式共享终端、命令测试、曲线和日志。UDP 启用后日志会显示本机接收端口，首次收包可确认对端有响应。

## 第一次测一块板

1. 在“测试配置”中选板型并点击“新建测试”。同一端口换了另一块实物也要新建测试。固件和 PCB 改版信息可以写入“通信终端 → 测试备注”。
2. 有出厂小智固件的一代正点原子 BOX 选择 **正点原子 · DNESP32S3B BOX**。观察原固件的屏幕、按键与语音反馈。选择 ESP ROM 探测会关闭当前串口监视、让设备复位进下载模式、读取芯片和闪存标识；操作结束后可以重新连接。自动复位失败时参照本板的 BOOT / RESET 说明进入下载模式。
   “备份 Flash”可读出整片 Flash，完成后从历史报告下载 BIN；同页显示 SHA-256。备份中可能包含 Wi-Fi 与应用凭证，按私密文件保存。
3. “通信终端”可发送 UTF-8、HEX，选择 CRLF、LF、CR 或无行尾。“数据曲线”显示 JSON 数值字段、数值 CSV 或 key=value 的输入。
4. 展开检测项目执行命令或记录实测现象。自动检测发送指定命令，等待本次收到的应答；失败和超时均有独立记录。人工项可标记通过、失败或跳过，必须填写依据。未操作的项目在报告中标为未测。
5. 在“历史与报告”导出 TXT 结论、JSON 完整会话或 CSV 事件流。页面显示最近 350 条，完整日志持久化在用户主目录的 `.boardbench/history.sqlite3`。原始串口内容可能包含设备标识、配网信息，分享报告前请检查。

## AI 分析

通过日志旁的“分析本次测试”、左侧“AI 分析”或历史记录进入。模型和接口设置支持 OpenAI 兼容的 Chat Completions API。

1. 填写 API 地址，例如本机 Ollama 的 `http://127.0.0.1:11434/v1`、LM Studio 的 `http://127.0.0.1:1234/v1`，或自己的 HTTPS 网关。也可填写完整的 `/chat/completions` 路径。服务端须已加载可用模型。
2. 按需填写 API Key、输入模型名，或点击“读取模型列表”。“测试连接”向所选模型发送最小请求，不包含板卡日志。地址与模型保存在本地；密钥默认仅在进程内使用，勾选记住后交给系统钥匙串。换地址时密钥不会带到新服务。
3. 选择会话与范围，填写问题并预览。预览列出抽样条数、实测结论、未测项目和将发送的诊断正文，可检查自动遮盖后的内容。点击“发送并分析”才会发送这份快照。原始 HEX、音频和 Flash 镜像不进入诊断正文。
4. 点击模型结论下方的 L 编号查看原始日志，R 编号查看当时的检测结果。无效引用标为待确认；未按格式提供证据的回复显示为未核验文本。每次分析及其证据快照单独归档在所选会话中，TXT / JSON 报告包含 AI 分析。实测状态仅由检测操作产生。

## 设备与能力

| 设备 / 固件 | 可用功能 |
|---|---|
| 能枚举为串口的 MCU、USB-TTL 和仪表 | 串口收发、原始字节、曲线、操作备注、自定义测试、历史报告 |
| esptool 支持的 ESP 芯片 | ROM 识别、MAC、Flash ID 和识别容量，输出依芯片和下载模式而异 |
| MicroPython REPL | 机器信息、堆内存、CPU、设备 ID、文件系统。需要先进入 `>>>` |
| 可更改 OTA 的小智 WebSocket v1 固件 | 局域网签到、语音握手、短录音原帧回送、屏幕测试文字。听感和画面由操作者确认 |
| 正点原子 DNESP32S3B BOX | 套装外设验收清单，覆盖按键、显示、音频、TF 卡、传感器、EEPROM、USB、扩展接口、可选摄像头等 |
| 配套 BoardBenchProbe 固件 | 有应答判定的串口、芯片资源、片内 SRAM / PSRAM 小块读写、Wi-Fi 扫描、连续遥测。针对已核对板型的一代 BOX，还提供 I²C 地址枚举 |

### 自定义测试

下载内置配置或编辑 [示例配置](examples/serial-device.json)，把 `tx` 换成固件支持的命令、`expect` 换成只在有效应答中出现的文本。导入后选择配置并新建测试。`timeout` 单位为秒，范围 1–60；文本命令默认附加 CRLF。可选字段 `encoding` 支持 `text`、`hex`，`ending` 支持 `CRLF`、`LF`、`CR`、`none`，HEX 默认无行尾。`reject` 可配置设备的明确失败应答。固件会回显命令时，应选择与 TX 不同的应答标志。“运行命令项”依次执行配置内全部串口命令；单项结果和每次尝试的收发保留在日志里。

### 小智回声

让板子和电脑连同一局域网。打开“小智回声”，填写电脑在该局域网的 IPv4 地址并开启监听。先记下板上的原 OTA 地址，再把面板生成的地址填入板子配网页。设备下次签到后按键或唤醒，报一串数字；收到停止录音或累积约 3 秒的 Opus 帧后，测试台向板子回传。录音仅在内存中缓存。测完关闭监听并恢复原 OTA。若仅签到没有握手，请检查固件是否允许自定义 OTA、WebSocket v1、明文局域网连接，以及电脑防火墙。固定 MQTT 固件需要相应网关。这一模式可独立于云端账号或大模型使用。

### 配套诊断固件

[BoardBenchProbe](firmware/BoardBenchProbe/BoardBenchProbe.ino) 使用 Espressif Arduino core，编译目标为实际 ESP32 板型。用 Arduino IDE 打开同名目录，选择对应芯片、串口及正确的 PSRAM 类型。烧录此程序会替换当前应用；保留出厂小智时使用面板的出厂固件配置即可。串口速度 115200。通用固件不在启动时配置板载外设引脚。I²C 引脚必须按具体原理图选择。地址应答只能表明该地址有设备。整屏纯色测试、传感器物理量、TF 文件校验和摄像头预览需要相应板型的例程或固件命令，面板支持把这些命令加入配置。

## 借鉴和依赖

- 串口枚举与传输采用 [pySerial](https://github.com/pyserial/pyserial)，ESP ROM 探测调用 [esptool](https://github.com/espressif/esptool)。
- 可配置检测项目和实时串口趋势参考 [Serial Studio](https://github.com/Serial-Studio/Serial-Studio) 与 [SerialPlot](https://github.com/hyOzd/serialplot)，界面和绘图代码在本项目中实现。
- 本地回声使用 [xiaozhi-esp32 的 WebSocket 协议](https://github.com/78/xiaozhi-esp32/blob/main/main/protocols/websocket_protocol.cc)。一代 BOX 的 I²C 引脚参考 [对应板级实现](https://github.com/78/xiaozhi-esp32/tree/main/main/boards/alientek/atk-dnesp32s3-box)。

## 验证

`python -m unittest discover -s tests -v` 检查串口收发、应答和超时、取消、会话隔离、报告导出以及局域网音频往返。Unix 另外通过实际系统伪终端测试收发。跨平台打包使用 `python -m pip install pyinstaller`、`python package_app.py` 和 `python tests/smoke_binary.py`。CI 编译 ESP32-S3 配套固件。协议测试和打包检查不能代替实物验收。
