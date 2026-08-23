import asyncio
import inspect
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import ChatWriteForbiddenError, FloodWaitError, ReactionInvalidError, RPCError
from telethon.sessions import StringSession
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

BOT_VERSION = "2026-08-23-user-filters"
# Стоковый лайк Telegram — ❤ (U+2764). Слово heart и ♥ из интернета сюда нормализуются.
STOCK_LIKE = "❤"
POOP_EMOJI = "💩"
REACTION_ALIASES = {
    "heart": STOCK_LIKE,
    "love": STOCK_LIKE,
    "like": STOCK_LIKE,
    "лайк": STOCK_LIKE,
    "сердечко": STOCK_LIKE,
    "сердце": STOCK_LIKE,
    "stock": STOCK_LIKE,
    "poop": POOP_EMOJI,
    "какашки": POOP_EMOJI,
    "какашка": POOP_EMOJI,
}
HEART_CHARS = set("❤♥♡❣💕💖💗💘💝💞💟💔🧡💛💚💙💜🖤🤍🤎😍🥰")


def normalize_reaction(raw: str, default: str = STOCK_LIKE) -> str:
    value = (raw or "").strip()
    if not value:
        return default

    alias = REACTION_ALIASES.get(value.lower())
    if alias:
        return alias

    cleaned = value.replace("\ufe0f", "").replace("\ufe0e", "").strip()
    if cleaned in HEART_CHARS or any(char in HEART_CHARS for char in cleaned):
        return STOCK_LIKE
    return cleaned or default


