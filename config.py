import os

from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY=os.getenv("DEEPSEEK_API_KEY")
REDIS_URL = os.getenv("REDIS_URL")
