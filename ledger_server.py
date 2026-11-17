#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鱼记 · YuLedger · 本地数据服务
=====================================================================
让网页真正连接 ledger.db（SQLite），而不是读浏览器里写死的数据。

零第三方依赖，只用 Python 标准库。控制台启动信息只打印一次，
访问日志固定占用下方 10 行、原地覆盖刷新，不会把数据库路径 / 访问地址顶出屏幕；
每行带时间、客户端 IP 与设备标识（安卓 / 苹果 / 电脑 / Linux 等），
下方一行汇总已见设备。Windows 控制台与 Linux / macOS 终端都适用；
把 stdout 或 stderr 重定向到文件即可自动恢复完整逐行日志（守护进程模式同理）。

启动：

    python ledger_server.py                      # 默认监听 0.0.0.0:8765（本机 + 局域网手机）
    python ledger_server.py --host 127.0.0.1     # 只想本机访问时
    python ledger_server.py --port 9000          # 换端口
    python ledger_server.py --db "D:/data/ledger.db"    # 指定数据库位置（部署用）
    python ledger_server.py --no-browser         # 不自动打开浏览器

接口（JSON）：
    GET    /api/health                 服务与数据库状态
    GET    /api/time                   权威「今天」：阿里云授时 → 最新记录 → 系统时间（三级回退）
    GET    /api/state                  完整账本 { records, accounts, settings, profile }
    PUT    /api/state                  全量写回（单事务，网页保存时调用）
    POST   /api/records                新增 / 更新一条流水
    DELETE /api/records?id=xxx         删除一条；不带 id 则清空流水
    PUT    /api/settings               更新单个设置项，如 {"key":"saving_goal","value":500000}
    PUT    /api/accounts               新增 / 更新一个账户
    GET    /api/export.sql             导出当前库内容为 SQL 备份脚本
    GET    /api/writes                 最近写入审计（读 log 表，每次写库的字段级变更）

建库规则：只有启动时发现 ledger.db 不存在（或完全是空库）才创建空表结构，
并预置 5 个默认资产账户（余额均为 0）；已有账目的库一律不动。
运行期间数据库文件丢失一律返回 503，绝不自动重建——恢复文件或重启服务即可。
"""

from __future__ import annotations

import argparse
import email.utils
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import struct
import sys
import threading
import time
import unicodedata
import urllib.request
import webbrowser
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

# PyInstaller / Tauri sidecar 下 __file__ 指向临时解压目录，静态资源要用
# _MEIPASS 定位；账本则始终落在 --db 或 app_db_path() 指定的用户可写目录。
RESOURCE_BASE = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)
BASE = RESOURCE_BASE
DB_PATH = BASE / "ledger.db"
HTML_PATH = RESOURCE_BASE / "index.html"

# 本机应用模式：只绑回环、端口由系统随机分配、每次启动生成一次性 token。
# 默认关闭，原有 0.0.0.0:8765 的局域网 / NAS 用法完全不变。
APP_MODE = False
APP_TOKEN = ""
APP_PORT = 0

# 新账本的默认账户：全部是资产类（没有应收 / 待还），note 统一写「余额」，金额从 0 开始。
# 只在建新库时插入；已有账本（哪怕账户被删空）不会被重新塞回这些账户。
DEFAULT_ACCOUNTS = (
    ("a1", "支付宝"),
    ("a2", "微信"),
    ("a3", "现金"),
    ("a4", "宁波银行卡"),
    ("a5", "江苏银行卡"),
)

# settings 表里全部存 TEXT，读出来时按 key 还原类型，否则 "false" 会被前端当成 true
# saving_goal 是页面「当前存款」进度条的储蓄目标；theme 由服务端按授时自动维护
# 只允许这三个键写入数据库：旧页面 / 旧快照推来的 budget、savingsTarget 等一律拒收，
# 否则清空记录或旧快照页全量写回时会把废弃键复活进 settings 表。
SETTING_KEYS = {"saving_goal", "theme", "profile"}
NUMERIC_KEYS = {"saving_goal"}
# lockToday 已改为前端常量 LOCK_TODAY，不再由数据库控制。
# 库里若还残留这一行：读出时忽略、全量写回时不写入、导出脚本也不带它。
DEPRECATED_KEYS = {"lockToday", "budget", "savingsTarget"}

# 授时：阿里云公共 NTP（官方文档 help.aliyun.com/document_detail/187600.htm）
NTP_HOSTS = ("ntp.aliyun.com", "ntp1.aliyun.com", "time.pool.aliyun.com")
NTP_TIMEOUT = 0.8
HTTP_TIME_URL = "https://www.aliyun.com/"   # UDP 123 被拦时读它的 Date 响应头
HTTP_TIME_TIMEOUT = 1.5
NTP_EPOCH = 2208988800                      # 1900-01-01 → 1970-01-01 的秒数


# --------------------------------------------------------------------------
# 数据库
# --------------------------------------------------------------------------
class DatabaseMissing(Exception):
    """运行期间数据库文件不存在——绝不自动重建，交给用户恢复或重启服务。"""


def connect(create: bool = False) -> sqlite3.Connection:
    """打开数据库。create=False（默认）时文件缺失直接抛 DatabaseMissing：
    sqlite3.connect 会悄悄建一个 0KB 空库，那会让「恢复 ledger.db」的提示失去意义；
    只有服务启动流程（init_db / main 对时）允许 create=True。"""
    if not create and not DB_PATH.exists():
        raise DatabaseMissing(str(DB_PATH))
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 10000")
    return con


def init_db() -> None:
    """只在启动时建库：不存在 / 空库就建表并预置默认账户；已有账目的库一律不动。"""
    if not DB_PATH.exists():
        DB_PATH.touch()

    con = connect(create=True)
    try:
        has_tables = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('records','accounts','settings')"
        ).fetchone()[0] == 3
        if has_tables:
            n_records = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            print(f"[init] 沿用现有账本：{n_records} 条流水")
        else:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS records
                  (id TEXT PRIMARY KEY, date TEXT NOT NULL,
                   type TEXT NOT NULL CHECK (type IN ('in','out')),
                   amount REAL NOT NULL, category TEXT, title TEXT, account TEXT,
                   linked INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS idx_records_date ON records (date);
                CREATE TABLE IF NOT EXISTS accounts
                  (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT, note TEXT,
                   amount REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                """
            )
            # 半建好的旧库可能已有 accounts 数据，只有空表才预置，避免主键冲突 / 覆盖账本
            if con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
                con.executemany(
                    "INSERT INTO accounts (id, kind, name, note, amount) "
                    "VALUES (?, 'asset', ?, '余额', 0)",
                    DEFAULT_ACCOUNTS,
                )
            con.commit()
            print(f"[init] 已创建空表结构；默认账户 {len(DEFAULT_ACCOUNTS)} 个（已有数据则不覆盖）")

        # 平滑升级：老库和旧 SQL 快照的 records 表没有 linked 列
        cols = {r[1] for r in con.execute("PRAGMA table_info(records)")}
        if "linked" not in cols:
            con.execute("ALTER TABLE records ADD COLUMN linked INTEGER NOT NULL DEFAULT 0")
            con.commit()
            print("[init] records 表已补充 linked 列（0=未计入余额，1=已计入）")

        # 写库审计表：替代早期的 ledger-writes.log 文本日志，不进目录、随库走
        con.execute(
            "CREATE TABLE IF NOT EXISTS log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
            "action TEXT NOT NULL, client TEXT, change TEXT)"
        )
        con.commit()
    finally:
        con.close()


