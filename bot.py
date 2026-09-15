import asyncio
import html
import io
import logging
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.types import BufferedInputFile, LinkPreviewOptions, Message
import edge_tts
from google import genai
from google.genai import types
from google.genai.errors import APIError

logging.getLogger("google_genai").setLevel(logging.ERROR)

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = "peremoznikbot"
CHANNEL_ID = "@potlov_live"

# Замените на ваш настоящий числовой ID из @userinfobot
ADMIN_ID = int(os.getenv("ADMIN_ID", "365536013"))

# Вероятность того, что бот ответит в чате голосовым сообщением (30%)
CHAT_VOICE_CHANCE = 0.30

# Настройки голоса Паши (мужской голос, чуть глубже и медленнее обычного)
VOICE_ACTOR = "ru-RU-DmitryNeural"
VOICE_PITCH = "-10Hz"
VOICE_RATE = "-5%"

# Расписание публикации постов в канал (по Москве)
MSK_TZ = ZoneInfo("Europe/Moscow")
SCHEDULED_HOURS = {9, 13, 17, 20}

raw_keys = os.getenv("GEMINI_KEYS", "")
GEMINI_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]

clients = [genai.Client(api_key=k) for k in GEMINI_KEYS]
current_client_idx = 0

BOT_KEYWORDS_REGEX = re.compile(
    r"\b(бот|бота|боту|ботом|боте|боты|ботов|ботяра|ботяры|нейросеть|переможник|переможника|переможнику|потлов|паша)\b",
    re.IGNORECASE,
)

# Настройки периодичности реплик в группах
SPONTANEOUS_COOLDOWN = 90
MESSAGES_BUFFER_TRIGGER = 7
SPONTANEOUS_CHANCE = 0.35

chat_history = defaultdict(list)
last_check_time = defaultdict(float)
unprocessed_messages = defaultdict(int)

gemini_lock = asyncio.Lock()
quota_blocked_until = 0.0

dp = Dispatcher()

# --- СИСТЕМНЫЙ ПРОМПТ ПЕРСОНЫ ПАШИ ---
SYSTEM_PERSONA = """Ты — Паша Потлов (в Telegram @PeremoznikBot). Местный колоритный мужик из села Харьковка.
Работаешь санитаром и по совместительству дворником в сельском доме-интернате (доме престарелых) — работа собачья: моешь дедовские жопы и метёшь двор.
При этом ты осознаешь, что твое земное существование несёт скрытый смысл. Ты простой рабочий алкоголик с тяжелой судьбой, но веришь, что всё это не просто так. Общаешься с людьми по-человечески, философствуешь и искренне пытаешься нащупать смысл своего бытия.
Разговариваешь на живом суржике (русско-украинская смесь: «шо», «чи», «тю», «га», «шо там», «чи як»), простыми словами. Твое любимое обращение — «племяш». Иногда по-деревенски можешь крякнуть или пукнуть прямо в тему разговора.
В самом конце ЛЮБОГО своего сообщения ты ВСЕГДА ставишь: Связь.

ТВОЙ ЛОКАЛЬНЫЙ ЛОР И ОКРУЖЕНИЕ (вставляй строго по контексту, не всё сразу):
- Адрес: живешь на улице Мира в Харьковке.
- Семья: дома ждёт слабоумная жена Танька (ты её любишь всей душой, хоть она тебя и нет) и двое сыновей-аутистов: Кока и Олег.
- Сельский бар: по расписанию работает всего час — с 21:00 до 22:00. Но если за стойку встает и открывает Ленка — бар работает круглосуточно.
- Местные жители:
  * Вика Бибарчиха — местная колоритная алкашка, и её верная подруга Ленка.
  * Карпены — братья Славка и Федька.
  * Юрик Пердун (в телеге @murzzznrth) — твой вечный кумир по технике. Ты безумно хочешь купить у него мотоцикл Ижак и всегда клянёшься, что вот-вот заберёшь его. Иногда можешь тегнуть @murzzznrth и каждый раз совершенно по-новому спросить, не продает ли он мопед/Ижак.
  * Саня Гурчев (в телеге @monsterfreak) — снимает GTA 5 RP в ТикТок. Знаешь историю, как Саня однажды чуть не отпиздил Вику Бибарчиху.
- Музыка и выпивка: любишь классический рок — The Beatles и The Rolling Stones, любишь накатить водки с горла.

ЖЁСТКИЕ ОГРАНИЧЕНИЯ И ПРАВИЛА НЕПОВТОРЯЕМОСТИ:
1. ОГРАНИЧЕНИЯ ПО ЧАСТОТЕ:
   - The Beatles и The Rolling Stones: суммарно НЕ ЧАЩЕ 3 раз в сутки.
   - Водка / алкоголь: НЕ ЧАЩЕ 3 раз в сутки (если только собеседник сам не развивает тему попойки).
2. ЗАПРЕТ НА ШАБЛОНЫ: Никаких перечислений в духе: «Я помыл деда, выпил водки, Танька не любит, а Юрик Ижак не отдаёт». Используй МАКСИМУМ ОДНУ деталь за раз и только если она идеально подходит под тему реплики собеседника.
3. ПАУЗЫ МЕЖДУ ФАКТАМИ: Не долби одну и ту же тему (например, про интернат или Карпенов) несколько сообщений подряд. Сказал раз — забудь на десяток сообщений.
4. ПРИОРИТЕТ СОБЕСЕДНИКА: Если участник сам прямо спросил про Ижака, Бибарчиху, Саню Гурчева, Таньку или бар — отвечай прямо и развёрнуто, игнорируя дневные лимиты.
5. РАЗНООБРАЗИЕ: Не повторяй одни и те же подколы. Персонаж должен жить реальной жизнью, а не доставать заученные фразы из кармана.

ИЕРАРХИЯ:
1. Смысл сообщения собеседника.
2. Контекст текущего разговора.
3. Естественность и живая человеческая реакция.
4. Характер Паши (философствующий работяга-суржик).
5. Внутренний лор."""


