"""Recognise a firmware file and decide, before anything is written, whether it fits the board.

Everything here is pure: it works on bytes and on what was read from the board, so every
check can be tested without hardware. Writing happens in flasher.py.
"""

import binascii
import hashlib
import io
import json
import posixpath
import re
import shlex
import struct
import zipfile
from pathlib import Path

from esptool.bin_image import LoadFirmwareImage
from esptool.targets import CHIP_DEFS

SECTOR = 0x1000
PT_OFFSET, PT_SIZE = 0x8000, 0xC00
APP_DESC_MAGIC = 0xABCD5432
MAX_FILE = 64 * 1024 * 1024
MAX_ZIP_TOTAL = 128 * 1024 * 1024
CHIPS = {c.IMAGE_CHIP_ID: c for c in CHIP_DEFS.values() if getattr(c, "IMAGE_CHIP_ID", None) is not None}
BOARD_DIR = re.compile(rb"main/boards/([A-Za-z0-9_.-]{2,64})/")
NVS_SUBTYPES = (0x02, 0x04)  # nvs, nvs_keys
PART_NAMES = {"nvs": "设置区（Wi-Fi、音量等）", "otadata": "开机选择记录", "phy_init": "无线校准数据",
              "factory": "出厂应用", "ota_0": "应用 A", "ota_1": "应用 B", "assets": "资源区（图片、声音等）",
              "model": "语音模型区", "storage": "存储区", "spiffs": "文件区", "fat": "文件区"}


class FirmwareError(ValueError):
    """A file that cannot be used, explained in plain words."""


def hexaddr(n):
    return f"0x{n:06x}"


def describe(part):
    name = part["name"]
    if name in PART_NAMES:
        return f"{PART_NAMES[name]} {name}"
    if part["type"] == 0:
        return f"应用区 {name}"
    return f"数据区 {name}"


def align_down(n):
    return n // SECTOR * SECTOR


