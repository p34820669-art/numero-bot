"""Подарок другу: контакт в Telegram или почта, дата рождения, продукт, оплата, подарочная ссылка."""
import pytest

from bot import config as cfg
from bot import content as ct
from bot.flow import add_months
from tests.test_flow import chat, labels_of, month_labels, onboard  # noqa: F401  (chat это фикстура)


def as_user(chat, uid: str):
    """Дальше действуем от имени другого пользователя (в общем списке сообщений остаются только его)."""
    chat.uid = uid
    chat.clear()


@pytest.mark.asyncio
async def test_gift_via_telegram_day_reading_end_to_end(chat):
    await onboard(chat)
    await chat.press("Подарить другу")
    assert "Кому дарим" in chat.last.text
    assert [b.text for r in chat.last.buttons for b in r] == ["📨 Контакт в Telegram", "📧 Указать почту"]
    await chat.press("Контакт в Telegram")
    assert "дату рождения того, кому дарим" in chat.last.text
    await chat.say("31.02.1990")
    assert "не существует" in chat.last.text
    await chat.say("02.09.1985")
    assert "2 сентября 1985" in chat.last.text
    await chat.press("Да, верно")
    assert chat.last.text == "Что дарим?"
    labels = labels_of(chat)
    assert len(labels) == 5
    for expected in ("Расклад на день", "Расклад на месяц", "Расклад на год",
                     "Подписка на ежедневную рассылку", "Подписка на ежемесячный расклад"):
        assert any(expected in t for t in labels), labels

    await chat.press("Расклад на год")  # год пока заглушка: сообщаем и предлагаем выбрать другое
    assert chat.sender.msgs[-2].text.endswith("Выбери другой подарок.") and chat.last.text == "Что дарим?"
    await chat.press("Расклад на день")
    assert f"{cfg.PRICES['gift_day']} ₽" in chat.last.text and "2 сентября 1985" in chat.last.text
    assert chat.db.gifts(chat.uid) == [], "до оплаты подарка нет"
    await chat.cancel_pay("Оплатить")
    assert chat.db.gifts(chat.uid) == []
    await chat.press("Оплатить")

    gift = chat.db.gifts(chat.uid)[0]
    link = cfg.GIFT_LINK.format(code=gift["code"])
    assert link in chat.last.text and gift["status"] == "new"
    share_btn = next(b for r in chat.last.buttons for b in r if b.kind == "share")
    assert link in share_btn.data and "расклад на день" in share_btn.data
    giver = chat.uid

    # друг открывает ссылку: подарок выдаётся, вводных текстов нет
    as_user(chat, "friend")
    await chat.bot.handle("friend", "start", f"gift_{gift['code']}")
    assert "Тебе подарили расклад на день" in chat.texts() and "Привет! Это" not in chat.texts()
    assert "Давай сверим часовые пояса" in chat.last.text
    await chat.press("Да")
    assert "Твой расклад на" in chat.last.text
    stored = chat.db.gift(gift["code"])
    assert stored["status"] == "redeemed" and stored["redeemed_by"] == "friend"

    # повторно ссылка не работает, а бот показывает обычный старт
    await chat.bot.handle("friend", "start", f"gift_{gift['code']}")
    assert "уже использована" in chat.sender.msgs[0].text
    assert "На чём всё построено" in chat.texts() and chat.last.text == "Что смотрим?"
    assert giver != chat.uid