def reaction_debug(value: str) -> str:
    codes = " ".join(f"U+{ord(char):04X}" for char in value)
    return f"{value} [{codes}]"

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("likebot")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_csv(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def parse_chat_ids(raw: str) -> list[int | str]:
    chats: list[int | str] = []
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        if value.startswith("@"):
            chats.append(value)
            continue
        try:
            chats.append(int(value))
        except ValueError:
            chats.append(value)
    return chats


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        logger.error("Не задана переменная окружения %s", name)
        sys.exit(1)
    return value


API_ID = int(require_env("TELEGRAM_API_ID"))
API_HASH = require_env("TELEGRAM_API_HASH")
CHAT_IDS = parse_chat_ids(os.getenv("CHAT_IDS", ""))
REACTION = normalize_reaction(os.getenv("REACTION", "heart"))
if REACTION != STOCK_LIKE and any(char in HEART_CHARS for char in REACTION):
    REACTION = STOCK_LIKE
SKIP_USERS = [item.lower() for item in parse_csv(os.getenv("SKIP_USERS", ""))]
POOP_USERS = [item.lower() for item in parse_csv(os.getenv("POOP_USERS", ""))]
POOP_REACTION = normalize_reaction(os.getenv("POOP_REACTION", "poop"), default=POOP_EMOJI)
SESSION_NAME = os.getenv("SESSION_NAME", "likebot").strip() or "likebot"
SESSION_DIR = Path(os.getenv("SESSION_DIR", "/data"))
SESSION_DIR.mkdir(parents=True, exist_ok=True)
SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
LIKE_OWN_MESSAGES = env_bool("LIKE_OWN_MESSAGES", False)
LIST_CHATS = env_bool("LIST_CHATS", False)
LIST_USERS = env_bool("LIST_USERS", False)
BACKFILL_LIMIT = int(os.getenv("BACKFILL_LIMIT", "0"))
DELAY_SECONDS = float(os.getenv("DELAY_SECONDS", "0.4"))
STATE_FILE = SESSION_DIR / "likes_enabled"


class LikeState:
    def __init__(self) -> None:
        self.enabled = self._load()

    def _load(self) -> bool:
        if not STATE_FILE.exists():
            return True
        return STATE_FILE.read_text(encoding="utf-8").strip().lower() in {"1", "true", "on", "yes"}

    def set(self, enabled: bool) -> None:
        self.enabled = enabled
        STATE_FILE.write_text("true" if enabled else "false", encoding="utf-8")

    def toggle(self) -> bool:
        self.set(not self.enabled)
        return self.enabled

    def status_text(self) -> str:
        return "включены ❤" if self.enabled else "выключены"


like_state = LikeState()


def build_client() -> TelegramClient:
    if SESSION_STRING:
        return TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

    session_path = SESSION_DIR / SESSION_NAME
    return TelegramClient(str(session_path), API_ID, API_HASH)


client = build_client()


def sender_labels(sender) -> list[str]:
    if sender is None:
        return []

    labels: list[str] = []
    user_id = getattr(sender, "id", None)
    first = (getattr(sender, "first_name", None) or "").strip()
    last = (getattr(sender, "last_name", None) or "").strip()
    username = (getattr(sender, "username", None) or "").strip()
    title = (getattr(sender, "title", None) or "").strip()

    if user_id is not None:
        labels.append(str(user_id))
    if username:
        labels.append(username.lower())
        labels.append(f"@{username.lower()}")
    if first:
        labels.append(first.lower())
    if last:
        labels.append(last.lower())
    if first and last:
        labels.append(f"{first} {last}".lower())
        labels.append(f"{first}_{last}".lower())
    if title:
        labels.append(title.lower())
    return labels


def sender_display(sender) -> str:
    if sender is None:
        return "unknown"
    first = (getattr(sender, "first_name", None) or "").strip()
    last = (getattr(sender, "last_name", None) or "").strip()
    username = (getattr(sender, "username", None) or "").strip()
    title = (getattr(sender, "title", None) or "").strip()
    name = " ".join(part for part in (first, last) if part) or title or "без имени"
    extra = f" @{username}" if username else ""
    user_id = getattr(sender, "id", "?")
    return f"{name}{extra} (id={user_id})"


def matches_user_filter(sender, filters: list[str]) -> bool:
    if not sender or not filters:
        return False
    labels = set(sender_labels(sender))
    for item in filters:
        if item in labels:
            return True
        for label in labels:
            if item.startswith("@") or item.lstrip("-").isdigit() or len(item) < 3:
                continue
            if item in label:
                return True
    return False


def reaction_for_sender(sender) -> str | None:
    if matches_user_filter(sender, SKIP_USERS):
        return None
    if matches_user_filter(sender, POOP_USERS):
        return POOP_REACTION
    return REACTION


def build_reaction_request(peer, msg_id: int, reaction: str) -> SendReactionRequest:
    emoji = ReactionEmoji(emoticon=reaction)
    params = inspect.signature(SendReactionRequest.__init__).parameters
    kwargs = {"peer": peer, "msg_id": msg_id}

    reaction_param = params.get("reaction")
    if reaction_param is not None:
        annotation = str(reaction_param.annotation)
        if "List" in annotation or "Sequence" in annotation or "list[" in annotation:
            kwargs["reaction"] = [emoji]
        else:
            kwargs["reaction"] = emoji

    if "add_to_recent" in params:
        kwargs["add_to_recent"] = True

    return SendReactionRequest(**kwargs)


async def send_reaction(message, reaction: str) -> None:
    await client(build_reaction_request(message.peer_id, message.id, reaction))


async def add_like(message, reaction: str) -> None:
    await asyncio.sleep(DELAY_SECONDS)
    try:
        await send_reaction(message, reaction)
    except FloodWaitError as error:
        logger.warning("FloodWait: жду %s сек.", error.seconds)
        await asyncio.sleep(error.seconds + 1)
        await send_reaction(message, reaction)


async def like_if_needed(message, *, reason: str) -> None:
    if not like_state.enabled:
        return
    if getattr(message, "action", None):
        return
    if message.out and not LIKE_OWN_MESSAGES:
        return

    sender = await message.get_sender()
    reaction = reaction_for_sender(sender)
    if reaction is None:
        logger.info("Пропуск %s message=%s", sender_display(sender), message.id)
        return

    try:
        await add_like(message, reaction)
        logger.info(
            "Реакция %s (%s) message=%s chat=%s from=%s",
            reaction_debug(reaction),
            reason,
            message.id,
            message.chat_id,
            sender_display(sender),
        )
    except ReactionInvalidError:
        logger.error("Реакция %s недоступна в этом чате", reaction_debug(reaction))
    except ChatWriteForbiddenError:
        logger.warning("Нет права ставить реакции в чате %s", message.chat_id)
    except RPCError as error:
        logger.error(
            "Telegram отклонил реакцию message=%s chat=%s reaction=%s: %s",
            message.id,
            getattr(message, "chat_id", None),
            reaction_debug(reaction),
            error,
        )
    except Exception:
        logger.exception("Не удалось поставить реакцию message=%s chat=%s", message.id, message.chat_id)


async def list_dialogs() -> None:
    logger.info("Список доступных диалогов (скопируйте нужные id в CHAT_IDS):")
    async for dialog in client.iter_dialogs():
        username = f" @{dialog.entity.username}" if getattr(dialog.entity, "username", None) else ""
        print(f"{dialog.id}\t{dialog.title}{username}")


async def list_recent_users(entities) -> None:
    logger.info("Авторы последних сообщений в указанных чатах:")
    seen: set[int] = set()
    for entity in entities:
        async for message in client.iter_messages(entity, limit=80):
            sender = await message.get_sender()
            user_id = getattr(sender, "id", None)
            if user_id is None or user_id in seen:
                continue
            seen.add(user_id)
            print(sender_display(sender))


async def backfill(entities) -> None:
    if BACKFILL_LIMIT <= 0:
        return

    logger.info("Ставлю лайки на последние %s сообщений в каждом чате", BACKFILL_LIMIT)
    for entity in entities:
        count = 0
        async for message in client.iter_messages(entity, limit=BACKFILL_LIMIT):
            await like_if_needed(message, reason="backfill")
            count += 1
        logger.info("Backfill завершён: chat=%s messages=%s", getattr(entity, "id", entity), count)


def likes_status_message() -> str:
    return f"Лайки сейчас {like_state.status_text()}.\nНажмите /like ещё раз, чтобы переключить."


async def start_control_bot(owner_id: int):
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN не задан. Создайте бота в @BotFather и добавьте токен в .env")
        return None

    bot = TelegramClient(str(SESSION_DIR / "control_bot"), API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    me_bot = await bot.get_me()
    logger.info("Контрольный бот запущен: @%s", me_bot.username)

    @bot.on(events.NewMessage(incoming=True, pattern=r"^/start(?:@\w+)?\s*$"))
    async def on_start(event: events.NewMessage.Event) -> None:
        if not event.is_private:
            return
        if event.sender_id != owner_id:
            await event.reply("Нет доступа.")
            return
        await event.reply(likes_status_message())

    @bot.on(events.NewMessage(incoming=True, pattern=r"^/like(?:@\w+)?\s*$"))
    async def on_like(event: events.NewMessage.Event) -> None:
        if not event.is_private:
            return
        if event.sender_id != owner_id:
            await event.reply("Нет доступа.")
            return

        enabled = like_state.toggle()
        if enabled:
            await event.reply("Лайки включены ❤")
            logger.info("Лайки включены командой /like")
        else:
            await event.reply("Лайки выключены")
            logger.info("Лайки выключены командой /like")

    return bot


async def resolve_chats() -> list:
    entities = []
    for chat in CHAT_IDS:
        try:
            entity = await client.get_entity(chat)
        except Exception:
            logger.exception("Не удалось найти чат %s", chat)
            sys.exit(1)

        title = getattr(entity, "title", None) or getattr(entity, "username", None) or chat
        logger.info("Чат подключён: %s (id=%s)", title, getattr(entity, "id", chat))
        entities.append(entity)
    return entities


async def main() -> None:
    await client.start()
    me = await client.get_me()
    logger.info("LikeBot %s", BOT_VERSION)
    logger.info("Вход выполнен: %s (id=%s)", me.username or me.first_name, me.id)

    if LIST_CHATS:
        await list_dialogs()
        return

    if not CHAT_IDS:
        logger.error("CHAT_IDS пуст. Укажите id чатов в .env или запустите с LIST_CHATS=true")
        sys.exit(1)

    entities = await resolve_chats()
    if LIST_USERS:
        await list_recent_users(entities)
        return

    logger.info(
        "Реакция: %s | какашки: %s | пропуск: %s | poop: %s | свои: %s | лайки: %s",
        reaction_debug(REACTION),
        reaction_debug(POOP_REACTION),
        SKIP_USERS or "-",
        POOP_USERS or "-",
        LIKE_OWN_MESSAGES,
        like_state.status_text(),
    )

    @client.on(events.NewMessage(chats=entities))
    async def on_new_message(event: events.NewMessage.Event) -> None:
        await like_if_needed(event.message, reason="new")

    await start_control_bot(me.id)
    await backfill(entities)
    logger.info("Бот слушает новые сообщения")
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