def setting_text(v) -> str:
    """settings 表统一存 TEXT；整数值不要写成 18000.0。"""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def decode_settings(rows) -> dict:
    out = {}
    for key, value in rows:
        if key in DEPRECATED_KEYS or key not in SETTING_KEYS:
            continue
        if key == "profile":
            try:
                out["profile"] = json.loads(value)
                continue
            except (ValueError, TypeError):
                continue
        if key in NUMERIC_KEYS:
            try:
                out[key] = float(value)
                continue
            except (ValueError, TypeError):
                out[key] = 0
                continue
        out[key] = value
    return out


def read_state(con: sqlite3.Connection) -> dict:
    records = [
        dict(r) for r in con.execute(
            "SELECT id, date, type, amount, category, title, account, linked "
            "FROM records ORDER BY date DESC, id DESC"
        )
    ]
    accounts = [
        dict(a) for a in con.execute(
            "SELECT id, kind, name, note, amount FROM accounts ORDER BY rowid"
        )
    ]
    settings = decode_settings(con.execute("SELECT key, value FROM settings"))
    profile = settings.pop("profile", {"name": "", "email": ""})
    return {
        "v": 1,
        "profile": profile,
        "settings": settings,
        "accounts": accounts,
        "records": records,
        "meta": {"source": "sqlite", "db": DB_PATH.name, "count": len(records)},
    }


def _clean_record(r: dict) -> tuple:
    rid = str(r.get("id") or "").strip()
    if not rid:
        raise ValueError("record.id 不能为空")
    date = str(r.get("date") or "").strip()[:10]
    if len(date) != 10:
        raise ValueError(f"record.date 非法：{date!r}")
    rtype = str(r.get("type") or "").strip()
    if rtype not in ("in", "out"):
        raise ValueError(f"record.type 必须是 in/out：{rtype!r}")
    amount = abs(float(r.get("amount") or 0))
    linked = 1 if r.get("linked") in (1, True, "1", "true", "True") else 0
    return (
        rid, date, rtype, amount,
        str(r.get("category") or ""), str(r.get("title") or ""), str(r.get("account") or ""),
        linked,
    )


def _clean_account(a: dict) -> tuple:
    aid = str(a.get("id") or "").strip()
    if not aid:
        raise ValueError("account.id 不能为空")
    return (
        aid, str(a.get("kind") or ""), str(a.get("name") or ""),
        str(a.get("note") or ""), float(a.get("amount") or 0),
    )


def write_state(con: sqlite3.Connection, data: dict) -> dict:
    """全量替换：一个事务内完成，中途失败会整体回滚。"""
    if not isinstance(data.get("records"), list):
        raise ValueError("缺少 records 数组")
    if not isinstance(data.get("accounts"), list):
        raise ValueError("缺少 accounts 数组")
    settings = data.get("settings") or {}
    if not isinstance(settings, dict):
        raise ValueError("settings 必须是对象")

    # linked 单调保护：已经计入账户余额的流水，不允许被旧页面 / 旧快照全量写回时抹成 0，
    # 否则删除或清空时会漏掉回滚，账户余额就算错了。
    linked_ids = {r[0] for r in con.execute("SELECT id FROM records WHERE linked=1")}
    rows_r = []
    for r in data["records"]:
        row = _clean_record(r)
        if row[0] in linked_ids and row[7] == 0:
            row = row[:7] + (1,)
        rows_r.append(row)
    rows_a = [_clean_account(a) for a in data["accounts"]]
    seen = set()
    for row in rows_r:
        if row[0] in seen:
            raise ValueError(f"重复的 id：{row[0]}")
        seen.add(row[0])

    rows_s = [(str(k), setting_text(v)) for k, v in settings.items()
              if str(k) in SETTING_KEYS and str(k) != "profile"]  # profile 单独收
    profile = data.get("profile")
    if isinstance(profile, dict):
        rows_s.append(("profile", json.dumps(profile, ensure_ascii=False)))

    with con:
        con.execute("DELETE FROM records")
        con.execute("DELETE FROM accounts")
        con.execute("DELETE FROM settings")
        con.executemany(
            "INSERT INTO records (id,date,type,amount,category,title,account,linked) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows_r,
        )
        con.executemany(
            "INSERT INTO accounts (id,kind,name,note,amount) VALUES (?,?,?,?,?)", rows_a
        )
        con.executemany("INSERT INTO settings (key,value) VALUES (?,?)", rows_s)
    return read_state(con)


