"""Сценарий бота по майнд-карте. Не знает про веб или Telegram: общается через Sender."""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from . import config as cfg
from . import content as ct
from . import parsing
from . import pdf as pdfgen
from . import texts as T
from .db import DB
from .mail import LocalMailer
from .models import Button, Message, Sender, cb, pay, rows, share, url

log = logging.getLogger("numero")

# состояния диалога
IDLE = "idle"
AWAIT_DATE = "await_date"
CONFIRM_DATE = "confirm_date"
CONFIRM_TZ = "confirm_tz"
PICK_TZ = "pick_tz"
AWAIT_DELIVERY = "await_delivery"
AWAIT_COMPAT_TYPE = "await_compat_type"
AWAIT_EMAIL = "await_email"
CONFIRM_EMAIL = "confirm_email"
AWAIT_MONTH_CHOICE = "await_month_choice"
GIFT_WHO = "gift_who"
GIFT_PRODUCT = "gift_product"
GIFT_MONTH = "gift_month"
CONFIRM_SUB_START = "confirm_sub_start"
AWAIT_SUB_TIME = "await_sub_time"
CONFIRM_SUB = "confirm_sub"


def add_months(d: date, n: int) -> date:
    """Первое число месяца, который на n месяцев позже месяца даты d."""
    idx = d.year * 12 + (d.month - 1) + n
    return date(idx // 12, idx % 12 + 1, 1)


def tz_label(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "-"
    h, m = divmod(abs(minutes), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def menu_buttons() -> list[list[Button]]:
    return rows(
        cb("☀️ Расклад на день", "day"),
        cb("🌙 Расклад на месяц", "month"),
        cb("🗓 Расклад на год", "year"),
        cb("🔮 Расклад личный", "personal"),
        cb("💞 Проверить совместимость", "compat"),
        cb("🎁 Подарить другу", "gift"),
        url("📣 Подписаться на канал", cfg.CHANNEL_URL),
        url("🛠 Техподдержка", cfg.SUPPORT_URL),
    )


def with_menu(*extra: Button) -> list[list[Button]]:
    """Кнопки по контексту (если есть) и под ними все кнопки главного меню."""
    return rows(*extra) + menu_buttons()


# Что предлагаем в конце воронки совместимости: первое из ещё не купленного, дальше по приоритету.
# Если куплено всё, предлагаем подарить другу.
UPSELL_PRIORITY = [
    ("personal", cb("🔮 Личный расклад", "personal")),
    ("daily", cb("📅 Подписка на ежедневный расклад", "sub")),
    ("year", cb("🗓 Расклад на год", "year")),
    ("month", cb("🌙 Расклад на месяц", "month")),
]
GIFT_BTN = cb("🎁 Подарить другу", "gift")

MENU_BTN = cb("🏠 В главное меню", "menu")
CHANNEL_BTN = url("📣 Подписаться на канал", cfg.CHANNEL_URL)


class Out:
    """Отправка серии сообщений за один ход бота с паузой между ними."""

    def __init__(self, bot: "Bot", uid: str):
        self.bot, self.uid, self.n = bot, uid, 0

    async def say(self, text: str = "", buttons: Optional[list[list[Button]]] = None,
                  image: Optional[str] = None) -> Optional[int]:
        if self.n and self.bot.gap:
            await asyncio.sleep(self.bot.gap)
        self.n += 1
        return await self.bot.sender.send(self.uid, Message(text=text, buttons=buttons or [], image=image))


class Bot:
    def __init__(self, db: DB, sender: Sender, mailer=None, *,
                 loading_seconds: Optional[float] = None, gap: Optional[float] = None,
                 outbox: Optional[Path] = None):
        self.db = db
        self.sender = sender
        self.mailer = mailer or LocalMailer(db)
        self.loading_seconds = cfg.LOADING_SECONDS if loading_seconds is None else loading_seconds
        self.gap = cfg.MESSAGE_GAP_SECONDS if gap is None else gap
        self.outbox = outbox or cfg.OUTBOX_DIR
        self._locks: dict[str, asyncio.Lock] = {}
        self._inflight: dict[str, int] = {}
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ входы

    def is_busy(self, uid: str) -> bool:
        return self._inflight.get(uid, 0) > 0

    async def _run(self, uid: str, coro_fn, counted: bool = False) -> None:
        if not counted:
            self._inflight[uid] = self._inflight.get(uid, 0) + 1
        try:
            async with self._locks.setdefault(uid, asyncio.Lock()):
                try:
                    await coro_fn(Out(self, uid))
                except Exception:
                    log.exception("Сбой в сценарии, uid=%s", uid)
                    await Out(self, uid).say("Что-то пошло не так на нашей стороне. Попробуй ещё раз.",
                                             rows(MENU_BTN))
        finally:
            self._inflight[uid] -= 1

    def submit(self, uid: str, kind: str, data: str = "") -> asyncio.Task:
        """Запускает обработку в фоне и сразу помечает пользователя как занятого."""
        self._inflight[uid] = self._inflight.get(uid, 0) + 1
        return self._spawn(self.handle(uid, kind, data, counted=True))

    def submit_payment(self, uid: str, order_id: int, ok: bool) -> asyncio.Task:
        self._inflight[uid] = self._inflight.get(uid, 0) + 1
        return self._spawn(self.payment_result(uid, order_id, ok, counted=True))

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def handle(self, uid: str, kind: str, data: str = "", counted: bool = False) -> None:
        """kind: start | text | cb"""
        async def go(out: Out) -> None:
            text = data.strip() if kind == "text" else ""
            if kind == "start":
                return await self.cmd_start(uid, out, data.strip())
            if text.lower().split(" ")[0] in ("/start", "/старт"):
                return await self.cmd_start(uid, out, text.partition(" ")[2].strip())
            if kind == "cb":
                return await self.on_callback(uid, data, out)
            if text.lower() in ("/menu", "/меню"):
                return await self.go_home(uid, out)
            return await self.on_text(uid, text, out)
        await self._run(uid, go, counted)

    async def payment_result(self, uid: str, order_id: int, ok: bool, counted: bool = False) -> None:
        """Вызывается платёжным шлюзом (в веб-прототипе: окном тестовой оплаты)."""
        async def go(out: Out) -> None:
            order = self.db.order(order_id)
            if not order or order["uid"] != uid or order["status"] != "new":
                return await out.say(T.STALE, rows(MENU_BTN))
            if not ok:
                return await out.say(T.PAY_CANCEL, rows(MENU_BTN))
            self.db.update_order(order_id, status="paid", paid_at=datetime.now().timestamp())
            await out.say(T.PAY_OK)
            await self.fulfil(uid, self.db.order(order_id), out)
        await self._run(uid, go, counted)

    async def tick(self) -> None:
        """Рассылка по подпискам. Вызывается планировщиком раз в несколько секунд."""
        for s in self.db.active_subs():
            local = self.db.now(s["uid"]) + timedelta(minutes=s["tz_min"])
            ld = local.date().isoformat()
            if ld > s["end_date"]:
                self.db.update_sub(s["id"], active=0)
                continue
            if local.strftime("%H:%M") < s["send_time"]:
                continue
            monthly = s["kind"] == "monthly"
            if monthly:  # раз в месяц: в первый тик нового месяца после времени рассылки
                last = date.fromisoformat(s["last_sent"] or s["start_date"])
                if local.year * 12 + local.month <= last.year * 12 + last.month:
                    continue
            elif ld <= s["start_date"] or s["last_sent"] == ld:
                continue
            self.db.update_sub(s["id"], last_sent=ld)
            birth = date.fromisoformat(s["birthdate"])
            if monthly:
                async def go_month(out: Out, birth=birth, on=local.date()) -> None:
                    await out.say(ct.month_reading(birth, on), rows(MENU_BTN))
                await self._run(s["uid"], go_month)
                continue
            days_left = (date.fromisoformat(s["end_date"]) - local.date()).days
            remind_oid = None
            if days_left <= 1 and not s["reminded"]:  # за сутки до последнего расчёта
                self.db.update_sub(s["id"], reminded=1)
                remind_oid = self.db.create_order(
                    s["uid"], "sub_month", cfg.PRICES["sub_month"],
                    {"birth": s["birthdate"], "tz": s["tz_min"], "time": s["send_time"]})

            async def go(out: Out, birth=birth, on=local.date(), oid=remind_oid, left=days_left) -> None:
                await out.say(ct.day_reading(birth, on), rows(MENU_BTN))
                if oid:
                    price = cfg.PRICES["sub_month"]
                    await out.say(T.sub_remind(left, cfg.SUB_DAYS, price),
                                  rows(pay(f"💳 Продлить за {price} ₽", oid, price)))
            await self._run(s["uid"], go)

    # ------------------------------------------------------------------ помощники

    def _set(self, uid: str, state: Optional[str] = None, **ctx_updates) -> dict:
        u = self.db.user(uid)
        ctx = u["ctx"]
        ctx.update(ctx_updates)
        self.db.update_user(uid, state=state or u["state"], ctx=ctx)
        return ctx

    def _begin(self, uid: str, flow: str) -> None:
        self.db.update_user(uid, state=IDLE, ctx={"flow": flow})

    def _local_now(self, uid: str) -> datetime:
        tz = self.db.user(uid)["tz_min"]
        return self.db.now(uid) + timedelta(minutes=cfg.DEFAULT_TZ_MINUTES if tz is None else tz)

    async def _stale(self, out: Out) -> None:
        await out.say(T.STALE, rows(MENU_BTN))

    def _need(self, uid: str, *states: str) -> bool:
        return self.db.user(uid)["state"] in states

    # ------------------------------------------------------------------ старт и меню

    async def cmd_start(self, uid: str, out: Out, payload: str = "") -> None:
        """Чистый старт: стираем чат, показываем вводные сообщения и меню.
        По подарочной ссылке (payload gift_<код>) вместо вводных сообщений открываем подарок."""
        self.db.update_user(uid, state=IDLE, ctx={})
        await self.sender.truncate(uid, 0)
        if payload.startswith("gift_") and await self.redeem_gift(uid, payload[5:], out):
            return
        await out.say(T.INTRO_1)
        anchor = await out.say(T.INTRO_2)
        self.db.update_user(uid, anchor=anchor or 0)
        await self.show_menu(uid, out)

    async def go_home(self, uid: str, out: Out) -> None:
        """«В главное меню»: чат возвращается к экрану «На чём всё построено», остальное стирается."""
        anchor = self.db.user(uid)["anchor"]
        if not anchor:
            return await self.cmd_start(uid, out)
        await self.sender.truncate(uid, anchor)
        await self.show_menu(uid, out)

    async def show_menu(self, uid: str, out: Out) -> None:
        self.db.update_user(uid, state=IDLE, ctx={})
        await out.say(T.MENU, menu_buttons())

    # ------------------------------------------------------------------ кнопки

    async def on_callback(self, uid: str, data: str, out: Out) -> None:
        name, _, arg = data.partition(":")
        u = self.db.user(uid)

        # навигация: работает всегда
        if name == "menu":
            return await self.go_home(uid, out)
        if name in ("day", "personal", "sub"):
            self._begin(uid, name)
            if name == "sub":
                await out.say(T.SUB_INTRO)
            return await self.ask_date(uid, out, "main")
        if name == "month":
            return await self.ask_month_choice(uid, out)
        if name == "msubstart":
            return await self.start_monthly_subscription(uid, int(arg), out)
        if name == "compat":
            self._begin(uid, "compat")
            self.db.update_user(uid, state=AWAIT_COMPAT_TYPE)
            return await out.say("Что проверяем?", rows(
                cb("💼 Работа", "ctype:work"), cb("❤️ Любовь", "ctype:love"), cb("🤝 Дружба", "ctype:friend")))
        if name == "year":
            return await out.say(T.STUB_YEAR, rows(MENU_BTN))
        if name == "gift":
            return await self.ask_gift_who(uid, out)
        if name == "readhere":
            return await self.read_here(uid, int(arg), out)
        if name == "pdfmail":
            return await self.start_postpay_email(uid, int(arg), out)
        if name == "sendto":
            return await self.send_to_friend_menu(uid, int(arg), out)
        if name == "sendmail":
            return await self.start_friend_email(uid, int(arg), out)
        if name == "next":
            oid, _, idx = arg.partition(":")
            return await self.next_block(uid, int(oid), int(idx), out)

        # шаги внутри сценария: только если состояние совпадает
        ctx = u["ctx"]
        if name == "gwho" and u["state"] == GIFT_WHO and arg in ("tg", "mail"):
            return await self.on_gift_who(uid, arg, out)
        if name == "gprod" and u["state"] == GIFT_PRODUCT and arg in ("day", "month", "year", "subd", "subm"):
            return await self.on_gift_product(uid, arg, out)
        if name == "gmonth" and u["state"] == GIFT_MONTH and arg in ("cur", "next"):
            cur, nxt = self._month_pair(uid)
            return await self.create_gift_order(uid, out, "month", (cur if arg == "cur" else nxt).isoformat())
        if name == "mchoice" and u["state"] == AWAIT_MONTH_CHOICE and arg in ("cur", "next"):
            self._set(uid, which_month=arg)
            return await self.ask_date(uid, out, "main")
        if name == "ctype" and u["state"] == AWAIT_COMPAT_TYPE and arg in ("work", "love", "friend"):
            self._set(uid, ctype=arg)
            return await self.ask_date(uid, out, "main")
        if name in ("date_ok", "date_no") and u["state"] == CONFIRM_DATE:
            return await self.on_date_answer(uid, name == "date_ok", out)
        if name in ("tz_ok", "tz_no") and u["state"] == CONFIRM_TZ:
            return await self.on_tz_answer(uid, name == "tz_ok", out)
        if name == "tz" and u["state"] == PICK_TZ:
            self.db.update_user(uid, tz_min=int(arg))
            return await self.ask_tz_check(uid, out)
        if name == "deliver" and u["state"] == AWAIT_DELIVERY and arg in ("here", "pdf"):
            return await self.on_delivery(uid, arg, out)
        if name in ("email_ok", "email_no") and u["state"] == CONFIRM_EMAIL:
            return await self.on_email_answer(uid, name == "email_ok", out)
        if name == "sub_ok" and u["state"] == CONFIRM_SUB:
            return await self.on_sub_confirm(uid, out)
        if name == "sub_agree" and u["state"] == CONFIRM_SUB_START:
            self.db.update_user(uid, state=AWAIT_SUB_TIME)
            return await out.say(T.MSUB_TIME_ASK if ctx.get("flow") in ("msub", "giftsubm") else T.SUB_TIME_ASK)
        if name == "sub_time" and u["state"] == CONFIRM_SUB:
            self.db.update_user(uid, state=AWAIT_SUB_TIME)
            return await out.say(T.MSUB_TIME_ASK if ctx.get("flow") in ("msub", "giftsubm") else T.SUB_TIME_ASK)
        await self._stale(out)

    # ------------------------------------------------------------------ текст

    async def on_text(self, uid: str, text: str, out: Out) -> None:
        state = self.db.user(uid)["state"]
        if state == AWAIT_DATE:
            return await self.on_date_text(uid, text, out)
        if state == AWAIT_EMAIL:
            return await self.on_email_text(uid, text, out)
        if state == AWAIT_SUB_TIME:
            return await self.on_sub_time_text(uid, text, out)
        if state in (CONFIRM_DATE, CONFIRM_TZ, CONFIRM_EMAIL):
            ans = parsing.yes_no(text)
            if ans is None:
                return await out.say(T.YESNO_HINT)
            data = {CONFIRM_DATE: ("date_ok", "date_no"), CONFIRM_TZ: ("tz_ok", "tz_no"),
                    CONFIRM_EMAIL: ("email_ok", "email_no")}[state]
            return await self.on_callback(uid, data[0] if ans else data[1], out)
        if state == CONFIRM_SUB_START and parsing.yes_no(text):
            return await self.on_callback(uid, "sub_agree", out)
        if state in (AWAIT_DELIVERY, AWAIT_COMPAT_TYPE, PICK_TZ, CONFIRM_SUB, CONFIRM_SUB_START,
                     AWAIT_MONTH_CHOICE, GIFT_WHO, GIFT_PRODUCT, GIFT_MONTH):
            return await out.say(T.USE_BUTTONS)
        await out.say(T.FALLBACK, menu_buttons())

    # ------------------------------------------------------------------ дата рождения

    async def ask_date(self, uid: str, out: Out, which: str) -> None:
        u = self.db.user(uid)
        if which == "main" and u["birthdate"]:
            iso = u["birthdate"]
            self._set(uid, CONFIRM_DATE, which="main", pending=iso)
            return await out.say(
                f"Твоя дата рождения: <b>{ct.fmt_date(date.fromisoformat(iso))}</b>. Верно?",
                [[cb("✅ Да", "date_ok"), cb("✏️ Нет, другая", "date_no")]])
        self._set(uid, AWAIT_DATE, which=which)
        await out.say({"main": T.DATE_ASK, "second": T.DATE_ASK_SECOND, "friend": T.DATE_ASK_FRIEND}[which])

    async def on_date_text(self, uid: str, text: str, out: Out) -> None:
        today = self._local_now(uid).date()
        parsed, err = parsing.parse_date(text, today)
        if err:
            return await out.say(f"{T.DATE_ERRORS[err]}\n\n{T.DATE_CRITERIA}\n\nВведи дату ещё раз.")
        self._set(uid, CONFIRM_DATE, pending=parsed.isoformat())
        await out.say(f"Верная дата: <b>{ct.fmt_date(parsed)}</b>?",
                      [[cb("✅ Да, верно", "date_ok"), cb("✏️ Нет, ввести заново", "date_no")]])

    async def on_date_answer(self, uid: str, ok: bool, out: Out) -> None:
        ctx = self.db.user(uid)["ctx"]
        which = ctx.get("which", "main")
        if not ok:
            self._set(uid, AWAIT_DATE)
            return await out.say(f"Хорошо, введи дату ещё раз.\n\n{T.DATE_CRITERIA}")
        iso = ctx["pending"]
        if which == "main":
            self._set(uid, IDLE, birth1=iso)
            self.db.update_user(uid, birthdate=iso)
            return await self.after_birth1(uid, out)
        if which == "friend":
            self._set(uid, IDLE, gift_birth=iso)
            return await self.ask_gift_product(uid, out)
        self._set(uid, IDLE, birth2=iso)
        await self.ask_delivery(uid, out)

    async def after_birth1(self, uid: str, out: Out) -> None:
        flow = self.db.user(uid)["ctx"]["flow"]
        if flow in ("day", "sub", "mread"):
            return await self.ask_tz_check(uid, out)
        if flow == "personal":
            return await self.ask_delivery(uid, out)
        if flow == "compat":
            return await self.ask_date(uid, out, "second")

    # ------------------------------------------------------------------ часовой пояс

    async def ask_tz_check(self, uid: str, out: Out) -> None:
        self.db.update_user(uid, state=CONFIRM_TZ)
        today = self._local_now(uid).date()
        await out.say(f"Давай сверим часовые пояса. Сегодня <b>{ct.fmt_date(today)}</b>?",
                      [[cb("✅ Да", "tz_ok"), cb("❌ Нет", "tz_no")]])

    async def on_tz_answer(self, uid: str, ok: bool, out: Out) -> None:
        if not ok:
            self.db.update_user(uid, state=PICK_TZ)
            return await out.say(T.TZ_PICK, rows(*[cb(label, f"tz:{m}") for m, label in cfg.TZ_OPTIONS]))
        u = self.db.user(uid)
        if u["tz_min"] is None:
            self.db.update_user(uid, tz_min=cfg.DEFAULT_TZ_MINUTES)
        flow = self.db.user(uid)["ctx"].get("flow")
        if flow in ("giftsubd", "giftsubm"):  # получатель подарочной подписки выбирает время
            self.db.update_user(uid, state=AWAIT_SUB_TIME)
            return await out.say(T.MSUB_TIME_ASK if flow == "giftsubm" else T.SUB_TIME_ASK)
        self.db.update_user(uid, state=IDLE)
        await self.give_day_reading(uid, out)

    async def give_day_reading(self, uid: str, out: Out) -> None:
        ctx = self.db.user(uid)["ctx"]
        if ctx["flow"] == "mread":
            return await self.offer_month_payment(uid, out)
        birth = date.fromisoformat(ctx["birth1"])
        await out.say(T.LOADING, image="loader")
        await asyncio.sleep(self.loading_seconds)
        on = self._local_now(uid).date()
        text = ct.day_reading(birth, on)
        if ctx["flow"] == "sub":
            await out.say(text)
            self.db.update_user(uid, state=CONFIRM_SUB_START)
            return await out.say(T.SUB_PITCH, rows(cb("✅ Хочу подписаться", "sub_agree")))
        await out.say(text, rows(
            cb("📅 Подписка на ежедневную рассылку на месяц", "sub"),
            MENU_BTN,
            cb("🔮 Расклад личный", "personal"),
            cb("🗓 Годовой расклад", "year"),
            CHANNEL_BTN,
            cb("💞 Проверить совместимость", "compat"),
        ))
        if ctx.get("gift"):
            self.db.redeem_gift(ctx["gift"], uid)
        self.db.update_user(uid, ctx={}, state=IDLE)

    # ------------------------------------------------------------------ подписка

    async def on_sub_time_text(self, uid: str, text: str, out: Out) -> None:
        t = parsing.parse_time(text)
        if not t:
            return await out.say(T.SUB_TIME_BAD)
        ctx = self._set(uid, CONFIRM_SUB, sub_time=t)
        tz = self.db.user(uid)["tz_min"] or cfg.DEFAULT_TZ_MINUTES
        if ctx.get("flow") == "giftsubd":
            confirm = T.gift_sub_confirm_daily(t, tz_label(tz), cfg.SUB_DAYS)
        elif ctx.get("flow") == "giftsubm":
            today = self._local_now(uid).date()
            first = ct.fmt_date(add_months(date(today.year, today.month, 1), 1))
            confirm = T.gift_sub_confirm_monthly(t, tz_label(tz), cfg.SUBM_MONTHS, first)
        elif ctx.get("flow") == "msub":
            first = ct.fmt_date(date.fromisoformat(ctx["sub_first"]))
            confirm = T.msub_confirm(t, tz_label(tz), cfg.SUBM_MONTHS, first, cfg.PRICES["msub"])
        else:
            confirm = T.sub_confirm(t, tz_label(tz), cfg.PRICES["sub_month"])
        await out.say(confirm, [[cb("✅ Подтверждаю", "sub_ok")], [cb("🕐 Другое время", "sub_time")]])

    async def on_sub_confirm(self, uid: str, out: Out) -> None:
        u = self.db.user(uid)
        ctx = u["ctx"]
        if ctx.get("flow") in ("giftsubd", "giftsubm"):
            return await self.redeem_gift_sub(uid, out)
        params = {"birth": ctx["birth1"], "tz": u["tz_min"] or cfg.DEFAULT_TZ_MINUTES, "time": ctx["sub_time"]}
        product = "msub" if ctx.get("flow") == "msub" else "sub_month"
        price = cfg.PRICES[product]
        if product == "msub":
            params["first"] = ctx["sub_first"]
        oid = self.db.create_order(uid, product, price, params)
        self.db.update_user(uid, state=IDLE)
        if product == "msub":
            text = T.msub_pay(ct.fmt_date(date.fromisoformat(ctx["sub_first"])), ctx["sub_time"])
        else:
            text = T.SUB_PAY.format(time=ctx["sub_time"])
        await out.say(text, rows(pay(f"💳 Оплатить {price} ₽", oid, price), MENU_BTN))

    # ------------------------------------------------------------------ расклад на месяц, потом подписка

    async def ask_month_choice(self, uid: str, out: Out) -> None:
        self._begin(uid, "mread")
        self.db.update_user(uid, state=AWAIT_MONTH_CHOICE)
        today = self._local_now(uid).date()
        cur = date(today.year, today.month, 1)
        nxt = add_months(cur, 1)
        await out.say(T.MONTH_CHOICE, rows(
            cb(f"Текущий месяц ({ct.MONTHS_NOM[cur.month - 1]} {cur.year})", "mchoice:cur"),
            cb(f"Следующий месяц ({ct.MONTHS_NOM[nxt.month - 1]} {nxt.year})", "mchoice:next")))

    async def offer_month_payment(self, uid: str, out: Out) -> None:
        ctx = self.db.user(uid)["ctx"]
        today = self._local_now(uid).date()
        first = add_months(date(today.year, today.month, 1), 1 if ctx.get("which_month") == "next" else 0)
        price = cfg.PRICES["month"]
        oid = self.db.create_order(uid, "month", price, {"birth": ctx["birth1"], "month": first.isoformat()})
        self.db.update_user(uid, state=IDLE, ctx={})
        await out.say(T.month_pay(ct.MONTHS_NOM[first.month - 1], first.year, price),
                      rows(pay(f"💳 Оплатить {price} ₽", oid, price), MENU_BTN))

    async def start_monthly_subscription(self, uid: str, order_id: int, out: Out) -> None:
        order = self.db.order(order_id)
        if not order or order["uid"] != uid or order["status"] != "paid" or order["product"] != "month":
            return await self._stale(out)
        first = add_months(date.fromisoformat(order["params"]["month"]), 1)  # подписка идёт со следующего месяца
        self.db.update_user(uid, state=AWAIT_SUB_TIME, ctx={
            "flow": "msub", "birth1": order["params"]["birth"], "sub_first": first.isoformat()})
        await out.say(T.MSUB_TIME_ASK)

    # ------------------------------------------------------------------ подарок другу

    def _month_pair(self, uid: str) -> tuple[date, date]:
        today = self._local_now(uid).date()
        cur = date(today.year, today.month, 1)
        return cur, add_months(cur, 1)

    def gift_title(self, params: dict) -> str:
        key = params["gift"]
        if key == "day":
            return "расклад на день"
        if key == "month":
            m = date.fromisoformat(params["month"])
            return f"расклад на {ct.MONTHS_NOM[m.month - 1]} {m.year}"
        if key == "subd":
            return f"подписку на ежедневную рассылку ({cfg.SUB_DAYS} дней)"
        return f"подписку на ежемесячный расклад ({T.plural_months(cfg.SUBM_MONTHS)})"

    async def ask_gift_who(self, uid: str, out: Out) -> None:
        self._begin(uid, "gift")
        self.db.update_user(uid, state=GIFT_WHO)
        await out.say(T.GIFT_WHO, rows(cb("📨 Контакт в Telegram", "gwho:tg"), cb("📧 Указать почту", "gwho:mail")))

    async def on_gift_who(self, uid: str, via: str, out: Out) -> None:
        self._set(uid, gift_via=via)
        if via == "mail":
            self.db.update_user(uid, state=AWAIT_EMAIL)
            return await out.say(T.GIFT_EMAIL_ASK)
        await self.ask_date(uid, out, "friend")

    async def ask_gift_product(self, uid: str, out: Out) -> None:
        self.db.update_user(uid, state=GIFT_PRODUCT)
        await out.say(T.GIFT_PRODUCT, rows(
            cb("☀️ Расклад на день", "gprod:day"),
            cb("🌙 Расклад на месяц", "gprod:month"),
            cb("🗓 Расклад на год", "gprod:year"),
            cb("📅 Подписка на ежедневную рассылку", "gprod:subd"),
            cb("📆 Подписка на ежемесячный расклад", "gprod:subm")))

    async def on_gift_product(self, uid: str, key: str, out: Out) -> None:
        if key == "year":
            await out.say(T.STUB_GIFT_YEAR)
            return await self.ask_gift_product(uid, out)
        if key == "month":
            self.db.update_user(uid, state=GIFT_MONTH)
            cur, nxt = self._month_pair(uid)
            return await out.say(T.GIFT_MONTH_ASK, rows(
                cb(f"Текущий месяц ({ct.MONTHS_NOM[cur.month - 1]} {cur.year})", "gmonth:cur"),
                cb(f"Следующий месяц ({ct.MONTHS_NOM[nxt.month - 1]} {nxt.year})", "gmonth:next")))
        await self.create_gift_order(uid, out, key)

    async def create_gift_order(self, uid: str, out: Out, key: str, month: Optional[str] = None) -> None:
        ctx = self.db.user(uid)["ctx"]
        price = {"day": cfg.PRICES["gift_day"], "month": cfg.PRICES["month"],
                 "subd": cfg.PRICES["sub_month"], "subm": cfg.PRICES["msub"]}[key]
        params = {"gift": key, "birth": ctx["gift_birth"], "via": ctx["gift_via"],
                  "email": ctx.get("gift_email"), "month": month}
        oid = self.db.create_order(uid, "gift", price, params)
        self.db.update_user(uid, state=IDLE, ctx={})
        birth = ct.fmt_date(date.fromisoformat(params["birth"]))
        await out.say(T.gift_pay(self.gift_title(params), birth, price),
                      rows(pay(f"💳 Оплатить {price} ₽", oid, price), MENU_BTN))

    async def deliver_gift(self, uid: str, order: dict, out: Out) -> None:
        params = order["params"]
        code = secrets.token_hex(4)
        self.db.create_gift(code, uid, params)
        link = cfg.GIFT_LINK.format(code=code)
        title = self.gift_title(params)
        if params["via"] == "mail":
            await self.mailer.send_text(uid, params["email"], f"{cfg.BOT_NAME}: тебе подарок",
                                        T.gift_letter(title, link))
            return await out.say(T.GIFT_SENT_MAIL.format(email=params["email"]),
                                 rows(self.next_offer(uid), CHANNEL_BTN, MENU_BTN))
        await out.say(T.gift_ready_tg(link), rows(
            share("📨 Переслать подарок", T.gift_share(title, link)),
            self.next_offer(uid), CHANNEL_BTN, MENU_BTN))

    async def redeem_gift(self, uid: str, code: str, out: Out) -> bool:
        """Друг открыл подарочную ссылку. True, если подарок выдан (или выдача началась)."""
        gift = self.db.gift(code)
        if not gift or gift["status"] != "new":
            await out.say(T.GIFT_BAD)
            return False
        params = gift["params"]
        await out.say(T.gift_hello(self.gift_title(params)))
        key = params["gift"]
        if key == "month":
            self.db.redeem_gift(code, uid)
            await out.say(ct.month_reading(date.fromisoformat(params["birth"]),
                                           date.fromisoformat(params["month"])),
                          rows(self.next_offer(uid), CHANNEL_BTN, MENU_BTN))
            return True
        flow = {"day": "day", "subd": "giftsubd", "subm": "giftsubm"}[key]
        self.db.update_user(uid, state=IDLE, ctx={"flow": flow, "birth1": params["birth"], "gift": code})
        await self.ask_tz_check(uid, out)
        return True

    async def redeem_gift_sub(self, uid: str, out: Out) -> None:
        u = self.db.user(uid)
        ctx = u["ctx"]
        params = {"birth": ctx["birth1"], "tz": u["tz_min"] or cfg.DEFAULT_TZ_MINUTES, "time": ctx["sub_time"]}
        today = self._local_now(uid).date()
        if ctx["flow"] == "giftsubd":
            text = self._activate_daily(uid, params, today)
        else:
            params["first"] = add_months(date(today.year, today.month, 1), 1).isoformat()
            text = self._activate_monthly(uid, params, today)
        self.db.redeem_gift(ctx["gift"], uid)
        self.db.update_user(uid, state=IDLE, ctx={})
        await out.say(text, rows(self.next_offer(uid), CHANNEL_BTN, MENU_BTN))

    def _activate_daily(self, uid: str, params: dict, start: date) -> str:
        """Ежедневная подписка на 30 дней: новая или продление действующей. Возвращает текст для пользователя."""
        current = next((s for s in self.db.subs(uid) if s["active"] and s["kind"] == "daily"
                        and s["end_date"] >= start.isoformat()), None)
        if current:  # продление: новые дни добавляются к концу действующей подписки
            new_end = date.fromisoformat(current["end_date"]) + timedelta(days=cfg.SUB_DAYS)
            self.db.update_sub(current["id"], end_date=new_end.isoformat(), reminded=0,
                               birthdate=params["birth"], tz_min=params["tz"], send_time=params["time"])
            return T.sub_extended(ct.fmt_date(new_end), params["time"])
        self.db.create_sub(uid, params["birth"], params["tz"], params["time"],
                           start.isoformat(), (start + timedelta(days=cfg.SUB_DAYS)).isoformat())
        return T.sub_done(params["time"])

    def _activate_monthly(self, uid: str, params: dict, today: date) -> str:
        """Ежемесячная подписка: params["first"] это первое число месяца первой рассылки."""
        current = next((s for s in self.db.subs(uid) if s["active"] and s["kind"] == "monthly"
                        and s["end_date"] >= today.isoformat()), None)
        if current:  # продление: месяцы добавляются к концу действующей подписки
            end = date.fromisoformat(current["end_date"])
            new_end = add_months(end, cfg.SUBM_MONTHS + 1) - timedelta(days=1)
            self.db.update_sub(current["id"], end_date=new_end.isoformat(), birthdate=params["birth"],
                               tz_min=params["tz"], send_time=params["time"])
            return T.msub_extended(ct.fmt_date(new_end), params["time"])
        first = date.fromisoformat(params["first"])
        end = add_months(first, cfg.SUBM_MONTHS) - timedelta(days=1)
        self.db.create_sub(uid, params["birth"], params["tz"], params["time"],
                           (first - timedelta(days=1)).isoformat(), end.isoformat(), kind="monthly")
        return T.msub_done(ct.fmt_date(first), params["time"])

    # ------------------------------------------------------------------ способ получения и почта

    async def ask_delivery(self, uid: str, out: Out) -> None:
        self.db.update_user(uid, state=AWAIT_DELIVERY)
        await out.say(T.DELIVERY_ASK, [[cb("💬 Тут", "deliver:here"), cb("📧 PDF на почту", "deliver:pdf")]])

    async def on_delivery(self, uid: str, mode: str, out: Out) -> None:
        self._set(uid, IDLE, mode=mode)
        if mode == "pdf":
            self.db.update_user(uid, state=AWAIT_EMAIL)
            return await out.say(T.EMAIL_ASK)
        await self.create_reading_order(uid, out, email=None)

    async def on_email_text(self, uid: str, text: str, out: Out) -> None:
        email = parsing.parse_email(text)
        if not email:
            return await out.say(T.EMAIL_BAD)
        self._set(uid, CONFIRM_EMAIL, pending_email=email)
        await out.say(f"Перепроверь почту: <b>{email}</b>. Всё верно?",
                      [[cb("✅ Да", "email_ok"), cb("✏️ Нет, ввести заново", "email_no")]])

    async def on_email_answer(self, uid: str, ok: bool, out: Out) -> None:
        ctx = self.db.user(uid)["ctx"]
        if not ok:
            self.db.update_user(uid, state=AWAIT_EMAIL)
            return await out.say(T.FRIEND_EMAIL_ASK if ctx.get("flow") == "friend"
                                 else T.GIFT_EMAIL_ASK if ctx.get("flow") == "gift" else T.EMAIL_ASK)
        email = ctx["pending_email"]
        self.db.update_user(uid, state=IDLE)
        if ctx.get("flow") == "gift":
            self._set(uid, IDLE, gift_email=email)
            return await self.ask_date(uid, out, "friend")
        if ctx.get("flow") == "friend":
            order = self.db.order(ctx["order"])
            await self.mail_pdf(uid, order, email, body=self.friend_letter(order))
            fresh = self.db.order(order["id"])  # в заказе уже может быть токен PDF
            self.db.update_order(order["id"], params={**fresh["params"], "friend_sent": True})
            return await out.say(T.FRIEND_SENT.format(email=email),
                                 self.compat_end_buttons(self.db.order(order["id"])))
        if ctx.get("flow") == "postpay":
            order = self.db.order(ctx["order"])
            await self.mail_pdf(uid, order, email)
            if order["product"] == "compat":
                return await out.say(T.EMAIL_SENT, self.compat_end_buttons(order, pdf=False))
            return await out.say(T.EMAIL_SENT, with_menu())
        await self.create_reading_order(uid, out, email=email)

    async def start_postpay_email(self, uid: str, order_id: int, out: Out) -> None:
        order = self.db.order(order_id)
        if not order or order["uid"] != uid or order["status"] != "paid":
            return await self._stale(out)
        self.db.update_user(uid, state=AWAIT_EMAIL, ctx={"flow": "postpay", "order": order_id})
        await out.say(T.EMAIL_ASK)

    # ------------------------------------------------------------------ отправить результат тому, с кем проверяли

    def _paid_compat(self, uid: str, order_id: int) -> Optional[dict]:
        order = self.db.order(order_id)
        if order and order["uid"] == uid and order["status"] == "paid" and order["product"] == "compat":
            return order
        return None

    def friend_share_text(self, order: dict) -> str:
        params = order["params"]
        r = ct.compat_result(params["ctype"], date.fromisoformat(params["birth"]),
                             date.fromisoformat(params["birth2"]))
        return (f"Мы проверили совместимость в «{cfg.BOT_NAME}»: {r.score}% ({r.cls_title}). "
                f"Проверь свою: {cfg.BOT_LINK}")

    def friend_letter(self, order: dict) -> str:
        return (f"Привет! Тебе отправили результат проверки совместимости из «{cfg.BOT_NAME}». "
                f"Подробности во вложении (PDF).\n\nПроверь свою совместимость: {cfg.BOT_LINK}")

    async def send_to_friend_menu(self, uid: str, order_id: int, out: Out) -> None:
        order = self._paid_compat(uid, order_id)
        if not order:
            return await self._stale(out)
        await out.say(T.FRIEND_SEND_HOW, rows(
            share("📨 Через Telegram", self.friend_share_text(order)),
            cb("📧 У него нет Telegram: на почту", f"sendmail:{order_id}"),
            MENU_BTN))

    async def start_friend_email(self, uid: str, order_id: int, out: Out) -> None:
        if not self._paid_compat(uid, order_id):
            return await self._stale(out)
        self.db.update_user(uid, state=AWAIT_EMAIL, ctx={"flow": "friend", "order": order_id})
        await out.say(T.FRIEND_EMAIL_ASK)

    # ------------------------------------------------------------------ заказы: личный расклад и совместимость

    async def create_reading_order(self, uid: str, out: Out, email: Optional[str]) -> None:
        ctx = self.db.user(uid)["ctx"]
        flow, mode = ctx["flow"], ctx["mode"]
        product = "personal" if flow == "personal" else "compat"
        price = cfg.PRICES[product]
        params = {"mode": mode, "email": email, "birth": ctx["birth1"]}
        if flow == "compat":
            params.update(ctype=ctx["ctype"], birth2=ctx["birth2"])
        oid = self.db.create_order(uid, product, price, params)
        self.db.update_user(uid, state=IDLE, ctx={})
        pay_btn = pay(f"💳 Оплатить {price} ₽", oid, price)

        if mode == "pdf":
            what = "Личный расклад" if flow == "personal" else "Результат совместимости"
            return await out.say(f"{what} оформлю в PDF и отправлю на <b>{email}</b>.\nСтоимость: {price} ₽.",
                                 rows(pay_btn, MENU_BTN))
        if flow == "personal":
            first = ct.personal_blocks(date.fromisoformat(ctx["birth1"]))[0]
            await out.say(first.render())
            return await out.say(
                f"Это первый из {first.total} блоков. Остальные {first.total - 1} откроются после оплаты: {price} ₽.",
                rows(pay_btn, MENU_BTN))
        await out.say(f"Результат готов. Чтобы открыть его, оплати {price} ₽.", rows(pay_btn, MENU_BTN))

    async def fulfil(self, uid: str, order: dict, out: Out) -> None:
        product, params = order["product"], order["params"]
        if product == "month":
            await out.say(ct.month_reading(date.fromisoformat(params["birth"]),
                                           date.fromisoformat(params["month"])))
            return await out.say(T.MSUB_OFFER, rows(
                cb("✅ Хочу подписаться", f"msubstart:{order['id']}"),
                self.next_offer(uid), CHANNEL_BTN, MENU_BTN))
        if product == "gift":
            return await self.deliver_gift(uid, order, out)
        if product == "msub":
            text = self._activate_monthly(uid, params, self._local_now(uid).date())
            return await out.say(text, rows(self.next_offer(uid), CHANNEL_BTN, MENU_BTN))
        if product == "sub_month":
            text = self._activate_daily(uid, params, self._local_now(uid).date())
            return await out.say(text, rows(self.next_offer(uid), CHANNEL_BTN, MENU_BTN))
        if params["mode"] == "pdf":
            await self.mail_pdf(uid, order, params["email"])
            read_btn = cb("💬 Всё-таки прочитать здесь", f"readhere:{order['id']}")
            if product == "compat":
                return await out.say(T.EMAIL_SENT_PAID, self.compat_end_buttons(order, pdf=False, first=(read_btn,)))
            return await out.say(T.EMAIL_SENT_PAID, with_menu(read_btn))
        await self.deliver_here(uid, order, out, skip_first=(product == "personal"))

    async def read_here(self, uid: str, order_id: int, out: Out) -> None:
        order = self.db.order(order_id)
        if not order or order["uid"] != uid or order["status"] != "paid":
            return await self._stale(out)
        await self.deliver_here(uid, order, out, skip_first=False)

    async def deliver_here(self, uid: str, order: dict, out: Out, skip_first: bool) -> None:
        params = order["params"]
        if order["product"] == "personal":
            return await self.send_block(order, 2 if skip_first else 1, out)
        r = ct.compat_result(params["ctype"], date.fromisoformat(params["birth"]),
                             date.fromisoformat(params["birth2"]))
        await out.say(r.render(), self.compat_end_buttons(order))

    def compat_end_buttons(self, order: dict, pdf: bool = True, first: tuple = ()) -> list[list[Button]]:
        """Конец воронки совместимости: короткий набор кнопок вместо полного меню."""
        btns = list(first)
        if not order["params"].get("friend_sent"):  # уже отправили тому, с кем проверяли
            btns.append(cb("📨 Отправить тому, с кем проверяли", f"sendto:{order['id']}"))
        if pdf:
            btns.append(cb("📧 Получить в PDF", f"pdfmail:{order['id']}"))
        btns += [self.next_offer(order["uid"]), CHANNEL_BTN, MENU_BTN]
        return rows(*btns)

    def owns(self, uid: str, product: str) -> bool:
        """Куплен ли продукт пользователем. Год и месяц пока нельзя купить, поэтому они всегда «не куплены»."""
        if product == "daily":
            return any(sub["active"] and sub["kind"] == "daily" for sub in self.db.subs(uid))
        products = ("month", "msub") if product == "month" else (product,)
        return any(o["status"] == "paid" and o["product"] in products for o in self.db.orders(uid))

    def next_offer(self, uid: str) -> Button:
        for product, button in UPSELL_PRIORITY:
            if not self.owns(uid, product):
                return button
        return GIFT_BTN

    async def send_block(self, order: dict, idx: int, out: Out) -> None:
        """Один блок личного расклада (нумерация с 1). Следующий приходит по кнопке «Далее»."""
        blocks = ct.personal_blocks(date.fromisoformat(order["params"]["birth"]))
        block = blocks[idx - 1]
        if idx < len(blocks):
            return await out.say(block.render(), rows(cb("Далее ▶", f"next:{order['id']}:{idx + 1}")))
        await out.say(block.render())
        await out.say(T.OFFER_PDF, with_menu(
            cb("📧 Прислать PDF на почту", f"pdfmail:{order['id']}")))

    async def next_block(self, uid: str, order_id: int, idx: int, out: Out) -> None:
        order = self.db.order(order_id)
        if (not order or order["uid"] != uid or order["status"] != "paid"
                or order["product"] != "personal" or not 1 < idx <= len(ct.PERSONAL_BLOCKS)):
            return await self._stale(out)
        await self.send_block(order, idx, out)

    async def mail_pdf(self, uid: str, order: dict, email: str, body: str = "") -> None:
        params = order["params"]
        token = params.get("pdf_token")
        if not token:
            token = secrets.token_hex(4)
            self.db.update_order(order["id"], params={**params, "pdf_token": token})
        path = self.outbox / f"{order['product']}_{order['id']}_{token}.pdf"
        if order["product"] == "personal":
            pdfgen.build_personal(date.fromisoformat(params["birth"]), path)
            subject = "Твой личный расклад"
        else:
            pdfgen.build_compat(params["ctype"], date.fromisoformat(params["birth"]),
                                date.fromisoformat(params["birth2"]), path)
            subject = "Результат совместимости"
        await self.mailer.send_pdf(uid, email, f"{cfg.BOT_NAME}: {subject}", path, body)
