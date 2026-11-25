"""Firmware recognition and every compatibility check, on small synthetic ESP images."""

import binascii
import hashlib
import io
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

import firmware as fw

MB = 1024 * 1024
LAYOUT = [("nvs", 1, 0x02, 0x9000, 0x4000), ("otadata", 1, 0x00, 0xD000, 0x2000),
          ("phy_init", 1, 0x01, 0xF000, 0x1000), ("ota_0", 0, 0x10, 0x20000, 0x100000),
          ("ota_1", 0, 0x11, 0x120000, 0x100000), ("assets", 1, 0x82, 0x220000, 0x100000)]
BOARD = dict(chip="ESP32-S3", chip_id=9, revision=2, flash_size=4 * MB, mac="f4:12:fa:00:00:01",
             secure_boot=False, flash_encryption=False, secure_download=False,
             reported_board=dict(value="life-box", log_id=7))


def image(chip_id=9, app=True, version="2.6.0", board="life-box", flash=0x20, min_full=0, max_full=99,
          payload=4096, digest=True):
    """A minimal but valid ESP image: header, extended header, segments, checksum, SHA-256."""
    if app:
        desc = struct.pack("<II8x32s32s16s16s32s", fw.APP_DESC_MAGIC, 0, version.encode(), b"xiaozhi",
                           b"12:02:16", b"Nov 25 2026", b"v5.5")
        desc += b"\0" * (256 - len(desc))
        names = f"./main/boards/{board}/board.cc\0{board}\0./main/boards/common/button.cc\0".encode() if board else b""
        rodata = desc + names
        segments = [(0x3C000020, rodata + b"\0" * (-len(rodata) % 4)), (0x42000020, bytes(payload))]
    else:
        segments = [(0x403C9000, bytes(payload))]
    out = bytearray(struct.pack("<BBBBI", 0xE9, len(segments), 2, flash | 0x0F, 0x40375000))
    out += struct.pack("<BBBBHBHH4sB", 0xEE, 0, 0, 0, chip_id, 0, min_full, max_full, bytes(4), int(digest))
    checksum = 0xEF
    for addr, data in segments:
        out += struct.pack("<II", addr, len(data)) + data
        for b in data:
            checksum ^= b
    out += bytes(15 - len(out) % 16) + bytes([checksum])
    if digest:
        out += hashlib.sha256(out).digest()
    return bytes(out)


def table(rows=LAYOUT):
    data = b"".join(struct.pack("<2sBBII16sI", b"\xaa\x50", t, st, off, size, name.encode(), 0)
                    for name, t, st, off, size in rows)
    data += b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(data).digest()
    return data + b"\xff" * (fw.PT_SIZE - len(data))


def merged(app=None, rows=LAYOUT, boot=None, nvs=b""):
    out = bytearray(b"\xff" * 0x230000)
    for offset, data in ((0, boot or image(app=False)), (0x8000, table(rows)), (0x9000, nvs),
                         (0x20000, app or image()), (0x220000, b"ASSETS" * 1000)):
        out[offset:offset + len(data)] = data
    return bytes(out)


def board(**changes):
    return {**BOARD, "table": table(), **changes}


def states(plan):
    return {c["key"]: c["state"] for c in plan["checks"]}


