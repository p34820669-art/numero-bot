from datetime import date

from bot import numerology as nm
from bot import parsing


def test_reduce():
    assert nm.reduce(29) == 2
    assert nm.reduce(29, keep_master=True) == 11
    assert nm.reduce(38) == 2
    assert nm.reduce(22, keep_master=True) == 22
    assert nm.reduce(22) == 4


def test_life_path_known_dates():
    # 14.03.1990: 1+4+0+3+1+9+9+0 = 27 -> 9
    assert nm.profile(date(1990, 3, 14)).life_path == 9
    # 29.11.1992 -> 2+9+1+1+1+9+9+2 = 34 -> 7
    assert nm.profile(date(1992, 11, 29)).life_path == 7
    # 11.09.1990 -> 1+1+0+9+1+9+9+0 = 30 -> 3
    assert nm.profile(date(1990, 9, 11)).life_path == 3
    # мастер-число: 20.03.1985 -> 2+0+0+3+1+9+8+5 = 28 -> 10 -> 1 ; 02.09.1980 -> 2+0+9+1+9+8+0 = 29 -> 11
    assert nm.profile(date(1980, 9, 2)).life_path == 11


def test_profile_numbers_in_range():
    d = date(1900, 1, 1)
    while d.year < 2010:
        p = nm.profile(d)
        for n in (p.day_num, p.month_num, p.year_num, p.challenge, p.money, p.love, p.resource, p.advice):
            assert 1 <= n <= 9
        assert p.life_path in (*range(1, 10), 11, 22, 33)
        d = date(d.year + 1, (d.month % 12) + 1, min(d.day + 3, 28))


def test_personal_day_range_and_determinism():
    b = date(1990, 3, 14)
    for i in range(1, 29):
        n = nm.personal_day(b, date(2026, 10, i))
        assert 1 <= n <= 9
    assert nm.personal_day(b, date(2026, 10, 4)) == nm.personal_day(b, date(2026, 10, 4))


def test_compat_symmetric_and_bounded():
    for a in range(1, 10):
        for b in range(1, 10):
            assert nm.compat_class(a, b) in nm.COMPAT_CLASSES
            assert 50 <= nm.compat_score(a, b) <= 98
    assert nm.compat_class(3, 3) == "mirror"
    assert nm.compat_class(1, 4) == "harmony"


def test_parse_date():
    today = date(2026, 10, 4)
    assert parsing.parse_date("14.03.1990", today) == (date(1990, 3, 14), None)
    assert parsing.parse_date("14/03/1990", today)[0] == date(1990, 3, 14)
    assert parsing.parse_date("14 3 1990", today)[0] == date(1990, 3, 14)
    assert parsing.parse_date("14031990", today)[0] == date(1990, 3, 14)
    assert parsing.parse_date("31.02.1990", today)[1] == "not_exist"
    assert parsing.parse_date("14.03.2030", today)[1] == "future"
    assert parsing.parse_date("14.03.1850", today)[1] == "too_old"
    assert parsing.parse_date("вчера", today)[1] == "format"
    assert parsing.parse_date("14.03.90", today)[1] == "format"


def test_parse_time():
    assert parsing.parse_time("7") == "07:00"
    assert parsing.parse_time("7:30") == "07:30"
    assert parsing.parse_time("в 7 утра") == "07:00"
    assert parsing.parse_time("8 вечера") == "20:00"
    assert parsing.parse_time("19.00") == "19:00"
    assert parsing.parse_time("25:00") is None
    assert parsing.parse_time("утром") is None


def test_parse_email_and_yesno():
    assert parsing.parse_email(" a.b@mail.ru ") == "a.b@mail.ru"
    assert parsing.parse_email("a@b") is None
    assert parsing.parse_email("не почта") is None
    assert parsing.yes_no("Да!") is True
    assert parsing.yes_no("нет") is False
    assert parsing.yes_no("может") is None


def test_personal_month_range_and_content_has_every_number():
    import json
    from bot.config import CONTENT_DIR
    b = date(1990, 3, 14)
    seen = set()
    for m in range(1, 13):
        n = nm.personal_month(b, date(2026, m, 1))
        assert 1 <= n <= 9
        seen.add(n)
    table = json.load(open(CONTENT_DIR / "month.json", encoding="utf-8"))
    for n in range(1, 10):
        assert {"title", "mood", "do", "avoid", "phrase"} <= set(table[str(n)])
