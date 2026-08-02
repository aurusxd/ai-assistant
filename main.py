import asyncio

from hydrogram import Client, filters

from config import API_HASH, API_ID
from services.llm_service import ask_agent


async def main():
    async with Client("my_account", API_ID,API_HASH) as app:
        @app.on_message(filters.text & filters.private)
        async def echo_handler(client, message):
            # Формируем ответ и отправляем его
            await message.reply(ask_agent(message.text, message.from_user.id))


asyncio.run(main())