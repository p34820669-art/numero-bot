"""Сквозные проверки сценария по майнд-карте. Сеть и браузер не нужны."""
import asyncio
from datetime import timedelta

import pytest

from bot import config as cfg
from bot.db import DB
from bot.flow import Bot
from bot.models import Message


class FakeSender:
    def __init__(self):
        self.msgs: list[Message] = []
        self.ids: list[int] = []
        self._next = 0

    async def send(self, uid, msg):
        self._next += 1
        self.msgs.append(msg)
        self.ids.append(self._next)
        return self._next

    async def truncate(self, uid, after_id):
        keep = [(i, m) for i, m in zip(self.ids, self.msgs) if i <= after_id]
        self.ids = [i for i, _ in keep]
        self.msgs[:] = [m for _, m in keep]


class Chat:
    """Мини-клиент: нажимает кнопки по тексту и пишет текст, как пользователь."""

    def __init__(self, tmp_path):
        self.db = DB(":memory:")
        self.sender = FakeSender()
        self.bot = Bot(self.db, self.sender, loading_seconds=0, gap=0, outbox=tmp_path)
        self.uid = "u1"

    async def start(self):
        await self.bot.handle(self.uid, "start")

    async def say(self, text):
        await self.bot.handle(self.uid, "text", text)

    def _find(self, label):
        for m in reversed(self.sender.msgs):
            for row in m.buttons:
                for b in row:
                    if label in b.text:
                        return b
        raise AssertionError(f"нет кнопки «{label}». Последние: {[m.text[:40] for m in self.sender.msgs[-3:]]}")

    async def press(self, label):
        b = self._find(label)
        if b.kind == "pay":
            return await self.bot.payment_result(self.uid, int(b.data), True)
        assert b.kind in ("cb",), f"кнопка «{label}» не callback: {b.kind}"
        await self.bot.handle(self.uid, "cb", b.data)

    async def cancel_pay(self, label):
        b = self._find(label)
        await self.bot.payment_result(self.uid, int(b.data), False)

    @property
    def last(self):
        return self.sender.msgs[-1]

    def texts(self):
        return "\n".join(m.text for m in self.sender.msgs)

    def clear(self):
        self.sender.msgs.clear()


@pytest.fixture
def chat(tmp_path):
    return Chat(tmp_path)


MENU_LABELS = ("Расклад на день", "Расклад личный", "Проверить совместимость",
               "Расклад на месяц", "Расклад на год", "Подарить другу", "Подписаться на канал", "Техподдержка")


def assert_full_menu(c: Chat):
    """Под последним сообщением покупки должны стоять все кнопки главного меню."""
    labels = [b.text for row in c.last.buttons for b in row]
    for expected in MENU_LABELS:
        assert any(expected in t for t in labels), f"нет кнопки «{expected}» в {labels}"


def labels_of(c: Chat) -> list[str]:
    return [b.text for r in c.last.buttons for b in r]


def assert_compat_end(c: Chat, pdf: bool, read_here: bool = False, upsell: str = "Личный расклад",
                      send: bool = True):
    """Конец воронки совместимости: только короткий набор кнопок, без полного меню."""
    got = labels_of(c)
    expected = ((["прочитать здесь"] if read_here else []) + (["Отправить тому"] if send else [])
                + (["Получить в PDF"] if pdf else []) + [upsell, "Подписаться на канал", "главное меню"])
    assert len(got) == len(expected), got
    for exp, label in zip(expected, got):
        assert exp in label, f"{exp!r} не в {label!r}; все: {got}"


async def read_blocks(c: Chat, first: int):
    """Читает личный расклад по кнопке «Далее», начиная с блока first. Блок i+1 приходит только после нажатия."""
    for i in range(first, 8):
        assert any(f"{i}/8" in m.text for m in c.sender.msgs), f"нет блока {i}"
        assert not any(f"{i + 1}/8" in m.text for m in c.sender.msgs), f"блок {i + 1} пришёл без «Далее»"
        assert [b.text for r in c.last.buttons for b in r] == ["Далее ▶"], f"под блоком {i} должна быть только «Далее»"
        await c.press("Далее")
    assert any("8/8" in m.text for m in c.sender.msgs)


async def onboard(c: Chat):
    await c.start()


