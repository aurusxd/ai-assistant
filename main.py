import logging

from hydrogram import Client, filters

from config import API_HASH, API_ID
from services.llm_service import ask_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

app = Client(
    "my_account",
    api_id=API_ID,
    api_hash=API_HASH,
)


@app.on_message(filters.text & filters.private)
async def echo_handler(client, message):
    logging.info(  # noqa: LOG015
        "Получено сообщение: chat_id=%s, text=%r",
        message.chat.id,
        message.text,
    )

    if message.outgoing:
        return

    try:
        answer = await ask_agent(
            user_message=message.text,
            session_id=str(message.chat.id),
        )

        await message.reply_text(answer)

    except Exception:
        logging.exception("Ошибка обработки сообщения")


if __name__ == "__main__":
    logging.info("Запускаю Hydrogram")
    app.run()