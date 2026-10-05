"""Разбор того, что пишет пользователь: дата, время, почта, да/нет."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from .config import MIN_BIRTH_YEAR

_DATE_RE = re.compile(r"^\s*(\d{1,2})\s*[./\-\s]\s*(\d{1,2})\s*[./\-\s]\s*(\d{4})\s*$")
_DIGITS8 = re.compile(r"^\s*(\d{2})(\d{2})(\d{4})\s*$")


def parse_date(text: str, today: date) -> tuple[Optional[date], Optional[str]]:
    """Возвращает (дата, None) или (None, код_ошибки): format | not_exist | future | too_old."""
    m = _DATE_RE.match(text) or _DIGITS8.match(text)
    if not m:
        return None, "format"
    d, mo, y = (int(x) for x in m.groups())
    try:
        parsed = date(y, mo, d)
    except ValueError:
        return None, "not_exist"
    if parsed > today:
        return None, "future"
    if y < MIN_BIRTH_YEAR:
        return None, "too_old"
    return parsed, None


_TIME_RE = re.compile(r"(\d{1,2})(?:\s*[:.\-]\s*(\d{2}))?\s*(утра|дня|вечера|ночи)?")


def parse_time(text: str) -> Optional[str]:
    """«7», «7:30», «19.00», «7 утра», «8 вечера» -> «HH:MM»."""
    m = _TIME_RE.search(text.lower())
    if not m:
        return None
    h = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    part = m.group(3)
    if part in ("вечера", "дня") and h < 12:
        h += 12
    elif part == "ночи" and h == 12:
        h = 0
    if h > 23 or minute > 59:
        return None
    return f"{h:02d}:{minute:02d}"


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")


def parse_email(text: str) -> Optional[str]:
    t = text.strip()
    return t if _EMAIL_RE.match(t) else None


_YES = {"да", "верно", "ага", "yes", "y", "ок", "ok", "угу", "конечно"}
_NO = {"нет", "неа", "no", "n", "не"}


def yes_no(text: str) -> Optional[bool]:
    t = text.strip().lower().strip(".!")
    if t in _YES:
        return True
    if t in _NO:
        return False
    return None
