"""
Бот для управления автоответчиком.

Через него можно включать/выключать автоответы, менять промпт
(то, как программа общается за тебя), задержку, креативность,
списки собеседников и сбрасывать историю диалогов.
Здесь же выполняется вход в Telegram-аккаунт, если сессии ещё нет.
"""

import asyncio
import logging
from html import escape

from hydrogram import Client, filters
from hydrogram.enums import NextCodeType, ParseMode, SentCodeType
from hydrogram.errors import (
    FloodWait,
    MessageNotModified,
    PasswordHashInvalid,
    PhoneCodeEmpty,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    RPCError,
    SessionPasswordNeeded,
)
from hydrogram.handlers import CallbackQueryHandler, MessageHandler
from hydrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    SentCode,
    User,
)

from config import API_HASH, API_ID, BOT_TOKEN
from services.settings_service import (
    ALLOWED_KEY,
    IGNORED_KEY,
    MODE_ALL,
    MODE_WHITELIST,
    Settings,
    settings_service,
)

logger = logging.getLogger(__name__)

PROMPT_PREVIEW_LIMIT = 3500
MAX_PROMPT_LENGTH = 20000
MAX_REPLY_DELAY = 60

DELAY_PRESETS = (0, 1, 3, 5, 10, 30, 60)
TEMPERATURE_PRESETS = (0.2, 0.5, 0.7, 1.0, 1.3)

COMMANDS = [
    "start", "menu", "status", "help", "on", "off", "prompt", "cancel",
    "reset", "ignore", "allow", "unignore", "disallow", "login", "resend",
]

# Куда Telegram отправил код входа.
SENT_CODE_PLACES = {
    SentCodeType.APP: (
        "в приложение Telegram: ищи сообщение в чате «Telegram» с синей "
        "галочкой на телефоне или компьютере, где этот аккаунт уже открыт. "
        "По SMS он не придёт"
    ),
    SentCodeType.SMS: "по SMS",
    SentCodeType.FRAGMENT_SMS: "по SMS через Fragment",
    SentCodeType.CALL: "звонком — код продиктуют",
    SentCodeType.FLASH_CALL: "звонком — код это последние цифры входящего номера",
    SentCodeType.MISSED_CALL: "пропущенным звонком — код это последние цифры номера",
    SentCodeType.EMAIL_CODE: "на почту, привязанную к аккаунту",
}

# Как Telegram может отправить код повторно.
NEXT_CODE_WAYS = {
    NextCodeType.SMS: "по SMS",
    NextCodeType.FRAGMENT_SMS: "по SMS через Fragment",
    NextCodeType.CALL: "звонком",
    NextCodeType.FLASH_CALL: "звонком",
    NextCodeType.MISSED_CALL: "пропущенным звонком",
}

LISTS = {
    "ignored": (IGNORED_KEY, "Чёрный список"),
    "allowed": (ALLOWED_KEY, "Белый список"),
}

HELP_TEXT = """
<b>Команды</b>

/menu — панель управления
/on — включить автоответы
/off — выключить автоответы
/prompt <i>текст</i> — задать новый промпт
/ignore <i>@username или id</i> — не отвечать человеку
/unignore <i>@username или id</i> — убрать из чёрного списка
/allow <i>@username или id</i> — добавить в белый список
/disallow <i>@username или id</i> — убрать из белого списка
/reset [<i>@username или id</i>] — сбросить историю диалога (без аргумента — всех)
/login — войти в Telegram-аккаунт (нужно только при первом запуске)
/resend — отправить код входа ещё раз другим способом
/cancel — отменить ввод

Длинный промпт можно прислать .txt файлом, когда бот попросит новый промпт.
""".strip()


def _yes_no(value: bool) -> str:
    return "вкл" if value else "выкл"


def _mask_phone(phone: str) -> str:
    return f"{phone[:3]}***{phone[-2:]}"


def _back_button(target: str = "menu") -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("⬅️ Назад", callback_data=target)]