def export_sql(con: sqlite3.Connection) -> str:
    def q(v):
        if v is None:
            return "NULL"
        if isinstance(v, (int, float)):
            return str(v)
        return "'" + str(v).replace("'", "''") + "'"

    lines = [
        "-- 由 ledger_server.py 导出 · " + DB_PATH.name,
        "PRAGMA foreign_keys = OFF;",
        "BEGIN TRANSACTION;",
        "DROP TABLE IF EXISTS records;",
        "CREATE TABLE records (id TEXT PRIMARY KEY, date TEXT NOT NULL, "
        "type TEXT NOT NULL CHECK (type IN ('in','out')), amount REAL NOT NULL, "
        "category TEXT, title TEXT, account TEXT, linked INTEGER NOT NULL DEFAULT 0);",
        "CREATE INDEX idx_records_date ON records (date);",
        "DROP TABLE IF EXISTS accounts;",
        "CREATE TABLE accounts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT, "
        "note TEXT, amount REAL NOT NULL DEFAULT 0);",
        "DROP TABLE IF EXISTS settings;",
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);",
    ]
    for r in con.execute("SELECT * FROM records ORDER BY rowid"):
        lines.append("INSERT INTO records VALUES (" + ", ".join(q(x) for x in tuple(r)) + ");")
    for a in con.execute("SELECT * FROM accounts ORDER BY rowid"):
        lines.append("INSERT INTO accounts VALUES (" + ", ".join(q(x) for x in tuple(a)) + ");")
    for k, v in con.execute("SELECT key, value FROM settings ORDER BY key"):
        if k in DEPRECATED_KEYS:
            continue
        lines.append("INSERT INTO settings VALUES (" + ", ".join(q(x) for x in (k, v)) + ");")
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def summarize_change(before: dict, after: dict) -> dict:
    """对比写前写后，把变更压成可读摘要。"""
    bm = {r["id"]: r for r in before.get("records", [])}
    am = {r["id"]: r for r in after.get("records", [])}
    bs, as_ = before.get("settings", {}), after.get("settings", {})
    return {
        "records": {
            "before": len(bm), "after": len(am),
            "added": sorted(set(am) - set(bm)),
            "removed": sorted(set(bm) - set(am)),
            "edited": sorted(k for k in set(bm) & set(am) if bm[k] != am[k]),
        },
        "accounts": {"before": len(before.get("accounts", [])),
                     "after": len(after.get("accounts", []))},
        "settings_changed": {
            k: [bs.get(k), as_.get(k)]
            for k in sorted(set(bs) | set(as_)) if bs.get(k) != as_.get(k)
        },
    }


def audit(con: sqlite3.Connection, action: str, before: dict, after: dict, client: str = "") -> None:
    """每次写库往 log 表记一行，方便回看「哪次写入把什么改成了什么」。
    审计失败不影响账本本身：吞掉 sqlite 错误即可。"""
    try:
        con.execute(
            "INSERT INTO log (ts, action, client, change) VALUES (?,?,?,?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                action,
                client,
                json.dumps(summarize_change(before, after), ensure_ascii=False),
            ),
        )
        con.commit()
    except sqlite3.Error:
        pass


# --------------------------------------------------------------------------
# 授时：阿里云 NTP → 阿里云 HTTP Date → 最新记录 → 本机
# --------------------------------------------------------------------------
def ntp_utc_now() -> tuple[float, str, float]:
    """向阿里云公共 NTP 服务器要一次 UTC 时间，返回 (epoch 秒, 主机, 往返毫秒)。"""
    packet = b"\x1b" + b"\x00" * 47          # LI=0, VN=3, Mode=3 (client)
    last_err = None
    for host in NTP_HOSTS:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(NTP_TIMEOUT)
            t0 = time.monotonic()
            sock.sendto(packet, (host, 123))
            data, _ = sock.recvfrom(48)
            rtt = (time.monotonic() - t0) * 1000
            if len(data) < 48:
                raise ValueError("NTP 响应过短")
            stratum, mode = data[1], data[0] & 0x07
            if stratum == 0 or mode not in (4, 5):
                raise ValueError("NTP 应答无效")
            tx = struct.unpack("!I", data[40:44])[0]
            epoch = float(tx) - NTP_EPOCH
            if epoch < 1577836800 or epoch > time.time() + 86400:
                raise ValueError("NTP 时间超出合理范围")
            return epoch, host, round(rtt, 1)
        except (OSError, ValueError, struct.error) as exc:
            last_err = exc
        finally:
            if sock is not None:
                sock.close()
    raise RuntimeError(f"阿里云 NTP 均不可达：{last_err}")


