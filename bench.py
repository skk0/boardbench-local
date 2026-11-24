"""Local sessions, device transport and repeatable tests."""
import asyncio
import codecs
import contextlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone

import serial
from serial.tools import list_ports
from profiles import BUILTINS, validate

def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

class Bench:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS profiles(name TEXT PRIMARY KEY, tests TEXT);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started TEXT, profile TEXT, tests TEXT, port TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY, run INTEGER, at TEXT, level TEXT, source TEXT, message TEXT, raw TEXT);
        CREATE INDEX IF NOT EXISTS run_logs ON logs(run,id);
        CREATE TABLE IF NOT EXISTS results(run INTEGER, name TEXT, state TEXT, detail TEXT, at TEXT, PRIMARY KEY(run,name));
        """)
        self.ser = self.reader = self.job = self.waiter = self.proc = None
        self.taskname = ""
        self.usb = ""
        self.baud = 115200
        self.rx = self.tx = 0
        self.new("通用串口设备")

    def profiles(self):
        return {**BUILTINS, **{r[0]: json.loads(r[1]) for r in self.db.execute("SELECT * FROM profiles")}}

    def add_profile(self, data):
        name, tests = validate(data)
        if name in self.profiles():
            raise ValueError("配置名已存在，请给新版本换一个名称。")
        self.db.execute("INSERT INTO profiles VALUES(?,?)",(name,json.dumps(tests,ensure_ascii=False)))
        self.db.commit()

    def new(self, profile):
        if self.ser or self.job:
            raise ValueError("请先停止任务并断开设备，再开始新的测试。")
        self.tests = self.profiles()[profile]
        self.profile = profile
        self.run = self.db.execute("INSERT INTO runs(started,profile,tests) VALUES(?,?,?)",
            (stamp(),profile,json.dumps(self.tests,ensure_ascii=False))).lastrowid
        self.usb = ""
        self.rx = self.tx = 0
        self.log("INFO","会话","开始测试 · "+profile)

    def log(self, level, source, message, raw=""):
        self.db.execute("INSERT INTO logs(run,at,level,source,message,raw) VALUES(?,?,?,?,?,?)",
                        (self.run,stamp(),level,source,str(message),raw))
        self.db.commit()

    def result(self,name,state,detail):
        self.db.execute("INSERT OR REPLACE INTO results VALUES(?,?,?,?,?)",(self.run,name,state,detail,stamp()))
        self.log({"通过":"PASS","失败":"ERROR","跳过":"WARN"}[state],"检测",f"{name} · {state} · {detail}")

    def rows(self,sql,params=()):
        return [dict(r) for r in self.db.execute(sql,params)]

    def report(self,run):
        rows=self.rows("SELECT * FROM runs WHERE id=?",(run,))
        if not rows:
            raise ValueError("没有这次测试。")
        info=rows[0]
        info["tests"]=json.loads(info["tests"])
        info["results"]=self.rows("SELECT name,state,detail,at FROM results WHERE run=? ORDER BY at",(run,))
        info["logs"]=self.rows("SELECT id,at,level,source,message,raw FROM logs WHERE run=? ORDER BY id",(run,))
        return info

    @staticmethod
    def ports():
        return [dict(path=p.device,label=p.description,vid=p.vid,pid=p.pid,serial=p.serial_number)
                for p in list_ports.comports() if "Bluetooth-Incoming" not in p.device and "debug-console" not in p.device]

    def select(self,port):
        if port not in {p["path"] for p in self.ports()}:
            raise ValueError("串口已离线，请刷新设备并检查 USB 数据线。")
        if self.usb and port != self.usb:
            raise ValueError("换设备请先新建测试，避免混入上一块板的结果。")

    def bind(self,port):
        self.usb=port
        self.db.execute("UPDATE runs SET port=? WHERE id=?",(port,self.run))
        self.db.commit()

    def connect(self,port,baud,framing='8N1'):
        if self.ser or self.job:
            raise ValueError("串口或检测任务正在使用中。")
        self.select(port)
        if not 300 <= baud <= 4000000:
            raise ValueError("波特率超出范围。")
        if framing not in ('8N1','8E1','8O1','8N2','7E1','7O1'):
            raise ValueError('不支持此串口格式')
        handle=serial.Serial(port=None,baudrate=baud,bytesize=int(framing[0]),parity=framing[1],stopbits=int(framing[2]),timeout=0,write_timeout=0.5)
        handle.dtr=handle.rts=False
        handle.port=port
        handle.open()
        self.ser=handle
        self.bind(port)
        self.baud=baud
        self.log("INFO","串口",f"已连接 {port} · {baud} {framing}")
        self.reader=asyncio.create_task(self.read_loop())

    async def read_loop(self):
        decoder=codecs.getincrementaldecoder("utf-8")("replace")
        pending=bytearray()
        last=time.monotonic()
        def output(raw):
            display=decoder.decode(raw)
            self.log("RX","串口",display.rstrip("\r\n"),raw.hex(" "))
        try:
            while self.ser:
                count=self.ser.in_waiting
                if count:
                    data=self.ser.read(min(count,8192))
                    self.rx+=len(data)
                    self.feed(data)
                    pending.extend(data)
                    last=time.monotonic()
                while b"\n" in pending or len(pending)>=4096:
                    end=pending.find(b"\n")+1 or 4096
                    end=min(end,4096)
                    output(bytes(pending[:end]))
                    del pending[:end]
                if pending and time.monotonic()-last>.15:
                    output(bytes(pending))
                    pending.clear()
                await asyncio.sleep(.025)
        except (OSError,serial.SerialException) as exc:
            self.log("ERROR","串口","读取中断 · "+str(exc))
            self.close_port()
        finally:
            if pending:
                output(bytes(pending))

    def feed(self,data):
        if self.waiter:
            future,needle,buf=self.waiter
            buf.extend(data)
            if needle in buf and not future.done():
                future.set_result(buf.decode("utf-8","replace")[-2048:])
            if len(buf)>8192:
                del buf[:-4096]

    def close_port(self):
        if self.ser:
            self.ser.close()
            self.ser=None
            self.log("INFO","串口","连接已关闭")
        if self.waiter and not self.waiter[0].done():
            self.waiter[0].set_exception(ValueError("串口已断开"))

    async def disconnect(self):
        self.close_port()
        if self.reader:
            await self.reader
            self.reader=None

    def send(self,data):
        if not self.ser:
            raise ValueError("请先连接串口。")
        if not 1<=len(data)<=8192:
            raise ValueError("每次发送限 1–8192 字节。")
        n=self.ser.write(data)
        self.tx+=n
        self.log("TX","串口",data[:n].decode("utf-8","replace").rstrip("\r\n"),data[:n].hex(" "))
        if n!=len(data):
            raise ValueError(f"只写出 {n}/{len(data)} 字节。")

    def start(self,name,fn):
        if self.job:
            raise ValueError("已有任务运行，可先停止。")
        async def runner():
            try:
                await fn()
            except asyncio.CancelledError:
                self.log("WARN","任务",name+" · 已取消")
                raise
            except Exception as exc:
                self.log("ERROR","任务",name+" · "+str(exc))
            finally:
                self.job=None
                self.taskname=""
        self.log("INFO","任务",name+" · 开始")
        self.taskname=name
        self.job=asyncio.create_task(runner())

    async def cases(self,items):
        for test in items:
            name=test["name"]
            self.log("INFO","检测",f"{name} · 等待 {test['expect']!r} · {test.get('timeout',5)} 秒")
            future=asyncio.get_running_loop().create_future()
            self.waiter=(future,test["expect"].encode(),bytearray())
            try:
                self.send((test["tx"]+"\r\n").encode())
                reply=await asyncio.wait_for(future,test.get("timeout",5))
                self.result(name,"通过","收到匹配应答 · "+reply.strip())
            except asyncio.TimeoutError:
                self.result(name,"失败","等待设备应答超时")
            except asyncio.CancelledError:
                self.result(name,"跳过","操作员取消")
                raise
            except Exception as exc:
                self.result(name,"失败",str(exc))
                break
            finally:
                self.waiter=None

    async def probe(self,port):
        await self.disconnect()
        self.bind(port)
        self.log("INFO","ESP ROM","进入下载模式，读取芯片与 Flash 标识，结束后复位。")
        self.proc=await asyncio.create_subprocess_exec(sys.executable,*(["--esp-worker"] if getattr(sys,"frozen",False) else ["-u","-m","esptool"]),"--port",port,"--no-stub","flash_id",
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
        lines=[]
        async def collect():
            async for line in self.proc.stdout:
                s=line.decode("utf-8","replace").strip()
                if s:
                    self.log("INFO","ESP ROM",s)
                    lines.append(s)
            return await self.proc.wait()
        try:
            rc=await asyncio.wait_for(collect(),35)
            evidence=" | ".join(s for s in lines if any(x in s for x in ("Chip is","Manufacturer:","Device:","flash size:","MAC:")))
            self.result("ESP 芯片与 Flash","通过" if rc==0 and evidence else "失败",evidence or "读取失败，检查端口、BOOT 下载模式和上方原始输出。")
        except asyncio.TimeoutError:
            self.result("ESP 芯片与 Flash","失败","35 秒超时，检查下载模式。")
        finally:
            if self.proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
                await self.proc.wait()
            self.proc=None

    async def stop(self):
        if self.job:
            task=self.job
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self.job=None
            self.taskname=""
