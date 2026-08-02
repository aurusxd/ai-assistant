from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_openai import ChatOpenAI
from langchain_redis import RedisChatMessageHistory

from config import DEEPSEEK_API_KEY, REDIS_URL

SYSTEM_PROMPT = """
Ты - ИИ-ассистент для общения с людьми.

Правила общения:
- отвечай дружелюбно и естественно;
- учитывай предыдущие сообщения диалога;
- не повторяй информацию без необходимости;
- старайся поддерживать и развивать разговор;
- когда это уместно, задавай в конце один вопрос для продолжения диалога;
- не придумывай факты, если не уверен в ответе.
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