@pytest.mark.asyncio
async def test_start_shows_intro_then_menu_without_consent(chat):
    await chat.start()
    assert "развлекательное" in chat.sender.msgs[0].text
    assert "На чём всё построено" in chat.sender.msgs[1].text
    all_text = chat.texts().lower() + " ".join(b.text.lower() for m in chat.sender.msgs for r in m.buttons for b in r)
    assert "оферт" not in all_text and "конфиденциальн" not in all_text and "18" not in all_text
    labels = [b.text for row in chat.last.buttons for b in row]
    for expected in ("Расклад на день", "Расклад личный", "Проверить совместимость",
                     "Расклад на месяц", "Расклад на год", "Подарить другу", "канал", "Техподдержка"):
        assert any(expected in t for t in labels), f"нет кнопки {expected}"
    assert not any("Подписка" in t for t in labels), f"подписок в главном меню быть не должно: {labels}"
    assert len(labels) == 8
    order = [next(i for i, t in enumerate(labels) if name in t)
             for name in ("Расклад на день", "Расклад на месяц", "Расклад на год")]
    assert order == sorted(order) and order[2] - order[0] == 2, "день, месяц, год идут подряд в этом порядке"
    assert chat.db.user(chat.uid)["anchor"] == chat.sender.ids[1], "якорь стартового экрана сохраняется сразу"
    # бот работает сразу, без согласия
    await chat.press("Расклад на день")
    assert "ДД.ММ.ГГГГ" in chat.last.text


@pytest.mark.asyncio
async def test_day_flow_with_bad_dates_and_timezone(chat):
    await onboard(chat)
    await chat.press("Расклад на день")
    assert "ДД.ММ.ГГГГ" in chat.last.text
    await chat.say("31.02.1990")
    assert "не существует" in chat.last.text and "Что считается неверной датой" in chat.last.text
    await chat.say("14.03.2090")
    assert "ещё не наступила" in chat.last.text
    await chat.say("14.03.1990")
    assert "14 марта 1990" in chat.last.text
    await chat.press("Нет, ввести заново")
    assert "Что считается неверной датой" in chat.last.text
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    assert "Давай сверим часовые пояса" in chat.last.text
    await chat.press("Нет")
    assert "часовой пояс" in chat.last.text
    await chat.press("UTC+10")
    assert "Давай сверим часовые пояса" in chat.last.text
    await chat.press("Да")
    assert any(m.image == "loader" for m in chat.sender.msgs)
    assert "Твой расклад на" in chat.last.text
    labels = [b.text for row in chat.last.buttons for b in row]
    assert len(labels) == 6
    assert "Подписка на ежедневную рассылку" in labels[0]
    assert "главное меню" in labels[1]
    assert "Расклад личный" in labels[2]
    assert "Годовой" in labels[3]
    assert "канал" in labels[4]
    assert "совместимость" in labels[5]


@pytest.mark.asyncio
async def test_day_flow_accepts_typed_yes(chat):
    await onboard(chat)
    await chat.press("Расклад на день")
    await chat.say("14.03.1990")
    await chat.say("да")
    assert "Давай сверим часовые пояса" in chat.last.text
    await chat.say("может быть")
    assert "Да" in chat.last.text and "Нет" in chat.last.text
    await chat.say("да")
    assert "Твой расклад на" in chat.last.text


@pytest.mark.asyncio
async def test_personal_here_teaser_then_pay_then_pdf(chat):
    await onboard(chat)
    await chat.press("Расклад личный")
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    assert "прочитать всё тут или прислать PDF" in chat.last.text
    await chat.press("Тут")
    texts = [m.text for m in chat.sender.msgs]
    assert any("1/8" in t for t in texts)
    assert not any("2/8" in t for t in texts), "до оплаты должен быть только 1 блок"
    assert "Оплатить" in "".join(b.text for row in chat.last.buttons for b in row)
    await chat.cancel_pay("Оплатить")
    assert "Оплата отменена" in chat.last.text
    assert not any("2/8" in m.text for m in chat.sender.msgs)
    await chat.press("Оплатить")
    assert any("2/8" in m.text for m in chat.sender.msgs)
    assert not any("3/8" in m.text for m in chat.sender.msgs), "третий блок только по «Далее»"
    await read_blocks(chat, 2)
    assert "PDF" in chat.last.text
    assert_full_menu(chat)
    assert any("Прислать PDF" in b.text for row in chat.last.buttons for b in row)
    await chat.press("Прислать PDF на почту")
    await chat.say("не почта")
    assert "не похоже на адрес" in chat.last.text
    await chat.say("alan@example.com")
    assert "alan@example.com" in chat.last.text
    await chat.press("Нет, ввести заново")
    await chat.say("alan@example.com")
    await chat.press("Да")
    assert "проверь папку «Спам»" in chat.last.text
    assert_full_menu(chat)
    sent = chat.db.emails(chat.uid)
    assert len(sent) == 1 and sent[0]["to_addr"] == "alan@example.com"


