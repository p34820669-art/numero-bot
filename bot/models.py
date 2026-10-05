"""Сообщения бота. Интерфейс-независимые: веб-чат и Telegram получают одно и то же."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class Button:
    text: str
    kind: str = "cb"            # cb | url | pay | share
    data: str = ""              # cb: callback id, pay: order id, share: текст для пересылки
    url: str = ""               # url-кнопка
    amount: int = 0             # pay-кнопка, рубли

    def to_dict(self) -> dict:
        return {"text": self.text, "kind": self.kind, "data": self.data, "url": self.url, "amount": self.amount}


def cb(text: str, data: str) -> Button:
    return Button(text, "cb", data=data)


def url(text: str, link: str) -> Button:
    return Button(text, "url", url=link)


def pay(text: str, order_id: int, amount: int) -> Button:
    return Button(text, "pay", data=str(order_id), amount=amount)


def share(text: str, share_text: str) -> Button:
    return Button(text, "share", data=share_text)


@dataclass
class Message:
    text: str = ""                                  # Telegram-HTML: только <b> и <i>
    buttons: list[list[Button]] = field(default_factory=list)
    image: Optional[str] = None                     # ключ картинки, например "loader"

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "image": self.image,
            "buttons": [[b.to_dict() for b in row] for row in self.buttons],
        }


def rows(*buttons: Button) -> list[list[Button]]:
    """Каждая кнопка на своей строке."""
    return [[b] for b in buttons]


class Sender(Protocol):
    """Транспорт. Для веба и Telegram пишется отдельная реализация."""

    async def send(self, uid: str, msg: Message) -> Optional[int]:
        """Отправляет сообщение и возвращает его id (числа растут по порядку отправки)."""

    async def truncate(self, uid: str, after_id: int) -> None:
        """Стирает из чата все сообщения бота и пользователя с id больше after_id."""