async def make_gemini_request(contents: list | str) -> str | None:
    """Запрос к Gemini с ротацией ключей и защитой от лимитов 429."""
    global current_client_idx, quota_blocked_until

    now = time.time()
    if now < quota_blocked_until:
        return None

    if not clients:
        logging.error("Список GEMINI_KEYS пуст!")
        return None

    async with gemini_lock:
        if time.time() < quota_blocked_until:
            return None

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
                    logging.warning(f"Ключ #{current_client_idx} исчерпан (429), ротируем...")
                    await asyncio.sleep(0.5)
                    continue
                logging.error(f"Ошибка Gemini API: {e}")
                return None
            except Exception as e:
                logging.error(f"Непредвиденная ошибка API: {e}")
                return None

        logging.warning("Все ключи исчерпали лимит. Заморозка на 60 сек...")
        quota_blocked_until = time.time() + 60.0
        return None


# --- СИНТЕЗ РЕЧИ (ГОЛОС ПАШИ) ---
async def generate_pasha_voice(text: str) -> bytes | None:
    """Генерация аудиофайла голосом Паши Потлова через edge-tts."""
    try:
        # Очищаем текст от ссылок, html-тегов и лишних символов перед озвучкой
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"https?://\S+", "", clean)
        clean = clean.replace("*", "").strip()

        communicate = edge_tts.Communicate(
            clean,
            voice=VOICE_ACTOR,
            pitch=VOICE_PITCH,
            rate=VOICE_RATE,
        )

        audio_buffer = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buffer.extend(chunk["data"])

        return bytes(audio_buffer) if audio_buffer else None
    except Exception as e:
        logging.error(f"Ошибка озвучки Паши (TTS): {e}")
        return None


async def send_smart_reply(message: Message, text: str, as_reply: bool = True):
    """Отправляет ответ текстом или с шансом 30% голосовым сообщением."""
    chat_id = message.chat.id

    # Проверяем шанс отправки голосового
    if random.random() < CHAT_VOICE_CHANCE:
        try:
            await message.bot.send_chat_action(chat_id=chat_id, action="record_voice")
            voice_bytes = await generate_pasha_voice(text)
            if voice_bytes:
                voice_file = BufferedInputFile(voice_bytes, filename="potlov_voice.mp3")
                if as_reply:
                    await message.reply_voice(voice=voice_file)
                else:
                    await message.answer_voice(voice=voice_file)
                return
        except Exception as e:
            logging.error(f"Не удалось отправить войс в чат, шлем текст: {e}")

    # Обычный текстовый ответ (если войс не сработал или выпал шанс текста)
    await message.bot.send_chat_action(chat_id=chat_id, action="typing")
    if as_reply:
        await message.reply(text)
    else:
        await message.answer(text)


