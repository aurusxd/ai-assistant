from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_openai import ChatOpenAI
from langchain_redis import RedisChatMessageHistory

from config import DEEPSEEK_API_KEY, REDIS_URL

SYSTEM_PROMPT = """
Ты — ассистент для общения с людьми.

Правила общения:

- Пиши так, как обычно общается живой человек в переписке.
- Отвечай естественно, без канцелярита и шаблонных фраз.
- Учитывай предыдущие сообщения диалога.
- Не повторяй уже сказанное без необходимости.
- Поддерживай разговор и развивай тему.
- Если это уместно, в конце сообщения задай один естественный вопрос, который поможет продолжить диалог.
- Не используй фразы вроде «чем ещё могу помочь?», если они звучат неестественно.
- Не начинай каждое сообщение с приветствия, если диалог уже идёт.
- Используй разговорный язык, но не перегибай со сленгом.
- Не пиши длинные ответы, если можно ответить короче.
- Не перечисляй пункты без необходимости — отвечай как в обычном чате.
- Не придумывай факты. Если чего-то не знаешь, честно скажи об этом.
- Подстраивайся под стиль собеседника: если он пишет коротко — отвечай коротко; если подробно — можешь отвечать подробнее.
- Не переигрывай с эмоциями, смайликами и восклицательными знаками.
- Не используй шаблонные фразы вроде «Спасибо за вопрос», «Отличный вопрос» или «Буду рад помочь», если они не подходят по контексту.
;
""".strip()

llm = ChatOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
    model="deepseek-v4-pro",
    temperature=0.7,
    max_retries=2,
    timeout=60,
)


prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),

        # Сюда RunnableWithMessageHistory подставит историю из Redis.
        MessagesPlaceholder(variable_name="history"),

        ("human", "{user_message}"),
    ]
)


# prompt | llm означает:
# 1. сформировать сообщения по шаблону;
# 2. передать их модели.
chain = prompt | llm


def get_session_history(session_id: str) -> BaseChatMessageHistory:
    """
    Возвращает историю конкретного диалога.

    Для каждого session_id в Redis будет храниться отдельная история.
    """
    return RedisChatMessageHistory(
        session_id=session_id,
        redis_url=REDIS_URL,
        key_prefix="assistant:history:",
        ttl=3600, 
    )


chain_with_history = RunnableWithMessageHistory(
    chain,
    get_session_history=get_session_history,

    # Ключ, в котором передаётся новое сообщение пользователя.
    input_messages_key="user_message",

    # Ключ MessagesPlaceholder, куда вставляется история.
    history_messages_key="history",
)


async def ask_agent(
    user_message: str,
    session_id: str,
) -> str:
    """
    Отправляет сообщение ИИ и сохраняет диалог в Redis.
    """
    if not user_message or not user_message.strip():
        raise ValueError("Сообщение пользователя не может быть пустым")

    response = await chain_with_history.ainvoke(
        {
            "user_message": user_message.strip(),
        },
        config={
            "configurable": {
                "session_id": session_id,
            }
        },
    )

    return str(response.content)

