import asyncio
import os

from dotenv import load_dotenv
from hydrogram import Client

load_dotenv()

# Replace these with your own values
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")


async def main():
    async with Client("my_account", api_id, api_hash) as app:
        # Send a message to yourself
        await app.send_message("me", "Greetings from **Hydrogram**!")

        # Get information about yourself
        me = await app.get_me()
        print(f"Successfully logged in as {me.first_name} ({me.id})")


asyncio.run(main())