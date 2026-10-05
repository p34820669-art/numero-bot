"""Сборка текстов раскладов из таблиц в content/*.json."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from . import numerology as nm
from .config import CONTENT_DIR

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def fmt_date(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}"


MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь",
              "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


def fmt_date_short(d: date) -> str:
    return d.strftime("%d.%m.%Y")


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    with open(CONTENT_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def reload() -> None:
    _load.cache_clear()


# Блоки личного расклада: (ключ поля в numbers.json, какое число из профиля берём, заголовок)
PERSONAL_BLOCKS = [
    ("core", "life_path", "Ядро личности"),
    ("strength", "day_num", "Сильные стороны"),
    ("growth", "challenge", "Зоны роста"),
    ("talents", "month_num", "Таланты и призвание"),
    ("money", "money", "Деньги и дела"),
    ("love", "love", "Отношения"),
    ("resource", "resource", "Ресурс и энергия"),
    ("advice", "advice", "Главный совет"),
]


@dataclass
class Block:
    index: int
    total: int
    title: str
    number: int
    archetype: str
    text: str

    def render(self) -> str:
        return (f"<b>{self.index}/{self.total}. {self.title}</b>\n"
                f"Число {self.number}: {self.archetype}\n\n{self.text}")


def _number_entry(n: int, field: str) -> tuple[str, str]:
    """Берём поле для числа; для мастер-чисел без такого поля падаем на сведённое."""
    table = _load("numbers")
    entry = table.get(str(n), {})
    if field not in entry:
        entry_plain = table[str(nm.reduce(n))]
        return entry.get("title", entry_plain["title"]), entry_plain[field]
    return entry.get("title", table[str(nm.reduce(n))]["title"]), entry[field]


def personal_blocks(birth: date) -> list[Block]:
    prof = nm.profile(birth)
    out = []
    for i, (field, attr, title) in enumerate(PERSONAL_BLOCKS, start=1):
        n = getattr(prof, attr)
        archetype, text = _number_entry(n, field)
        out.append(Block(i, len(PERSONAL_BLOCKS), title, n, archetype, text))
    return out


def day_reading(birth: date, on: date) -> str:
    n = nm.personal_day(birth, on)
    d = _load("day")[str(n)]
    return (
        f"☀️ <b>Твой расклад на {fmt_date(on)}</b>\n\n"
        f"Число дня: {n}. {d['title']}\n\n"
        f"{d['mood']}\n\n"
        f"✅ Сегодня: {d['do']}\n"
        f"⚠️ Осторожно: {d['avoid']}\n\n"
        f"💬 <i>{d['phrase']}</i>\n\n"
        f"<i>Развлекательный расклад, не руководство к действию.</i>"
    )


def month_reading(birth: date, on: date) -> str:
    n = nm.personal_month(birth, on)
    m = _load("month")[str(n)]
    return (
        f"🌙 <b>Твой расклад на {MONTHS_NOM[on.month - 1]} {on.year}</b>\n\n"
        f"Число месяца: {n}. {m['title']}\n\n"
        f"{m['mood']}\n\n"
        f"✅ Фокус месяца: {m['do']}\n"
        f"⚠️ Осторожно: {m['avoid']}\n\n"
        f"💬 <i>{m['phrase']}</i>\n\n"
        f"<i>Развлекательный расклад, не руководство к действию.</i>"
    )


@dataclass
class CompatResult:
    ctype: str
    type_title: str
    a: int
    b: int
    cls: str
    cls_title: str
    score: int
    headline: str
    works: str
    friction: str
    tip: str

    def render(self) -> str:
        return (
            f"💞 <b>Совместимость: {self.type_title}</b>\n\n"
            f"Числа пути: {self.a} и {self.b}\n"
            f"Совпадение: {self.score}%\n"
            f"Тип пары: {self.cls_title}\n\n"
            f"<b>{self.headline}</b>\n\n"
            f"✅ Что работает: {self.works}\n\n"
            f"⚠️ Где трение: {self.friction}\n\n"
            f"💡 Совет: {self.tip}\n\n"
            f"<i>Развлекательный расклад, не руководство к действию.</i>"
        )


def compat_result(ctype: str, birth1: date, birth2: date) -> CompatResult:
    data = _load("compat")
    a = nm.profile(birth1).life_path
    b = nm.profile(birth2).life_path
    cls = nm.compat_class(a, b)
    text = data[ctype][cls]
    return CompatResult(
        ctype=ctype, type_title=data["types"][ctype], a=a, b=b, cls=cls,
        cls_title=data["classes"][cls], score=nm.compat_score(a, b),
        headline=text["headline"], works=text["works"], friction=text["friction"], tip=text["tip"],
    )


def compat_type_title(ctype: str) -> str:
    return _load("compat")["types"][ctype]
