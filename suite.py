"""Run a board acceptance plan as a batch, with grouped physical observations."""

import asyncio
import json
from bench import stamp

GROUPS = {
    "上电与复位": ("供电与启动", "复位与启动日志"),
    "按键、显示与语音": (
        "K0 / K1 / K2",
        "LCD 显示",
        "麦克风采集",
        "扬声器播放",
        "Wi-Fi 联网",
    ),
    "指示与传感器": ("双色 LED", "蜂鸣器", "姿态传感器", "环境光"),
    "存储器": ("PSRAM", "TF 卡", "EEPROM"),
    "扩展与接口": (
        "触摸（选装 / 需驱动）",
        "摄像头（选装）",
        "蓝牙 BLE",
        "USB Slave / OTG",
        "PH2.0 扩展接口",
    ),
}


def make_plan(tests, mode):
    if mode not in ("full", "quick"):
        raise ValueError("检测范围无效。")
    automatic = []
    groups = {}
    for test in tests:
        if test["kind"] == "serial":
            if mode == "full" or test.get("quick", True):
                automatic.append(test["name"])
            continue
        name = test["name"]
        group = test.get("group") or next(
            (g for g, ns in GROUPS.items() if name in ns), "外设观察"
        )
        included = test.get(
            "quick", group in ("上电与复位", "按键、显示与语音", "外设观察")
        )
        if mode == "full" or included:
            groups.setdefault(group, []).append({"name": name, "step": test["step"]})
    ordered = list(dict.fromkeys([*GROUPS, *groups]))
    return dict(
        automatic=automatic,
        groups=[{"title": g, "items": groups[g]} for g in ordered if g in groups],
    )


class Suite:
    def __init__(self, bench):
        self.b = bench
        db = bench.db
        db.execute("""CREATE TABLE IF NOT EXISTS suites(id INTEGER PRIMARY KEY,run INTEGER,started TEXT,mode TEXT,
        status TEXT, plan TEXT, outcomes TEXT, finished TEXT)""")
        db.execute(
            "UPDATE suites SET status='中断',finished=? WHERE status='运行中'",
            (stamp(),),
        )
        db.commit()
        self.current = None
        self.future = None

    def persist(self, finished=None):
        c = self.current
        self.b.db.execute(
            "UPDATE suites SET status=?,outcomes=?,finished=? WHERE id=?",
            (
                c["status"],
                json.dumps(c["outcomes"], ensure_ascii=False),
                finished,
                c["id"],
            ),
        )
        self.b.db.commit()

    def history(self, run):
        rows = self.b.rows("SELECT * FROM suites WHERE run=? ORDER BY id DESC", (run,))
        for row in rows:
            for k in ("plan", "outcomes"):
                row[k] = json.loads(row[k])
        return rows

    def state(self):
        c = self.current
        if not c or c["run"] != self.b.run:
            return None
        return c

    def start(self, mode):
        if self.b.job:
            raise ValueError("已有任务运行。")
        p = make_plan(self.b.tests, mode)
        total = len(p["automatic"]) + sum(len(g["items"]) for g in p["groups"])
        if not total:
            raise ValueError("此范围内没有检测项目。")
        rid = self.b.db.execute(
            "INSERT INTO suites(run,started,mode,status,plan,outcomes) VALUES(?,?,?,?,?,?)",
            (
                self.b.run,
                stamp(),
                mode,
                "运行中",
                json.dumps(p, ensure_ascii=False),
                "{}",
            ),
        ).lastrowid
        self.b.db.commit()
        self.current = dict(
            id=rid,
            run=self.b.run,
            mode=mode,
            total=total,
            group_count=len(p["groups"]),
            automatic_count=len(p["automatic"]),
            status="运行中",
            phase="准备",
            index=-1,
            group=None,
            outcomes={},
        )
        self.b.start(
            "整板检测" if mode == "full" else "快速检测", lambda: self.execute(p)
        )

    async def execute(self, p):
        c = self.current
        try:
            c["phase"] = "自动检测"
            for name in p["automatic"]:
                if self.b.ser:
                    test = next(t for t in self.b.tests if t["name"] == name)
                    try:
                        await self.b.cases([test])
                    finally:
                        row = self.b.db.execute(
                            "SELECT state,detail FROM results WHERE run=? AND name=?",
                            (self.b.run, name),
                        ).fetchone()
                        if row is not None:
                            c["outcomes"][name] = dict(row)
                else:
                    self.b.result(name, "跳过", "未连接设备，未发送命令")
                    c["outcomes"][name] = dict(
                        state="跳过", detail="未连接设备，未发送命令"
                    )
                self.persist()
            for i, group in enumerate(p["groups"]):
                c.update(phase="等待观察", index=i, group=group)
                self.b.log(
                    "INFO",
                    "整板",
                    f"等待确认 {i + 1}/{len(p['groups'])} · {group['title']} · {len(group['items'])} 项",
                )
                self.future = asyncio.get_running_loop().create_future()
                outcomes = await self.future
                self.future = None
                for name, result in outcomes.items():
                    self.b.result(name, result["state"], result["detail"])
                c["outcomes"].update(outcomes)
                self.persist()
            c["status"] = "已完成"
            self.b.log(
                "INFO",
                "整板",
                f"本次流程完成 · {len(c['outcomes'])}/{c['total']} 项已记录",
            )
        except asyncio.CancelledError:
            c["status"] = "已停止"
            raise
        except Exception:
            c["status"] = "中断"
            raise
        finally:
            self.future = None
            c.update(phase=c["status"], group=None)
            self.persist(stamp())

    def confirm(self, d):
        c = self.state()
        if (
            not c
            or c["id"] != d.get("suite")
            or c["index"] != d.get("index")
            or not self.future
            or self.future.done()
        ):
            raise ValueError("当前组已经变化，请查看最新进度。")
        marks = d.get("marks")
        names = {item["name"] for item in c["group"]["items"]}
        if not isinstance(marks, dict) or set(marks) != names:
            raise ValueError("提交的项目与当前组不一致。")
        for name, item in marks.items():
            if (
                not isinstance(item, dict)
                or item.get("state") not in ("通过", "失败", "跳过")
                or not isinstance(item.get("detail"), str)
                or not 1 <= len(item["detail"].strip()) <= 1500
            ):
                raise ValueError(f"请核对 {name} 的结果与观察说明。")
        self.future.set_result(
            {
                n: dict(
                    state=v["state"], detail="人工分组确认 · " + v["detail"].strip()
                )
                for n, v in marks.items()
            }
        )
