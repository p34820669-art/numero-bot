"""Расчёты по цифрам. ВРЕМЕННАЯ схема: упрощённая нумерология-заглушка.
Когда экспертная методика будет готова, меняется только этот файл и файлы в content/."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

MASTER = (11, 22, 33)


def digit_sum(n: int) -> int:
    return sum(int(c) for c in str(abs(n)))


def reduce(n: int, keep_master: bool = False) -> int:
    """Сводит число к одной цифре 1-9. Мастер-числа 11/22/33 можно сохранить."""
    while n > 9:
        if keep_master and n in MASTER:
            return n
        n = digit_sum(n)
    return n


@dataclass(frozen=True)
class Profile:
    """Набор чисел человека, все блоки личного расклада берут числа отсюда."""
    birth: date
    life_path: int      # число жизненного пути (может быть 11/22/33)
    day_num: int        # день рождения, сведённый до цифры
    month_num: int      # месяц рождения
    year_num: int       # год рождения, сведённый до цифры
    challenge: int      # число роста, 1-9
    money: int
    love: int
    resource: int
    advice: int

    def as_dict(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in self.__dict__.items()}


def profile(birth: date) -> Profile:
    d = reduce(birth.day)
    m = reduce(birth.month)
    y = reduce(digit_sum(birth.year))
    total = digit_sum(birth.day) + digit_sum(birth.month) + digit_sum(birth.year)
    life = reduce(total, keep_master=True)
    life_plain = reduce(life)
    ch = abs(abs(d - m) - abs(d - y))
    ch = reduce(ch) if ch else 9
    return Profile(
        birth=birth,
        life_path=life,
        day_num=d,
        month_num=m,
        year_num=y,
        challenge=ch,
        money=reduce(life_plain + d),
        love=reduce(m + y),
        resource=reduce(life_plain + m),
        advice=reduce(d + y),
    )


def personal_year(birth: date, on: date) -> int:
    return reduce(digit_sum(birth.day) + digit_sum(birth.month) + digit_sum(on.year))


def personal_month(birth: date, on: date) -> int:
    return reduce(personal_year(birth, on) + digit_sum(on.month))


def personal_day(birth: date, on: date) -> int:
    py = personal_year(birth, on)
    return reduce(py + digit_sum(on.month) + digit_sum(on.day))


# ---------- совместимость ----------

COMPAT_CLASSES = ("mirror", "harmony", "neighbors", "complement", "contrast")
_BASE_SCORE = {"mirror": 78, "harmony": 90, "neighbors": 70, "complement": 82, "contrast": 61}


def compat_class(a: int, b: int) -> str:
    a, b = reduce(a), reduce(b)
    d = (a - b) % 9
    if d == 0:
        return "mirror"
    if d in (3, 6):
        return "harmony"
    if d in (1, 8):
        return "neighbors"
    if d in (2, 7):
        return "complement"
    return "contrast"


def compat_score(a: int, b: int) -> int:
    cls = compat_class(a, b)
    bump = (reduce(a) + reduce(b)) % 7  # 0..6, чтобы пары внутри класса отличались
    return min(98, _BASE_SCORE[cls] + bump)
