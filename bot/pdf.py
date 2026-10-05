"""PDF-версии раскладов (fpdf2). Шрифт с кириллицей ищется в assets/fonts и в системе."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from fpdf import FPDF

from . import content as ct
from . import numerology as nm
from .config import BOT_NAME, ROOT

_FONT_DIRS = [
    ROOT / "assets" / "fonts",
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
]
_FONT_SETS = [  # (regular, bold, italic)
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf"),
    ("arial.ttf", "arialbd.ttf", "ariali.ttf"),
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf"),
    ("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf"),
]

INK = (30, 27, 60)
ACCENT = (96, 62, 190)
MUTED = (120, 120, 140)


def _find_fonts() -> tuple[str, str, str]:
    for reg, bold, ital in _FONT_SETS:
        for d in _FONT_DIRS:
            if (d / reg).exists() and (d / bold).exists():
                i = d / ital
                return str(d / reg), str(d / bold), str(i if i.exists() else d / reg)
    raise RuntimeError("Не найден шрифт с кириллицей. Положите DejaVuSans.ttf, DejaVuSans-Bold.ttf, "
                       "DejaVuSans-Oblique.ttf в assets/fonts/.")


_TAG = re.compile(r"</?[a-z]+>")
_EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u200d]")


def plain(text: str) -> str:
    return _EMOJI.sub("", _TAG.sub("", text)).strip()


class _Doc(FPDF):
    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Main", "I", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 8, f"{BOT_NAME}. Развлекательный материал, 18+. Не является прогнозом или советом. Стр. {self.page_no()}",
                  align="C")


def _new_doc() -> _Doc:
    reg, bold, ital = _find_fonts()
    pdf = _Doc(format="A4")
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font("Main", "", reg)
    pdf.add_font("Main", "B", bold)
    pdf.add_font("Main", "I", ital)
    pdf.add_page()
    return pdf


def _title(pdf: _Doc, title: str, subtitle: str) -> None:
    pdf.set_font("Main", "B", 11)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 7, BOT_NAME.upper(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Main", "B", 24)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 11, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Main", "", 11)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 6, subtitle, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.6)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(6)


def _heading(pdf: _Doc, text: str) -> None:
    pdf.set_font("Main", "B", 14)
    pdf.set_text_color(*ACCENT)
    pdf.multi_cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")


def _body(pdf: _Doc, text: str, italic: bool = False) -> None:
    pdf.set_font("Main", "I" if italic else "", 11)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 6.2, text, new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.ln(2)


def build_personal(birth: date, path: Path) -> Path:
    prof = nm.profile(birth)
    blocks = ct.personal_blocks(birth)
    pdf = _new_doc()
    _title(pdf, "Личный расклад", f"Дата рождения: {ct.fmt_date(birth)}")

    _heading(pdf, "Твои числа")
    _body(pdf, (f"Число жизненного пути: {prof.life_path}    Число дня рождения: {prof.day_num}\n"
                f"Число месяца: {prof.month_num}    Число года: {prof.year_num}"))
    pdf.ln(3)
    for b in blocks:
        _heading(pdf, f"{b.index}. {b.title}")
        pdf.set_font("Main", "I", 10)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 6, f"Число {b.number}: {b.archetype}", new_x="LMARGIN", new_y="NEXT")
        _body(pdf, b.text)
        pdf.ln(2)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return path


def build_compat(ctype: str, birth1: date, birth2: date, path: Path) -> Path:
    r = ct.compat_result(ctype, birth1, birth2)
    pdf = _new_doc()
    _title(pdf, f"Совместимость: {r.type_title}",
           f"{ct.fmt_date(birth1)} и {ct.fmt_date(birth2)}")
    _heading(pdf, f"Совпадение {r.score}%. Тип пары: {r.cls_title}")
    _body(pdf, f"Числа жизненного пути: {r.a} и {r.b}")
    pdf.ln(2)
    _body(pdf, r.headline)
    _heading(pdf, "Что работает")
    _body(pdf, r.works)
    _heading(pdf, "Где трение")
    _body(pdf, r.friction)
    _heading(pdf, "Совет")
    _body(pdf, r.tip)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return path
