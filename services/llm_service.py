from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import ConfigurableField
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_openai import ChatOpenAI
from langchain_redis import RedisChatMessageHistory

from config import DEEPSEEK_API_KEY, REDIS_URL
from services.settings_service import HISTORY_PREFIX


llm = ChatOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
    model="deepseek-v4-pro",
    temperature=0.7,
    max_retries=2,
    timeout=60,
).configurable_fields(
    # Температуру можно менять через бота управления.
    temperature=ConfigurableField(id="temperature"),
)


prompt = ChatPromptTemplate.from_messages(
    [
        # Промпт хранится в настройках и подставляется при каждом запросе.
        ("system", "{system_prompt}"),

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
        key_prefix=HISTORY_PREFIX,
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
    system_prompt: str,
    temperature: float,
) -> str:
    """
    Отправляет сообщение ИИ и сохраняет диалог в Redis.
    """
    if not user_message or not user_message.strip():
        raise ValueError("Сообщение пользователя не может быть пустым")

    response = await chain_with_history.ainvoke(
        {
            "user_message": user_message.strip(),
            "system_prompt": system_prompt,
        },
        config={
            "configurable": {
                "session_id": session_id,
                "temperature": temperature,
            }
        },
    )

    return str(response.content)