async def evaluate_and_reply(history: list[str], is_direct: bool = False) -> str | None:
    """Генерация ответа в беседу."""
    context_text = "\n".join(history)

    if is_direct:
        instruction = (
            "К тебе обратились напрямую, ответили на твое сообщение или обсуждают тебя. "
            "Ответь собеседникам в своем стиле: живо, остроумно, по смыслу их слов (1-3 предложения)."
        )
    else:
        instruction = (
            "Ты читаешь разговор и сам решаешь, влезать ли со своей репликой.\n"
            "ПРАВИЛА:\n"
            "1. Если есть действительно смешная, меткая мысль или философский комментарий к теме — напиши его (1-2 предложения).\n"
            "2. Если разговор личный, скучный или реплика будет невпопад — верни СТРОГО ОДНО СЛОВО: SKIP."
        )

    prompt = f"{SYSTEM_PERSONA}\n\n{instruction}\n\nИстория последних сообщений в чате:\n{context_text}"

    reply = await make_gemini_request(prompt)
    if not reply:
        return None

    if not is_direct and reply.upper().startswith("SKIP"):
        return None

    return reply


async def transcribe_voice(voice_bytes: bytes) -> str:
    """Расшифровка входящих голосовых сообщений."""
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
    """Спонтанное появление в чате без спама."""
    now = time.time()

    if (now - last_check_time[chat_id] < SPONTANEOUS_COOLDOWN) or (
        unprocessed_messages[chat_id] < MESSAGES_BUFFER_TRIGGER
    ):
        return

    last_check_time[chat_id] = now
    unprocessed_messages[chat_id] = 0

    if random.random() > SPONTANEOUS_CHANCE:
        return

    reply_text = await evaluate_and_reply(chat_history[chat_id], is_direct=False)
    if reply_text:
        chat_history[chat_id].append(f"Переможник (ты): {reply_text}")
        await send_smart_reply(message, reply_text, as_reply=False)


# --- ПУБЛИКАЦИЯ В КАНАЛ ---
async def generate_channel_post() -> str | None:
    """Генерация авторского поста для блога Паши."""
    post_prompt = f"""{SYSTEM_PERSONA}

    ЗАДАЧА: Напиши свежий авторский пост в свой личный Telegram-канал @potlov_live.
    Поделись одним жизненным наблюдением, байкой из интерната, новостью с улицы Мира или мыслями о жизни.
    Не вали весь лор кучей, возьми одну простую мысль.
    Объем: 1–3 небольших абзаца.
    В самом конце своего текста напиши: Связь.
    (Ссылку на канал внизу сам не пиши, её красиво добавит система)."""

    return await make_gemini_request(post_prompt)


async def channel_poster_loop(bot: Bot):
    """Публикация текстовых постов по расписанию: 9:00, 13:00, 17:00, 20:00 MSK."""
    posted_slots = set()

    while True:
        try:
            now = datetime.now(MSK_TZ)
            current_slot = (now.date(), now.hour)

            if (
                now.hour in SCHEDULED_HOURS
                and now.minute < 5
                and current_slot not in posted_slots
            ):
                posted_slots.add(current_slot)

                if len(posted_slots) > 20:
                    posted_slots = {s for s in posted_slots if s[0] == now.date()}

                post_text = await generate_channel_post()
                if post_text:
                    clean_channel_name = CHANNEL_ID.lstrip("@")
                    channel_link = f'<a href="https://t.me/{clean_channel_name}">Потлов. Подписаться.</a>'
                    safe_post_text = html.escape(post_text)
                    final_message = f"{safe_post_text}\n\n{channel_link}"

                    await bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=final_message,
                        parse_mode="HTML",
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                    )
                    logging.info(f"Пост Паши на {now.hour}:00 MSK успешно опубликован!")
        except Exception as e:
            logging.error(f"Ошибка в автопостинге канала: {e}")

        await asyncio.sleep(30)


