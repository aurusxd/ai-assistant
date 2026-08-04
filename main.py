import asyncio
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from hydrogram import Client, filters
from hydrogram.types import Message

from config import API_HASH, API_ID
from services.llm_service import ask_agent
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
        )

        await asyncio.sleep(3)
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


if __name__ == "__main__":
    logging.info("Запускаю Hydrogram")
    app.run()