@pytest.mark.asyncio
async def test_personal_pdf_first_then_read_here(chat, tmp_path):
    await onboard(chat)
    await chat.press("Расклад личный")
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    await chat.press("PDF на почту")
    assert "почту" in chat.last.text
    await chat.say("alan@example.com")
    await chat.press("Да")
    assert "Оплатить" in "".join(b.text for row in chat.last.buttons for b in row)
    assert chat.db.emails(chat.uid) == []
    await chat.press("Оплатить")
    assert "Заходи на почту" in chat.last.text
    assert_full_menu(chat)
    assert any("прочитать здесь" in b.text for row in chat.last.buttons for b in row)
    pdfs = list(tmp_path.glob("personal_*.pdf"))
    assert len(pdfs) == 1 and pdfs[0].stat().st_size > 2000
    assert pdfs[0].read_bytes()[:4] == b"%PDF"
    await chat.press("прочитать здесь")
    await read_blocks(chat, 1)
    assert "PDF" in chat.last.text and any("Прислать PDF" in b.text for r in chat.last.buttons for b in r)


@pytest.mark.asyncio
async def test_compat_flow_here_and_pdf(chat, tmp_path):
    await onboard(chat)
    await chat.press("Проверить совместимость")
    await chat.press("Любовь")
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    assert "второго человека" in chat.last.text
    await chat.say("02.09.1985")
    await chat.press("Да, верно")
    await chat.press("Тут")
    assert "Совместимость" not in chat.last.text, "результат только после оплаты"
    await chat.press("Оплатить")
    assert "Совместимость: Любовь" in chat.last.text
    labels = [b for row in chat.last.buttons for b in row]
    assert any(b.kind == "cb" and "Отправить тому" in b.text for b in labels)
    assert_compat_end(chat, pdf=True)
    # после оплаты можно отправить результат на почту
    await chat.press("Получить в PDF")
    await chat.say("friend@example.com")
    await chat.press("Да")
    assert "Смотри на почте" in chat.last.text
    assert_compat_end(chat, pdf=False)
    assert list(tmp_path.glob("compat_*.pdf"))
    # PDF-ветка с оплатой: новая проверка, сначала почта, потом оплата
    await chat.press("В главное меню")
    await chat.press("Проверить совместимость")
    await chat.press("Работа")
    await chat.press("Да")  # дата уже известна
    await chat.say("02.09.1985")
    await chat.press("Да, верно")
    await chat.press("PDF на почту")
    await chat.say("friend@example.com")
    await chat.press("Да")
    assert len(chat.db.emails(chat.uid)) == 1
    await chat.press("Оплатить")
    assert "Заходи на почту" in chat.last.text
    assert_compat_end(chat, pdf=False, read_here=True)
    assert len(chat.db.emails(chat.uid)) == 2
    await chat.press("прочитать здесь")
    assert "Совместимость: Работа" in chat.last.text
    assert_compat_end(chat, pdf=True)


@pytest.mark.asyncio
async def test_subscription_flow_and_daily_delivery(chat):
    await onboard(chat)
    await chat.bot.handle(chat.uid, "cb", "sub")
    assert "покажу тебе" in chat.sender.msgs[-2].text or "покажу тебе" in chat.texts()
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    await chat.press("Да")  # часовой пояс
    assert "Твой расклад на" in chat.texts()
    # согласие: одна кнопка, без кнопок главного меню, времени пока не спрашиваем
    assert "Вот теперь ты понимаешь" in chat.last.text and "В какое время" not in chat.last.text
    assert labels_of(chat) == ["✅ Хочу подписаться"]
    await chat.say("07:00")  # время до согласия не принимаем
    assert "кнопкой" in chat.last.text and not chat.db.active_subs()
    await chat.press("Хочу подписаться")
    assert "В какое время" in chat.last.text and "формате ЧЧ:ММ" in chat.last.text and "07:00" in chat.last.text
    assert chat.last.buttons == []
    await chat.say("плохое время")
    assert "Нужен формат ЧЧ:ММ" in chat.last.text and "от 00 до 23" in chat.last.text
    await chat.say("25:00")
    assert "Нужен формат ЧЧ:ММ" in chat.last.text
    await chat.say("7 утра")
    assert "07:00" in chat.last.text
    await chat.press("Подтверждаю")
    await chat.press("Оплатить")
    assert "завтра в 07:00" in chat.last.text.lower().replace("завтра в", "завтра в")
    subs = chat.db.active_subs()
    assert len(subs) == 1 and subs[0]["send_time"] == "07:00"
    # после оплаты: следующий непокупенный продукт (личный расклад), канал и главное меню
    labels = labels_of(chat)
    assert len(labels) == 3, labels
    assert "Личный расклад" in labels[0] and "Подписаться на канал" in labels[1] and "главное меню" in labels[2]

    chat.clear()
    await chat.bot.tick()
    assert chat.sender.msgs == [], "в день подписки рассылки быть не должно"
    chat.db.shift_time(1)
    await chat.bot.tick()
    assert len(chat.sender.msgs) == 1 and "Твой расклад на" in chat.sender.msgs[0].text
    await chat.bot.tick()
    assert len(chat.sender.msgs) == 1, "второй раз за тот же день не шлём"
    chat.db.shift_time(1)
    await chat.bot.tick()
    assert len(chat.sender.msgs) == 2
    chat.db.shift_time(40)
    await chat.bot.tick()
    assert chat.db.active_subs() == [], "после 30 дней подписка закрывается"


