import unittest

from boardbench.bench import Bench
from boardbench.devices.device_identity import identify


class DeviceIdentityTests(unittest.TestCase):
    def test_repl_formats_and_unrelated_output(self):
        for line, firmware, board, chip in (
            ("MicroPython v1.24.1 on 2024-11-29; Raspberry Pi Pico with RP2040",
             "MicroPython v1.24.1", "Raspberry Pi Pico", "RP2040"),
            ("Adafruit CircuitPython 11.0.0-alpha.1 on 2026-09-24; Adafruit Feather RP2350 with rp2350a",
             "Adafruit CircuitPython 11.0.0-alpha.1", "Adafruit Feather RP2350", "rp2350a"),
        ):
            with self.subTest(line=line):
                self.assertEqual(identify(line)[1], dict(firmware=firmware, board=board, chip=chip))
        for unrelated in ("CP210x USB to UART Bridge", "E (55) Board: SKU=demo",
                          ">>> print('ESP-ROM:esp32s3-20210327')", "ESP-ROM:esp32s99-20210327",
                          "MicroPython v1.2 on 2025-01-01; " + "x" * 500):
            self.assertIsNone(identify(unrelated))

    def test_board_declarations_on_late_attach(self):
        b = Bench(":memory:")
        self.addCleanup(b.db.close)
        sku = "I (125) Board: UUID=00000000-0000-4000-8000-000000000001 SKU=zhengchen/cam"
        self.assertEqual(identify(sku)[1]["board"], "zhengchen/cam")
        b.log("RX", "串口", "BoardBenchProbe ready")
        b.log("RX", "串口", "BoardBenchProbe board: ATK-DNESP32S3B BOX")
        self.assertEqual(b.components.identity["board"]["value"], "ATK-DNESP32S3B BOX")
        self.assertEqual(b.components.identity["firmware"]["value"], "BoardBenchProbe")
        self.assertIsNone(identify("USB-SERIAL CH340 (COM5)"))

    def test_late_join_and_rom_restart(self):
        b = Bench(":memory:")
        self.addCleanup(b.db.close)
        b.log("RX", "串口", '{"bb":1,"part":"mcu","status":"sample","value":"ESP32-S3 | CPU 240 MHz | uptime 4216 ms"}')
        self.assertEqual(set(b.components.identity), {"chip"})
        self.assertEqual(b.components.identity["chip"]["log_id"], b.current_rx_log_id)
        b.log("RX", "串口", '{"bb":1,"part":"camera","status":"absent"}')
        generation = b.connection_id
        b.log("RX", "串口", "ESP-ROM:esp32s3-20210327")
        self.assertEqual(b.connection_id, generation + 1)
        self.assertFalse(b.components.last)
        self.assertEqual(b.tx, 0)

    def test_scoped_rx_only_and_log_provenance(self):
        b = Bench(":memory:")
        self.addCleanup(b.db.close)
        b.log("TX", "串口", "BoardBenchProbe ready")
        self.assertEqual(b.components.identity, {})
        for rom, chip in (("esp32c3-api1-20210207", "ESP32-C3"), ("esp32c6-20220919", "ESP32-C6"),
                          ("esp32s3-20210327", "ESP32-S3")):
            b.log("RX", "串口", "ESP-ROM:" + rom)
            self.assertEqual(b.components.identity["chip"]["value"], chip)
        rom_log = b.current_rx_log_id
        sku = "I (125) Board: UUID=00000000-0000-4000-8000-000000000001 SKU=example-kit"
        b.log("RX", "串口", "\x1b[0;32m" + sku + "\x1b[0m")
        fields = b.components.identity
        self.assertEqual(set(fields), {"chip", "board"})
        self.assertEqual(fields["chip"]["log_id"], rom_log)
        self.assertEqual(fields["board"]["value"], "example-kit")
        self.assertEqual(fields["board"]["log_id"], b.current_rx_log_id)
        b.log("RX", "串口", "ESP-ROM:esp32c6-20220919")
        self.assertNotIn("board", fields)
        b.log("RX", "串口", "BoardBenchProbe ready")
        self.assertEqual(fields["firmware"]["value"], "BoardBenchProbe")
        self.assertEqual(b.profile, "通用串口设备")
        self.assertEqual(b.tx, 0)
        b.new(b.profile)
        self.assertFalse(fields)
