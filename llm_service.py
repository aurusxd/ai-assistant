import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from app.tools.weather import get_weather
from app.tools.web_search import search_web
from app.tools.calculator import calc

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

async def ask_agent(user_message: str) -> str:
    messages = [
        {
            "role": "system",
            "content": """
        Ты ии-асисстент для общения с людьми.
        Всегда отвечай дружелюбно и пытайся развить диалог.
        В конце своего ответа старайся задавать вопрос, чтобы продолжить диалог.
""",
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    respone = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=messages,
    )

    return respone.choices[0].message