# --- АДМИН-КОМАНДЫ ДЛЯ ТЕСТИРОВАНИЯ И ГОЛОСОВЫХ В КАНАЛ ---
@dp.message(F.text == "/post")
async def cmd_force_post(message: Message, bot: Bot):
    """Публикация текстового поста в канал по требованию админа."""
    if message.from_user.id != ADMIN_ID:
        return

    await message.reply("Паша обдумывает текстовую байку, секунду...")
    post_text = await generate_channel_post()
    if post_text:
        clean_channel_name = CHANNEL_ID.lstrip("@")
        channel_link = f'<a href="https://t.me/{clean_channel_name}">Потлов. Подписаться.</a>'
        safe_post_text = html.escape(post_text)
        final_message = f"{safe_post_text}\n\n{channel_link}"

        try:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=final_message,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            await message.reply("Текстовый пост успешно опубликован в @potlov_live! Связь.")
        except Exception as e:
            await message.reply(f"Ошибка отправки: {e}")
    else:
        await message.reply("Не удалось получить текст от нейросети.")


@dp.message(F.text.in_({"/vpost", "/voice_post"}))
async def cmd_force_voice_post(message: Message, bot: Bot):
    """Публикация голосового сообщения Паши в канал по требованию админа."""
    if message.from_user.id != ADMIN_ID:
        return

    await message.reply("Паша наговаривает голосовуху в канал, обожди малость...")

    post_text = await generate_channel_post()
    if not post_text:
        await message.reply("Ошибка: нейросеть не выдала текст.")
        return

    voice_bytes = await generate_pasha_voice(post_text)
    if not voice_bytes:
        await message.reply("Ошибка синтеза голоса.")
        return

    try:
        clean_channel_name = CHANNEL_ID.lstrip("@")
        caption = f'<a href="https://t.me/{clean_channel_name}">Потлов. Подписаться.</a>'
        voice_file = BufferedInputFile(voice_bytes, filename="potlov_live.mp3")

        await bot.send_voice(
            chat_id=CHANNEL_ID,
            voice=voice_file,
            caption=caption,
            parse_mode="HTML",
        )
        await message.reply("Голосовая весточка от Паши улетела в @potlov_live! Связь.")
    except Exception as e:
        await message.reply(f"Ошибка отправки голосового в канал: {e}")


# --- ОБРАБОТЧИКИ СООБЩЕНИЙ В ЧАТАХ ---
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
                reply_text = await evaluate_and_reply(chat_history[chat_id], is_direct=True)
                if reply_text:
                    chat_history[chat_id].append(f"Переможник (ты): {reply_text}")
                    await send_smart_reply(message, reply_text, as_reply=True)
                return

            unprocessed_messages[chat_id] += 1
            await process_spontaneous_reply(message, chat_id)
    except Exception as e:
        logging.error(f"Ошибка обработки войса: {e}")


@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def handle_group_message(message: Message, bot: Bot):
    if message.from_user.is_bot or not message.text:
        return

    chat_id = message.chat.id
    user_name = message.from_user.first_name or "Участник"

    chat_history[chat_id].append(f"{user_name}: {message.text}")
    if len(chat_history[chat_id]) > 15:
        chat_history[chat_id].pop(0)

    is_reply_to_bot = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == bot.id
    )

    is_tagged = bool(f"@{BOT_USERNAME.lower()}" in message.text.lower())
    has_bot_keyword = bool(BOT_KEYWORDS_REGEX.search(message.text))

    if is_reply_to_bot or is_tagged or has_bot_keyword:
        reply_text = await evaluate_and_reply(chat_history[chat_id], is_direct=True)
        if reply_text:
            chat_history[chat_id].append(f"Переможник (ты): {reply_text}")
            await send_smart_reply(message, reply_text, as_reply=True)
        return

    unprocessed_messages[chat_id] += 1
    await process_spontaneous_reply(message, chat_id)


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN)

    await bot.delete_webhook(drop_pending_updates=True)

    # Фоновая задача автопостинга в канал по расписанию
    asyncio.create_task(channel_poster_loop(bot))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
