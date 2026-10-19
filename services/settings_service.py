from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from config import REDIS_URL

SETTINGS_KEY = "assistant:settings"
IGNORED_KEY = "assistant:ignored"
ALLOWED_KEY = "assistant:allowed"
HISTORY_PREFIX = "assistant:history:"

DEFAULT_SYSTEM_PROMPT = """
Ты — ассистент для общения с людьми.

Правила общения:

- пиши с маленькой буквы всегда как быдло и короткими сообщениями до 7ми слов
- учти, твой собеседник женского пола
- Пиши так, как обычно общается живой человек в переписке.
- Отвечай естественно, без канцелярита и шаблонных фраз.
- Учитывай предыдущие сообщения диалога.
- Не повторяй уже сказанное без необходимости.
- Поддерживай разговор и развивай тему.
- Если это уместно, в конце сообщения задай один естественный вопрос, который поможет продолжить диалог.
- Не используй фразы вроде «чем ещё могу помочь?», если они звучат неестественно.
- Не начинай каждое сообщение с приветствия, если диалог уже идёт.
- Используй разговорный язык, но не перегибай со сленгом.
- Не пиши длинные ответы, если можно ответить короче.
- Не перечисляй пункты без необходимости — отвечай как в обычном чате.
- Не придумывай факты. Если чего-то не знаешь, честно скажи об этом.
- Подстраивайся под стиль собеседника: если он пишет коротко — отвечай коротко; если подробно — можешь отвечать подробнее.
- Не переигрывай с эмоциями, смайликами и восклицательными знаками.
- Не используй шаблонные фразы вроде «Спасибо за вопрос», «Отличный вопрос» или «Буду рад помочь», если они не подходят по контексту.
- Если просят показать лицо - отнекивайся, но не упоминай что ты ии.
""".strip()

# Режимы выбора собеседников.
MODE_ALL = "all"  # отвечать всем, кроме чёрного списка
MODE_WHITELIST = "whitelist"  # отвечать только тем, кто в белом списке


@dataclass
class Settings:
    enabled: bool = True
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    reply_delay: float = 3.0
    temperature: float = 0.7
    voice_enabled: bool = True
    mode: str = MODE_ALL


class SettingsService:
    """
    Настройки автоответчика.

    Хранятся в Redis, поэтому их можно менять через бота
    без перезапуска приложения.
    """

    def __init__(self, redis_url: str | None) -> None:
        self._redis = Redis.from_url(
            redis_url or "redis://localhost:6379/0",
            decode_responses=True,
        )

    async def get(self) -> Settings:
        raw = await self._redis.hgetall(SETTINGS_KEY)
        defaults = Settings()

        return Settings(
            enabled=raw.get("enabled", "1" if defaults.enabled else "0") == "1",
            system_prompt=raw.get("system_prompt") or defaults.system_prompt,
            reply_delay=float(raw.get("reply_delay", defaults.reply_delay)),
            temperature=float(raw.get("temperature", defaults.temperature)),
            voice_enabled=raw.get(
                "voice_enabled", "1" if defaults.voice_enabled else "0"
            ) == "1",
            mode=raw.get("mode", defaults.mode),
        )

    async def set_enabled(self, value: bool) -> None:
        await self._redis.hset(SETTINGS_KEY, "enabled", "1" if value else "0")

    async def set_system_prompt(self, value: str | None) -> None:
        """None — вернуть стандартный промпт."""
        if value is None:
            await self._redis.hdel(SETTINGS_KEY, "system_prompt")
        else:
            await self._redis.hset(SETTINGS_KEY, "system_prompt", value)

    async def set_reply_delay(self, value: float) -> None:
        await self._redis.hset(SETTINGS_KEY, "reply_delay", str(value))

    async def set_temperature(self, value: float) -> None:
        await self._redis.hset(SETTINGS_KEY, "temperature", str(value))

    async def set_voice_enabled(self, value: bool) -> None:
        await self._redis.hset(
            SETTINGS_KEY, "voice_enabled", "1" if value else "0"
        )

    async def set_mode(self, value: str) -> None:
        await self._redis.hset(SETTINGS_KEY, "mode", value)

    # Чёрный и белый списки хранят id чатов и подпись для отображения.

    async def get_list(self, key: str) -> dict[int, str]:
        raw = await self._redis.hgetall(key)
        return {int(chat_id): title for chat_id, title in raw.items()}

    async def add_to_list(self, key: str, chat_id: int, title: str) -> None:
        await self._redis.hset(key, str(chat_id), title)

    async def remove_from_list(self, key: str, chat_id: int) -> None:
        await self._redis.hdel(key, str(chat_id))

    async def should_reply(self, chat_id: int, settings: Settings) -> bool:
        if not settings.enabled:
            return False

        if await self._redis.hexists(IGNORED_KEY, str(chat_id)):
            return False

        if settings.mode == MODE_WHITELIST:
            return bool(await self._redis.hexists(ALLOWED_KEY, str(chat_id)))

        return True

    async def clear_history(self, chat_id: int | None = None) -> int:
        """Удаляет историю одного чата или всех чатов. Возвращает число ключей."""
        pattern = (
            f"{HISTORY_PREFIX}{chat_id}:*"
            if chat_id is not None
            else f"{HISTORY_PREFIX}*"
        )

        deleted = 0
        batch: list[str] = []

        async for key in self._redis.scan_iter(match=pattern, count=500):
            batch.append(key)

            if len(batch) >= 500:
                deleted += await self._redis.delete(*batch)
                batch.clear()

        if batch:
            deleted += await self._redis.delete(*batch)

        return deleted


settings_service = SettingsService(REDIS_URL)