@pytest.mark.asyncio
async def test_stubs_and_menu_and_stale(chat):
    await onboard(chat)
    await chat.press("Расклад на год")
    assert "ещё готовится" in chat.last.text
    await chat.press("В главное меню")
    assert "Что смотрим" in chat.last.text
    # устаревшая контекстная кнопка не ломает бот
    await chat.bot.handle(chat.uid, "cb", "date_ok")
    assert "неактуально" in chat.last.text
    await chat.say("привет")
    assert any("Расклад на день" in b.text for row in chat.last.buttons for b in row)


@pytest.mark.asyncio
async def test_returning_user_confirms_stored_date(chat):
    await onboard(chat)
    await chat.press("Расклад на день")
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    await chat.press("Да")
    await chat.press("В главное меню")
    await chat.press("Расклад личный")
    assert "14 марта 1990" in chat.last.text and "Верно?" in chat.last.text
    await chat.press("Нет, другая")
    assert "Введи" in chat.sender.msgs[-1].text or "введи" in chat.last.text


@pytest.mark.asyncio
async def test_main_menu_button_wipes_chat_back_to_start_screen(chat):
    await onboard(chat)
    start_texts = [m.text for m in chat.sender.msgs]
    assert len(start_texts) == 3 and "На чём всё построено" in start_texts[1]

    # из заглушки
    await chat.press("Расклад на год")
    assert len(chat.sender.msgs) == 4
    await chat.press("В главное меню")
    assert [m.text[:20] for m in chat.sender.msgs[:2]] == [t[:20] for t in start_texts[:2]]
    assert len(chat.sender.msgs) == 3 and chat.last.text.startswith("Что смотрим")
    assert_full_menu(chat)

    # из расклада на день (длинный сценарий), из отменённой оплаты, из устаревшей кнопки
    await chat.press("Расклад на день")
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    await chat.press("Да")
    assert len(chat.sender.msgs) > 5
    await chat.press("В главное меню")
    assert len(chat.sender.msgs) == 3 and "На чём всё построено" in chat.sender.msgs[1].text

    await chat.press("Расклад личный")
    await chat.press("Да")  # дата уже известна
    await chat.press("Тут")
    await chat.cancel_pay("Оплатить")
    assert "Оплата отменена" in chat.last.text
    await chat.press("В главное меню")
    assert len(chat.sender.msgs) == 3

    await chat.bot.handle(chat.uid, "cb", "date_ok")
    assert "неактуально" in chat.last.text
    await chat.press("В главное меню")
    assert len(chat.sender.msgs) == 3

    # после оплаты полного расклада кнопки «В главное меню» нет: прочитанное не стирается
    await chat.press("Расклад личный")
    await chat.press("Да")
    await chat.press("Тут")
    await chat.press("Оплатить")
    await read_blocks(chat, 2)
    assert len(chat.sender.msgs) > 10
    assert not any("В главное меню" in b.text for r in chat.last.buttons for b in r)


@pytest.mark.asyncio
async def test_start_command_resets_chat_and_menu_command_goes_home(chat):
    await onboard(chat)
    await chat.press("Расклад на год")
    await chat.start()
    assert len(chat.sender.msgs) == 3, "повторный /start не копит вводные сообщения"
    await chat.press("Расклад на год")
    await chat.say("/menu")
    assert len(chat.sender.msgs) == 3 and chat.last.text.startswith("Что смотрим")


@pytest.mark.asyncio
async def test_next_button_rejects_unpaid_foreign_or_out_of_range(chat):
    await onboard(chat)
    await chat.press("Расклад личный")
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    await chat.press("Тут")  # заказ создан, но не оплачен
    oid = chat.db.orders(chat.uid)[0]["id"]
    await chat.bot.handle(chat.uid, "cb", f"next:{oid}:2")
    assert "неактуально" in chat.last.text and not any("2/8" in m.text for m in chat.sender.msgs)
    await chat.press("Оплатить")
    await chat.bot.handle(chat.uid, "cb", f"next:{oid}:9")
    assert "неактуально" in chat.last.text
    await chat.bot.handle("other-user", "cb", f"next:{oid}:3")
    assert "неактуально" in chat.last.text


async def run_compat_to_result(c: Chat):
    """Совместимость «Тут» с оплатой; дата пользователя уже известна."""
    c.db.update_user(c.uid, birthdate="1990-03-14")
    await c.press("Проверить совместимость")
    await c.press("Любовь")
    await c.press("Да")
    await c.say("02.09.1985")
    await c.press("Да, верно")
    await c.press("Тут")
    await c.press("Оплатить")
    assert "Совместимость: Любовь" in c.last.text


