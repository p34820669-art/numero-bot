"""SQLite-хранилище. Один файл, без внешних сервисов."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    uid TEXT PRIMARY KEY,
    consent INTEGER NOT NULL DEFAULT 0,
    birthdate TEXT,
    tz_min INTEGER,
    email TEXT,
    state TEXT NOT NULL DEFAULT 'idle',
    ctx TEXT NOT NULL DEFAULT '{}',
    anchor INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL,
    product TEXT NOT NULL,
    amount INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    params TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    paid_at REAL
);
CREATE TABLE IF NOT EXISTS subs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL,
    birthdate TEXT NOT NULL,
    tz_min INTEGER NOT NULL,
    send_time TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    last_sent TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    reminded INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'daily'
);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    pdf_file TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    sent_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS web_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL,
    ts REAL NOT NULL,
    role TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS web_messages_uid ON web_messages(uid, id);
CREATE TABLE IF NOT EXISTS gifts (
    code TEXT PRIMARY KEY,
    uid TEXT NOT NULL,
    params TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    redeemed_by TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class DB:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        for ddl in ("ALTER TABLE users ADD COLUMN anchor INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE emails ADD COLUMN body TEXT NOT NULL DEFAULT ''",
                    "ALTER TABLE subs ADD COLUMN reminded INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE subs ADD COLUMN kind TEXT NOT NULL DEFAULT 'daily'"):
            try:  # база от прежней версии: добавляем новые колонки
                self.conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    # ---- общее ----
    def _one(self, sql: str, args: tuple = ()) -> Optional[dict]:
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def _run(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur

    # ---- время (в разработке можно перематывать) ----
    def time_shift(self) -> timedelta:
        row = self._one("SELECT value FROM meta WHERE key='time_shift_s'")
        return timedelta(seconds=float(row["value"])) if row else timedelta(0)

    def shift_time(self, days: float) -> None:
        total = self.time_shift().total_seconds() + days * 86400
        self._run("INSERT INTO meta(key,value) VALUES('time_shift_s',?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(total),))

    def now(self) -> datetime:
        return datetime.now(timezone.utc) + self.time_shift()

    # ---- пользователи ----
    def user(self, uid: str) -> dict:
        row = self._one("SELECT * FROM users WHERE uid=?", (uid,))
        if not row:
            self._run("INSERT INTO users(uid, created_at) VALUES(?,?)", (uid, time.time()))
            row = self._one("SELECT * FROM users WHERE uid=?", (uid,))
        row["ctx"] = json.loads(row["ctx"])
        return row

    def update_user(self, uid: str, **fields: Any) -> None:
        self.conn.execute("INSERT OR IGNORE INTO users(uid, created_at) VALUES(?,?)", (uid, time.time()))
        if "ctx" in fields:
            fields["ctx"] = json.dumps(fields["ctx"], ensure_ascii=False)
        cols = ", ".join(f"{k}=?" for k in fields)
        self._run(f"UPDATE users SET {cols} WHERE uid=?", (*fields.values(), uid))

    def delete_user(self, uid: str) -> None:
        for table in ("users", "orders", "subs", "emails", "web_messages", "gifts"):
            self._run(f"DELETE FROM {table} WHERE uid=?", (uid,))

    # ---- заказы ----
    def create_order(self, uid: str, product: str, amount: int, params: dict) -> int:
        cur = self._run(
            "INSERT INTO orders(uid, product, amount, params, created_at) VALUES(?,?,?,?,?)",
            (uid, product, amount, json.dumps(params, ensure_ascii=False), time.time()),
        )
        return int(cur.lastrowid)

    def order(self, order_id: int) -> Optional[dict]:
        row = self._one("SELECT * FROM orders WHERE id=?", (order_id,))
        if row:
            row["params"] = json.loads(row["params"])
        return row

    def update_order(self, order_id: int, **fields: Any) -> None:
        if "params" in fields:
            fields["params"] = json.dumps(fields["params"], ensure_ascii=False)
        cols = ", ".join(f"{k}=?" for k in fields)
        self._run(f"UPDATE orders SET {cols} WHERE id=?", (*fields.values(), order_id))

    def orders(self, uid: str) -> list[dict]:
        rows = self._all("SELECT * FROM orders WHERE uid=? ORDER BY id DESC", (uid,))
        for r in rows:
            r["params"] = json.loads(r["params"])
        return rows

    # ---- подписки ----
    def create_sub(self, uid: str, birthdate: str, tz_min: int, send_time: str,
                   start_date: str, end_date: str, kind: str = "daily") -> int:
        self._run("UPDATE subs SET active=0 WHERE uid=? AND kind=?", (uid, kind))
        cur = self._run(
            "INSERT INTO subs(uid, birthdate, tz_min, send_time, start_date, end_date, kind) VALUES(?,?,?,?,?,?,?)",
            (uid, birthdate, tz_min, send_time, start_date, end_date, kind),
        )
        return int(cur.lastrowid)

    def active_subs(self) -> list[dict]:
        return self._all("SELECT * FROM subs WHERE active=1")

    def subs(self, uid: str) -> list[dict]:
        return self._all("SELECT * FROM subs WHERE uid=? ORDER BY id DESC", (uid,))

    def update_sub(self, sub_id: int, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self._run(f"UPDATE subs SET {cols} WHERE id=?", (*fields.values(), sub_id))

    # ---- почта (имитация) ----
    def add_email(self, uid: str, to_addr: str, subject: str, pdf_file: str, body: str = "") -> int:
        cur = self._run(
            "INSERT INTO emails(uid, to_addr, subject, pdf_file, body, sent_at) VALUES(?,?,?,?,?,?)",
            (uid, to_addr, subject, pdf_file, body, time.time()),
        )
        return int(cur.lastrowid)

    def emails(self, uid: str) -> list[dict]:
        return self._all("SELECT * FROM emails WHERE uid=? ORDER BY id DESC", (uid,))

    # ---- подарки ----
    def create_gift(self, code: str, uid: str, params: dict) -> None:
        self._run("INSERT INTO gifts(code, uid, params, created_at) VALUES(?,?,?,?)",
                  (code, uid, json.dumps(params, ensure_ascii=False), time.time()))

    def gift(self, code: str) -> Optional[dict]:
        row = self._one("SELECT * FROM gifts WHERE code=?", (code,))
        if row:
            row["params"] = json.loads(row["params"])
        return row

    def redeem_gift(self, code: str, by_uid: str) -> None:
        self._run("UPDATE gifts SET status='redeemed', redeemed_by=? WHERE code=?", (by_uid, code))

    def gifts(self, uid: str) -> list[dict]:
        rows = self._all("SELECT * FROM gifts WHERE uid=? ORDER BY created_at DESC", (uid,))
        for r in rows:
            r["params"] = json.loads(r["params"])
        return rows

    # ---- очередь сообщений веб-чата ----
    def delete_web_after(self, uid: str, after_id: int) -> None:
        self._run("DELETE FROM web_messages WHERE uid=? AND id>?", (uid, after_id))

    def push_web(self, uid: str, role: str, payload: dict) -> int:
        cur = self._run(
            "INSERT INTO web_messages(uid, ts, role, payload) VALUES(?,?,?,?)",
            (uid, time.time(), role, json.dumps(payload, ensure_ascii=False)),
        )
        return int(cur.lastrowid)

    def web_since(self, uid: str, after: int) -> list[dict]:
        rows = self._all("SELECT * FROM web_messages WHERE uid=? AND id>? ORDER BY id", (uid, after))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows
