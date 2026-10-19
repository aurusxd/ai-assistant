import asyncio
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from hydrogram import Client, filters, idle
from hydrogram.types import Message

from config import ADMIN_IDS, API_HASH, API_ID, BOT_TOKEN
from control_bot import ControlBot
from services.llm_service import ask_agent
from services.settings_service import settings_service
from services.speech_service import (
    SpeechRecognitionError,
    speech_service,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

class UserbotClient(Client):
    """Аккаунт, в который можно войти через бота управления, а не через консоль."""

    login_handler = None

    async def authorize(self):
        # Hydrogram вызывает authorize() из start(), только если сессии ещё нет.
        if self.login_handler is None:
            return await super().authorize()

        return await self.login_handler(self)


app = UserbotClient(
    "my_account",
    api_id=API_ID,
    api_hash=API_HASH,
    workdir="/app/sessions",
)


# Ссылки на фоновые задачи, чтобы сборщик мусора не удалил их раньше времени.
background_tasks: set[asyncio.Task] = set()


async def send_with_delay(
    message: Message,
    answer: str,
    delay: float,
) -> None:
    try:
        await asyncio.sleep(delay)
        await message.reply_text(answer)
    except Exception:
        logging.exception("Ошибка отправки ответа")


async def get_message_text(
    client: Client,
    message: Message,
) -> str | None:
    """Возвращает текст обычного или голосового сообщения."""

    if message.text:
        return message.text.strip()

    if not message.voice:
        return None

    if message.voice.duration > 180:
        raise SpeechRecognitionError(
            "Голосовое сообщение длиннее трёх минут"
        )

    with TemporaryDirectory(prefix="assistant_voice_") as temp_dir:
        downloaded_path = await client.download_media(
            message,
            file_name=f"{temp_dir}/",
        )

        if not downloaded_path:
            raise SpeechRecognitionError(
                "Hydrogram не смог скачать голосовое сообщение"
            )

        logging.info(
            "Голосовое скачано: chat_id=%s, path=%s",
            message.chat.id,
            downloaded_path,
        )

        return await speech_service.transcribe(
            Path(downloaded_path)
        )


@app.on_message(
    filters.private
    & filters.incoming
    & ~filters.bot
    & (filters.text | filters.voice)
)
async def message_handler(
    client: Client,
    message: Message,
) -> None:
    try:
        settings = await settings_service.get()

        if not await settings_service.should_reply(message.chat.id, settings):
            return

        if message.voice and not settings.voice_enabled:
            return

        user_message = await get_message_text(
            client=client,
            message=message,
        )

        if not user_message:
            return

        logging.info(
            "Обрабатываем сообщение: chat_id=%s, text=%r",
            message.chat.id,
            user_message,
        )

        answer = await ask_agent(
            user_message=user_message,
            session_id=str(message.chat.id),
            system_prompt=settings.system_prompt,
            temperature=settings.temperature,
        )

        # Ждём в фоне, чтобы задержка не занимала воркер Hydrogram
        # и другие сообщения обрабатывались параллельно.
        task = asyncio.create_task(
            send_with_delay(message, answer, settings.reply_delay)
        )
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    except SpeechRecognitionError as exc:
        logging.exception(
            "Ошибка распознавания голосового"
        )

        await message.reply_text(
            "Не получилось разобрать голосовое. "
            "Можешь записать ещё раз или написать текстом."
        )

    except Exception:
        logging.exception(
            "Ошибка обработки сообщения"
        )


async def main() -> None:
    control_bot = None

    # Бот поднимается раньше аккаунта, чтобы через него можно было войти.
    if BOT_TOKEN:
        control_bot = ControlBot(userbot=app, admin_ids=set(ADMIN_IDS))

        try:
            await control_bot.start()
        except Exception:
            # Автоответчик должен работать, даже если бот не поднялся.
            logging.exception(
                "Не удалось запустить бота управления, проверь BOT_TOKEN"
            )
            control_bot = None
        else:
            app.login_handler = control_bot.login_userbot
    else:
        logging.warning(
            "BOT_TOKEN не задан — бот управления не запущен"
        )

    await app.start()

    me = await app.get_me()
    logging.info("Аккаунт запущен: %s, id=%s", me.first_name, me.id)

    if control_bot:
        # Владелец аккаунта всегда может управлять ботом.
        control_bot.admin_ids.add(me.id)

    await idle()

    if control_bot:
        await control_bot.stop()

    await app.stop()


if __name__ == "__main__":
    logging.info("Запускаю Hydrogram")
    app.run(main())