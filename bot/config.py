"""Настройки прототипа. Всё, что хочется крутить при тестах, берётся из переменных окружения."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("NUMERO_DATA_DIR", ROOT / "data"))
OUTBOX_DIR = DATA_DIR / "outbox"
DB_PATH = DATA_DIR / "numero.sqlite3"
CONTENT_DIR = ROOT / "content"

# Сколько секунд на самом деле ждём после сообщения «3 минуты, готовлю информацию».
# Для боевого бота поставить 180.
LOADING_SECONDS = float(os.getenv("NUMERO_LOADING_SECONDS", "5"))
# Пауза между сообщениями бота, чтобы чат читался как живой диалог.
MESSAGE_GAP_SECONDS = float(os.getenv("NUMERO_MESSAGE_GAP", "0.7"))

BOT_NAME = "Базовый код человека"
CHANNEL_URL = os.getenv("NUMERO_CHANNEL_URL", "https://t.me/your_channel_here")  # заглушка
BOT_LINK = os.getenv("NUMERO_BOT_LINK", "https://t.me/your_bot_here")  # заглушка
SUPPORT_URL = os.getenv("NUMERO_SUPPORT_URL", "https://t.me/your_support_here")  # заглушка
# Ссылка на подарок. В Telegram будет вида https://t.me/<бот>?start=gift_{code}
_BASE_URL = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:8765").rstrip("/")  # RENDER_EXTERNAL_URL ставит Render
GIFT_LINK = os.getenv("NUMERO_GIFT_LINK", _BASE_URL + "/?gift={code}")

# Цены-заглушки, рубли. Оплата в прототипе имитируется.
PRICES = {
    "personal": 399,
    "compat": 299,
    "sub_month": 499,
    "msub": 699,
    "month": 299,
    "gift_day": 99,  # подарок «расклад на день»; остальные подарки по цене самого продукта
}
SUB_DAYS = 30
SUBM_MONTHS = 3  # на сколько месяцев вперёд покупается подписка на ежемесячный расклад

MIN_BIRTH_YEAR = 1900
DEFAULT_TZ_MINUTES = 180  # Москва, пока пользователь не выбрал свой пояс

TZ_OPTIONS = [
    (120, "UTC+2 Калининград, Киев, Кишинёв"),
    (180, "UTC+3 Москва, Минск"),
    (240, "UTC+4 Самара, Ереван, Баку, Тбилиси"),
    (300, "UTC+5 Екатеринбург, Ташкент, Алматы"),
    (360, "UTC+6 Омск, Бишкек"),
    (420, "UTC+7 Новосибирск, Красноярск"),
    (480, "UTC+8 Иркутск"),
    (540, "UTC+9 Якутск"),
    (600, "UTC+10 Владивосток"),
    (660, "UTC+11 Магадан"),
    (720, "UTC+12 Камчатка"),
]