@pytest.mark.asyncio
async def test_compat_end_offers_next_unbought_product_by_priority(chat):
    await onboard(chat)

    # ничего не куплено: личный расклад
    await run_compat_to_result(chat)
    assert_compat_end(chat, pdf=True, upsell="Личный расклад")

    # купил личный расклад: вместо него ежедневная подписка
    oid = chat.db.create_order(chat.uid, "personal", 399, {})
    chat.db.update_order(oid, status="paid")
    await chat.press("В главное меню")
    await run_compat_to_result(chat)
    assert_compat_end(chat, pdf=True, upsell="ежедневный расклад")

    # купил и подписку: расклад на год
    chat.db.create_sub(chat.uid, "1990-03-14", 180, "07:00", "2026-10-04", "2026-11-03")
    await chat.press("В главное меню")
    await run_compat_to_result(chat)
    assert_compat_end(chat, pdf=True, upsell="Расклад на год")

    # год куплен: расклад на месяц
    oid = chat.db.create_order(chat.uid, "year", 0, {})
    chat.db.update_order(oid, status="paid")
    await chat.press("В главное меню")
    await run_compat_to_result(chat)
    assert_compat_end(chat, pdf=True, upsell="Расклад на месяц")

    # куплено всё: подарить другу, и это же на экране после отправки PDF
    oid = chat.db.create_order(chat.uid, "month", 0, {})
    chat.db.update_order(oid, status="paid")
    await chat.press("В главное меню")
    await run_compat_to_result(chat)
    assert_compat_end(chat, pdf=True, upsell="Подарить другу")
    await chat.press("Получить в PDF")
    await chat.say("friend@example.com")
    await chat.press("Да")
    assert_compat_end(chat, pdf=False, upsell="Подарить другу")


@pytest.mark.asyncio
async def test_unpaid_order_and_expired_subscription_do_not_count_as_bought(chat):
    await onboard(chat)
    chat.db.create_order(chat.uid, "personal", 399, {})  # не оплачен
    await run_compat_to_result(chat)
    assert_compat_end(chat, pdf=True, upsell="Личный расклад")

    oid = chat.db.create_order(chat.uid, "personal", 399, {})
    chat.db.update_order(oid, status="paid")
    sid = chat.db.create_sub(chat.uid, "1990-03-14", 180, "07:00", "2026-08-01", "2026-08-31")
    chat.db.update_sub(sid, active=0)  # подписка закончилась
    await chat.press("В главное меню")
    await run_compat_to_result(chat)
    assert_compat_end(chat, pdf=True, upsell="ежедневный расклад")


async def compat_result_chat(c: Chat):
    await onboard(c)
    await run_compat_to_result(c)


@pytest.mark.asyncio
async def test_send_to_friend_offers_telegram_or_email(chat, tmp_path):
    await compat_result_chat(chat)
    await chat.press("Отправить тому")
    assert "нет Telegram" in chat.last.text
    btns = [b for r in chat.last.buttons for b in r]
    tg = next(b for b in btns if b.kind == "share")
    assert "Проверь свою" in tg.data and "Telegram" in tg.text
    assert any(b.kind == "cb" and "нет Telegram" in b.text for b in btns)
    assert any("главное меню" in b.text for b in btns)

    await chat.press("нет Telegram")
    assert "почту" in chat.last.text
    await chat.say("не почта")
    assert "не похоже на адрес" in chat.last.text
    await chat.say("friend@example.com")
    assert "friend@example.com" in chat.last.text
    await chat.press("Нет, ввести заново")
    assert "почту того" in chat.last.text, "при ошибке снова просим почту друга, а не свою"
    await chat.say("friend@example.com")
    assert chat.db.emails(chat.uid) == []
    await chat.press("Да")

    sent = chat.db.emails(chat.uid)
    assert len(sent) == 1 and sent[0]["to_addr"] == "friend@example.com"
    assert cfg.BOT_LINK in sent[0]["body"] and "PDF" in sent[0]["body"]
    assert list(tmp_path.glob("compat_*.pdf"))
    assert "friend@example.com" in chat.last.text and "Спам" in chat.last.text
    assert_compat_end(chat, pdf=True, send=False)

    # кнопка не возвращается и на следующих экранах этой покупки
    await chat.press("Получить в PDF")
    await chat.say("me@example.com")
    await chat.press("Да")
    assert "Смотри на почте" in chat.last.text
    assert_compat_end(chat, pdf=False, send=False)
    assert len(chat.db.emails(chat.uid)) == 2


