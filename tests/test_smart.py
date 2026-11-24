import json
import unittest
from unittest.mock import patch

from bench import Bench
from smart import evaluate, factory_detail, key_cycles


class SmartChecks(unittest.TestCase):
    def setUp(self):
        self.bench = Bench(":memory:")
        self.addCleanup(self.bench.db.close)
        self.bench.new("正点原子 · DNESP32S3B BOX")
        self.bench.ser = object()

    def report(self, part, status, value):
        self.bench.log(
            "RX", "串口", json.dumps(dict(bb=1, part=part, status=status, value=value))
        )

    def rows(self):
        return {row["id"]: row for row in evaluate(self.bench)}

    def test_passive_scoped_results_keep_log_evidence(self):
        self.report("imu", "sample", "ax=0.2 g")
        self.report("eeprom", "ack", "0x50 ACK")
        self.report("speaker", "control", "amp=1")
        self.report("sram", "sample", "NaN")
        self.report("light", "error", "timeout")
        self.report("wifi", "idle", "not associated")
        self.report("ble", "control", "controller enabled")
        with patch.object(
            self.bench, "send", side_effect=AssertionError("unexpected TX")
        ):
            rows = self.rows()
        self.assertEqual(len(rows), 22)
        for part, status in {
            "imu": "verified",
            "eeprom": "verified",
            "speaker": "observe",
            "light": "fault",
            "wifi": "unknown",
            "ble": "unknown",
            "sram": "unknown",
        }.items():
            with self.subTest(part=part):
                self.assertEqual(rows[part]["status"], status)
                self.assertIsInstance(rows[part]["log_id"], int)
        self.assertIn("总线通信", rows["eeprom"]["detail"])
        self.bench.components.last["imu"]["tick"] -= 12
        self.assertEqual(self.rows()["imu"]["status"], "unknown")
        self.bench.ser = None
        self.assertTrue(all(r["status"] == "unknown" for r in self.rows().values()))

    def test_generic_inventory_and_device_reported_models(self):
        bench = self.bench
        bench.add_profile({"name": "客户 BOX", "tests": [
            {"kind": "manual", "name": "上电", "step": "观察"}
        ]})
        bench.new("客户 BOX", keep_connection=True)
        self.assertEqual(self.rows(), {})
        bench.log("RX", "串口", "ordinary text")
        self.assertEqual(set(self.rows()), {"serial:rx"})
        for model in ("TMP117", "TMP102"):
            bench.log("RX", "串口", json.dumps(dict(
                bb=1, part="temperature", model=model, status="sample", value="24 C"
            )))
            self.report("temperature", "sample", "25 C")
            rows = self.rows()
            self.assertEqual(set(rows), {"temperature", "serial:rx"})
            self.assertIn(model, rows["temperature"]["name"])
            self.assertEqual(rows["temperature"]["status"], "verified")
        bench.new("通用串口设备", keep_connection=True)
        self.assertEqual(self.rows(), {})
        self.report("temperature", "sample", "20 C")
        self.assertNotIn("TMP", self.rows()["temperature"]["name"])

    def test_absence_is_explicit_and_scoped_to_current_connection(self):
        self.assertEqual(self.rows()["camera"]["status"], "unknown")
        self.report("camera", "unavailable", "no driver")
        self.assertEqual(self.rows()["camera"]["status"], "unknown")
        self.report("camera", "missing", "no reply")
        self.assertEqual(self.rows()["camera"]["status"], "fault")
        self.report("camera", "absent", "variant without camera")
        self.bench.components.last["camera"]["tick"] -= 60
        row = self.rows()["camera"]
        self.assertEqual(row["status"], "absent")
        self.assertIsInstance(row["log_id"], int)
        component = next(r for r in self.bench.components.states(self.bench.profile, True)
                         if r["part"] == "camera")
        self.assertFalse(component["expected"])
        self.bench.new(self.bench.profile, keep_connection=True)
        self.assertEqual(self.rows()["camera"]["status"], "unknown")

    def test_ordinary_firmware_reports_are_specific(self):
        for msg, expected in (
            ("I (91) wifi: state: auth -> run (0x10)", "verified"),
            ("I (92) wifi: state: run -> init", "unknown"),
            ("I (93) wifi: mode : sta", "unknown"),
            ("E (94) wifi: association failed", "fault"),
        ):
            self.bench.log("RX", "串口", msg)
            self.assertEqual(self.rows()["wifi"]["status"], expected)
        self.assertTrue(
            factory_detail(
                "wifi", "I (23) esp_netif_handlers: sta ip: 192.168.3.8, mask:"
            )
        )
        for line in (
            "I (23) esp_netif_handlers: eth ip: 192.168.3.8, mask:",
            "I (23) esp_netif_handlers: sta ip: 0.0.0.0",
            "W (23) wifi: state: auth -> run",
            "I (23) wifi: skipped state: auth -> run",
        ):
            self.assertFalse(factory_detail("wifi", line))
        self.assertTrue(
            factory_detail("psram", "I (1) esp_psram: Found 8MB PSRAM device")
        )
        self.assertTrue(factory_detail("mcu", "I (88) app_init: Project name: xiaozhi"))

    def test_all_three_keys_must_be_pressed_then_released(self):
        previous = {}
        reports = (
            (10, "K0=UP K1=UP K2=UP"),
            (11, "K0=DOWN K1=UP K2=DOWN"),
            (12, "K0=DOWN K1=UP K2=DOWN"),
            (13, "K0=UP K1=DOWN K2=UP"),
        )
        for log_id, value in reports:
            previous = key_cycles(previous, "有读数", value, log_id)
        self.assertEqual(previous["key_cycle_logs"], {"K0": (11, 13), "K2": (11, 13)})
        previous = key_cycles(previous, "有读数", "K0=UP K1=UP K2=UP", 14)
        self.assertEqual(previous["key_cycle_logs"]["K1"], (13, 14))
        previous = key_cycles(previous, "有读数", "K0=UP K1=UP K2=UP", 15)

        partial = key_cycles({}, "有读数", "K0=DOWN K1=UP K2=UP", 16)
        partial = key_cycles(partial, "无应答", "read failed", 17)
        self.assertFalse(
            key_cycles(partial, "有读数", "K0=UP K1=UP K2=UP", 18)["key_cycle_logs"]
        )
        self.report("buttons", "sample", "K0=UP K1=UP K2=UP")
        self.assertEqual(self.rows()["buttons"]["status"], "observe")
        self.bench.components.last["buttons"].update(previous)
        buttons = self.rows()["buttons"]
        self.assertEqual(buttons["status"], "verified")
        self.assertEqual(buttons["log_id"], 14)
        self.assertIn("K0 L11→L13、K1 L13→L14、K2 L11→L13", buttons["detail"])

    def test_key_edges_link_to_actual_rx_rows_and_reset_on_new_run(self):
        for value in (
            "K0=DOWN K1=UP K2=UP",
            "K0=UP K1=UP K2=UP",
            "K0=UP K1=DOWN K2=UP",
            "K0=UP K1=UP K2=UP",
            "K0=UP K1=UP K2=DOWN",
            "K0=UP K1=UP K2=UP",
        ):
            self.report("buttons", "sample", value)
        completion_id = self.bench.components.last["buttons"]["log_id"]
        self.report("buttons", "sample", "K0=UP K1=UP K2=UP")
        result = self.rows()["buttons"]
        self.assertEqual(result["log_id"], completion_id)
        for key, (press, release) in self.bench.components.last["buttons"][
            "key_cycle_logs"
        ].items():
            self.assertIn(f"{key} L{press}→L{release}", result["detail"])
            logs = [
                self.bench.db.execute(
                    "SELECT message FROM logs WHERE id=?", (n,)
                ).fetchone()[0]
                for n in (press, release)
            ]
            self.assertIn(key + "=DOWN", logs[0])
            self.assertIn(key + "=UP", logs[1])
        self.bench.new(self.bench.profile, keep_connection=True)
        self.report("buttons", "sample", "K0=UP K1=UP K2=UP")
        self.assertEqual(self.rows()["buttons"]["status"], "observe")

    def test_wireless_samples_need_association_evidence(self):
        self.report("wifi", "sample", "SDK status=6")
        self.report("ble", "sample", "controller state 1")
        self.assertEqual(self.rows()["wifi"]["status"], "unknown")
        self.assertEqual(self.rows()["ble"]["status"], "unknown")
        self.report("wifi", "sample", "associated | RSSI -65 dBm")
        self.assertEqual(self.rows()["wifi"]["status"], "verified")

    def test_only_serial_rx_proves_serial_reception(self):
        self.bench.current_rx_log_id = None
        self.assertEqual(self.rows()["usb"]["status"], "unknown")
        self.bench.current_rx_log_id = 73
        self.assertEqual(self.rows()["usb"]["log_id"], 73)
        self.assertEqual(self.rows()["usb"]["name"], "串口接收")
        self.bench.transport = "TCP"
        self.report("usb", "control", "diagnostic serial heartbeat | 115200 baud")
        self.assertEqual(self.rows()["usb"]["status"], "unknown")
        self.report("usb", "ack", "0x01 ACK")
        self.assertEqual(self.rows()["usb"]["status"], "unknown")