class ControlBot:
    def __init__(self, userbot: Client, admin_ids: set[int]) -> None:
        # Через userbot ищем людей по @username: у бота нет доступа
        # к твоим контактам и диалогам.
        self.userbot = userbot
        self.admin_ids = admin_ids

        # Чего бот ждёт от админа следующим сообщением:
        # admin_id -> "prompt" | "delay" | "reset" | "add:ignored" | "add:allowed"
        #             | "login:phone" | "login:code" | "login:password"
        self.pending: dict[int, str] = {}

        # Пока аккаунт не авторизован, userbot ждёт этот future.
        self.login_future: asyncio.Future[User] | None = None
        self.login_phone = ""
        self.login_code_hash = ""
        # Каким способом Telegram может прислать код повторно (None — никаким).
        self.login_next_type: NextCodeType | None = None

        self.client = Client(
            "control_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            # Бот авторизуется по токену при каждом запуске, поэтому смена
            # BOT_TOKEN сразу подхватывается, а не берётся старая сессия.
            in_memory=True,
            parse_mode=ParseMode.HTML,
        )

        is_admin = filters.create(
            lambda _, __, update: bool(
                update.from_user and update.from_user.id in self.admin_ids
            ),
            name="IsAdmin",
        )
        # Бот получает и свои исходящие сообщения: без incoming он принимал
        # их за чужие и отвечал сам себе «Нет доступа».
        private = filters.private & filters.incoming
        admin_private = private & is_admin

        # Hydrogram вызывает все подходящие обработчики, а не только первый,
        # поэтому фильтры не должны пересекаться.
        handlers = [
            (self.cmd_menu, filters.command(["start", "menu", "status"])),
            (self.cmd_help, filters.command("help")),
            (self.cmd_on, filters.command("on")),
            (self.cmd_off, filters.command("off")),
            (self.cmd_prompt, filters.command("prompt")),
            (self.cmd_cancel, filters.command("cancel")),
            (self.cmd_reset, filters.command("reset")),
            (self.cmd_list_add, filters.command(["ignore", "allow"])),
            (self.cmd_list_remove, filters.command(["unignore", "disallow"])),
            (self.cmd_login, filters.command("login")),
            (self.cmd_resend, filters.command("resend")),
            (
                self.on_input,
                (filters.text & ~filters.command(COMMANDS)) | filters.document,
            ),
        ]

        for callback, flt in handlers:
            self.client.add_handler(MessageHandler(callback, admin_private & flt))

        self.client.add_handler(
            MessageHandler(self.on_stranger, private & ~is_admin)
        )
        self.client.add_handler(CallbackQueryHandler(self.on_callback, is_admin))

    async def start(self) -> None:
        await self.client.start()
        me = await self.client.get_me()
        logger.info("Бот управления запущен: @%s", me.username)

    async def stop(self) -> None:
        if self.login_required:
            self.login_future.cancel()

        await self.client.stop()

    @property
    def login_required(self) -> bool:
        return bool(self.login_future and not self.login_future.done())

    async def login_userbot(self, _: Client) -> User:
        """Вместо ввода в консоли ждёт, пока админ войдёт в аккаунт через бота."""
        self.login_future = asyncio.get_running_loop().create_future()

        if not self.admin_ids:
            logger.error(
                "Аккаунт не авторизован, а войти через бота некому: "
                "добавь свой Telegram id в ADMIN_IDS в .env и перезапусти"
            )
        else:
            logger.warning("Аккаунт не авторизован — жду входа через бота: /login")

        for admin_id in self.admin_ids:
            try:
                await self.client.send_message(
                    admin_id,
                    "🔐 Аккаунт Telegram не авторизован, автоответчик не работает.\n"
                    "Нажми /login, чтобы войти.",
                )
            except Exception:
                # Бот не может написать первым, пока ему не нажали /start.
                logger.warning("Не удалось написать админу %s", admin_id)

        return await self.login_future

    # region Экраны

    async def main_screen(self) -> tuple[str, InlineKeyboardMarkup]:
        settings = await settings_service.get()
        ignored = await settings_service.get_list(IGNORED_KEY)
        allowed = await settings_service.get_list(ALLOWED_KEY)

        if settings.mode == MODE_WHITELIST:
            audience = f"только белый список ({len(allowed)})"
        else:
            audience = f"всем, кроме чёрного списка ({len(ignored)})"

        text = (
            "⚙️ <b>Автоответчик</b>\n\n"
            f"Статус: {'🟢 включён' if settings.enabled else '🔴 выключен'}\n"
            f"Кому отвечать: {audience}\n"
            f"Задержка перед ответом: {settings.reply_delay:g} сек\n"
            f"Креативность: {settings.temperature:g}\n"
            f"Голосовые: {_yes_no(settings.voice_enabled)}\n"
            f"Промпт: {len(settings.system_prompt)} символов"
        )

        if self.login_required:
            text = "🔐 <b>Аккаунт не авторизован</b> — войди: /login\n\n" + text

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔴 Выключить" if settings.enabled else "🟢 Включить",
                        callback_data="toggle:enabled",
                    )
                ],
                [
                    InlineKeyboardButton("📝 Промпт", callback_data="prompt"),
                    InlineKeyboardButton("⏱ Задержка", callback_data="delay"),
                ],
                [
                    InlineKeyboardButton("🌡 Креативность", callback_data="temp"),
                    InlineKeyboardButton(
                        f"🎙 Голосовые: {_yes_no(settings.voice_enabled)}",
                        callback_data="toggle:voice",
                    ),
                ],
                [
                    InlineKeyboardButton("👥 Собеседники", callback_data="lists"),
                    InlineKeyboardButton("🧹 История", callback_data="history"),
                ],
            ]
        )

        return text, keyboard

    async def prompt_screen(self) -> tuple[str, InlineKeyboardMarkup]:
        settings = await settings_service.get()
        prompt = settings.system_prompt

        preview = prompt[:PROMPT_PREVIEW_LIMIT]
        if len(prompt) > PROMPT_PREVIEW_LIMIT:
            preview += "\n…"

        text = (
            f"📝 <b>Текущий промпт</b> ({len(prompt)} символов)\n\n"
            f"<pre>{escape(preview)}</pre>"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✏️ Изменить", callback_data="ask:prompt")],
                [
                    InlineKeyboardButton(
                        "↩️ Вернуть стандартный",
                        callback_data="prompt:default",
                    )
                ],
                _back_button(),
            ]
        )

        return text, keyboard

    async def delay_screen(self) -> tuple[str, InlineKeyboardMarkup]:
        settings = await settings_service.get()

        text = (
            "⏱ <b>Задержка перед ответом</b>\n\n"
            f"Сейчас: {settings.reply_delay:g} сек\n\n"
            "Пауза после генерации ответа, чтобы он не приходил мгновенно."
        )

        buttons = [
            InlineKeyboardButton(
                f"{'✅ ' if settings.reply_delay == value else ''}{value}",
                callback_data=f"delay:{value}",
            )
            for value in DELAY_PRESETS
        ]

        keyboard = InlineKeyboardMarkup(
            [
                buttons[:4],
                buttons[4:],
                [InlineKeyboardButton("✏️ Своё значение", callback_data="ask:delay")],
                _back_button(),
            ]
        )

        return text, keyboard

    async def temperature_screen(self) -> tuple[str, InlineKeyboardMarkup]:
        settings = await settings_service.get()

        text = (
            "🌡 <b>Креативность (temperature)</b>\n\n"
            f"Сейчас: {settings.temperature:g}\n\n"
            "Меньше — ответы спокойнее и предсказуемее.\n"
            "Больше — живее и разнообразнее, но могут быть странности."
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"{'✅ ' if settings.temperature == value else ''}{value:g}",
                        callback_data=f"temp:{value}",
                    )
                    for value in TEMPERATURE_PRESETS
                ],
                _back_button(),
            ]
        )

        return text, keyboard

    async def lists_screen(self) -> tuple[str, InlineKeyboardMarkup]:
        settings = await settings_service.get()

        if settings.mode == MODE_WHITELIST:
            mode_text = "отвечать <b>только белому списку</b>"
            mode_button = "👥 Режим: только белый список"
        else:
            mode_text = "отвечать <b>всем, кроме чёрного списка</b>"
            mode_button = "👥 Режим: всем"

        lines = [f"👥 <b>Собеседники</b>\n\nРежим: {mode_text}"]
        rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton(mode_button, callback_data="toggle:mode")]
        ]

        for list_name, (key, title) in LISTS.items():
            entries = await settings_service.get_list(key)

            lines.append(f"\n<b>{title}</b>:")
            if not entries:
                lines.append("— пусто")

            for chat_id, name in entries.items():
                lines.append(f"• {escape(name)} (<code>{chat_id}</code>)")
                rows.append(
                    [
                        InlineKeyboardButton(
                            f"❌ {name} — {title.lower()}",
                            callback_data=f"rm:{list_name}:{chat_id}",
                        )
                    ]
                )

        rows.append(
            [
                InlineKeyboardButton("➕ В чёрный", callback_data="ask:add:ignored"),
                InlineKeyboardButton("➕ В белый", callback_data="ask:add:allowed"),
            ]
        )
        rows.append(_back_button())

        return "\n".join(lines), InlineKeyboardMarkup(rows)

    async def history_screen(self) -> tuple[str, InlineKeyboardMarkup]:
        text = (
            "🧹 <b>История диалогов</b>\n\n"
            "Бот помнит переписку, чтобы отвечать по контексту. "
            "Сбрось её, если поменял промпт или разговор пошёл не туда."
        )

        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🗑 Сбросить все", callback_data="reset:all")],
                [InlineKeyboardButton("👤 Сбросить один чат", callback_data="ask:reset")],
                _back_button(),
            ]
        )

        return text, keyboard

    # endregion

    # region Команды

    async def cmd_menu(self, _: Client, message: Message) -> None:
        self.pending.pop(message.from_user.id, None)
        text, keyboard = await self.main_screen()
        await message.reply_text(text, reply_markup=keyboard)

    async def cmd_help(self, _: Client, message: Message) -> None:
        await message.reply_text(HELP_TEXT)

    async def cmd_on(self, _: Client, message: Message) -> None:
        await settings_service.set_enabled(True)
        await message.reply_text("🟢 Автоответы включены")

    async def cmd_off(self, _: Client, message: Message) -> None:
        await settings_service.set_enabled(False)
        await message.reply_text("🔴 Автоответы выключены")

    async def cmd_prompt(self, _: Client, message: Message) -> None:
        new_prompt = self._command_argument(message)

        if not new_prompt:
            text, keyboard = await self.prompt_screen()
            await message.reply_text(text, reply_markup=keyboard)
            return

        await self._save_prompt(message, new_prompt)

    async def cmd_cancel(self, _: Client, message: Message) -> None:
        if self.pending.pop(message.from_user.id, None):
            await message.reply_text("Отменено")
        else:
            await message.reply_text("Нечего отменять")

    async def cmd_reset(self, _: Client, message: Message) -> None:
        target = self._command_argument(message)

        if not target:
            deleted = await settings_service.clear_history()
            await message.reply_text(f"🗑 История всех диалогов сброшена ({deleted})")
            return

        await self._reset_chat(message, target)

    async def cmd_list_add(self, _: Client, message: Message) -> None:
        list_name = "ignored" if message.command[0] == "ignore" else "allowed"
        target = self._command_argument(message)

        if not target:
            await message.reply_text(
                f"Укажи @username или id: /{message.command[0]} @username"
            )
            return

        await self._add_to_list(message, list_name, target)

    async def cmd_list_remove(self, _: Client, message: Message) -> None:
        list_name = "ignored" if message.command[0] == "unignore" else "allowed"
        key, title = LISTS[list_name]
        target = self._command_argument(message)

        if not target:
            await message.reply_text(
                f"Укажи @username или id: /{message.command[0]} @username"
            )
            return

        chat_id, _ = await self._resolve_user(target)
        if chat_id is None:
            await message.reply_text("Не нашёл такого пользователя")
            return

        await settings_service.remove_from_list(key, chat_id)
        await message.reply_text(f"Убрал из списка «{title}»")

    async def cmd_login(self, _: Client, message: Message) -> None:
        logger.info(
            "Вход: /login от %s, аккаунт ждёт входа: %s",
            message.from_user.id,
            self.login_required,
        )

        if not self.login_required:
            await message.reply_text("✅ Аккаунт уже авторизован")
            return

        self.pending[message.from_user.id] = "login:phone"
        await message.reply_text(
            "📱 Пришли номер телефона аккаунта в международном формате, "
            "например +79991234567\n\n/cancel — отмена"
        )

    async def cmd_resend(self, _: Client, message: Message) -> None:
        user_id = message.from_user.id
        logger.info("Вход: /resend от %s", user_id)

        if not self.login_required:
            await message.reply_text("✅ Аккаунт уже авторизован")
            return

        if self.pending.get(user_id) != "login:code":
            await message.reply_text("Сначала запроси код: /login")
            return

        if self.login_next_type is None:
            # Иначе Telegram отвечает SEND_CODE_UNAVAILABLE.
            logger.info("Вход: Telegram не предлагает другого способа отправки")
            await message.reply_text(
                "Telegram не даёт отправить код другим способом: для этого "
                "номера он приходит только в приложение Telegram, в чат "
                "«Telegram» с синей галочкой.\n\n"
                "Если кода там нет, подожди и запроси новый: /login"
            )
            return

        try:
            sent_code = await self.userbot.resend_code(
                self.login_phone, self.login_code_hash
            )
        except FloodWait as exc:
            logger.warning("Вход: FloodWait на %s сек при повторной отправке", exc.value)
            await message.reply_text(
                f"Telegram просит подождать {exc.value} сек перед новой попыткой"
            )
            return
        except Exception as exc:
            # Hydrogram 0.2.0 знает не все ошибки Telegram и на незнакомых
            # падает с AttributeError вместо RPCError.
            logger.warning("Вход: не удалось отправить код повторно: %r", exc)
            reason = (
                exc.MESSAGE if isinstance(exc, RPCError) else "подробности в логах"
            )
            await message.reply_text(
                f"Telegram не отправил код повторно: {escape(reason)}\n"
                "Можно начать заново: /login"
            )
            return

        self.login_code_hash = sent_code.phone_code_hash
        self.login_next_type = sent_code.next_type
        await message.reply_text(self._code_sent_text(sent_code))

    # endregion

    # region Ввод значений

    async def on_input(self, _: Client, message: Message) -> None:
        action = self.pending.get(message.from_user.id)

        if message.text and message.text.startswith("/"):
            await message.reply_text("Не знаю такой команды. /help")
            return

        if not action:
            if self.login_required:
                logger.info(
                    "Вход: сообщение от %s без активного шага входа, "
                    "показываю меню",
                    message.from_user.id,
                )

            text, keyboard = await self.main_screen()
            await message.reply_text(text, reply_markup=keyboard)
            return

        if action.startswith("login:"):
            await self._login_step(message, action.removeprefix("login:"))
            return

        if action == "prompt":
            new_prompt = await self._read_prompt(message)
            if new_prompt is None:
                return

            self.pending.pop(message.from_user.id, None)
            await self._save_prompt(message, new_prompt)
            return

        if not message.text:
            await message.reply_text("Пришли текстом или /cancel")
            return

        value = message.text.strip()

        if action == "delay":
            try:
                delay = float(value.replace(",", "."))
            except ValueError:
                delay = -1

            if not 0 <= delay <= MAX_REPLY_DELAY:
                await message.reply_text(
                    f"Нужно число от 0 до {MAX_REPLY_DELAY}. Или /cancel"
                )
                return

            self.pending.pop(message.from_user.id, None)
            await settings_service.set_reply_delay(delay)
            await message.reply_text(f"⏱ Задержка: {delay:g} сек")
            return

        self.pending.pop(message.from_user.id, None)

        if action == "reset":
            await self._reset_chat(message, value)
        elif action.startswith("add:"):
            await self._add_to_list(message, action.removeprefix("add:"), value)

    async def _read_prompt(self, message: Message) -> str | None:
        if message.text:
            return message.text.strip()

        document = message.document
        if not document or not (document.file_name or "").endswith(".txt"):
            await message.reply_text("Пришли промпт текстом или .txt файлом. Или /cancel")
            return None

        if document.file_size and document.file_size > MAX_PROMPT_LENGTH * 4:
            await message.reply_text("Файл слишком большой")
            return None

        file = await message.download(in_memory=True)

        try:
            return bytes(file.getbuffer()).decode("utf-8").strip()
        except UnicodeDecodeError:
            await message.reply_text("Файл должен быть в кодировке UTF-8")
            return None

    async def _save_prompt(self, message: Message, new_prompt: str) -> None:
        if not new_prompt:
            await message.reply_text("Промпт не может быть пустым")
            return

        if len(new_prompt) > MAX_PROMPT_LENGTH:
            await message.reply_text(
                f"Слишком длинный промпт: максимум {MAX_PROMPT_LENGTH} символов"
            )
            return

        await settings_service.set_system_prompt(new_prompt)
        await message.reply_text(
            f"✅ Промпт обновлён ({len(new_prompt)} символов).\n"
            "Если старые диалоги мешают новому стилю — сбрось историю: /reset"
        )

    # endregion

    # region Вход в аккаунт

    async def _login_step(self, message: Message, step: str) -> None:
        user_id = message.from_user.id
        # Сам код и пароль в лог не пишем — только шаг и исход.
        logger.info("Вход: шаг %s, сообщение от %s", step, user_id)

        if not self.login_required:
            self.pending.pop(user_id, None)
            await message.reply_text("✅ Аккаунт уже авторизован")
            return

        if not message.text:
            await message.reply_text("Пришли текстом или /cancel")
            return

        value = message.text.strip()

        if step != "phone":
            # Код и пароль не должны оставаться в переписке.
            try:
                await message.delete()
            except Exception:
                logger.warning("Вход: не удалось удалить сообщение с шага %s", step)

        userbot = self.userbot

        try:
            if step == "phone":
                phone = "+" + "".join(ch for ch in value if ch.isdigit())
                logger.info("Вход: отправляю код на %s", _mask_phone(phone))
                sent_code = await userbot.send_code(phone)

                self.login_phone = phone
                self.login_code_hash = sent_code.phone_code_hash
                self.login_next_type = sent_code.next_type
                self.pending[user_id] = "login:code"

                await message.reply_text(self._code_sent_text(sent_code))
                return

            if step == "code":
                code = "".join(ch for ch in value if ch.isdigit())
                logger.info("Вход: проверяю код (%s цифр)", len(code))

                try:
                    user = await userbot.sign_in(
                        self.login_phone, self.login_code_hash, code
                    )
                except SessionPasswordNeeded:
                    logger.info("Вход: код принят, нужен облачный пароль")
                    self.pending[user_id] = "login:password"
                    hint = await userbot.get_password_hint()
                    await message.reply_text(
                        "🔑 Включена двухэтапная аутентификация. Пришли облачный "
                        "пароль — сообщение с ним я сразу удалю."
                        + (f"\n\nПодсказка: {escape(hint)}" if hint else "")
                        + "\n\n/cancel — отмена"
                    )
                    return

                if not isinstance(user, User):
                    logger.warning("Вход: на номер не зарегистрирован аккаунт")
                    self.pending.pop(user_id, None)
                    await message.reply_text(
                        "На этот номер не зарегистрирован аккаунт Telegram. "
                        "Попробуй другой номер: /login"
                    )
                    return

            else:
                logger.info("Вход: проверяю облачный пароль")
                user = await userbot.check_password(value)

        except (PhoneCodeInvalid, PhoneCodeEmpty):
            logger.warning("Вход: неверный код")
            await message.reply_text("Неверный код, пришли ещё раз. Или /cancel")
            return
        except PhoneCodeExpired:
            logger.warning(
                "Вход: код устарел (возможно, Telegram аннулировал его, "
                "увидев в переписке без пробелов)"
            )
            self.pending.pop(user_id, None)
            await message.reply_text("Код устарел. Начни заново: /login")
            return
        except PasswordHashInvalid:
            logger.warning("Вход: неверный облачный пароль")
            await message.reply_text("Неверный пароль, пришли ещё раз. Или /cancel")
            return
        except FloodWait as exc:
            logger.warning("Вход: FloodWait на %s сек", exc.value)
            self.pending.pop(user_id, None)
            await message.reply_text(
                f"Telegram просит подождать {exc.value} сек перед новой попыткой"
            )
            return
        except RPCError as exc:
            logger.warning("Вход: ошибка Telegram на шаге %s: %s", step, exc)
            reason = escape(exc.MESSAGE or str(exc))

            if step == "phone":
                await message.reply_text(
                    f"Не получилось отправить код: {reason}\n"
                    "Проверь номер и пришли ещё раз. Или /cancel"
                )
            else:
                self.pending.pop(user_id, None)
                await message.reply_text(
                    f"Не получилось войти: {reason}\nНачни заново: /login"
                )
            return

        logger.info("Вход: успешно, аккаунт id=%s", user.id)
        self.pending.pop(user_id, None)
        self.login_future.set_result(user)

        await message.reply_text(
            f"✅ Вход выполнен: {escape(user.first_name or str(user.id))}. "
            "Автоответчик запускается. /menu"
        )

    @staticmethod
    def _code_sent_text(sent_code: SentCode) -> str:
        logger.info(
            "Вход: код отправлен, способ: %s, повторно можно: %s, через %s сек",
            sent_code.type,
            sent_code.next_type,
            sent_code.timeout,
        )

        place = SENT_CODE_PLACES.get(sent_code.type, "в Telegram")
        text = (
            f"✉️ Код отправлен {place}.\n\n"
            "Пришли его <b>с пробелами между цифрами</b>, например "
            "<code>1 2 3 4 5</code>. Если прислать код как есть, "
            "Telegram заметит его в переписке и сразу аннулирует."
        )

        next_way = NEXT_CODE_WAYS.get(sent_code.next_type)
        if next_way:
            wait = f" через {sent_code.timeout} сек" if sent_code.timeout else ""
            text += f"\n\nНе пришёл? Отправлю {next_way}{wait}: /resend"

        return text + "\n\n/cancel — отмена"

    # endregion

    # region Кнопки

    async def on_callback(self, _: Client, query: CallbackQuery) -> None:
        data = query.data or ""
        user_id = query.from_user.id
        notice: str | None = None

        if data.startswith("ask:"):
            await self._ask(query, data.removeprefix("ask:"))
            return

        # Любая кнопка, кроме запроса ввода, отменяет ожидание ввода.
        self.pending.pop(user_id, None)
        screen = self.main_screen

        if data == "toggle:enabled":
            settings = await settings_service.get()
            await settings_service.set_enabled(not settings.enabled)
            notice = "Выключено" if settings.enabled else "Включено"

        elif data == "toggle:voice":
            settings = await settings_service.get()
            await settings_service.set_voice_enabled(not settings.voice_enabled)

        elif data == "toggle:mode":
            settings = await settings_service.get()
            await settings_service.set_mode(
                MODE_ALL if settings.mode == MODE_WHITELIST else MODE_WHITELIST
            )
            screen = self.lists_screen

        elif data == "prompt":
            screen = self.prompt_screen

        elif data == "prompt:default":
            await settings_service.set_system_prompt(None)
            notice = "Вернул стандартный промпт"
            screen = self.prompt_screen

        elif data == "delay":
            screen = self.delay_screen

        elif data.startswith("delay:"):
            await settings_service.set_reply_delay(float(data.split(":", 1)[1]))
            screen = self.delay_screen

        elif data == "temp":
            screen = self.temperature_screen

        elif data.startswith("temp:"):
            await settings_service.set_temperature(float(data.split(":", 1)[1]))
            screen = self.temperature_screen

        elif data == "lists":
            screen = self.lists_screen

        elif data.startswith("rm:"):
            _, list_name, chat_id = data.split(":", 2)
            key, _ = LISTS[list_name]
            await settings_service.remove_from_list(key, int(chat_id))
            screen = self.lists_screen

        elif data == "history":
            screen = self.history_screen

        elif data == "reset:all":
            deleted = await settings_service.clear_history()
            notice = f"История сброшена ({deleted})"
            screen = self.history_screen

        await self._show(query, screen, notice)

    async def _ask(self, query: CallbackQuery, action: str) -> None:
        questions = {
            "prompt": (
                "✏️ Пришли новый промпт одним сообщением или .txt файлом.\n"
                "Опиши, как программа должна общаться за тебя: стиль, длина "
                "сообщений, с кем общается, о чём молчать и т.д.\n\n"
                "Длиннее 4000 символов — только файлом, иначе Telegram "
                "разрежет сообщение на части."
            ),
            "delay": "Пришли задержку в секундах (например, 4.5)",
            "reset": "Пришли @username или id чата, историю которого сбросить",
            "add:ignored": "Пришли @username или id — ему бот отвечать не будет",
            "add:allowed": "Пришли @username или id — добавлю в белый список",
        }

        if action not in questions:
            await query.answer()
            return

        self.pending[query.from_user.id] = action
        await query.answer()
        await query.message.reply_text(f"{questions[action]}\n\n/cancel — отмена")

    async def _show(self, query: CallbackQuery, screen, notice: str | None) -> None:
        text, keyboard = await screen()

        try:
            await query.edit_message_text(text, reply_markup=keyboard)
        except MessageNotModified:
            # Telegram ругается, если текст и кнопки не изменились.
            pass

        await query.answer(notice)

    # endregion

    async def on_stranger(self, _: Client, message: Message) -> None:
        logger.warning(
            "Попытка доступа к боту управления: user_id=%s",
            message.from_user.id if message.from_user else None,
        )
        # id нужен, чтобы владелец мог прописать себя в ADMIN_IDS.
        await message.reply_text(
            "⛔️ Нет доступа\n\n"
            f"Твой id: <code>{message.from_user.id if message.from_user else '?'}</code>"
        )

    # region Вспомогательное

    @staticmethod
    def _command_argument(message: Message) -> str:
        parts = (message.text or "").split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    async def _resolve_user(self, target: str) -> tuple[int | None, str]:
        """Находит пользователя по @username или id через аккаунт-userbot."""
        target = target.strip()
        lookup: int | str = target

        if target.lstrip("-").isdigit():
            lookup = int(target)
        elif target.startswith("@"):
            lookup = target[1:]
        elif "t.me/" in target:
            lookup = target.rsplit("/", 1)[-1]

        try:
            user = await self.userbot.get_users(lookup)
        except Exception:
            # Пользователя по id можно добавить, даже если аккаунт
            # с ним ещё не переписывался.
            if isinstance(lookup, int):
                return lookup, str(lookup)
            return None, ""

        name = " ".join(filter(None, [user.first_name, user.last_name])) or str(user.id)
        if user.username:
            name += f" @{user.username}"

        return user.id, name

    async def _add_to_list(self, message: Message, list_name: str, target: str) -> None:
        key, title = LISTS[list_name]
        chat_id, name = await self._resolve_user(target)

        if chat_id is None:
            await message.reply_text("Не нашёл такого пользователя")
            return

        await settings_service.add_to_list(key, chat_id, name)

        settings: Settings = await settings_service.get()
        hint = ""
        if list_name == "allowed" and settings.mode != MODE_WHITELIST:
            hint = (
                "\n\nСейчас бот отвечает всем. Чтобы отвечать только белому "
                "списку, переключи режим в «👥 Собеседники»."
            )

        await message.reply_text(
            f"Добавил {escape(name)} в список «{title}»{hint}"
        )

    async def _reset_chat(self, message: Message, target: str) -> None:
        chat_id, name = await self._resolve_user(target)

        if chat_id is None:
            await message.reply_text("Не нашёл такого пользователя")
            return

        deleted = await settings_service.clear_history(chat_id)
        await message.reply_text(
            f"🗑 История с {escape(name)} сброшена ({deleted})"
        )

    # endregion
