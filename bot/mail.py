"""Отправка писем. В прототипе письмо не уходит наружу: PDF кладётся в data/outbox
и записывается в таблицу emails (видно в панели разработчика). Для боевого режима
здесь будет SMTP или почтовый сервис с тем же интерфейсом."""
from __future__ import annotations

from pathlib import Path

from .db import DB


class LocalMailer:
    def __init__(self, db: DB):
        self.db = db

    async def send_pdf(self, uid: str, to_addr: str, subject: str, pdf_path: Path, body: str = "") -> None:
        self.db.add_email(uid, to_addr, subject, pdf_path.name, body)

    async def send_text(self, uid: str, to_addr: str, subject: str, body: str) -> None:
        self.db.add_email(uid, to_addr, subject, "", body)