def align_up(n):
    return -(-n // SECTOR) * SECTOR


def blank(data):
    return data.count(0xFF) == len(data)


def parse_partitions(data):
    """Parse an ESP-IDF partition table. Raises FirmwareError when it is damaged."""
    rows = []
    for i in range(0, min(len(data), PT_SIZE), 32):
        entry = data[i:i + 32]
        if entry[:2] == b"\xaa\x50":
            kind, sub, offset, size = struct.unpack_from("<BBII", entry, 2)
            name = entry[12:28].split(b"\0")[0].decode("ascii", "replace")
            flags = struct.unpack_from("<I", entry, 28)[0]
            rows.append(dict(name=name, type=kind, subtype=sub, offset=offset, size=size, encrypted=bool(flags & 1)))
        elif entry[:2] == b"\xeb\xeb":
            if entry[16:32] != hashlib.md5(data[:i]).digest():
                raise FirmwareError("分区表的校验值不对，文件可能损坏了。")
            break
        elif blank(entry):
            break
        else:
            raise FirmwareError("分区表格式不对。")
    if not rows:
        raise FirmwareError("分区表是空的。")
    spans = sorted((p["offset"], p["offset"] + p["size"]) for p in rows)
    for (start, end), (nxt, _) in zip(spans, spans[1:]):
        if end > nxt:
            raise FirmwareError("分区表里有互相重叠的分区。")
    if any(p["offset"] % SECTOR or p["size"] % SECTOR or not p["size"] for p in rows):
        raise FirmwareError("分区表里有没对齐的分区。")
    return rows


def table_key(parts):
    return [(p["name"], p["type"], p["subtype"], p["offset"], p["size"]) for p in parts or []]


def text(raw):
    return raw.split(b"\0")[0].decode("utf-8", "replace").strip()


def esp_image(data):
    """Parse and verify one ESP image (bootloader or app) at the start of data, or None if it is not one."""
    if len(data) < 32 or data[0] != 0xE9:
        return None
    chip_id = struct.unpack_from("<H", data, 12)[0]
    chip = CHIPS.get(chip_id)
    if not chip:
        raise FirmwareError(f"固件里写的芯片编号 {chip_id} 不认识。")
    stream = io.BytesIO(data)
    try:
        image = LoadFirmwareImage(chip.CHIP_NAME, stream)
    except Exception as exc:  # esptool raises several kinds for truncated or damaged images
        raise FirmwareError("固件内容不完整或已损坏（" + str(exc).strip()[:120] + "）。") from exc
    if image.checksum != image.calculate_checksum():
        raise FirmwareError("固件的校验和不对，文件可能损坏了。")
    if image.append_digest and image.stored_digest != image.calc_digest:
        raise FirmwareError("固件的 SHA-256 校验值不对，文件可能损坏了。")
    length = stream.tell() + (32 if image.append_digest else 0)
    info = dict(chip=chip.CHIP_NAME, chip_id=chip_id, length=length,
                min_rev=image.min_rev, min_rev_full=image.min_rev_full, max_rev_full=image.max_rev_full,
                flash_size=None, app=any(image.is_flash_addr(s.addr) for s in image.segments), desc=None)
    sizes = {v: k for k, v in getattr(chip, "FLASH_SIZES", {}).items()}
    info["flash_size"] = sizes.get(image.flash_size_freq & 0xF0)
    if struct.unpack_from("<I", data, 32)[0] == APP_DESC_MAGIC:
        info["desc"] = dict(version=text(data[48:80]), project=text(data[80:112]),
                            time=text(data[112:128]), date=text(data[128:144]), idf=text(data[144:176]))
        info["app"] = True
    return info


def board_names(app):
    """Board names a xiaozhi-style build mentions (its main/boards/<name>/ sources)."""
    return sorted({m.decode() for m in BOARD_DIR.findall(app)} - {"common"})


def boot_slot(parts, otadata):
    """The app partition the bootloader starts, read from the otadata sectors like ESP-IDF's otatool."""
    # ponytail: ignores rollback states (an aborted OTA); good enough to name what is installed.
    apps = [p for p in parts if p["type"] == 0]
    otas = sorted((p for p in apps if 0x10 <= p["subtype"] < 0x20), key=lambda p: p["subtype"])
    seqs = []
    for at in (0, SECTOR):
        if len(otadata) >= at + 32:
            seq, _, _, crc = struct.unpack_from("<I20sII", otadata, at)
            if seq not in (0, 0xFFFFFFFF) and crc == binascii.crc32(struct.pack("<I", seq), 0xFFFFFFFF):
                seqs.append(seq)
    if seqs and otas:
        return otas[(max(seqs) - 1) % len(otas)]
    return next((p for p in apps if p["subtype"] == 0), None) or (otas[0] if otas else None)


def installed(head, slot):
    """What the board is running, from the first bytes of its boot app (name, version, board names)."""
    if head[:1] != b"\xe9" or struct.unpack_from("<I", head, 32)[0] != APP_DESC_MAGIC:
        return None
    return dict(slot=slot["name"], desc=dict(version=text(head[48:80]), project=text(head[80:112]),
                                              date=text(head[128:144])), boards=board_names(head))


def size_bytes(label):
    if not label:
        return None
    number = int(re.match(r"\d+", label)[0])
    return number * (1024 * 1024 if "MB" in label else 1024)


# ---------------------------------------------------------------- loading


def firmware(source, kind, regions, settings=None):
    for r in regions:
        if len(r["data"]) > MAX_FILE:
            raise FirmwareError("文件太大，超过 64 MB。")
    return dict(source=source, kind=kind, regions=regions, settings=settings or {})


def from_bin(name, data):
    if not data:
        raise FirmwareError("文件是空的。")
    if len(data) > MAX_FILE:
        raise FirmwareError("文件太大，超过 64 MB。")
    has_table = data[PT_OFFSET:PT_OFFSET + 2] == b"\xaa\x50"
    for boot in (0x0, 0x1000, 0x2000):  # bootloader offset differs by chip family
        if has_table and data[boot:boot + 1] == b"\xe9" and blank(data[:boot]):
            return firmware(name, "merged", [dict(offset=0, data=data, label=name)])
    if data[:2] == b"\xaa\x50":
        raise FirmwareError("这只是分区表文件，不是完整固件。请选整包固件（常叫 merged-binary.bin）或编译文件夹。")
    image = esp_image(data)
    if not image:
        raise FirmwareError("看不出这是乐鑫（ESP32 系列）芯片的固件。")
    if not image["app"]:
        raise FirmwareError("这是引导程序（bootloader），不是完整固件。请选整包固件或编译文件夹。")
    return firmware(name, "app", [dict(offset=None, data=data, label=name)])


def parse_flash_args(text_):
    """ESP-IDF build/flash_args: one line of options, then "offset path" lines."""
    lines = [ln for ln in text_.splitlines() if ln.strip()]
    options = shlex.split(lines[0]) if lines and lines[0].lstrip().startswith("--") else []
    settings = {k[2:].replace("-", "_"): v for k, v in zip(options[::2], options[1::2])}
    files = {}
    for ln in lines[1 if options else 0:]:
        offset, path = ln.split(None, 1)
        files[offset] = path.strip()
    return files, settings, {}


def build_regions(files, read):
    regions = []
    for offset, path in files.items():
        try:
            where = int(offset, 0)
        except ValueError as exc:
            raise FirmwareError(f"写入位置 {offset} 看不懂。") from exc
        data = read(path)
        regions.append(dict(offset=where, data=data, label=posixpath.basename(path)))
    if not regions:
        raise FirmwareError("编译结果里没有要写入的文件。")
    return sorted(regions, key=lambda r: r["offset"])


def from_args(source, files, settings, encrypted, read):
    if any(str(v.get("encrypted", "false")).lower() == "true" for v in encrypted.values() if isinstance(v, dict)):
        raise FirmwareError("这个固件要求加密写入，本软件不支持，以免把板子锁死。")
    return firmware(source, "build", build_regions(files, read), settings)


def from_dir(folder):
    folder = Path(folder)
    for base in (folder, folder / "build"):
        base = base.resolve()

        def read(rel, base=base):
            path = (base / rel).resolve()
            if not path.is_relative_to(base):
                raise FirmwareError(f"编译结果引用了文件夹以外的文件：{rel}")
            if not path.is_file():
                raise FirmwareError(f"编译文件夹里少了 {rel}，请先完整编译一次。")
            if path.stat().st_size > MAX_FILE:
                raise FirmwareError(f"{rel} 太大，超过 64 MB。")
            return path.read_bytes()

        if (base / "flasher_args.json").is_file():
            args = json.loads((base / "flasher_args.json").read_text())
            return from_args(str(base), args.get("flash_files", {}), args.get("flash_settings", {}), args, read)
        if (base / "flash_args").is_file():
            files, settings, extra = parse_flash_args((base / "flash_args").read_text())
            return from_args(str(base), files, settings, extra, read)
    raise FirmwareError("这个文件夹里没找到编译结果（flasher_args.json 或 flash_args）。请选 ESP-IDF 工程的 build 文件夹。")


def from_zip(name, stream):
    try:
        archive = zipfile.ZipFile(stream)
    except zipfile.BadZipFile as exc:
        raise FirmwareError("压缩包打不开，可能下载不完整。") from exc
    with archive:
        infos = {i.filename: i for i in archive.infolist() if not i.is_dir()}
        if sum(i.file_size for i in infos.values()) > MAX_ZIP_TOTAL:
            raise FirmwareError("压缩包解开后太大。")

        def member(path):
            info = infos.get(path)
            if not info:
                raise FirmwareError(f"压缩包里少了 {path}。")
            if info.file_size > MAX_FILE:
                raise FirmwareError(f"{path} 太大，超过 64 MB。")
            return archive.read(info)

        for marker in ("flasher_args.json", "flash_args"):
            found = sorted((p for p in infos if posixpath.basename(p) == marker), key=len)
            if found:
                base = posixpath.dirname(found[0])

                def read(rel, base=base):
                    path = posixpath.normpath(posixpath.join(base, rel))
                    if path.startswith("../") or path.startswith("/"):
                        raise FirmwareError(f"压缩包引用了包外的文件：{rel}")
                    return member(path)

                if marker == "flasher_args.json":
                    args = json.loads(member(found[0]))
                    return from_args(name, args.get("flash_files", {}), args.get("flash_settings", {}), args, read)
                files, settings, extra = parse_flash_args(member(found[0]).decode())
                return from_args(name, files, settings, extra, read)
        bins = [p for p in infos if p.lower().endswith(".bin")]
        merged = [p for p in bins if "merged" in posixpath.basename(p).lower()]
        pick = merged or bins
        if len(pick) != 1:
            raise FirmwareError("压缩包里没找到唯一的整包固件（.bin）。" if not pick else
                                "压缩包里有好几个 .bin，分不清该刷哪个。请解压后选整包固件。")
        fw = from_bin(pick[0], member(pick[0]))
        fw["source"] = name + " · " + pick[0]
        return fw


def load(path):
    path = Path(path).expanduser()
    if path.is_dir():
        return from_dir(path)
    if not path.is_file():
        raise FirmwareError("找不到这个文件或文件夹。")
    if path.name in ("flasher_args.json", "flash_args"):
        return from_dir(path.parent)
    if path.stat().st_size > MAX_ZIP_TOTAL:
        raise FirmwareError("文件太大。")
    if zipfile.is_zipfile(path):
        with path.open("rb") as stream:
            return from_zip(path.name, stream)
    return from_bin(path.name, path.read_bytes())


def load_bytes(name, data):
    if zipfile.is_zipfile(io.BytesIO(data)):
        return from_zip(name, io.BytesIO(data))
    return from_bin(name, data)


# ---------------------------------------------------------------- deciding


def slice_at(regions, offset):
    for r in regions:
        if r["offset"] <= offset < r["offset"] + len(r["data"]):
            return r["data"][offset - r["offset"]:]
    return b""


def container(parts, start, end):
    return next((p for p in parts if p["offset"] <= start and end <= p["offset"] + p["size"]), None)


def cut(regions, holes):
    """Split regions around holes (sorted [start, end) ranges)."""
    out = []
    for r in regions:
        pieces = [(r["offset"], r["offset"] + len(r["data"]))]
        for hs, he in holes:
            pieces = [p for s, e in pieces for p in ((s, min(e, hs)), (max(s, he), e)) if p[0] < p[1]]
        for s, e in pieces:
            out.append(dict(offset=s, data=r["data"][s - r["offset"]:e - r["offset"]], label=r["label"]))
    return out


def merge_ranges(ranges):
    out = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [tuple(r) for r in out]


def revision_problem(image, rev):
    """esptool's own min/max chip revision rule, returning plain words."""
    lo, hi = image["min_rev_full"], image["max_rev_full"]
    if hi == 0 or (hi == 65535 and lo == 0 and image["min_rev"]):
        have = rev % 100 if image["chip"] == "ESP32-C3" else rev // 100  # legacy field: C3 minor, others major
        return None if have >= image["min_rev"] else \
            f"固件要求更新的芯片版本（{image['min_rev']}），板子是 v{rev // 100}.{rev % 100}。"
    if lo <= rev <= hi:
        return None
    upper = "以上" if hi == 65535 else f"到 v{hi // 100}.{hi % 100}"
    return f"固件只支持芯片版本 v{lo // 100}.{lo % 100} {upper}，板子是 v{rev // 100}.{rev % 100}。"


def assess(fw, board, factory_reset=False):
    """Every check before writing, plus exactly what will be written, skipped and backed up."""
    checks = []

    def add(key, state, title, detail, confirm=False):
        checks.append(dict(key=key, state=state, title=title, detail=detail, confirm=confirm))

    plan = dict(checks=checks, writes=[], skipped=[], backup=[], table_changes=[], firmware=None)
    if board.get("secure_download"):
        add("security", "block", "芯片安全设置", "板子开启了“安全下载模式”，本软件不能安全地刷写它。")
        return plan
    if board.get("secure_boot") or board.get("flash_encryption"):
        what = "安全启动" if board.get("secure_boot") else "闪存加密"
        add("security", "block", "芯片安全设置", f"板子开启了{what}。普通固件写进去会让板子无法开机，所以不刷。")
    else:
        add("security", "pass", "芯片安全设置", "没有开启安全启动或闪存加密，可以正常刷写。")

    try:
        current = parse_partitions(board["table"]) if board.get("table") is not None else None
    except FirmwareError:
        current = None
    regions = [dict(r) for r in fw["regions"]]
    new_table = None
    table_bytes = slice_at(regions, PT_OFFSET)[:PT_SIZE] if fw["kind"] != "app" else b""
    if table_bytes[:2] == b"\xaa\x50":
        try:
            new_table = parse_partitions(table_bytes)
        except FirmwareError as exc:
            add("image", "block", "文件是否完好", str(exc))
            return plan
    parts = new_table or current

    if fw["kind"] == "app":
        if not current:
            add("fit", "block", "放得下吗", "读不到板子现在的分区表，不知道应用该放哪，所以不刷。请改用整包固件。")
            return plan
        apps = [p for p in current if p["type"] == 0]
        target = next((p for p in apps if p["subtype"] == 0), None) or next((p for p in apps if p["subtype"] == 0x10), None)
        if not target:
            add("fit", "block", "放得下吗", "板子的分区表里没有能放应用的地方。")
            return plan
        regions[0]["offset"] = target["offset"]

    # ---- the images inside: complete, right chip, right revision
    images = []
    chip = CHIPS.get(board["chip_id"])
    boot_offset = getattr(chip, "BOOTLOADER_FLASH_OFFSET", 0)
    try:
        boot = esp_image(slice_at(regions, boot_offset)) if fw["kind"] != "app" else None
        if boot:
            images.append(("引导程序", boot_offset, boot, None))
        for p in parts or []:
            data = slice_at(regions, p["offset"]) if p["type"] == 0 else b""
            if not data or blank(data[:32]):
                continue
            try:
                image = esp_image(data)
                if not image or not image["app"]:
                    raise FirmwareError(f"{describe(p)} 的位置上不是有效的应用。")
            except FirmwareError:
                if any(i[3] for i in images):
                    continue  # only the app that boots has to be valid; a stale second slot is left as it is
                raise
            images.append((describe(p), p["offset"], image, p))
    except FirmwareError as exc:
        add("image", "block", "文件是否完好", str(exc))
        return plan
    apps = [i for i in images if i[3]]
    if not apps:
        add("image", "block", "文件是否完好", "文件里没找到可以开机的应用程序。")
        return plan
    add("image", "pass", "文件是否完好", "文件格式正确，每段内容的校验值都对得上。")

    wrong = [i for i in images if i[2]["chip_id"] != board["chip_id"]]
    if wrong:
        add("chip", "block", "芯片型号", f"这个固件是给 {wrong[0][2]['chip']} 做的，你的板子是 {board['chip']}，刷了会开不了机。")
    else:
        add("chip", "pass", "芯片型号", f"固件和板子都是 {board['chip']}。")
    problems = [p for i in images if (p := revision_problem(i[2], board["revision"]))]
    if problems and not wrong:
        add("revision", "block", "芯片版本", problems[0])
    elif not wrong:
        add("revision", "pass", "芯片版本", f"板子的芯片版本 v{board['revision'] // 100}.{board['revision'] % 100} 在固件支持的范围内。")

    real = board["flash_size"]
    claimed = size_bytes((boot or apps[0][2])["flash_size"]) or size_bytes(fw["settings"].get("flash_size"))
    if claimed and claimed > real:
        add("flash", "block", "闪存大小", f"固件按 {claimed >> 20} MB 闪存编译，板子只有 {real >> 20} MB，放不下。")
    elif claimed:
        extra = "" if claimed == real else f"（板子有 {real >> 20} MB，多出的空间不会用到）"
        add("flash", "pass", "闪存大小", f"固件按 {claimed >> 20} MB 闪存编译{extra}。")
    else:
        add("flash", "warn", "闪存大小", "固件里没写闪存大小，没法核对。")
    end = max(r["offset"] + len(r["data"]) for r in regions)
    if end > real:
        add("fit", "block", "放得下吗", f"文件一直写到 {hexaddr(end)}，超出了板子 {real >> 20} MB 的闪存。")

    # ---- partition table
    if new_table and current is None:
        add("table", "warn", "分区布局", "读不到板子现在的分区表（可能是空板子），会直接写入新的分区表。")
    elif new_table and table_key(new_table) != table_key(current):
        old = {p["name"]: p for p in current}
        for p in new_table:
            o = old.pop(p["name"], None)
            if not o:
                plan["table_changes"].append(f"新增 {describe(p)}：{hexaddr(p['offset'])}，{p['size'] >> 10} KB")
            elif (o["offset"], o["size"], o["type"], o["subtype"]) != (p["offset"], p["size"], p["type"], p["subtype"]):
                plan["table_changes"].append(
                    f"{describe(p)}：{hexaddr(o['offset'])}/{o['size'] >> 10} KB → {hexaddr(p['offset'])}/{p['size'] >> 10} KB")
        plan["table_changes"] += [f"去掉 {describe(p)}" for p in old.values()]
        add("table", "warn", "分区布局", "新固件会改变闪存的分区布局：" + "；".join(plan["table_changes"]) + "。")
    elif new_table:
        add("table", "pass", "分区布局", "分区布局和板子现在的一样。")
    else:
        add("table", "pass", "分区布局", "不改分区表，沿用板子现在的布局。")

    nvs_now = [p for p in current or [] if p["type"] == 1 and p["subtype"] in NVS_SUBTYPES]
    nvs_new = [p for p in parts or [] if p["type"] == 1 and p["subtype"] in NVS_SUBTYPES]
    moved = new_table is not None and current is not None and table_key(nvs_now) != table_key(nvs_new)
    if moved and not factory_reset:
        add("settings", "block", "Wi-Fi 和设置",
            "新固件把设置区挪了位置，旧的 Wi-Fi 和设置一定会丢。要刷的话请勾选“恢复出厂（会清空 Wi-Fi 等设置）”。")
    elif moved:
        add("settings", "warn", "Wi-Fi 和设置", "设置区换了位置，刷完后 Wi-Fi 和设置会清空，需要重新配网。")
    elif factory_reset:
        add("settings", "warn", "Wi-Fi 和设置", "已选“恢复出厂”：设置区也会被写，Wi-Fi 和设置会清空或换成文件里的内容。")
    else:
        add("settings", "pass", "Wi-Fi 和设置", "设置区不会被写，Wi-Fi、音量等设置都保留。")

    for name, offset, image, part in apps:
        if image["length"] > part["size"]:
            add("fit", "block", "放得下吗",
                f"应用有 {image['length'] >> 10} KB，{describe(part)} 只有 {part['size'] >> 10} KB，放不下。")
    for r in regions if fw["kind"] == "build" else []:
        start, stop = r["offset"], r["offset"] + len(r["data"])
        home = container(parts or [], start, stop)
        if start % SECTOR:
            add("fit", "block", "放得下吗", f"{r['label']} 的写入位置 {hexaddr(start)} 没有对齐，不安全。")
        elif not home and start >= min((p["offset"] for p in parts or []), default=PT_OFFSET + SECTOR):
            add("fit", "block", "放得下吗", f"{r['label']}（{len(r['data']) >> 10} KB）超出了留给它的位置。")
    if not any(c["key"] == "fit" for c in checks):
        add("fit", "pass", "放得下吗", "每一段都放得进对应的位置。")

    # ---- what the firmware is and which board it was built for
    main = apps[0][2]
    desc = main["desc"] or {}
    names = sorted({n for _, offset, _, part in apps for n in board_names(slice_at(regions, offset)[:part["size"]])})
    plan["firmware"] = dict(desc=desc, boards=names, chip=main["chip"])
    if desc:
        add("about", "pass", "这是什么固件",
            f"{desc['project']} {desc['version']}，{desc['date']} {desc['time']} 编译（ESP-IDF {desc['idf']}）" +
            (f"，给 {'、'.join(names)} 板子用" if names else "") + "。")
    else:
        add("about", "warn", "这是什么固件", "固件里没有名称和版本信息，看不出是什么固件。")
    reported = (board.get("reported_board") or {}).get("value")
    now = board.get("installed") or {}
    if now.get("desc"):
        d = now["desc"]
        add("current", "pass", "板子上现在是什么", f"{d['project']} {d['version']}（{d['date']} 编译）" +
            (f"，给 {'、'.join(now['boards'])} 板子用" if now.get("boards") else "") + "。")
    where = "板子自报型号"
    if not reported and len(now.get("boards") or []) == 1:
        reported, where = now["boards"][0], "板子上现在的固件是给"
    app_data = slice_at(regions, apps[0][1])[:apps[0][3]["size"]]
    if reported and (reported in names or b"\0" + reported.encode() + b"\0" in app_data):
        add("board", "pass", "是不是给这块板子的", f"{where} {reported}，新固件也是给 {reported} 的，对得上。")
    elif reported and names:
        add("board", "warn", "是不是给这块板子的",
            f"{where} {reported}，但这个固件是给 {'、'.join(names)} 做的。刷错板型可能屏幕不亮、没声音、按键不灵。", confirm=True)
    elif reported:
        add("board", "warn", "是不是给这块板子的", f"{where} {reported}，新固件里没找到板型信息，没法核对。")
    else:
        add("board", "warn", "是不是给这块板子的",
            "还没看到板子自报型号（连上监视、板子开机时会显示），没法核对。" +
            (f"固件是给 {'、'.join(names)} 做的。" if names else ""))

    # ---- what gets written, skipped and backed up
    holes = merge_ranges((p["offset"], p["offset"] + p["size"]) for p in nvs_now + nvs_new)
    writes = regions if factory_reset else cut(regions, holes)
    if fw["kind"] == "merged":  # split the image by partition so every piece is labelled
        edges = sorted({p["offset"] for p in parts or []} | {p["offset"] + p["size"] for p in parts or []}
                       | {PT_OFFSET, PT_OFFSET + SECTOR})
        split = []
        for w in writes:
            points = [e for e in edges if w["offset"] < e < w["offset"] + len(w["data"])]
            for s, e in zip([w["offset"], *points], [*points, w["offset"] + len(w["data"])]):
                split.append(dict(offset=s, data=w["data"][s - w["offset"]:e - w["offset"]], label=w["label"]))
        writes = split
    if not factory_reset:
        for s, e in holes:
            if any(w["offset"] < e and s < w["offset"] + len(w["data"]) for w in regions):
                plan["skipped"].append(dict(offset=s, size=e - s, label="设置区（Wi-Fi、音量等），保持不变"))
    else:
        for p in nvs_new:
            if not any(w["offset"] <= p["offset"] and p["offset"] + p["size"] <= w["offset"] + len(w["data"]) for w in writes):
                writes.append(dict(offset=p["offset"], data=b"\xff" * p["size"], label="清空设置区"))
    otadata = next((p for p in parts or [] if p["type"] == 1 and p["subtype"] == 0), None)
    boot_app = apps[0][3]
    if otadata and not any(w["offset"] <= otadata["offset"] < w["offset"] + len(w["data"]) for w in writes):
        first = next((p for p in parts if p["type"] == 0 and p["subtype"] == 0), None) or \
            next((p for p in parts if p["type"] == 0 and p["subtype"] == 0x10), None)
        if boot_app is first:
            writes.append(dict(offset=otadata["offset"], data=b"\xff" * otadata["size"], label="清空开机选择记录，让板子从新应用开机"))
        else:
            add("boot", "warn", "刷完能开机吗", f"新应用放在 {describe(boot_app)}，板子不一定从这里开机。")
    if not any(c["key"] == "boot" for c in checks):
        add("boot", "pass", "刷完能开机吗", f"板子下次开机会从 {describe(boot_app)} 启动新应用。")
    for w in writes:
        home = container(parts or [], w["offset"], w["offset"] + len(w["data"]))
        w["where"] = describe(home) if home else ("引导程序" if w["offset"] < PT_OFFSET else
                                                   "分区表" if w["offset"] < PT_OFFSET + SECTOR else "未分区空间")
        w["size"] = len(w["data"])
        w["blank"] = blank(w["data"])
    plan["writes"] = sorted((w for w in writes if w["data"]), key=lambda w: w["offset"])
    for w in plan["writes"] if not factory_reset else []:
        lo, hi = align_down(w["offset"]), align_up(w["offset"] + w["size"])
        if any(lo < e and s < hi for s, e in holes):
            raise FirmwareError("内部检查没通过：写入范围会碰到设置区，已停止。")
    # Back up every sector a write touches (restored by "恢复备份"), plus the partition table and the
    # settings area for safe keeping (never written back unless they were written).
    touched = merge_ranges((align_down(w["offset"]), align_up(w["offset"] + w["size"])) for w in plan["writes"])
    plan["backup"] = [dict(start=s, end=e, restore=True) for s, e in touched]
    for s, e in [(PT_OFFSET, PT_OFFSET + SECTOR)] + [(p["offset"], p["offset"] + p["size"]) for p in nvs_now]:
        if not any(ts <= s and e <= te for ts, te in touched):
            plan["backup"].append(dict(start=s, end=e, restore=False))
    plan["backup"].sort(key=lambda r: r["start"])
    return plan


def blocked(plan):
    return [c for c in plan["checks"] if c["state"] == "block"]


# ---------------------------------------------------------------- after writing

CRASH = re.compile(r"Guru Meditation|abort\(\) was called|Backtrace: ?0x|panic'ed|assert failed|"
                   r"Brownout detector|invalid header|No bootable app|Failed to verify app|"
                   r"ota data partition invalid|stack overflow", re.I)
VERSION = re.compile(r"(?:app_init: (Project name|App version)|Ota: (Current version)):\s+(\S+)")


def boot_verdict(lines, expected=None, came_back=True, drops=0):
    """Plain-language verdict on the boot log seen after writing."""
    if not came_back:
        return dict(state="fail", text="刷完后板子没有重新连上电脑。请拔插一下 USB 线；还不行就用“恢复备份”。")
    seen = {}
    boots = 0
    for line in lines:
        plain = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        if CRASH.search(plain):
            return dict(state="fail", text="板子开机时出错了：" + plain[:160] + "。可以用“恢复备份”回到刷写前。")
        if plain.startswith("rst:0x"):
            boots += 1
        if m := VERSION.search(plain):
            seen["App version" if m[2] else m[1]] = m[3]
    boots = max(boots, drops)  # a reboot shows as a ROM banner, a USB drop, or both
    if boots >= 2:
        return dict(state="fail", text=f"板子在反复重启（看到 {boots} 次开机）。可以用“恢复备份”回到刷写前。")
    expected = expected or {}
    if seen.get("App version") and expected.get("version") and seen["App version"] != expected["version"]:
        return dict(state="fail", text=f"板子开机跑的是 {seen['App version']} 版，不是刚刷的 {expected['version']} 版。")
    if seen.get("App version"):
        name = (seen.get('Project name', '') + ' ' + seen['App version']).strip()
        return dict(state="ok", text=f"新固件已经开机：{name}。")
    if any(line.strip() for line in lines):
        return dict(state="ok", text="板子已经开机，正在正常输出日志，没有发现出错或反复重启。")
    return dict(state="unknown", text="板子已重新连上电脑，但没有输出开机信息。请看看屏幕或指示灯是否正常。")