class DetectionTests(unittest.TestCase):
    def test_single_bin_kinds(self):
        self.assertEqual(fw.from_bin("m.bin", merged())["kind"], "merged")
        self.assertEqual(fw.from_bin("app.bin", image())["kind"], "app")
        for data, words in [(image(app=False), "引导程序"), (table(), "分区表"), (b"hello" * 100, "看不出"), (b"", "空的")]:
            with self.assertRaisesRegex(fw.FirmwareError, words):
                fw.from_bin("x.bin", data)

    def test_zip_release_with_merged_binary(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            z.writestr("v2.6.0_life-box/merged-binary.bin", merged())
            z.writestr("README.txt", "hi")
        loaded = fw.load_bytes("release.zip", stream.getvalue())
        self.assertEqual(loaded["kind"], "merged")
        self.assertIn("merged-binary.bin", loaded["source"])

    def test_zip_with_build_output(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            z.writestr("build/flasher_args.json", json.dumps({"flash_files": {"0x0": "b/boot.bin", "0x20000": "app.bin"},
                                                              "flash_settings": {"flash_size": "4MB"}}))
            z.writestr("build/b/boot.bin", image(app=False))
            z.writestr("build/app.bin", image())
        loaded = fw.load_bytes("build.zip", stream.getvalue())
        self.assertEqual([r["offset"] for r in loaded["regions"]], [0, 0x20000])

    def test_build_directory_json_and_text_args(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "build" / "bootloader").mkdir(parents=True)
            (root / "build" / "bootloader" / "bootloader.bin").write_bytes(image(app=False))
            (root / "build" / "app.bin").write_bytes(image())
            (root / "build" / "flash_args").write_text("--flash-mode dio --flash-size 4MB\n0x0 bootloader/bootloader.bin\n0x20000 app.bin\n", encoding="utf-8")
            loaded = fw.load(root)  # the project folder works too
            self.assertEqual(loaded["kind"], "build")
            self.assertEqual(loaded["settings"]["flash_size"], "4MB")
            (root / "build" / "flasher_args.json").write_text(json.dumps({"flash_files": {"0x20000": "../../escape.bin"}}), encoding="utf-8")
            with self.assertRaisesRegex(fw.FirmwareError, "以外"):
                fw.load(root / "build" / "flasher_args.json")
            (root / "build" / "flasher_args.json").write_text(json.dumps(
                {"flash_files": {"0x20000": "app.bin"}, "app": {"file": "app.bin", "encrypted": "true"}}), encoding="utf-8")
            with self.assertRaisesRegex(fw.FirmwareError, "加密"):
                fw.load(root / "build")

    def test_damaged_images_are_rejected(self):
        good = bytearray(image())
        good[200] ^= 0xFF
        with self.assertRaisesRegex(fw.FirmwareError, "校验"):
            fw.esp_image(bytes(good))
        with self.assertRaisesRegex(fw.FirmwareError, "不完整"):
            fw.esp_image(image()[:300])
        broken = bytearray(table())
        broken[40] ^= 1
        with self.assertRaisesRegex(fw.FirmwareError, "校验"):
            fw.parse_partitions(bytes(broken))

    def test_app_description_and_board_names(self):
        info = fw.esp_image(image(version="1.9.4", board="atk-dnesp32s3-box"))
        self.assertEqual((info["desc"]["project"], info["desc"]["version"]), ("xiaozhi", "1.9.4"))
        self.assertEqual(fw.board_names(image(board="atk-dnesp32s3-box")), ["atk-dnesp32s3-box"])
        self.assertFalse(fw.esp_image(image(app=False))["app"])


class CheckTests(unittest.TestCase):
    def test_matching_merged_image_passes(self):
        plan = fw.assess(fw.from_bin("m.bin", merged()), board())
        self.assertFalse(fw.blocked(plan))
        self.assertEqual(states(plan)["board"], "pass")
        self.assertEqual(plan["firmware"]["desc"]["version"], "2.6.0")

    def test_wrong_chip_revision_flash_and_security_block(self):
        cases = [
            (merged(app=image(chip_id=0), boot=image(app=False, chip_id=0)), {}, "chip"),
            (merged(app=image(min_full=3)), {}, "revision"),
            (merged(app=image(max_full=1), boot=image(app=False, max_full=1)), {}, "revision"),
            (merged(boot=image(app=False, flash=0x40)), {}, "flash"),
            (merged(), dict(secure_boot=True), "security"),
            (merged(), dict(flash_encryption=True), "security"),
        ]
        for data, changes, key in cases:
            with self.subTest(key=key, changes=changes):
                plan = fw.assess(fw.from_bin("m.bin", data), board(**changes))
                self.assertEqual(states(plan)[key], "block")
        self.assertEqual(states(fw.assess(fw.from_bin("m.bin", merged()), board(secure_download=True)))["security"], "block")

    def test_image_bigger_than_flash_blocks(self):
        plan = fw.assess(fw.from_bin("m.bin", merged()), board(flash_size=2 * MB))
        self.assertEqual(states(plan)["fit"], "block")

    def test_app_too_big_for_its_partition_blocks(self):
        small = [r if r[0] != "ota_0" else ("ota_0", 0, 0x10, 0x20000, 0x1000) for r in LAYOUT]
        plan = fw.assess(fw.from_bin("m.bin", merged(rows=small)), board(table=table(small)))
        self.assertEqual(states(plan)["fit"], "block")
        app_only = fw.assess(fw.from_bin("app.bin", image(payload=0x2000)), board(table=table(small)))
        self.assertEqual(states(app_only)["fit"], "block")

    def test_board_mismatch_needs_second_confirmation(self):
        plan = fw.assess(fw.from_bin("m.bin", merged(app=image(board="atk-dnesp32s3-box"))), board())
        check = next(c for c in plan["checks"] if c["key"] == "board")
        self.assertEqual((check["state"], check["confirm"]), ("warn", True))
        self.assertFalse(fw.blocked(plan))
        unknown = fw.assess(fw.from_bin("m.bin", merged()), board(reported_board=None))
        self.assertEqual(states(unknown)["board"], "warn")

    def test_installed_firmware_stands_in_for_the_boot_log(self):
        installed = fw.installed(image(board="atk-dnesp32s3-box"), dict(name="ota_0"))
        self.assertEqual(installed["boards"], ["atk-dnesp32s3-box"])
        plan = fw.assess(fw.from_bin("m.bin", merged()), board(reported_board=None, installed=installed))
        check = next(c for c in plan["checks"] if c["key"] == "board")
        self.assertEqual((check["state"], check["confirm"]), ("warn", True))
        self.assertIn("xiaozhi 2.6.0", next(c for c in plan["checks"] if c["key"] == "current")["detail"])
        same = fw.installed(image(), dict(name="ota_0"))
        ok = fw.assess(fw.from_bin("m.bin", merged()), board(reported_board=None, installed=same))
        self.assertEqual(states(ok)["board"], "pass")

    def test_boot_slot_follows_otadata(self):
        parts = fw.parse_partitions(table())
        def entry(seq):
            return struct.pack("<I20sII", seq, b"\xff" * 20, 0, binascii.crc32(struct.pack("<I", seq), 0xFFFFFFFF))
        self.assertEqual(fw.boot_slot(parts, b"\xff" * 0x2000)["name"], "ota_0")
        otadata = entry(2).ljust(0x1000, b"\xff") + entry(3).ljust(0x1000, b"\xff")
        self.assertEqual(fw.boot_slot(parts, otadata)["name"], "ota_0")
        otadata = entry(2).ljust(0x1000, b"\xff") + b"\xff" * 0x1000
        self.assertEqual(fw.boot_slot(parts, otadata)["name"], "ota_1")
        broken = b"\x02\0\0\0" + b"\xff" * 28
        self.assertEqual(fw.boot_slot(parts, broken.ljust(0x2000, b"\xff"))["name"], "ota_0", "bad CRC is ignored")

    def test_moved_settings_area_needs_factory_reset(self):
        moved = [r if r[0] != "nvs" else ("nvs", 1, 0x02, 0x10000, 0x6000) for r in LAYOUT]
        plan = fw.assess(fw.from_bin("m.bin", merged(rows=moved)), board())
        self.assertEqual(states(plan)["settings"], "block")
        self.assertEqual(states(plan)["table"], "warn")
        factory = fw.assess(fw.from_bin("m.bin", merged(rows=moved)), board(), factory_reset=True)
        self.assertEqual(states(factory)["settings"], "warn")
        self.assertFalse(fw.blocked(factory))


class PlanTests(unittest.TestCase):
    def test_merged_image_is_split_around_nvs(self):
        plan = fw.assess(fw.from_bin("m.bin", merged(nvs=b"FROM-THE-FILE")), board())
        for w in plan["writes"]:
            lo, hi = fw.align_down(w["offset"]), fw.align_up(w["offset"] + w["size"])
            self.assertFalse(lo < 0xD000 and 0x9000 < hi, f"write {hex(w['offset'])} touches NVS")
        self.assertEqual([(s["offset"], s["size"]) for s in plan["skipped"]], [(0x9000, 0x4000)])
        covered = sorted((w["offset"], w["offset"] + w["size"]) for w in plan["writes"])
        self.assertEqual(covered[0][0], 0)
        self.assertEqual(covered[-1][1], 0x230000)
        gaps = [(a[1], b[0]) for a, b in zip(covered, covered[1:]) if a[1] != b[0]]
        self.assertEqual(gaps, [(0x9000, 0xD000)], "everything except NVS is written")
        nvs_backup = [r for r in plan["backup"] if r["start"] == 0x9000]
        self.assertEqual(nvs_backup, [dict(start=0x9000, end=0xD000, restore=False)])

    def test_factory_reset_writes_nvs(self):
        plan = fw.assess(fw.from_bin("m.bin", merged()), board(), factory_reset=True)
        self.assertFalse(plan["skipped"])
        self.assertTrue(any(w["offset"] <= 0x9000 < w["offset"] + w["size"] for w in plan["writes"]))

    def test_build_without_nvs_factory_reset_erases_it(self):
        build = fw.firmware("b", "build", [dict(offset=0x20000, data=image(), label="app.bin")])
        plan = fw.assess(build, board(), factory_reset=True)
        self.assertIn((0x9000, 0x4000, True), [(w["offset"], w["size"], w["blank"]) for w in plan["writes"]])

    def test_app_only_goes_to_first_app_slot_and_resets_boot_choice(self):
        plan = fw.assess(fw.from_bin("app.bin", image()), board())
        self.assertFalse(fw.blocked(plan))
        writes = [(w["offset"], w["where"]) for w in plan["writes"]]
        self.assertEqual(writes[0], (0xD000, "开机选择记录 otadata"))
        self.assertEqual(writes[1][0], 0x20000)
        self.assertEqual(states(plan)["boot"], "pass")
        no_table = fw.assess(fw.from_bin("app.bin", image()), board(table=b"\xff" * fw.PT_SIZE))
        self.assertEqual(states(no_table)["fit"], "block")


class BootVerdictTests(unittest.TestCase):
    def test_verdicts(self):
        expected = dict(version="2.6.0")
        cases = [
            (["I (310) app_init: Project name:     xiaozhi", "I (311) app_init: App version:      2.6.0"], "ok"),
            (["I (900) Display: ready"], "ok"),
            ([], "unknown"),
            (["Guru Meditation Error: Core  0 panic'ed (LoadProhibited)"], "fail"),
            (["rst:0xc (RTC_SW_CPU_RST),boot:0x8", "rst:0xc (RTC_SW_CPU_RST),boot:0x8"], "fail"),
            (["I (311) app_init: App version:      1.9.4"], "fail"),
            (["I (2728) Ota: Current version: 2.6.0", "I (5308) Ota: Running partition: ota_0"], "ok"),
            (["I (2728) Ota: Current version: 1.9.4"], "fail"),
        ]
        for lines, state in cases:
            with self.subTest(lines=lines):
                self.assertEqual(fw.boot_verdict(lines, expected)["state"], state)
        self.assertEqual(fw.boot_verdict([], expected, came_back=False)["state"], "fail")
        self.assertEqual(fw.boot_verdict(["I (900) x"], expected, drops=2)["state"], "fail")
        one_reboot = ["rst:0x15 (USB_UART_CHIP_RESET),boot:0x8", "I (900) Display: ready"]
        self.assertEqual(fw.boot_verdict(one_reboot, expected, drops=1)["state"], "ok", "drop + banner is one reboot")


if __name__ == "__main__":
    unittest.main()
