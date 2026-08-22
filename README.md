# LikeBot

Userbot для Telegram: входит в ваш аккаунт и ставит реакцию на каждое новое сообщение в выбранных чатах. Запускается в Docker.

Обычный бот из [@BotFather](https://t.me/BotFather) не может ставить лайки **от вашего имени**. Поэтому здесь используется клиент [Telethon](https://docs.telethon.dev): бот работает как вы, а не как отдельный бот-аккаунт.

## Что нужно заранее

1. Аккаунт Telegram.
2. `api_id` и `api_hash` с [my.telegram.org](https://my.telegram.org) → **API development tools**.
3. Docker и Docker Compose.
4. Id чатов, в которых нужно ставить лайки.

## Быстрый старт

1. Скопируйте пример окружения и заполните свои значения:

```bash
cp .env.example .env
```

2. В `.env` укажите ключи API и чаты:

```env
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash
CHAT_IDS=-1001234567890,-1009876543210
REACTION=👍
```

3. Первый запуск должен быть интерактивным: Telegram пришлёт код подтверждения.

```bash
docker compose run --rm likebot
```

Введите номер телефона в международном формате (`+79001234567`), код из Telegram и пароль 2FA, если он включён. После успешного входа сессия сохранится в `./data`.

4. Дальше запускайте в фоне:

```bash
docker compose up -d --build
```

Логи:

```bash
docker compose logs -f likebot
```

Остановка:

```bash
docker compose down
```

## Как узнать id чата

Самый простой способ — один раз запустить бота в режиме списка диалогов.

В `.env`:

```env
LIST_CHATS=true
```

Затем:

```bash
docker compose run --rm likebot
```

В консоли появятся строки вида:

```text
-1001234567890    Название чата
777000            Telegram
```

Скопируйте нужные id в `CHAT_IDS` через запятую и верните `LIST_CHATS=false`.

Также можно указать публичный `@username` чата.

Для супергрупп и каналов id почти всегда начинается с `-100`.

## Переменные окружения

| Переменная | Обязательная | Описание |
|---|---|---|
| `TELEGRAM_API_ID` | да | Числовой api_id с my.telegram.org |
| `TELEGRAM_API_HASH` | да | api_hash с my.telegram.org |
| `CHAT_IDS` | да | Чаты через запятую: `-100...` или `@username` |
| `REACTION` | нет | Эмодзи реакции. По умолчанию `👍` |
| `LIKE_OWN_MESSAGES` | нет | `true`, если нужно лайкать и свои сообщения |
| `DELAY_SECONDS` | нет | Пауза перед реакцией. По умолчанию `0.4` |
| `BACKFILL_LIMIT` | нет | Сколько последних сообщений лайкнуть при старте. `0` — только новые |
| `SESSION_NAME` | нет | Имя файла сессии в `./data` |
| `LIST_CHATS` | нет | `true` — вывести список чатов и выйти |
| `TELEGRAM_SESSION_STRING` | нет | Готовая StringSession вместо файла |
| `LOG_LEVEL` | нет | Уровень логов: `INFO`, `DEBUG` |

Файл `.env` не коммитится. В репозиторий кладите только `.env.example`.

## Структура

```text
.
├── bot.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── .gitignore
```

Сессия Telegram монтируется в `./data`. Не публикуйте эту папку и не коммитьте `*.session`: по файлу сессии можно войти в аккаунт без кода из SMS.

## Запуск без Docker

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

Сессию по умолчанию бот пишет в `/data`. Локально задайте другую папку:

```env
SESSION_DIR=./data
```

## Безопасность

- Храните `.env` и `./data` только у себя.
- Не включайте `*.session` и `.env` в git — они уже в `.gitignore`.
- Если сессия утекла, завершите все сеансы в Telegram: **Настройки → Устройства**.
- Ставьте умеренный `DELAY_SECONDS`. Слишком частые реакции Telegram может временно ограничить.

## Как это работает

Бот авторизуется вашим аккаунтом, подписывается на новые сообщения в чатах из `CHAT_IDS` и ставит выбранную реакцию. Свои сообщения по умолчанию пропускаются. Если задать `BACKFILL_LIMIT`, при старте он также пройдётся по последним сообщениям в каждом чате.
