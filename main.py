import asyncio

from hydrogram import Client, filters, idle

from config import API_HASH, API_ID
from services.llm_service import ask_agent

app = Client("my_account",API_ID,API_HASH)

@app.on_message(filters.text & filters.private)
async def echo_handler(client, message):
    if not message.text:
        return
    # Формируем ответ и отправляем его
    await message.reply(await ask_agent(message.text, message.chat.id))



async def main() -> None:
    await app.start()

    try:
        await idle()
    finally:
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())

