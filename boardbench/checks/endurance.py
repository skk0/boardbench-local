"""Connection-scoped passive endurance recordings and frozen reference measurements."""

import asyncio
import json
import time
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Endurance:
    def __init__(self, db):
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS endurance(
            id INTEGER PRIMARY KEY,run INTEGER,profile TEXT,variant TEXT,started TEXT,
            status TEXT,report TEXT,reference INTEGER DEFAULT 0)""")
        db.execute("UPDATE endurance SET status='进程中断' WHERE status='记录中'")
        db.commit()
        self.active = self.timer = None

    def start(self, bench, variant, minutes):
        if self.active or not bench.ser:
            raise ValueError("请先连上板子；同一时间只能做一轮稳定性测试。")
        if not isinstance(variant, str) or not 1 <= len(variant.strip()) <= 80:
            raise ValueError("请写一下板子型号和配件，例如 BOX 一代 / 无摄像头。")
        if type(minutes) is not int or not 1 <= minutes <= 10080:
            raise ValueError("时长要在 1–10080 分钟之间（最长 7 天）。")
        stamp = now()
        row = self.db.execute(
            "INSERT INTO endurance(run,profile,variant,started,status,report) VALUES(?,?,?,?,?,?)",
            (bench.run, bench.profile, variant.strip(), stamp, "记录中", "{}"),
        )
        self.active = {
            "id": row.lastrowid,
            "run": bench.run,
            "profile": bench.profile,
            "variant": variant.strip(),
            "started": stamp,
            "minutes": minutes,
            "status": "记录中",
            "seconds": 0,
            "rx_logs": 0,
            "boots": 0,
            "startup_ms": None,
            "components": {},
            "gaps": {},
            "identity": dict(bench.components.identity),
        }
        self.begin = self.flushed = time.monotonic()
        self.last = {}
        self.boot = None
        self.timer = asyncio.get_running_loop().call_later(
            minutes * 60, self.stop, "已到时"
        )
        self.persist()

    def update(self):
        if not self.active:
            return
        tick = time.monotonic()
        self.active["seconds"] = round(tick - self.begin, 1)
        gaps = self.active["gaps"]
        for part, previous in self.last.items():
            gaps[part] = max(gaps.get(part, 0), round(tick - previous, 2))

    def observe(self, bench, text, log_id, reboot):
        rec = self.active
        if not rec:
            return
        tick = time.monotonic()
        rec["rx_logs"] += 1
        if reboot:
            self.update()
            rec["boots"] += 1
            self.last.clear()
            self.boot = tick
        elif self.boot is not None and (
            text.strip() == "BoardBenchProbe ready"
            or bench.components.last.get("mcu", {}).get("log_id") == log_id
        ):
            rec["startup_ms"] = round((tick - self.boot) * 1000)
            self.boot = None
        rec["identity"] = dict(bench.components.identity)
        for part, row in bench.components.last.items():
            if row["log_id"] != log_id:
                continue
            rec["components"][part] = {
                "status": row["status"],
                "model": row["model"],
                "log_id": log_id,
            }
            if bench.components.periodic(part, row):
                previous = self.last.get(part)
                if previous is not None:
                    rec["gaps"][part] = max(
                        rec["gaps"].get(part, 0), round(tick - previous, 2)
                    )
                self.last[part] = tick
            else:
                self.last.pop(part, None)
            break
        if tick - self.flushed >= 5:
            self.persist()

    def persist(self):
        self.update()
        rec = self.active
        self.db.execute(
            "UPDATE endurance SET status=?,report=? WHERE id=?",
            (rec["status"], json.dumps(rec, ensure_ascii=False), rec["id"]),
        )
        self.db.commit()
        self.flushed = time.monotonic()

    def stop(self, reason="手动结束"):
        if self.active:
            self.timer.cancel()
            self.active["status"] = reason
            self.persist()
            self.active = self.timer = None

    def history(self):
        self.update()
        rows = self.db.execute(
            "SELECT id,status,report,reference FROM endurance ORDER BY id DESC"
        )
        reports = []
        for row in rows:
            report = (
                self.active
                if self.active and row[0] == self.active["id"]
                else json.loads(row[2])
            )
            reports.append(dict(report, status=row[1], reference=bool(row[3])))
        return reports

    def baseline(self, record):
        row = self.db.execute(
            "SELECT profile,variant,status FROM endurance WHERE id=?", (record,)
        ).fetchone()
        if not row or row[2] not in ("已到时", "手动结束"):
            raise ValueError("只能把正常结束的测试设成标准样板。")
        with self.db:
            self.db.execute(
                "UPDATE endurance SET reference=0 WHERE profile=? AND variant=?",
                tuple(row[:2]),
            )
            self.db.execute("UPDATE endurance SET reference=1 WHERE id=?", (record,))
