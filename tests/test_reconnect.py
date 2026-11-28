"""Reproduce the ESP32-S3 USB-Serial/JTAG reboot loop with a fake serial layer and prove it is gone."""

import asyncio
import time
import unittest
from unittest.mock import patch

import serial

from boardbench.bench import Bench, open_without_reset
from boardbench.devices.monitor import AutoUSB, MAX_RESETS

PORT = "/dev/cu.usbmodem1101"
DEVICE = dict(path=PORT, vid=0x303A, pid=0x1001, serial="F4:12:FA:00:00:01", label="USB JTAG/serial debug unit")
BANNER = b"ESP-ROM:esp32s3-20210327\r\nBuild:Mar 27 2021\r\nrst:0x15 (USB_UART_CHIP_RESET),boot:0x8 (SPI_FAST_FLASH_BOOT)\r\n"


class FakeBoard:
    """ESP32-S3 on its native USB port: DTR off + RTS on resets the chip, the USB device drops
    off the bus and comes back under the same path printing the ROM boot banner."""

    def __init__(self):
        self.present = True
        self.lines = (False, False)
        self.history = []
        self.resets = 0
        self.generation = 0
        self.output = bytearray(b"I (5120) Board: UUID=12345678-1234-1234-1234-123456789abc SKU=life-box\r\n")

    def set_lines(self, dtr, rts):
        self.lines = (dtr, rts)
        self.history.append(self.lines)
        if self.lines == (False, True) and self.present:
            self.resets += 1
            self.present = False
            self.generation += 1

    def come_back(self):
        if not self.present:
            self.present = True
            self.lines = (False, False)
            self.output = bytearray(BANNER)


class FakeSerial:
    """The parts of pyserial that Bench uses, including how each OS applies DTR/RTS on open."""

    board = None
    windows = False

    def __init__(self, port=None, **settings):
        self.port = port
        self._dtr = self._rts = True  # pyserial's defaults
        self.is_open = False
        self.generation = None

    def _apply(self):
        if self.is_open:
            self.board.set_lines(self._dtr, self._rts)

    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, value):
        self._dtr = value
        if self.is_open:
            self.board.set_lines(value, self.board.lines[1])

    @property
    def rts(self):
        return self._rts

    @rts.setter
    def rts(self, value):
        self._rts = value
        if self.is_open:
            self.board.set_lines(self.board.lines[0], value)

    def open(self):
        if not self.board.present:
            raise serial.SerialException(2, "could not open port")
        self.is_open = True
        self.generation = self.board.generation
        if self.windows:  # SetCommState applies both initial states at once
            self.board.set_lines(self._dtr, self._rts)
        else:  # the OS raises both lines, then pyserial writes DTR, then RTS
            self.board.set_lines(True, True)
            self.board.set_lines(self._dtr, True)
            self.board.set_lines(self._dtr, self._rts)

    def _alive(self):
        if self.generation != self.board.generation or not self.board.present:
            raise serial.SerialException("device reports readiness to read but returned no data")

    @property
    def in_waiting(self):
        self._alive()
        return len(self.board.output)

    def read(self, size):
        self._alive()
        data = bytes(self.board.output[:size])
        del self.board.output[:size]
        return data

    def write(self, data):
        return len(data)

    def close(self):
        self.is_open = False


def legacy_open(handle):
    """BoardBench before the fix: both lines off before open()."""
    handle.dtr = handle.rts = False
    handle.open()


class LineOrderTests(unittest.TestCase):
    def open_with(self, opener, windows):
        board = FakeBoard()
        FakeSerial.board, FakeSerial.windows = board, windows
        handle = FakeSerial()
        handle.port = PORT
        opener(handle)
        return board

    def test_old_sequence_resets_on_macos_and_linux(self):
        board = self.open_with(legacy_open, windows=False)
        self.assertIn((False, True), board.history)
        self.assertEqual(board.resets, 1)

    def test_new_sequence_never_passes_through_reset(self):
        for windows in (False, True):
            with self.subTest(windows=windows):
                board = self.open_with(lambda h, w=windows: open_without_reset(h, windows=w), windows)
                self.assertNotIn((False, True), board.history)
                self.assertEqual(board.resets, 0)
                self.assertEqual(board.lines, (False, False))


class ReconnectLoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.board = FakeBoard()
        FakeSerial.board, FakeSerial.windows = self.board, False
        self.b = Bench(":memory:")
        self.auto = AutoUSB(self.b, asyncio.Lock())
        self.patches = [
            patch("boardbench.bench.serial.Serial", FakeSerial),
            # The fake emulates macOS/Linux line handling on every OS, so pick the matching sequence.
            patch("boardbench.bench.open_without_reset", lambda handle: open_without_reset(handle, windows=False)),
            patch.object(Bench, "ports", staticmethod(lambda: [DEVICE] if self.board.present else [])),
        ]
        for p in self.patches:
            p.start()
        self.auto.remember(PORT, 115200, "8N1")

    async def asyncTearDown(self):
        await self.b.stop()
        await self.b.disconnect()
        for p in self.patches:
            p.stop()
        self.b.db.close()

    async def cycle(self, rounds):
        """Let the watcher run: board re-enumerates between polls, backoff waits are fast-forwarded."""
        waits = []
        for _ in range(rounds):
            await self.auto.check()
            await asyncio.sleep(0.08)  # read loop notices a vanished device
            self.board.come_back()
            for path in list(self.auto.next_try):
                waits.append(round(self.auto.next_try.pop(path) - time.monotonic()))
        return waits

    async def test_new_ch340_without_serial_recognizes_only_received_board_name(self):
        self.auto.db.execute("DELETE FROM remembered_usb")
        ch340 = dict(DEVICE, vid=0x1A86, pid=0x7523, serial=None, label="USB-SERIAL CH340")
        virtual = dict(path="COM1", vid=None, pid=None, serial=None, label="communications port")
        with patch.object(Bench, "ports", return_value=[virtual]) as ports:
            await self.auto.check()
            ports.return_value = [virtual, ch340]
            await self.auto.check()
            self.assertIsNotNone(self.b.ser)
            await asyncio.sleep(.12)
            identity = self.b.components.identity["board"]
            self.assertEqual(identity["value"], "life-box")
            self.assertEqual(self.b.tx, 0)
            self.assertEqual(self.b.profile, "通用串口设备")
            self.assertGreater(self.b.connection_rx, 0)
            await self.b.disconnect()
            self.b.connect(PORT, 115200)
            self.assertFalse(self.b.components.identity)
            self.assertEqual(self.b.connection_rx, 0)

    async def test_reset_on_open_loop_is_capped(self):
        run = self.b.run
        with patch("boardbench.bench.open_without_reset", legacy_open):
            waits = await self.cycle(12)
        self.assertEqual(self.board.resets, MAX_RESETS, "stops reopening a board that resets on every open")
        self.assertEqual(waits, [3, 6], "backs off exponentially between attempts")
        self.assertIn("已停止自动连接", self.auto.message)
        self.assertIsNone(self.b.ser)
        self.assertEqual(self.b.run, run, "the same board keeps its session")
        logs = [r["message"] for r in self.b.report(run)["logs"]]
        self.assertTrue(any("已停止自动连接" in m for m in logs))

    async def test_fixed_open_does_not_reset_and_stays_connected(self):
        await self.cycle(10)
        self.assertEqual(self.board.resets, 0)
        self.assertIsNotNone(self.b.ser)
        self.assertNotIn((False, True), self.board.history)

    async def test_one_reset_reconnects_into_same_session(self):
        run = self.b.run
        await self.cycle(1)
        self.assertIsNotNone(self.b.ser)
        self.board.set_lines(False, True)  # something else reset the board once
        await self.cycle(3)
        self.assertEqual(self.board.resets, 1)
        self.assertIsNotNone(self.b.ser, "reconnected after the backoff")
        self.assertEqual(self.b.run, run)
        messages = [r["message"] for r in self.b.report(run)["logs"]]
        self.assertTrue(any("可能是打开串口时板子重启了" in m for m in messages))
        self.assertTrue(any("刚连上时重启了一次" in m for m in messages), "ROM banner right after reopening")

    async def test_manual_connect_clears_the_stop(self):
        with patch("boardbench.bench.open_without_reset", legacy_open):
            await self.cycle(6)
        self.assertEqual(self.auto.gave_up, self.auto.current_key)
        self.b.connect(PORT, 115200)
        self.auto.remember(PORT, 115200, "8N1")
        self.assertIsNone(self.auto.gave_up)
        self.assertEqual(self.board.resets, MAX_RESETS)


if __name__ == "__main__":
    unittest.main()

class ChipProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_probe_restores_monitor_and_keeps_board_separate(self):
        from unittest.mock import AsyncMock
        for chip in ("ESP32-S3", "ESP32-C3", None):
            with self.subTest(chip=chip):
                b = Bench(":memory:")
                FakeSerial.board, FakeSerial.windows = FakeBoard(), False
                FakeSerial.board.output.clear()
                reader = AsyncMock(return_value=chip, side_effect=None if chip else ValueError("无 ROM 应答"))
                with (patch.object(Bench, "ports", return_value=[DEVICE]),
                      patch("boardbench.bench.serial.Serial", FakeSerial),
                      patch("boardbench.bench.read_chip", reader)):
                    try:
                        b.connect(PORT, 57600, "8E1")
                        run = b.run
                        b.start("识别主控", lambda: b.identify_mcu(PORT))
                        await b.job
                        reader.assert_awaited_once_with(PORT)
                        self.assertTrue(b.ser)
                        self.assertEqual((b.run, b.baud, b.framing), (run, 57600, "8E1"))
                        self.assertNotIn("board", b.device_identity)
                        if chip:
                            entry = b.device_identity["chip"]
                            self.assertEqual(entry["value"], chip)
                            self.assertIn(chip, b.db.execute("SELECT message FROM logs WHERE id=?", (entry["log_id"],)).fetchone()[0])
                        else:
                            self.assertNotIn("chip", b.device_identity)
                            self.assertIn("无 ROM 应答", b.chip_probe_status)
                        await b.disconnect()
                        b.connect(PORT, 115200)
                        self.assertNotIn("chip", b.device_identity)
                        self.assertFalse(b.chip_probe_status)
                    finally:
                        await b.disconnect()
                        b.db.close()

    def test_worker_only_detects_and_resets(self):
        from unittest.mock import Mock, call
        from boardbench.devices.chip_probe import detect
        rom = Mock(CHIP_NAME="ESP32-S3")
        with patch("boardbench.devices.chip_probe.detect_chip", return_value=rom) as detect_chip:
            self.assertEqual(detect(PORT), "ESP32-S3")
            detect_chip.assert_called_once_with(PORT, baud=115200, connect_attempts=2)
        self.assertEqual(rom.mock_calls, [call.hard_reset(), call._port.close()])
