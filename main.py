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

app = Client(
    "my_account",
    api_id=API_ID,
    api_hash=API_HASH,
    workdir="/app/sessions",
)


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

        await asyncio.sleep(settings.reply_delay)
        await message.reply_text(answer)

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
    await app.start()

    me = await app.get_me()
    logging.info("Аккаунт запущен: %s, id=%s", me.first_name, me.id)

    control_bot = None

    if BOT_TOKEN:
        # Владелец аккаунта всегда может управлять ботом.
        control_bot = ControlBot(userbot=app, admin_ids={me.id, *ADMIN_IDS})
        await control_bot.start()
    else:
        logging.warning(
            "BOT_TOKEN не задан — бот управления не запущен"
        )

    await idle()

    if control_bot:
        await control_bot.stop()

    await app.stop()


if __name__ == "__main__":
    logging.info("Запускаю Hydrogram")
    app.run(main())