@pytest.mark.asyncio
async def test_send_to_friend_rejects_unpaid_or_foreign_order(chat):
    await onboard(chat)
    chat.db.update_user(chat.uid, birthdate="1990-03-14")
    await chat.press("Проверить совместимость")
    await chat.press("Любовь")
    await chat.press("Да")
    await chat.say("02.09.1985")
    await chat.press("Да, верно")
    await chat.press("Тут")  # заказ создан, но не оплачен
    oid = chat.db.orders(chat.uid)[0]["id"]
    for data in (f"sendto:{oid}", f"sendmail:{oid}"):
        await chat.bot.handle(chat.uid, "cb", data)
        assert "неактуально" in chat.last.text
    await chat.press("Оплатить")
    await chat.bot.handle("someone-else", "cb", f"sendmail:{oid}")
    assert "неактуально" in chat.last.text


@pytest.mark.asyncio
async def test_subscription_agree_accepts_typed_yes_and_rejects_stale_button(chat):
    await onboard(chat)
    await chat.bot.handle(chat.uid, "cb", "sub_agree")  # вне сценария
    assert "неактуально" in chat.last.text
    await chat.bot.handle(chat.uid, "cb", "sub")
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    await chat.press("Да")
    await chat.say("да")
    assert "формате ЧЧ:ММ" in chat.last.text
    await chat.say("19:30")
    assert "19:30" in chat.last.text
    await chat.press("Другое время")
    assert "формате ЧЧ:ММ" in chat.last.text


@pytest.mark.asyncio
async def test_after_subscription_payment_offer_skips_already_bought_personal(chat):
    await onboard(chat)
    oid = chat.db.create_order(chat.uid, "personal", 399, {})
    chat.db.update_order(oid, status="paid")
    await chat.bot.handle(chat.uid, "cb", "sub")
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    await chat.press("Да")
    await chat.press("Хочу подписаться")
    await chat.say("07:00")
    await chat.press("Подтверждаю")
    await chat.press("Оплатить")
    labels = labels_of(chat)
    assert len(labels) == 3, labels
    assert "Расклад на год" in labels[0] and "Подписаться на канал" in labels[1] and "главное меню" in labels[2]


@pytest.mark.asyncio
async def test_main_menu_has_support_link_button_last(chat):
    await chat.start()
    btns = [b for r in chat.last.buttons for b in r]
    support = btns[-1]
    assert support.kind == "url" and "Техподдержка" in support.text
    assert support.url == cfg.SUPPORT_URL


def _start_sub(chat, send_time="00:00"):
    """Подписка, оформленная сегодня: 30 дней, расчёт приходит сразу как наступил день."""
    start = chat.bot._local_now(chat.uid).date()
    from datetime import timedelta as td
    return chat.db.create_sub(chat.uid, "1990-03-14", 180, send_time, start.isoformat(),
                              (start + td(days=cfg.SUB_DAYS)).isoformat())


@pytest.mark.asyncio
async def test_subscription_reminder_a_day_before_end_and_renewal_extends(chat):
    await onboard(chat)
    sid = _start_sub(chat)
    price = cfg.PRICES["sub_month"]

    chat.clear()
    chat.db.shift_time(28)  # за два дня до конца: только расчёт
    await chat.bot.tick()
    assert len(chat.sender.msgs) == 1 and "Твой расклад на" in chat.sender.msgs[0].text

    chat.db.shift_time(1)  # за сутки до последнего расчёта: расчёт и напоминание
    await chat.bot.tick()
    assert len(chat.sender.msgs) == 3
    remind = chat.last
    assert "Завтра последний расчёт" in remind.text and f"{price} ₽" in remind.text
    assert [b.kind for r in remind.buttons for b in r] == ["pay"]
    await chat.bot.tick()
    assert len(chat.sender.msgs) == 3, "второй раз за день ничего не шлём"

    chat.db.shift_time(1)  # последний день: расчёт есть, повторного напоминания нет
    await chat.bot.tick()
    assert len(chat.sender.msgs) == 4 and "Твой расклад на" in chat.last.text

    end_before = chat.db.subs(chat.uid)[0]["end_date"]
    await chat.press("Продлить")
    assert "Подписка продлена" in chat.last.text
    sub = chat.db.subs(chat.uid)[0]
    assert sub["id"] == sid and sub["active"] and sub["reminded"] == 0
    from datetime import date as d, timedelta as td
    assert d.fromisoformat(sub["end_date"]) == d.fromisoformat(end_before) + td(days=cfg.SUB_DAYS)
    assert len(chat.db.active_subs()) == 1

    chat.db.shift_time(1)  # день 31: подписка продолжается, а не закрылась
    await chat.bot.tick()
    assert "Твой расклад на" in chat.last.text and len(chat.db.active_subs()) == 1

    labels = labels_of(chat)
    assert labels == ["🏠 В главное меню"]  # у ежедневного расчёта только кнопка меню


@pytest.mark.asyncio
async def test_reminder_when_time_jumps_to_last_day_says_last_and_not_paid_subscription_ends(chat):
    await onboard(chat)
    _start_sub(chat)
    chat.clear()
    chat.db.shift_time(30)  # перескочили сразу на последний день
    await chat.bot.tick()
    assert len(chat.sender.msgs) == 2
    assert "Это последний расчёт" in chat.last.text and "Завтра" not in chat.last.text
    chat.db.shift_time(1)  # не оплатили: подписка закрывается, расчётов больше нет
    await chat.bot.tick()
    assert chat.db.active_subs() == [] and len(chat.sender.msgs) == 2


