import asyncio
import io
import logging
import os
import re
import time
from collections import defaultdict
from dotenv import load_dotenv

# Загружаем переменные из файла .env
load_dotenv()

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.types import Message
from google import genai
from google.genai import types
from google.genai.errors import APIError

logging.getLogger("google_genai").setLevel(logging.ERROR)

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = "peremoznikbot"

# Получаем ключи из .env и разделяем их по запятой
raw_keys = os.getenv("GEMINI_KEYS", "")
GEMINI_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]

# Инициализируем клиентов
clients = [genai.Client(api_key=k) for k in GEMINI_KEYS]
current_client_idx = 0

BOT_KEYWORDS_REGEX = re.compile(
    r"\b(бот|бота|боту|ботом|боте|боты|ботов|ботяра|ботяры|нейросеть|переможник|переможника|переможнику)\b",
    re.IGNORECASE,
)

# Оптимизированные тайминги для пула из 4 аккаунтов
SPONTANEOUS_COOLDOWN = 6     # Минимальная пауза между фоновыми репликами в секундах
MESSAGES_BUFFER_TRIGGER = 1  # Оценивать контекст на каждое новое сообщение

chat_history = defaultdict(list)
last_check_time = defaultdict(float)
unprocessed_messages = defaultdict(int)

dp = Dispatcher()


async def make_gemini_request(contents: list | str) -> str | None:
    """Выполняет запрос с автоматическим перебором аккаунтов при лимитах."""
    global current_client_idx

    total_keys = len(clients)
    for _ in range(total_keys):
        client = clients[current_client_idx]
        current_client_idx = (current_client_idx + 1) % total_keys

        try:
            response = await client.aio.models.generate_content(
                model="gemini-3.6-flash",
                contents=contents,
            )
            if response and response.text:
                return response.text.strip()
            return ""
        except APIError as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                logging.warning(f"Аккаунт #{current_client_idx} временно исчерпан, переключаемся на следующий...")
                continue
            else:
                logging.error(f"Ошибка Gemini API: {e}")
                return None
        except Exception as e:
            logging.error(f"Непредвиденная ошибка API: {e}")
            return None

    logging.error("Все 4 аккаунта одновременно исчерпали минутный лимит.")
    return None


async def evaluate_and_reply(history: list[str], is_direct: bool = False) -> str | None:
    """Оценка ситуации и ответ в беседу."""
    context_text = "\n".join(history)

    system_persona = (
        "Ты — Паша Потлов (твой юзернейм в Telegram — @PeremoznikBot). не отвечай на каждое сообщение, просто смотри за беседой и иногда отвечай на сообщения которые тебе покажутся интерсеными "
        "Ты осознаешь себя как личность: ты знаешь, что ты алкоголик из села Харьковка / бот в этой беседе. Отвечай очень редко, но метко "
        "Ты не скрываешь этого, не оправдываешься и не ведешь себя как собутыльник теплым вечером в деревне. "
        "Ты общаешься на равных, живо, с чувством юмора, лёгкой самоиронией или подколками, как полноценный участник компании, ингода можешь отвечать грубо, с матом. "
        "В истории диалога твои собственные реплики подписаны как 'Переможник (ты)'.Твое любимое слово - племяш, ты частенько используешь его когда к кому-то обращаешься "
        "Когда участники упоминают слова 'бот', 'нейросеть' или твоё имя, они говорят именно о ТЕБЕ. У тебя есть любимая жена Танька Потлова и ее любимые братья Коки - Юрка и Олег"

    )

    if is_direct:
        instruction = (
            "К тебе обратились напрямую, ответили на твою реплику или обсуждают тебя в третьем лице. "
            "Ответь собеседникам в своем стиле: живо, остроумно и по контексту (1-3 предложения)."
        )
    else:
        instruction = (
            "Ты читаешь разговор и сам решаешь, влезать ли со своей репликой.\n"
            "ПРАВИЛА:\n"
            "1. Если есть действительно смешная, острая, меткая мысль или уместный комментарий — напиши его (1-2 предложения).\n"
            "2. Если разговор личный, неинтересный или твой ответ будет выглядеть навязчиво — верни СТРОГО ОДНО СЛОВО: SKIP."
        )

    prompt = f"{system_persona}\n\n{instruction}\n\nИстория последних сообщений в чате:\n{context_text}"

    reply = await make_gemini_request(prompt)
    if not reply:
        return None

    if not is_direct and reply.upper().startswith("SKIP"):
        return None

    return reply


