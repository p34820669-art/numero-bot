"""Что нужно для публичного демо: у каждого тестировщика своё время, чужие файлы не угадать."""
import re
from datetime import timedelta

import pytest

from bot import config as cfg
from tests.test_flow import chat, onboard  # noqa: F401  (chat это фикстура)


def make_daily_sub(chat, uid: str):
    chat.db.update_user(uid, consent=1)
    start = chat.bot._local_now(uid).date()
    chat.db.create_sub(uid, "1990-03-14", 180, "00:00", start.isoformat(),
                       (start + timedelta(days=cfg.SUB_DAYS)).isoformat())


@pytest.mark.asyncio
async def test_time_shift_is_per_user(chat):
    make_daily_sub(chat, "alice")
    make_daily_sub(chat, "bob")
    chat.clear()

    chat.db.shift_time(1, "alice")
    assert chat.db.time_shift("alice") == timedelta(days=1)
    assert chat.db.time_shift("bob") == timedelta(0)
    assert chat.bot._local_now("alice").date() != chat.bot._local_now("bob").date()

    await chat.bot.tick()
    assert len(chat.sender.msgs) == 1, "расчёт пришёл только тому, у кого сдвинуто время"
    assert chat.db.subs("alice")[0]["last_sent"] is not None and chat.db.subs("bob")[0]["last_sent"] is None

    chat.db.shift_time(40, "alice")  # у Алисы подписка закончилась, у Боба всё как было
    await chat.bot.tick()
    assert chat.db.subs("alice")[0]["active"] == 0 and chat.db.subs("bob")[0]["active"] == 1


@pytest.mark.asyncio
async def test_pdf_file_names_cannot_be_guessed_by_order_number(chat, tmp_path):
    await onboard(chat)
    await chat.press("Расклад личный")
    await chat.say("14.03.1990")
    await chat.press("Да, верно")
    await chat.press("PDF на почту")
    await chat.say("me@example.com")
    await chat.press("Да")
    await chat.press("Оплатить")
    files = [p.name for p in tmp_path.glob("personal_*.pdf")]
    assert len(files) == 1
    assert re.fullmatch(r"personal_\d+_[0-9a-f]{8}\.pdf", files[0]), files
    assert chat.db.emails(chat.uid)[0]["pdf_file"] == files[0]

    # повторная отправка того же заказа не плодит новые файлы
    await chat.press("прочитать здесь")
    while any("Далее" in b.text for r in chat.last.buttons for b in r):
        await chat.press("Далее")
    await chat.press("Прислать PDF на почту")
    await chat.say("other@example.com")
    await chat.press("Да")
    assert [p.name for p in tmp_path.glob("personal_*.pdf")] == files


def test_gift_link_uses_public_url_when_hosted(monkeypatch):
    import importlib
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://numero-demo.onrender.com/")
    monkeypatch.delenv("NUMERO_GIFT_LINK", raising=False)
    try:
        importlib.reload(cfg)
        assert cfg.GIFT_LINK.format(code="abc") == "https://numero-demo.onrender.com/?gift=abc"
    finally:
        monkeypatch.undo()
        importlib.reload(cfg)
