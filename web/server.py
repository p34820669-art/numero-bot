"""Локальный веб-чат для обкатки бота. Всё, что видит пользователь, идёт через тот же
движок bot.flow.Bot, который потом подключается к Telegram."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bot import config as cfg
from bot.db import DB
from bot.flow import Bot, tz_label
from bot.models import Message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

WEB = Path(__file__).resolve().parent
cfg.OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

db = DB(cfg.DB_PATH)


class WebSender:
    """Складывает исходящие сообщения в очередь, которую забирает браузер."""

    async def send(self, uid: str, msg: Message) -> int:
        return db.push_web(uid, "bot", msg.to_dict())

    async def truncate(self, uid: str, after_id: int) -> None:
        db.delete_web_after(uid, after_id)
        db.push_web(uid, "ctl", {"truncate_after": after_id})


bot = Bot(db, WebSender())


async def _ticker() -> None:
    while True:
        try:
            await bot.tick()
        except Exception:
            logging.exception("Сбой планировщика")
        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_ticker())
    yield
    task.cancel()


app = FastAPI(title="Базовый код человека (локальный тест)", lifespan=lifespan)


_UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def check_uid(uid: str) -> str:
    """Сервер открыт в интернете: пускаем только короткие безопасные id."""
    if not _UID_RE.match(uid):
        raise HTTPException(400, "bad uid")
    return uid


class EventIn(BaseModel):
    uid: str
    kind: str            # start | text | cb
    data: str = Field("", max_length=500)
    label: str = Field("", max_length=200)  # подпись нажатой кнопки, чтобы показать её как реплику пользователя


class PayIn(BaseModel):
    uid: str
    order_id: int
    ok: bool


class ShiftIn(BaseModel):
    uid: str
    days: float = Field(0, ge=-400, le=400)


@app.post("/api/event")
async def api_event(e: EventIn):
    check_uid(e.uid)
    if e.kind not in ("start", "text", "cb"):
        raise HTTPException(400, "bad kind")
    if e.kind == "start":
        db.push_web(e.uid, "user", {"text": "/start"})
    elif e.kind == "text":
        db.push_web(e.uid, "user", {"text": e.data})
    elif e.label:
        db.push_web(e.uid, "user", {"text": e.label})
    bot.submit(e.uid, e.kind, e.data)
    return {"ok": True}


@app.post("/api/pay")
async def api_pay(p: PayIn):
    check_uid(p.uid)
    order = db.order(p.order_id)
    if not order or order["uid"] != p.uid:
        raise HTTPException(404, "order not found")
    bot.submit_payment(p.uid, p.order_id, p.ok)
    return {"ok": True}


@app.get("/api/poll")
async def api_poll(uid: str, after: int = 0):
    check_uid(uid)
    rows = db.web_since(uid, after)
    return {
        "messages": [{"id": r["id"], "role": r["role"], "ts": r["ts"], **r["payload"]} for r in rows],
        "busy": bot.is_busy(uid),
    }


@app.get("/api/dev")
async def api_dev(uid: str):
    check_uid(uid)
    u = db.user(uid)
    tz = u["tz_min"] if u["tz_min"] is not None else cfg.DEFAULT_TZ_MINUTES
    local = db.now(uid) + timedelta(minutes=tz)
    return {
        "bot_time": local.strftime("%d.%m.%Y %H:%M"),
        "tz": tz_label(tz),
        "shift_days": round(db.time_shift(uid).total_seconds() / 86400, 2),
        "loading_seconds": bot.loading_seconds,
        "user": {"state": u["state"], "birthdate": u["birthdate"],
                 "tz_min": u["tz_min"]},
        "emails": [{"to": e["to_addr"], "subject": e["subject"], "file": e["pdf_file"], "body": e["body"]}
                   for e in db.emails(uid)],
        "gifts": [{"code": g["code"], "gift": g["params"]["gift"], "status": g["status"],
                   "link": cfg.GIFT_LINK.format(code=g["code"])} for g in db.gifts(uid)],
        "orders": [{"id": o["id"], "product": o["product"], "amount": o["amount"], "status": o["status"]}
                   for o in db.orders(uid)],
        "subs": [{"kind": s["kind"], "time": s["send_time"], "start": s["start_date"], "end": s["end_date"],
                  "last_sent": s["last_sent"], "active": bool(s["active"])} for s in db.subs(uid)],
    }


@app.post("/api/dev/shift")
async def api_shift(s: ShiftIn):
    check_uid(s.uid)
    db.shift_time(s.days, s.uid)
    return {"ok": True}


@app.post("/api/dev/reset")
async def api_reset(uid: str):
    check_uid(uid)
    db.delete_user(uid)
    return {"ok": True}


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(WEB / "static" / "index.html")


app.mount("/legal", StaticFiles(directory=WEB / "legal"), name="legal")
app.mount("/outbox", StaticFiles(directory=cfg.OUTBOX_DIR), name="outbox")
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
