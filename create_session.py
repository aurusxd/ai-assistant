from hydrogram import Client

from config import API_HASH, API_ID

app = Client(
    "my_account",
    api_id=API_ID,
    api_hash=API_HASH,
    workdir="/app/sessions",
)


async def create_session():
    await app.start()

    me = await app.get_me()

    print(f"Авторизация успешна: {me.first_name}, id={me.id}")

    await app.stop()


app.run(create_session())