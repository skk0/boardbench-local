"""Portable, previewable diagnostic excerpts. Imported recordings never bind a port."""

import asyncio
import hashlib
import json
import re
from datetime import datetime

from boardbench.reports.ai_analysis import scrub
from boardbench.bench import stamp
from boardbench.reports.replay import annotate

FORMAT = "boardbench-fault-v1"
LIMIT = 524288
UUID = re.compile(r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IDENT = re.compile(
    r"""(?ix)(["']?(?:serial(?:_number)?|device[-_]id|client[-_]id|uuid)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^,;\s}]+)"""
)
URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def clean(value, limit=480):
    value = IDENT.sub(r"\1[标识]", scrub(value))
    value = URL.sub("[网址]", UUID.sub("[标识]", value))
    return IP.sub("[地址]", EMAIL.sub("[邮箱]", value))[:limit]


def install(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS capture_info(run INTEGER PRIMARY KEY,settings TEXT);
    CREATE TABLE IF NOT EXISTS imported_packs(digest TEXT PRIMARY KEY,run INTEGER);
    """)


async def excerpt(bench, run):
    info = bench.report(run, include_logs=False)
    db = bench.db
    upper = (
        db.execute("SELECT max(id) FROM logs WHERE run=?", (run,)).fetchone()[0] or 0
    )
    # Capture the newest issues even if they are separated by long stretches of telemetry.
    cursor = upper + 1
    issues = []
    while cursor and len(issues) < 8:
        rows = bench.rows(
            "SELECT id,level,source,message FROM logs WHERE run=? AND id<? ORDER BY id DESC LIMIT 256",
            (run, cursor),
        )
        if not rows:
            break
        issues.extend(r["id"] for r in rows if annotate(r)["issue_level"])
        cursor = rows[-1]["id"]
        await asyncio.sleep(0)
    chosen = {}

    def collect(query, args):
        for r in bench.rows(
            "SELECT id,at,level,source,message FROM logs WHERE run=? AND id<=? "
            + query,
            (run, upper, *args),
        ):
            chosen[r["id"]] = r

    collect("ORDER BY id DESC LIMIT 20", ())
    collect(
        "AND source='小智' AND message LIKE '设备签到%' ORDER BY id DESC LIMIT 1", ()
    )
    for issue in issues[:8]:
        collect("AND id<=? ORDER BY id DESC LIMIT 5", (issue,))
        collect("AND id>? ORDER BY id LIMIT 4", (issue,))
    meta = db.execute(
        "SELECT settings FROM capture_info WHERE run=?", (run,)
    ).fetchone()
    settings = (
        {clean(k, 60): clean(v, 120) for k, v in json.loads(meta[0]).items()}
        if meta
        else {}
    )
    return {
        "format": FORMAT,
        "profile": clean(info["profile"], 80),
        "settings": settings,
        "tests": [clean(t["name"], 100) for t in info["tests"]][:100],
        "results": [
            {
                "name": clean(r["name"], 100),
                "state": r["state"],
                "detail": clean(r["detail"]),
            }
            for r in info["results"][:100]
        ],
        "events": [
            {
                "at": r["at"],
                "level": r["level"],
                "source": clean(r["source"], 60),
                "message": clean(r["message"]),
                "original": r["id"],
            }
            for _, r in sorted(chosen.items())
        ],
    }


def read_pack(bench, pack):
    def string(s, n):
        if (
            not isinstance(s, str)
            or len(s) > n
            or any(ord(c) < 32 and c not in "\r\n\t" for c in s)
        ):
            raise ValueError("记录中的文本字段无效。")
        return s

    if not isinstance(pack, dict) or pack.get("format") != FORMAT:
        raise ValueError("请选择 BoardBench v1 故障包。")

    def items(key, limit):
        a = pack.get(key)
        if not isinstance(a, list) or len(a) > limit:
            raise ValueError("故障包项目过多或格式不完整。")
        return a

    profile = string(pack.get("profile"), 80)
    events = []
    for e in items("events", 128):
        if not isinstance(e, dict):
            raise TypeError("事件无效。")
        at = datetime.fromisoformat(string(e.get("at"), 40))
        if at.tzinfo is None:
            raise ValueError("日志时间需要时区。")
        level = string(e.get("level"), 12)
        source = string(e.get("source"), 60)
        if level not in {"RX", "TX", "INFO", "WARN", "ERROR", "NOTE", "PASS"}:
            raise ValueError("日志级别无效。")
        if type(e.get("original")) is not int or not 0 < e["original"] <= 2**63 - 1:
            raise ValueError("来源行号无效。")
        events.append(
            (
                e["original"],
                at.isoformat(timespec="milliseconds"),
                level,
                source,
                string(e.get("message"), 480),
            )
        )
    if len({r[0] for r in events}) != len(events):
        raise ValueError("来源行号重复。")
    tests = [
        {"name": string(n, 100), "kind": "manual", "step": "离线记录"}
        for n in items("tests", 100)
    ]
    results = []
    for r in items("results", 100):
        if not isinstance(r, dict) or r.get("state") not in ("通过", "失败", "跳过"):
            raise ValueError("检测结果无效。")
        results.append(
            (string(r.get("name"), 100), r["state"], string(r.get("detail"), 480))
        )
    settings = pack.get("settings", {})
    if not isinstance(settings, dict) or len(settings) > 12:
        raise ValueError("测试设置无效。")
    settings = {string(k, 60): string(v, 120) for k, v in settings.items()}
    digest = hashlib.sha256(
        json.dumps(pack, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    existing = bench.db.execute(
        "SELECT run FROM imported_packs WHERE digest=?", (digest,)
    ).fetchone()
    if existing:
        return existing[0]
    bench.check_space()
    with bench.db:
        run = bench.db.execute(
            "INSERT INTO runs(started,profile,tests,port) VALUES(?,?,?,?)",
            (
                events[0][1] if events else stamp(),
                profile,
                json.dumps(tests),
                "离线故障包",
            ),
        ).lastrowid
        bench.db.execute("INSERT INTO imported_packs VALUES(?,?)", (digest, run))
        bench.db.execute(
            "INSERT INTO capture_info VALUES(?,?)", (run, json.dumps(settings))
        )
        bench.db.execute(
            "INSERT INTO logs(run,at,level,source,message,raw) VALUES(?,?,'INFO','故障包',?,'')",
            (
                run,
                events[0][1] if events else stamp(),
                "离线导入 · " + json.dumps(settings, ensure_ascii=False),
            ),
        )
        local_ids = {}
        for original, at, level, source, message in events:
            row = bench.db.execute(
                "INSERT INTO logs(run,at,level,source,message,raw) VALUES(?,?,?,?,?,'')",
                (run, at, level, source, message),
            )
            local_ids[str(original)] = row.lastrowid

        def source_refs(text):
            def replace(match):
                origin = match[1]
                return (
                    f"L{local_ids[origin]}"
                    if origin in local_ids
                    else f"来源行 {origin}"
                )

            return re.sub(r"(?<![A-Za-z0-9])L(\d+)", replace, text)

        for original, _, _, _, message in events:
            remapped = source_refs(message)
            if remapped != message:
                bench.db.execute(
                    "UPDATE logs SET message=? WHERE id=?",
                    (remapped, local_ids[str(original)]),
                )
        for name, state, detail in results:
            bench.db.execute(
                "INSERT OR REPLACE INTO results VALUES(?,?,?,?,?)",
                (run, name, state, source_refs(detail), stamp()),
            )
    return run