@pytest.mark.asyncio
async def test_buying_subscription_again_while_active_extends_it(chat):
    await onboard(chat)
    _start_sub(chat)
    before = chat.db.subs(chat.uid)[0]["end_date"]
    oid = chat.db.create_order(chat.uid, "sub_month", cfg.PRICES["sub_month"],
                               {"birth": "1990-03-14", "tz": 180, "time": "08:30"})
    await chat.bot.payment_result(chat.uid, oid, True)
    subs = chat.db.subs(chat.uid)
    assert len(subs) == 1 and subs[0]["send_time"] == "08:30"
    from datetime import date as d, timedelta as td
    assert d.fromisoformat(subs[0]["end_date"]) == d.fromisoformat(before) + td(days=cfg.SUB_DAYS)
    assert "Подписка продлена" in chat.last.text and "08:30" in chat.last.text


def month_labels(chat: Chat):
    """Подписи кнопок выбора месяца: текущий и следующий."""
    from bot import content as ct
    from bot.flow import add_months
    today = chat.bot._local_now(chat.uid).date()
    cur = today.replace(day=1)
    nxt = add_months(cur, 1)
    return cur, nxt, f"{ct.MONTHS_NOM[cur.month - 1]} {cur.year}", f"{ct.MONTHS_NOM[nxt.month - 1]} {nxt.year}"


async def buy_month_reading(chat: Chat, which: str):
    """Расклад на месяц: выбор месяца, дата, часовой пояс, оплата. which: Текущий | Следующий."""
    await chat.press("Расклад на месяц")
    await chat.press(which)
    if chat.db.user(chat.uid)["birthdate"]:
        await chat.press("Да")  # дата уже известна
    else:
        await chat.say("14.03.1990")
        await chat.press("Да, верно")
    await chat.press("Да")  # часовой пояс
    await chat.press("Оплатить")


async def go_monthly_to_pay(chat: Chat, which: str = "Текущий", time_text: str = "07:00"):
    await buy_month_reading(chat, which)
    await chat.press("Хочу подписаться")
    await chat.say(time_text)
    await chat.press("Подтверждаю")


@pytest.mark.asyncio
async def test_monthly_button_asks_current_or_next_month_without_old_intro(chat):
    await onboard(chat)
    await chat.press("Расклад на месяц")
    assert "На какой месяц" in chat.last.text
    assert "Супер" not in chat.texts() and "покажу" not in chat.texts()
    cur, nxt, cur_name, nxt_name = month_labels(chat)
    labels = labels_of(chat)
    assert len(labels) == 2 and cur_name in labels[0] and "Текущий" in labels[0]
    assert nxt_name in labels[1] and "Следующий" in labels[1]
    await chat.say("14.03.1990")  # без выбора кнопкой дату не принимаем
    assert "кнопкой" in chat.last.text


@pytest.mark.asyncio
async def test_month_reading_is_paid_then_delivered_then_subscription_is_offered(chat):
    from bot import content as ct
    from bot.flow import add_months
    await onboard(chat)
    await chat.press("Расклад на месяц")
    cur, nxt, cur_name, nxt_name = month_labels(chat)
    await chat.press("Следующий")
    assert "Введи свою дату" in chat.last.text
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    await chat.press("Да")
    # перед оплатой расклада нет
    assert f"Расклад на {nxt_name} готов" in chat.last.text and f"{cfg.PRICES['month']} ₽" in chat.last.text
    assert "Число месяца" not in chat.texts() and not any(m.image == "loader" for m in chat.sender.msgs)
    await chat.cancel_pay("Оплатить")
    assert "Число месяца" not in chat.texts()
    await chat.press("Оплатить")

    # приходит расклад на выбранный месяц, дальше предложение подписки
    reading = chat.sender.msgs[-2].text
    assert f"Твой расклад на {nxt_name}" in reading and "Число месяца" in reading
    assert "каждый месяц" in chat.last.text
    labels = labels_of(chat)
    assert "Хочу подписаться" in labels[0] and "Подписаться на канал" in labels[-2] and "главное меню" in labels[-1]
    assert not chat.db.subs(chat.uid), "подписки ещё нет"

    await chat.press("Хочу подписаться")
    assert "формате ЧЧ:ММ" in chat.last.text and chat.last.buttons == []
    await chat.say("7 утра")
    after_next = ct.fmt_date(add_months(nxt, 1))
    assert "1 числа каждого месяца" in chat.last.text and "3 месяца" in chat.last.text
    assert f"начиная с {after_next}" in chat.last.text, "подписка идёт со следующего месяца после купленного"
    await chat.press("Подтверждаю")
    assert after_next in chat.last.text and f"{cfg.PRICES['msub']} ₽" not in chat.last.text
    await chat.press("Оплатить")
    assert "жди свой расклад на месяц" in chat.last.text and after_next in chat.last.text
    sub = chat.db.subs(chat.uid)[0]
    assert sub["kind"] == "monthly" and sub["send_time"] == "07:00"