@pytest.mark.asyncio
async def test_gift_by_email_month_reading_sends_letter_with_link(chat):
    await onboard(chat)
    await chat.press("Подарить другу")
    await chat.press("Указать почту")
    assert "электронную почту" in chat.last.text
    await chat.say("не почта")
    assert "не похоже на адрес" in chat.last.text
    await chat.say("friend@example.com")
    await chat.press("Нет, ввести заново")
    assert "электронную почту того, кому дарим" in chat.last.text
    await chat.say("friend@example.com")
    await chat.press("Да")
    assert "дату рождения того, кому дарим" in chat.last.text
    await chat.say("02.09.1985")
    await chat.press("Да, верно")
    await chat.press("Расклад на месяц")
    cur, nxt, cur_name, nxt_name = month_labels(chat)
    assert "На какой месяц" in chat.last.text and nxt_name in labels_of(chat)[1]
    await chat.press("Следующий")
    assert f"расклад на {nxt_name}" in chat.last.text and f"{cfg.PRICES['month']} ₽" in chat.last.text
    await chat.press("Оплатить")

    gift = chat.db.gifts(chat.uid)[0]
    link = cfg.GIFT_LINK.format(code=gift["code"])
    letters = chat.db.emails(chat.uid)
    assert len(letters) == 1 and letters[0]["to_addr"] == "friend@example.com"
    assert link in letters[0]["body"] and letters[0]["pdf_file"] == ""
    assert "friend@example.com" in chat.last.text and "Спам" in chat.last.text
    assert not any(b.kind == "share" for r in chat.last.buttons for b in r), "для почты пересылать нечего"

    as_user(chat, "friend")
    await chat.say(f"/start gift_{gift['code']}")
    assert f"расклад на {nxt_name}" in chat.sender.msgs[0].text
    assert f"Твой расклад на {nxt_name}" in chat.sender.msgs[1].text and "Число месяца" in chat.sender.msgs[1].text
    assert chat.db.gift(gift["code"])["status"] == "redeemed"


@pytest.mark.asyncio
async def test_gift_daily_subscription_activates_for_friend_without_payment(chat):
    await onboard(chat)
    await chat.press("Подарить другу")
    await chat.press("Контакт в Telegram")
    await chat.say("02.09.1985")
    await chat.press("Да, верно")
    await chat.press("Подписка на ежедневную рассылку")
    assert f"{cfg.PRICES['sub_month']} ₽" in chat.last.text
    await chat.press("Оплатить")
    code = chat.db.gifts(chat.uid)[0]["code"]
    assert chat.db.subs(chat.uid) == [], "у дарителя подписка не появляется"

    as_user(chat, "friend")
    await chat.bot.handle("friend", "start", f"gift_{code}")
    assert "подписку на ежедневную рассылку" in chat.sender.msgs[0].text
    await chat.press("Да")  # часовой пояс
    assert "формате ЧЧ:ММ" in chat.last.text
    await chat.say("7 утра")
    assert "Это подарок, платить не нужно" in chat.last.text
    await chat.press("Подтверждаю")
    assert "Завтра в 07:00" in chat.last.text
    subs = chat.db.subs("friend")
    assert len(subs) == 1 and subs[0]["kind"] == "daily" and subs[0]["birthdate"] == "1985-09-02"
    assert chat.db.orders("friend") == [], "получатель ничего не платит"
    assert chat.db.gift(code)["status"] == "redeemed"


@pytest.mark.asyncio
async def test_gift_monthly_subscription_activates_for_friend_from_next_month(chat):
    await onboard(chat)
    await chat.press("Подарить другу")
    await chat.press("Контакт в Telegram")
    await chat.say("02.09.1985")
    await chat.press("Да, верно")
    await chat.press("Подписка на ежемесячный расклад")
    assert f"{cfg.PRICES['msub']} ₽" in chat.last.text
    await chat.press("Оплатить")
    code = chat.db.gifts(chat.uid)[0]["code"]

    as_user(chat, "friend")
    await chat.bot.handle("friend", "start", f"gift_{code}")
    await chat.press("Да")  # часовой пояс
    await chat.say("08:30")
    today = chat.bot._local_now("friend").date()
    first = add_months(today.replace(day=1), 1)
    assert f"начиная с {ct.fmt_date(first)}" in chat.last.text and "Это подарок" in chat.last.text
    await chat.press("Подтверждаю")
    sub = chat.db.subs("friend")[0]
    assert sub["kind"] == "monthly" and sub["send_time"] == "08:30" and sub["birthdate"] == "1985-09-02"
    assert ct.fmt_date(first) in chat.last.text
    assert chat.db.gift(code)["status"] == "redeemed"


@pytest.mark.asyncio
async def test_gift_buttons_are_state_bound_and_unknown_code_is_safe(chat):
    await onboard(chat)
    for data in ("gwho:tg", "gprod:day", "gmonth:cur"):
        await chat.bot.handle(chat.uid, "cb", data)
        assert "неактуально" in chat.last.text
    as_user(chat, "stranger")
    await chat.bot.handle("stranger", "start", "gift_nonexistent")
    assert "уже использована или неверна" in chat.sender.msgs[0].text
    assert chat.last.text == "Что смотрим?"
