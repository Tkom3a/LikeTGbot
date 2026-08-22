import asyncio
import inspect
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import ChatWriteForbiddenError, FloodWaitError, ReactionInvalidError
from telethon.sessions import StringSession
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

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
REACTION = os.getenv("REACTION", "👍").strip() or "👍"
SESSION_NAME = os.getenv("SESSION_NAME", "likebot").strip() or "likebot"
SESSION_DIR = Path(os.getenv("SESSION_DIR", "/data"))
SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING", "").strip()
LIKE_OWN_MESSAGES = env_bool("LIKE_OWN_MESSAGES", False)
LIST_CHATS = env_bool("LIST_CHATS", False)
BACKFILL_LIMIT = int(os.getenv("BACKFILL_LIMIT", "0"))
DELAY_SECONDS = float(os.getenv("DELAY_SECONDS", "0.4"))


def build_client() -> TelegramClient:
    if SESSION_STRING:
        return TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    session_path = SESSION_DIR / SESSION_NAME
    return TelegramClient(str(session_path), API_ID, API_HASH)


client = build_client()


def build_reaction_request(peer, msg_id: int) -> SendReactionRequest:
    emoji = ReactionEmoji(emoticon=REACTION)
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


async def send_reaction(message) -> None:
    await client(build_reaction_request(message.peer_id, message.id))


async def add_like(message) -> None:
    await asyncio.sleep(DELAY_SECONDS)
    try:
        await send_reaction(message)
    except FloodWaitError as error:
        logger.warning("FloodWait: жду %s сек.", error.seconds)
        await asyncio.sleep(error.seconds + 1)
        await send_reaction(message)


async def like_if_needed(message, *, reason: str) -> None:
    if getattr(message, "action", None):
        return
    if message.out and not LIKE_OWN_MESSAGES:
        return

    try:
        await add_like(message)
        chat = message.chat_id
        logger.info("Лайк поставлен (%s) message=%s chat=%s", reason, message.id, chat)
    except ReactionInvalidError:
        logger.error("Реакция %s недоступна в этом чате", REACTION)
    except ChatWriteForbiddenError:
        logger.warning("Нет права ставить реакции в чате %s", message.chat_id)
    except Exception:
        logger.exception("Не удалось лайкнуть message=%s chat=%s", message.id, message.chat_id)


async def list_dialogs() -> None:
    logger.info("Список доступных диалогов (скопируйте нужные id в CHAT_IDS):")
    async for dialog in client.iter_dialogs():
        username = f" @{dialog.entity.username}" if getattr(dialog.entity, "username", None) else ""
        print(f"{dialog.id}\t{dialog.title}{username}")


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
    logger.info("Вход выполнен: %s (id=%s)", me.username or me.first_name, me.id)

    if LIST_CHATS:
        await list_dialogs()
        return

    if not CHAT_IDS:
        logger.error("CHAT_IDS пуст. Укажите id чатов в .env или запустите с LIST_CHATS=true")
        sys.exit(1)

    entities = await resolve_chats()
    logger.info("Реакция: %s | свои сообщения: %s", REACTION, LIKE_OWN_MESSAGES)

    @client.on(events.NewMessage(chats=entities))
    async def on_new_message(event: events.NewMessage.Event) -> None:
        await like_if_needed(event.message, reason="new")

    await backfill(entities)
    logger.info("Бот слушает новые сообщения")
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