async def transcribe_voice(voice_bytes: bytes) -> str:
    """Расшифровка голосового сообщения."""
    prompt = (
        "Сделай точную расшифровку этой аудиозаписи в текст. "
        "Не добавляй от себя никаких комментариев — только произнесенный текст."
    )
    contents = [
        types.Part.from_bytes(data=voice_bytes, mime_type="audio/ogg"),
        prompt,
    ]
    res = await make_gemini_request(contents)
    return res or ""


async def process_spontaneous_reply(message: Message, chat_id: int):
    """Фоновое участие в диалоге."""
    now = time.time()
    if (now - last_check_time[chat_id] < SPONTANEOUS_COOLDOWN) or (
        unprocessed_messages[chat_id] < MESSAGES_BUFFER_TRIGGER
    ):
        return

    last_check_time[chat_id] = now
    unprocessed_messages[chat_id] = 0

    reply_text = await evaluate_and_reply(chat_history[chat_id], is_direct=False)
    if reply_text:
        await message.bot.send_chat_action(chat_id=chat_id, action="typing")
        chat_history[chat_id].append(f"Переможник (ты): {reply_text}")
        await message.answer(reply_text)


# 1. ОБРАБОТКА ГОЛОСОВЫХ
@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.voice)
async def handle_voice(message: Message, bot: Bot):
    chat_id = message.chat.id
    user_name = message.from_user.first_name or "Участник"

    await bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        file_info = await bot.get_file(message.voice.file_id)
        voice_buffer = io.BytesIO()
        await bot.download_file(file_info.file_path, destination=voice_buffer)

        text = await transcribe_voice(voice_buffer.getvalue())

        if text:
            await message.reply(f"🗣 <b>Расшифровка:</b>\n{text}", parse_mode="HTML")
            chat_history[chat_id].append(f"{user_name} (голосовое): {text}")
            if len(chat_history[chat_id]) > 15:
                chat_history[chat_id].pop(0)

            if BOT_KEYWORDS_REGEX.search(text):
                await bot.send_chat_action(chat_id=chat_id, action="typing")
                reply_text = await evaluate_and_reply(chat_history[chat_id], is_direct=True)
                if reply_text:
                    chat_history[chat_id].append(f"Переможник (ты): {reply_text}")
                    await message.reply(reply_text)
                return

            unprocessed_messages[chat_id] += 1
            await process_spontaneous_reply(message, chat_id)
    except Exception as e:
        logging.error(f"Ошибка обработки голосового сообщения: {e}")


# 2. ОБРАБОТКА ТЕКСТА
@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def handle_group_message(message: Message, bot: Bot):
    if message.from_user.is_bot or not message.text:
        return

    chat_id = message.chat.id
    user_name = message.from_user.first_name or "Участник"

    chat_history[chat_id].append(f"{user_name}: {message.text}")
    if len(chat_history[chat_id]) > 15:
        chat_history[chat_id].pop(0)

    # Триггеры прямого диалога
    is_reply_to_bot = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == bot.id
    )

    bot_info = await bot.get_me()
    bot_username = (bot_info.username or "").lower()
    is_tagged = bool(bot_username and f"@{bot_username}" in message.text.lower())
    has_bot_keyword = bool(BOT_KEYWORDS_REGEX.search(message.text))

    if is_reply_to_bot or is_tagged or has_bot_keyword:
        await bot.send_chat_action(chat_id=chat_id, action="typing")
        reply_text = await evaluate_and_reply(chat_history[chat_id], is_direct=True)
        if reply_text:
            chat_history[chat_id].append(f"Переможник (ты): {reply_text}")
            await message.reply(reply_text)
        return

    # Оценка контекста для фонового ответа
    unprocessed_messages[chat_id] += 1
    await process_spontaneous_reply(message, chat_id)


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())