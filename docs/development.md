# 开发与验证

## 验证

`python -X warn_default_encoding -W error::EncodingWarning -m unittest discover -s tests -v`（与 CI 相同）检查串口收发、应答和超时、取消、会话隔离、报告导出以及局域网音频往返；`test_reconnect.py` 用模拟的 ESP32-S3 USB-Serial/JTAG 串口复现“打开即重启”的循环并验证修复，`test_firmware.py` / `test_flasher.py` 用合成镜像和模拟的 esptool 加载器覆盖格式识别、每一项兼容性检查、切开设置区、备份与恢复。Unix 另外通过实际系统伪终端测试收发。姿态计算、静置采样和能源数据解析使用 `node --test tests/motion.test.cjs tests/energy.test.cjs` 检查。跨平台打包使用 `python -m pip install pyinstaller`、`python package_app.py` 和 `python tests/smoke_binary.py`。CI 编译 ESP32-S3 配套固件。协议测试和打包检查不能代替实物验收。

## 借鉴和依赖

- 串口枚举与传输采用 [pySerial](https://github.com/pyserial/pyserial)，“读芯片型号和闪存”与“刷固件”调用 [esptool](https://github.com/espressif/esptool)。
- 可配置检测项目和实时串口趋势参考 [Serial Studio](https://github.com/Serial-Studio/Serial-Studio) 与 [SerialPlot](https://github.com/hyOzd/serialplot)，界面和绘图代码在本项目中实现。
- 本地回声使用 [xiaozhi-esp32 的 WebSocket 协议](https://github.com/78/xiaozhi-esp32/blob/main/main/protocols/websocket_protocol.cc)。一代 BOX 的 I²C 引脚参考 [对应板级实现](https://github.com/78/xiaozhi-esp32/tree/main/main/boards/alientek/atk-dnesp32s3-box)。