@pytest.mark.asyncio
async def test_current_month_choice_gives_current_month_and_subscription_starts_next_month(chat):
    from bot import content as ct
    from bot.flow import add_months
    from datetime import date as d, timedelta as td
    await onboard(chat)
    cur, nxt, cur_name, nxt_name = month_labels(chat)
    await go_monthly_to_pay(chat, "Текущий")
    assert any(f"Твой расклад на {cur_name}" in m.text for m in chat.sender.msgs)
    assert f"{ct.fmt_date(nxt)}" in chat.last.text
    await chat.press("Оплатить")
    sub = chat.db.subs(chat.uid)[0]
    assert d.fromisoformat(sub["end_date"]) == add_months(nxt, cfg.SUBM_MONTHS) - td(days=1)

    # доставка: раз в месяц, три раза, потом подписка закрывается
    chat.clear()
    chat.db.shift_time(1)
    await chat.bot.tick()
    assert chat.sender.msgs == [], "в месяц покупки подписка ничего не присылает"
    for n in range(1, cfg.SUBM_MONTHS + 1):
        now = chat.bot._local_now(chat.uid).date()
        chat.db.shift_time((add_months(now, 1) - now).days)
        await chat.bot.tick()
        await chat.bot.tick()  # повторный тик в том же месяце не шлёт второй раз
        assert len(chat.sender.msgs) == n
        assert "Твой расклад на" in chat.last.text and "Число месяца" in chat.last.text
        assert labels_of(chat) == ["🏠 В главное меню"]
    chat.db.shift_time(31)
    await chat.bot.tick()
    assert chat.db.active_subs() == [] and len(chat.sender.msgs) == cfg.SUBM_MONTHS


@pytest.mark.asyncio
async def test_monthly_and_daily_subscriptions_are_independent(chat):
    from datetime import timedelta as td
    await onboard(chat)
    start = chat.bot._local_now(chat.uid).date()
    chat.db.create_sub(chat.uid, "1990-03-14", 180, "07:00", start.isoformat(),
                       (start + td(days=cfg.SUB_DAYS)).isoformat(), kind="daily")
    await go_monthly_to_pay(chat)
    await chat.press("Оплатить")
    kinds = sorted((s["kind"], s["active"]) for s in chat.db.subs(chat.uid))
    assert kinds == [("daily", 1), ("monthly", 1)], "покупка ежемесячной не закрывает ежедневную"


@pytest.mark.asyncio
async def test_buying_monthly_subscription_again_extends_it(chat):
    from bot.flow import add_months
    from datetime import date as d, timedelta as td
    await onboard(chat)
    await go_monthly_to_pay(chat)
    await chat.press("Оплатить")
    before = d.fromisoformat(chat.db.subs(chat.uid)[0]["end_date"])
    await chat.press("В главное меню")
    await go_monthly_to_pay(chat, "Текущий", "08:30")
    await chat.press("Оплатить")
    subs = chat.db.subs(chat.uid)
    assert len(subs) == 1 and subs[0]["send_time"] == "08:30"
    assert d.fromisoformat(subs[0]["end_date"]) == add_months(before, cfg.SUBM_MONTHS + 1) - td(days=1)
    assert "Подписка продлена" in chat.last.text


@pytest.mark.asyncio
async def test_monthly_subscription_start_rejects_unpaid_or_foreign_order(chat):
    await onboard(chat)
    await chat.press("Расклад на месяц")
    await chat.press("Текущий")
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    await chat.press("Да")  # заказ создан, не оплачен
    oid = chat.db.orders(chat.uid)[0]["id"]
    await chat.bot.handle(chat.uid, "cb", f"msubstart:{oid}")
    assert "неактуально" in chat.last.text
    await chat.press("Оплатить")
    await chat.bot.handle("someone-else", "cb", f"msubstart:{oid}")
    assert "неактуально" in chat.last.text


@pytest.mark.asyncio
async def test_daily_subscription_is_reachable_from_day_result_and_month_from_menu(chat):
    await onboard(chat)
    await chat.press("Расклад на день")
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    await chat.press("Да")
    await chat.press("Подписка на ежедневную рассылку")  # первая кнопка под раскладом на день
    assert "покажу тебе" in chat.texts()
    await chat.press("В главное меню")
    assert not any("Подписка" in b.text for r in chat.last.buttons for b in r)
    await chat.press("Расклад на месяц")
    assert "На какой месяц" in chat.last.text
