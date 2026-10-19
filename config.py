import os

from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY=os.getenv("DEEPSEEK_API_KEY")
REDIS_URL = os.getenv("REDIS_URL")

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

# Бот для управления автоответчиком (токен от @BotFather).
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Кому разрешено управлять ботом. Владелец аккаунта добавляется
# автоматически, здесь можно перечислить дополнительные id через запятую.
_admin_ids_raw = os.getenv("ADMIN_IDS", "")

try:
    ADMIN_IDS = {
        int(admin_id)
        for admin_id in _admin_ids_raw.replace(" ", "").split(",")
        if admin_id
    }
except ValueError:
    raise ValueError(
        f"ADMIN_IDS должен содержать числовые id через запятую: {_admin_ids_raw!r}"
    ) from None