def http_utc_now() -> tuple[float, str]:
    """UDP 123 被防火墙拦时，退而用阿里云 HTTPS 响应的 Date 头（仍是网络时间）。"""
    req = urllib.request.Request(HTTP_TIME_URL, method="HEAD",
                                 headers={"User-Agent": "ledger-local/1.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIME_TIMEOUT) as resp:
        raw = resp.headers.get("Date")
    if not raw:
        raise ValueError("响应缺少 Date 头")
    dt = email.utils.parsedate_to_datetime(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp(), HTTP_TIME_URL


def theme_state_for_hour(hour: int) -> str:
    """主题时段：6–20 点（含首尾）为 day 白色，其余为 night 黑色。"""
    return "day" if 6 <= hour <= 20 else "night"


def sync_theme(con: sqlite3.Connection, dt: datetime) -> dict:
    """按权威时间刷新 settings.theme 的 time_h 与 state；值没变就不写库。"""
    hour = dt.hour
    state = theme_state_for_hour(hour)
    payload = json.dumps({"time_h": str(hour), "state": state}, ensure_ascii=False)
    row = con.execute("SELECT value FROM settings WHERE key = 'theme'").fetchone()
    if not row or row[0] != payload:
        con.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("theme", payload),
        )
        con.commit()
    return {"time_h": str(hour), "state": state}


def authoritative_time(con: sqlite3.Connection) -> dict:
    """决定网页里的「今天」：1 阿里云授时 2 最新记录日期 3 系统时间。"""
    row = con.execute("SELECT MAX(date) FROM records").fetchone()
    latest = (row[0] if row else None) or None
    local_now = datetime.now()
    off = int((local_now.astimezone().utcoffset() or timedelta(0)).total_seconds() // 60)
    tz_text = ("+" if off >= 0 else "-") + f"{abs(off) // 60:02d}:{abs(off) % 60:02d}"

    epoch = None
    host = rtt = None
    source = label = None
    try:
        epoch, host, rtt = ntp_utc_now()
        source, label = "aliyun-ntp", "阿里云 NTP 授时"
    except Exception:
        try:
            epoch, host = http_utc_now()
            source, label = "aliyun-http-date", "阿里云 HTTP 授时"
        except Exception:
            epoch = None

    if epoch is not None:
        dt = datetime.fromtimestamp(epoch)       # 转成本机本地时区
    elif latest:
        try:
            dt = datetime.strptime(str(latest)[:10], "%Y-%m-%d")
            source, label = "latest-record", "最新记录时间"
        except ValueError:
            dt, source, label = local_now, "local", "系统时间"
    else:
        dt, source, label = local_now, "local", "系统时间"

    # 主题时段跟随「权威时间」：取到了网络时间用网络小时，否则退回本机小时
    try:
        theme = sync_theme(con, dt if epoch is not None else local_now)
    except sqlite3.Error:
        theme = {"time_h": str(dt.hour), "state": theme_state_for_hour(dt.hour)}

    return {
        "date": dt.strftime("%Y-%m-%d"),
        "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": tz_text,
        "source": source,
        "sourceLabel": label,
        "host": host,
        "rtt_ms": rtt,
        "latest_record": latest,
        "network_ok": source.startswith("aliyun"),
        "fallback": source in ("latest-record", "local"),
        "local_datetime": local_now.strftime("%Y-%m-%d %H:%M:%S"),
        "theme_h": theme["time_h"],
        "theme_state": theme["state"],
    }


# --------------------------------------------------------------------------
# 控制台访问日志：固定 10 行的「原地滚动区」+ 来源设备标识
#   · 启动信息只打印一次；之后的请求日志始终写回同一块 10 行区域，光标不越界，
#     上方的数据库路径 / 局域网地址不会被顶出屏幕。
#   · 每行 = 时间 / 客户端 IP / 设备标识 / 请求 / 状态码；区域下方再跟一行
#     「已见设备」汇总，安卓 / 苹果 / 电脑 / Linux 谁在访问一目了然。
#   · Windows 用控制台缓冲区 API，Linux / macOS 用 ANSI 光标序列；输出被重定向、
#     以守护进程运行（start.sh daemon 等）时两者都不可用，自动降级为逐行
#     追加，完整日志不丢。
# --------------------------------------------------------------------------
LOG_ROWS = 10
_ACCESS_RE = re.compile(r'^"(\S+)\s+(\S+)\s+HTTP/[\d.]+"\s+(\d{3})\b')


def _tty_ready():
    return (getattr(sys.stdout, "isatty", lambda: False)()
            and getattr(sys.stderr, "isatty", lambda: False)())


def _disp_width(text):
    """控制台显示列宽：中文 / 全角算 2 列。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in text)


def _clip(text, width):
    """按控制台显示列裁剪：中文 / 全角算 2 列，并返回已用列数。"""
    out, used = [], 0
    for ch in text:
        if ch in "\r\n\t":
            ch = " "
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + cw > width:
            break
        out.append(ch)
        used += cw
    return "".join(out), used


def _pad_to(text, width):
    s, used = _clip(text, width)
    return s + " " * (width - used)


def device_of(ua, ip):
    """按 User-Agent 给客户端打设备标识；不做 DNS 反查，避免阻塞请求线程。"""
    if ip in ("127.0.0.1", "::1") or ip.startswith("127."):
        return "本机"
    u = (ua or "").lower()
    if "iphone" in u or "ipad" in u or "ipod" in u:
        return "苹果"
    if "android" in u:
        return "安卓"
    if "windows" in u:
        return "电脑"
    if "cros" in u:
        return "Chromebook"
    if "macintosh" in u or "mac os x" in u:
        return "苹果电脑"
    if "linux" in u:
        return "Linux"
    return "未知" if not u else "其它"


def access_line(ip, device, message):
    """表格化的一行访问日志：时间 / 来源 IP / 设备 / 方法与路径 / 状态码。"""
    ts = datetime.now().strftime("%H:%M:%S")
    m = _ACCESS_RE.match(message)
    if m:
        method, path, code = m.group(1), m.group(2), m.group(3)
        return "%s  %s  %s  %s %s  %s" % (
            ts, _pad_to(ip, 15), _pad_to(device, 8),
            _pad_to(method, 5), _pad_to(path, 24), code)
    return "%s  %s  %s  %s" % (
        ts, _pad_to(ip, 15), _pad_to(device, 8), message)


def _ansi_console_api():
    """Linux / macOS 终端：ANSI 光标上移 + 清行，在同一块区域原地重写。"""
    if sys.platform == "win32" or not _tty_ready():
        return None
    try:
        state = {"started": False, "rows": 0}

        def width():
            try:
                return max(20, shutil.get_terminal_size((80, 24)).columns - 1)
            except Exception:
                return 79

        def capacity():
            """ANSI 没有窗口坐标，按启动信息约 14 行估算剩余可用行。"""
            try:
                return max(4, shutil.get_terminal_size((80, 24)).lines - 14)
            except Exception:
                return 10

        def render(lines, w):
            n = len(lines)
            buf = []
            if state["started"]:
                buf.append("\r\x1b[%dA" % max(1, state["rows"] - 1))
            for k, s in enumerate(lines):
                buf.append("\r\x1b[2K" + _pad_line(s, w))
                if k < n - 1:
                    buf.append("\n")
            sys.stderr.write("".join(buf))
            sys.stderr.flush()
            state["started"], state["rows"] = True, n
            return True

        def finish():
            if state["started"]:
                sys.stderr.write("\r\n")
                sys.stderr.flush()

        return {"width": width, "render": render,
                "finish": finish, "capacity": capacity}
    except Exception:
        return None


def _win_console_api():
    """Windows 控制台缓冲区：逐行定位重写。不可用返回 None，由调用方降级。"""
    if sys.platform != "win32" or not _tty_ready():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        class _RECT(ctypes.Structure):
            _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                        ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]

        class _CSBI(ctypes.Structure):
            _fields_ = [("dwSize", _COORD), ("dwCursorPosition", _COORD),
                        ("wAttributes", wintypes.WORD), ("srWindow", _RECT),
                        ("dwMaximumWindowSize", _COORD)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # GetStdHandle 默认 restype 是 c_int，在 64 位下会截断句柄，必须显式声明
        k32.GetStdHandle.argtypes = [wintypes.DWORD]
        k32.GetStdHandle.restype = wintypes.HANDLE
        k32.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_CSBI)]
        k32.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
        k32.SetConsoleCursorPosition.argtypes = [wintypes.HANDLE, _COORD]
        k32.SetConsoleCursorPosition.restype = wintypes.BOOL

        h = k32.GetStdHandle(0xFFFFFFF5)          # STD_OUTPUT_HANDLE = -11
        if not h:
            return None
        probe = _CSBI()
        if not k32.GetConsoleScreenBufferInfo(h, ctypes.byref(probe)):
            return None

        state = {"anchor": None, "top": None, "rows": 0}

        def info():
            i = _CSBI()
            if not k32.GetConsoleScreenBufferInfo(h, ctypes.byref(i)):
                return None
            # 留两列不写，避免写到缓冲区最后一列触发自动换行
            return {"width": max(20, i.dwSize.X - 2),
                    "top": i.srWindow.Top, "bottom": i.srWindow.Bottom,
                    "cursor_y": i.dwCursorPosition.Y}

        def move(y):
            return bool(k32.SetConsoleCursorPosition(h, _COORD(0, y)))

        def width():
            i = info()
            return i["width"] if i else 80

        def capacity():
            """可见区还剩几行可用（留 1 行给光标 / 退出提示）。"""
            i = info()
            if i is None:
                return None
            y = state["anchor"] if state["anchor"] is not None else i["cursor_y"]
            return max(0, i["bottom"] - y - 1)

        def render(lines, w):
            i = info()
            if i is None:
                return False
            n = len(lines)
            if state["anchor"] is None:
                state["anchor"], state["top"] = i["cursor_y"], i["top"]
            elif i["top"] != state["top"]:
                # 用户滚动过窗口 / 改过窗口大小：以当前位置重建日志区
                state["anchor"], state["top"] = i["cursor_y"], i["top"]
            if state["anchor"] + n > i["bottom"] - 1:
                return False
            state["rows"] = n
            for k, s in enumerate(lines):
                if not move(state["anchor"] + k):
                    return False
                sys.stderr.write(_pad_line(s, w))
                # 每行必须立刻落屏：stderr 有缓冲时，末尾一次 flush 会把攒下的
                # 整批文本全写到最后一次定位的位置，反而撑乱日志区。
                sys.stderr.flush()
            return True

        def finish():
            if state["anchor"] is not None:
                move(state["anchor"] + state["rows"])

        return {"width": width, "render": render,
                "finish": finish, "capacity": capacity}
    except Exception:
        return None


def _pad_line(text, width):
    """整行覆盖：写满整行擦掉旧痕迹，最后一列留空不触发自动换行。"""
    return _pad_to(text, width)


class _RollingLog:
    """线程安全的固定行日志区；ThreadingHTTPServer 会并发调用 log_message。

    frame = 最近 10 条访问 + 1 行「已见设备」汇总，整块原地重写。
    """

    def __init__(self, rows=LOG_ROWS):
        self.rows = rows
        self.lines = []
        self.devices = OrderedDict()   # ip -> {label, count}，按最近访问排序
        self.total = 0
        self.lock = threading.Lock()
        self.backend = _win_console_api() or _ansi_console_api()

    def _append(self, text):
        sys.stderr.write(text + "\n")
        sys.stderr.flush()

    def _footer(self):
        if not self.devices:
            return "已见设备：等待第一次访问"
        recent = list(self.devices.items())[-4:]
        parts = ["%s %s×%d" % (ip, d["label"], d["count"]) for ip, d in recent]
        more = " …" if len(self.devices) > 4 else ""
        return "已见设备 %s%s  ·  共 %d 次访问" % (
            " · ".join(parts), more, self.total)

    def _frame(self):
        n = self.rows
        if self.backend:
            cap = self.backend["capacity"]()
            # 窗口矮时少列几条日志，但绝不让整块区域向下刷新
            if cap:
                n = max(3, min(self.rows, cap - 1))
        rows = self.lines[-n:] + [""] * max(0, n - len(self.lines))
        return rows + [self._footer()]

    def push(self, text, ip=None, device=None):
        text = str(text).replace("\r", " ").replace("\n", " ")
        with self.lock:
            self.lines.append(text)
            if len(self.lines) > self.rows:
                del self.lines[:-self.rows]
            self.total += 1
            if ip and ip != "-":
                d = self.devices.get(ip) or {"label": device or "未知", "count": 0}
                d["count"] += 1
                if device and device not in ("未知", "其它"):
                    d["label"] = device
                self.devices[ip] = d
                self.devices.move_to_end(ip)
            if self.backend is None:
                self._append(text)
                return
            frame = self._frame()
            if not self.backend["render"](frame, self.backend["width"]()):
                self.backend = None
                self._append(text)

    def finish(self):
        """退出前把光标挪到日志区下方，让「已停止」等后续输出不覆盖日志。"""
        with self.lock:
            if self.backend:
                self.backend["finish"]()


ROLLING_LOG = _RollingLog()


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "LedgerSQLite/1.0"

    def log_message(self, fmt, *args):
        # 客户端 IP 来自已建立的连接；设备标识只按 User-Agent 判断，
        # 不做反向 DNS 解析，避免局域网无 PTR 记录时把请求线程卡住。
        try:
            ip = self.client_address[0] if self.client_address else "-"
        except Exception:
            ip = "-"
        headers = getattr(self, "headers", None)
        ua = headers.get("User-Agent", "") if headers else ""
        dev = device_of(ua, ip)
        ROLLING_LOG.push(access_line(ip, dev, fmt % args), ip=ip, device=dev)

    def _app_guard(self):
        """本机应用模式的安全边界：只认回环 Host、只认同源 Origin、
        只认启动时生成的一次性 token 路径前缀。校验通过后剥掉前缀，
        下面的路由判断保持原样，token 也不会出现在访问日志里。"""
        if not APP_MODE:
            return True
        p = self.path
        if p.startswith("/favicon.ico"):
            return True
        allowed_hosts = {"127.0.0.1:%d" % APP_PORT, "localhost:%d" % APP_PORT}
        if (self.headers.get("Host") or "").lower() not in allowed_hosts:
            self._json(403, {"error": "host-not-allowed"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin.rstrip("/") not in (
                "http://127.0.0.1:%d" % APP_PORT, "http://localhost:%d" % APP_PORT):
            self._json(403, {"error": "origin-not-allowed"})
            return False
        prefix = "/" + APP_TOKEN
        if p == prefix or p == prefix + "/":
            self.path = "/"
        elif p.startswith(prefix + "/"):
            self.path = p[len(prefix):]
        else:
            self._json(403, {"error": "invalid-token"})
            return False
        return True

    def _cors(self):
        if not APP_MODE:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"JSON 解析失败：{exc}")
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def do_OPTIONS(self):
        if not self._app_guard(): return
        self._send(204, b"", "text/plain; charset=utf-8")

    def do_HEAD(self):
        if not self._app_guard(): return
        """预览面板和存活探测会先发 HEAD，别回 501。"""
        is_api = unquote(urlparse(self.path).path).startswith("/api/")
        self._send(200, b"", "application/json; charset=utf-8" if is_api
                   else "text/html; charset=utf-8")

    # ---------------- GET ----------------
    def _safe_connect(self):
        """运行期开库：文件缺失时回 503 并返回 None，绝不自动重建空库。
        建库只发生在服务启动（init_db），运行中丢库必须由用户恢复文件或重启。"""
        try:
            return connect()
        except DatabaseMissing:
            self._json(503, {"error": "database-missing",
                             "message": "数据库文件(ledger.db)丢失，请恢复后重启服务重新初始化"})
            return None

    def do_GET(self):
        if not self._app_guard(): return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path.startswith("/api/"):
            return self._api_get(path, parse_qs(parsed.query))
        return self._static(path)

    def _api_get(self, path, query):
        con = self._safe_connect()
        if con is None:
            return
        try:
            if path == "/api/health":
                return self._json(200, {
                    "ok": True, "db": str(DB_PATH),
                    "records": con.execute("SELECT COUNT(*) FROM records").fetchone()[0],
                })
            if path == "/api/time":
                return self._json(200, authoritative_time(con))
            if path == "/api/state":
                return self._json(200, read_state(con))
            if path == "/api/export.sql":
                return self._send(200, export_sql(con).encode("utf-8"),
                                  "application/sql; charset=utf-8")
            if path == "/api/writes":
                try:
                    limit = max(1, min(int(query.get("limit", ["50"])[0]), 500))
                except (ValueError, TypeError):
                    limit = 50
                rows = con.execute(
                    "SELECT ts, action, client, change FROM log ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                items = []
                for r in rows:
                    try:
                        change = json.loads(r["change"]) if r["change"] else None
                    except ValueError:
                        change = r["change"]
                    items.append({"ts": r["ts"], "action": r["action"],
                                  "client": r["client"], "change": change})
                total = con.execute("SELECT COUNT(*) FROM log").fetchone()[0]
                return self._json(200, {"table": "log", "count": total, "items": items})
            return self._json(404, {"error": "未知接口"})
        finally:
            con.close()

    def _static(self, path):
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if path in ("/", ""):
            target = HTML_PATH
        else:
            target = (BASE / path.lstrip("/")).resolve()
            if not target.is_relative_to(BASE):
                return self._json(403, {"error": "禁止访问目录外的文件"})
        if not target.is_file():
            return self._send(404, b"Not Found", "text/plain; charset=utf-8")
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    # ---------------- PUT ----------------
    def do_PUT(self):
        if not self._app_guard(): return
        path = unquote(urlparse(self.path).path)
        try:
            data = self._body()
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        con = self._safe_connect()
        if con is None:
            return
        try:
            if path == "/api/state":
                before = read_state(con)
                after = write_state(con, data)
                audit(con, "PUT /api/state", before, after, self.client_address[0])
                return self._json(200, after)
            if path == "/api/settings":
                key = str(data.get("key") or "").strip()
                if not key:
                    return self._json(400, {"error": "缺少 key"})
                if key in DEPRECATED_KEYS:
                    return self._json(400, {"error": key + " 已废弃，不再写入数据库"})
                if key not in SETTING_KEYS:
                    return self._json(400, {"error": "不支持的设置项：" + key})
                text = setting_text(data.get("value"))
                before = read_state(con)
                with con:
                    con.execute("INSERT INTO settings (key,value) VALUES (?,?) "
                                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, text))
                after = read_state(con)
                audit(con, "PUT /api/settings " + key, before, after, self.client_address[0])
                return self._json(200, after)
            if path == "/api/accounts":
                row = _clean_account(data)
                before = read_state(con)
                with con:
                    con.execute("INSERT INTO accounts (id,kind,name,note,amount) VALUES (?,?,?,?,?) "
                                "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, name=excluded.name, "
                                "note=excluded.note, amount=excluded.amount", row)
                after = read_state(con)
                audit(con, "PUT /api/accounts " + str(row[0]), before, after, self.client_address[0])
                return self._json(200, after)
            return self._json(404, {"error": "未知接口"})
        except (ValueError, TypeError, sqlite3.Error) as exc:
            return self._json(400, {"error": str(exc)})
        finally:
            con.close()

    # ---------------- POST ----------------
    def do_POST(self):
        if not self._app_guard(): return
        path = unquote(urlparse(self.path).path)
        try:
            data = self._body()
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        con = self._safe_connect()
        if con is None:
            return
        try:
            if path == "/api/records":
                row = _clean_record(data)
                before = read_state(con)
                with con:
                    con.execute("INSERT INTO records (id,date,type,amount,category,title,account,linked) "
                                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                                "date=excluded.date, type=excluded.type, amount=excluded.amount, "
                                "category=excluded.category, title=excluded.title, "
                                "account=excluded.account, linked=excluded.linked", row)
                after = read_state(con)
                audit(con, "POST /api/records " + str(row[0]), before, after, self.client_address[0])
                return self._json(200, after)
            return self._json(404, {"error": "未知接口"})
        except (ValueError, TypeError, sqlite3.Error) as exc:
            return self._json(400, {"error": str(exc)})
        finally:
            con.close()

    # ---------------- DELETE ----------------
    def do_DELETE(self):
        if not self._app_guard(): return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        con = self._safe_connect()
        if con is None:
            return
        try:
            if path == "/api/records":
                rid = parse_qs(parsed.query).get("id", [None])[0]
                before = read_state(con)
                with con:
                    if rid:
                        cur = con.execute("DELETE FROM records WHERE id=?", (rid,))
                    else:
                        cur = con.execute("DELETE FROM records")
                after = read_state(con)
                audit(con, "DELETE /api/records " + (str(rid) if rid else "ALL"),
                      before, after, self.client_address[0])
                return self._json(200, {"deleted": cur.rowcount, "state": after})
            return self._json(404, {"error": "未知接口"})
        except sqlite3.Error as exc:
            return self._json(400, {"error": str(exc)})
        finally:
            con.close()


def lan_ips() -> list[str]:
    """列出手机可能用来访问的本机 IPv4；走默认路由的那块网卡排最前。"""
    found = []
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("223.5.5.5", 80))   # 不真正发包，只让系统选出默认出口网卡
        found.append(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET, socket.SOCK_STREAM):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in found:
        if ip and not ip.startswith(("127.", "169.254.")) and ip not in out:
            out.append(ip)
    return out


def _disable_console_quickedit() -> None:
    """Windows 控制台默认开启「快速编辑模式」：鼠标点一下窗口就进入选择/标记状态，
    控制台输出被冻结，HTTP 请求线程因写日志卡住，表现为「卡死、按回车才恢复」。
    这里在启动最早期关掉它。非 Windows、或运行环境无控制台时静默跳过。

    只清 QUICK_EDIT(0x40) 与 INSERT_MODE(0x20)，保留 EXTENDED_FLAGS(0x80)、
    PROCESSED_INPUT(0x01) 等，Ctrl+C 仍可正常中断。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        STD_INPUT_HANDLE = -10
        ENABLE_QUICK_EDIT = 0x0040
        ENABLE_INSERT_MODE = 0x0020
        h = k32.GetStdHandle(STD_INPUT_HANDLE)
        if h in (0, -1) or h is None:
            return
        mode = ctypes.c_uint32()
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            return
        k32.SetConsoleMode(h, mode.value & ~ENABLE_QUICK_EDIT & ~ENABLE_INSERT_MODE)
    except Exception:
        pass


def app_db_path():
    """应用模式的账本位置：用户可写的数据目录，
    避免装到 Program Files / .app / APK 这类只读目录里。"""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    d = base / "ETS-dash"
    d.mkdir(parents=True, exist_ok=True)
    return d / "ledger.db"


def main():
    _disable_console_quickedit()
    global APP_MODE, APP_TOKEN, APP_PORT, DB_PATH
    ap = argparse.ArgumentParser(description="鱼记 · YuLedger · SQLite 本地数据服务")
    ap.add_argument("--port", type=int, default=8765, help="端口，默认 8765")
    ap.add_argument("--host", default="0.0.0.0",
                    help="监听地址，默认 0.0.0.0（本机 + 同一局域网设备）")
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    ap.add_argument("--db", default=None,
                    help="数据库文件路径，默认与脚本同目录的 ledger.db（部署时可把数据单独放）")
    ap.add_argument("--app-mode", action="store_true",
                    help="本机应用模式：仅绑 127.0.0.1、随机端口、一次性 token，供应用壳调用")
    ap.add_argument("--app-ready-file", default=None,
                    help="应用模式下把带 token 的访问 URL 写进该文件，供应用壳读取")
    ap.add_argument("--app-port", type=int, default=0,
                    help="应用模式固定端口（默认 0=随机）；设备版用固定端口以保留 WebView 缓存")
    args = ap.parse_args()

    APP_MODE = args.app_mode
    if APP_MODE:
        APP_TOKEN = secrets.token_urlsafe(24)

    if args.db:
        DB_PATH = Path(args.db).expanduser().resolve()
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    elif APP_MODE:
        DB_PATH = app_db_path()

    init_db()

    # 启动即对时，顺手把 settings.theme 的 time_h / state 刷成当前时段
    try:
        _con0 = connect(create=True)
        _t0 = authoritative_time(_con0)
        _con0.close()
        print(f"[init] 主题时段已按授时刷新：{_t0['theme_state']}（{_t0['theme_h']} 点）")
    except Exception as exc:
        print(f"[init] 启动对时失败，theme 待首次 /api/time 刷新：{exc}")

    bind_host = "127.0.0.1" if APP_MODE else args.host
    # 应用模式默认随机端口（桌面 Tauri 行为不变）。设备版会指定 --app-port：
    # WebView 的 localStorage 按 origin(含端口) 隔离，端口一变缓存就全丢，
    # 所以优先固定端口，被占用时顺延到下一个可用端口，尽量保持 origin 稳定。
    if APP_MODE:
        preferred = args.app_port or 0
        srv = None
        for candidate in ([preferred + i for i in range(8)] if preferred else [0]):
            try:
                srv = ThreadingHTTPServer((bind_host, candidate), Handler)
                break
            except OSError:
                continue
        if srv is None:
            srv = ThreadingHTTPServer((bind_host, 0), Handler)
    else:
        srv = ThreadingHTTPServer((bind_host, args.port), Handler)
    APP_PORT = srv.server_address[1]

    if APP_MODE:
        url = "http://127.0.0.1:%d/%s/" % (APP_PORT, APP_TOKEN)
        print("=" * 62)
        print("  鱼记 · YuLedger · 本机应用模式")
        print(f"  数据库：{DB_PATH}")
        print(f"  监听：  127.0.0.1:{APP_PORT}（仅本机回环，不对局域网开放）")
        print("  访问：  由应用壳接管（一次性 token）")
        print("  Ctrl+C 停止服务")
        print("=" * 62)
        print("APP_READY " + url, flush=True)
        if args.app_ready_file:
            ready = Path(args.app_ready_file).expanduser()
            ready.parent.mkdir(parents=True, exist_ok=True)
            ready.write_text(url, encoding="utf-8")
    else:
        ips = lan_ips()
        # 展示与自动打开浏览器一律用真实的局域网 IP，不再出现 127.0.0.1
        url = (f"http://{ips[0]}:{args.port}/" if ips
               else f"http://localhost:{args.port}/")
        print("=" * 62)
        print("  鱼记 · YuLedger · 已连接 SQLite")
        print(f"  数据库：{DB_PATH}")
        extra = ("  （其它网卡 " + "、".join(ips[1:]) + "）") if len(ips) > 1 else ""
        print(f"  访问：  {url}{extra}")
        if bind_host == "0.0.0.0":
            print("  手机须与电脑连同一个 WiFi；同网段设备都能读写账本，无密码校验。")
        print(f"  监听：  {bind_host}:{args.port}")
        print("  Ctrl+C 停止服务")
        # 分隔线里直接嵌列头，不额外占一行，避免把固定日志区挤出可见窗口
        head = " 最近 10 条访问 · 时间 / 来源 IP / 设备 / 请求 / 状态 "
        print("=" * 6 + head + "=" * max(0, 62 - 6 - _disp_width(head)))
        if not args.no_browser:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        # 先把光标移出固定日志区，否则「已停止」会覆盖最后一条访问日志
        ROLLING_LOG.finish()
        print("\n已停止。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
