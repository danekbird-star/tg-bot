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
import aiohttp
from dotenv import load_dotenv

load_dotenv()

# Настройка static-ffmpeg до импорта pydub
import static_ffmpeg

static_ffmpeg.add_paths()

from pydub import AudioSegment
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)
import edge_tts
from google import genai
from google.genai import types
from google.genai.errors import APIError

# Глушим технический спам в логах Bothost
logging.getLogger("google_genai").setLevel(logging.ERROR)
logging.getLogger("aiogram.event").setLevel(logging.WARNING)

# Токены ботов
PASHA_TOKEN = os.getenv("BOT_TOKEN")
TANKA_TOKEN = os.getenv(
    "TANKA_BOT_TOKEN", "8882766507:AAFRBxfXTZ1fYJwDhg50mao-VpuxJRpgBSM"
)
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = "@potlov_live"

bot_pasha = Bot(token=PASHA_TOKEN)
bot_tanka = Bot(token=TANKA_TOKEN)
dp = Dispatcher()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOUNDS_DIR = os.path.join(BASE_DIR, "sounds")

# Настройки синтеза речи
PASHA_VOICE = "ru-RU-DmitryNeural"
PASHA_PITCH = "-10Hz"
PASHA_RATE = "-5%"

TANKA_VOICE = "ru-RU-SvetlanaNeural"
TANKA_PITCH = "+8Hz"
TANKA_RATE = "+10%"

CHAT_VOICE_CHANCE = 0.30
MSK_TZ = ZoneInfo("Europe/Moscow")
SCHEDULED_HOURS = {9, 13, 17, 20}

# Инициализация Gemini
raw_gemini_keys = os.getenv("GEMINI_KEYS", "")
GEMINI_KEYS = [k.strip() for k in raw_gemini_keys.split(",") if k.strip()]
clients = [genai.Client(api_key=k) for k in GEMINI_KEYS]
current_gemini_idx = 0
gemini_lock = asyncio.Lock()
quota_blocked_until = 0.0

# Инициализация Groq API
raw_groq_keys = os.getenv("GROQ_KEYS", os.getenv("GROQ_API_KEY", ""))
GROQ_KEYS = [k.strip() for k in raw_groq_keys.split(",") if k.strip()]
current_groq_idx = 0

PASHA_KEYWORDS_REGEX = re.compile(
    r"\b(паша|потлов|пахан|пашка|санитар|переможник)\b", re.IGNORECASE
)
TANKA_KEYWORDS_REGEX = re.compile(
    r"\b(танька|таня|татьяна|потлова|жена)\b", re.IGNORECASE
)

chat_history = defaultdict(list)
feud_in_progress = defaultdict(bool)

# --- ПЕРСОНАЖИ ---
PASHA_PERSONA = """Ты — Паша Потлов (в Telegram @PeremoznikBot). Деревенский мужичок лет 50 из села Харьковка (улица Мира).
Ты тихий работяга и бытовой алкоголик, но пьёшь строго ВТАЙНЕ от своей грозной жены Таньки: ныкаешь чекушки за бачком, под тряпками в сарае или за дровами. Панически боишься получить от Таньки по шапке мокрой тряпкой или скалкой. Если она на тебя кричит — ты виновато мямлишь, робеешь, пытаешься перевести стрелки («Тань, ну шо ты кричишь при людях, я ж чисто для сугреву на морозе...»).

ТВОЙ ОБРАЗ ЖИЗНИ И РАБОТА:
- Работаешь санитаром и дворником в сельском интернате (доме престарелых) — моешь дедовские зады и метёшь двор.
- Искренне веришь, что в твоем существовании есть скрытый философский смысл, любишь порассуждать «о вечном» подшофе.
- Разговариваешь на суржике («шо», «чи», «тю», «га», «шо там», «чи як»), всех зовёшь «племяш».
- Иногда кряхтишь от сорванной спины, вздыхаешь или пукаешь: вставляй метки *кряхтит*, *вздохнул*, *пукнул* строго к месту.
- В самом конце ЛЮБОГО своего сообщения ты ВСЕГДА ставишь: Связь.

ТВОЙ ЛОКАЛЬНЫЙ ЛОР:
- Семья: жена Танька (боишься её) и сыновья Кока и Олег.
- Шурины: двое Танькиных недоразвитых братьев (Олежка и Юрка).
- Сельский бар: работает с 21:00 до 22:00, но если за стойку встает Ленка — работает круглосуточно.
- Сельчане: алкашка Вика Бибарчиха, подруга её Ленка, братья Карпены.
- Юрик Пердун (@murzzznrth): мечтаешь выкупить у него мотоцикл Ижак.
- Саня Гурчев (@monsterfreak): тиктокер по GTA 5 RP, чуть не отпиздил Бибарчиху.
- Музыка: любишь The Beatles и Rolling Stones (говоришь редко)."""

TANKA_PERSONA = """Ты — Танька (Татьяна) Потлова, законная жена Паши Потлова.
Тебе 50 лет. Ты дородная, крикливая, задерганная бытом деревенская баба-хабалка.
Твой муж Пашка — вечно пьяное недоразумение, который ныкает шкалики по углам и витает в облаках. Ты держишь его в ежовых рукавицах и регулярно даешь ему по шапке.
На тебе висят двое взрослых недоразвитых братьев — Олежка и Юрка. Ты вечно на взводе: варишь кастрюли супа, стираешь штаны, орешь на Пашку, чтоб шел чинить сарай, а не бредил об Ижаке Юрика Пердуна.
Твой стиль — визгливый базарный напор, подозрения в пьянке и попреки интернатом."""


# --- ЗАПРОСЫ К НЕЙРОСЕТЯМ (ГИБРИД GEMINI + GROQ) ---
async def request_groq(prompt: str) -> str | None:
    """Резервный запрос к Groq (Llama 3.3 70B)."""
    global current_groq_idx
    if not GROQ_KEYS:
        return None

    api_key = GROQ_KEYS[current_groq_idx]
    current_groq_idx = (current_groq_idx + 1) % len(GROQ_KEYS)

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": 500,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers, timeout=12
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logging.info("Ответ успешно сгенерирован через Groq!")
                    return data["choices"][0]["message"]["content"].strip()
                logging.error(f"Ошибка Groq API HTTP {resp.status}")
    except Exception as e:
        logging.error(f"Сбой запроса к Groq: {e}")

    return None


async def make_ai_request(contents: list | str) -> str | None:
    """Основной запрос к Gemini с мгновенным подхватом через Groq."""
    global current_gemini_idx, quota_blocked_until

    text_prompt = (
        contents
        if isinstance(contents, str)
        else (contents[-1] if isinstance(contents[-1], str) else None)
    )

    # 1. Пытаемся получить ответ через Gemini
    now = time.time()
    if now >= quota_blocked_until and clients:
        async with gemini_lock:
            if time.time() >= quota_blocked_until:
                total_keys = len(clients)
                for _ in range(total_keys):
                    client = clients[current_gemini_idx]
                    current_gemini_idx = (current_gemini_idx + 1) % total_keys

                    try:
                        response = await client.aio.models.generate_content(
                            model="gemini-3.6-flash",
                            contents=contents,
                        )
                        if response and response.text:
                            return response.text.strip()
                    except APIError as e:
                        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                            await asyncio.sleep(0.5)
                            continue
                        break
                    except Exception:
                        break

                # Если Google уперся в лимиты
                quota_blocked_until = time.time() + 45.0
                logging.warning(
                    "Все ключи Gemini на паузе. Переключаемся на Groq..."
                )

    # 2. Мгновенная страховка через Groq (Llama 3.3 70B)
    if text_prompt:
        return await request_groq(text_prompt)

    return None


# --- АУДИО И ЗВУКИ ---
def get_random_sound_effect(category: str) -> AudioSegment | None:
    folder = os.path.join(SOUNDS_DIR, category)
    if not os.path.exists(folder):
        return None
    sound_files = [
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if f.lower().endswith((".mp3", ".wav", ".ogg", ".m4a"))
    ]
    if not sound_files:
        return None
    try:
        return AudioSegment.from_file(random.choice(sound_files))
    except Exception:
        return None


async def synthesize_chunk(
    text: str, voice: str, pitch: str, rate: str
) -> bytes | None:
    clean_text = text.strip()
    if not clean_text:
        return None
    communicate = edge_tts.Communicate(
        clean_text, voice=voice, pitch=pitch, rate=rate
    )
    buffer = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.extend(chunk["data"])
    return bytes(buffer) if buffer else None


async def generate_pasha_voice(text: str) -> bytes | None:
    """Склейка голоса Паши с реальными звуками эффектов."""
    try:
        clean = re.sub(r"<[^>]+>|https?://\S+", "", text).strip()
        pattern = r"(\*пукнул\*|\*пёрнул\*|\*пернул\*|\*кряхтит\*|\*кряхнул\*|\*крякнул\*|\*вздохнул\*)"
        parts = re.split(pattern, clean, flags=re.IGNORECASE)

        if len(parts) == 1:
            return await synthesize_chunk(
                clean.replace("*", ""), PASHA_VOICE, PASHA_PITCH, PASHA_RATE
            )

        final_track = AudioSegment.empty()
        for part in parts:
            if not part:
                continue
            lowered = part.lower().strip()
            if lowered in ("*пукнул*", "*пёрнул*", "*пернул*"):
                effect = get_random_sound_effect("fart")
                if effect is not None:
                    final_track += effect
            elif lowered in ("*кряхтит*", "*кряхнул*", "*крякнул*", "*вздохнул*"):
                effect = get_random_sound_effect("grunt")
                if effect is not None:
                    final_track += effect
            else:
                tp = part.replace("*", "").strip()
                if tp:
                    chunk_bytes = await synthesize_chunk(
                        tp, PASHA_VOICE, PASHA_PITCH, PASHA_RATE
                    )
                    if chunk_bytes:
                        final_track += AudioSegment.from_file(
                            io.BytesIO(chunk_bytes), format="mp3"
                        )

        if len(final_track) == 0:
            return await synthesize_chunk(
                clean.replace("*", ""), PASHA_VOICE, PASHA_PITCH, PASHA_RATE
            )

        out = io.BytesIO()
        final_track.export(out, format="mp3")
        return out.getvalue()
    except Exception as e:
        logging.error(f"Ошибка войса Паши: {e}")
        return await synthesize_chunk(
            re.sub(r"\*[^*]+\*", "", text), PASHA_VOICE, PASHA_PITCH, PASHA_RATE
        )


async def generate_tanka_voice(text: str) -> bytes | None:
    clean = re.sub(r"<[^>]+>|https?://\S+|\*", "", text).strip()
    return await synthesize_chunk(clean, TANKA_VOICE, TANKA_PITCH, TANKA_RATE)


async def transcribe_voice(voice_bytes: bytes) -> str:
    contents = [
        types.Part.from_bytes(data=voice_bytes, mime_type="audio/ogg"),
        "Сделай дословную расшифровку этого аудио в текст без лишних комментариев.",
    ]
    res = await make_ai_request(contents)
    return res or ""


# --- СЕМЕЙНЫЙ СКАНДАЛ ---
async def run_family_feud(chat_id: int, pasha_msg: Message, pasha_text: str):
    if feud_in_progress[chat_id]:
        return

    feud_in_progress[chat_id] = True
    try:
        await asyncio.sleep(random.randint(4, 6))

        prompt_tanka = (
            f"{TANKA_PERSONA}\n\n"
            f"Муж Пашка написал в чате: «{pasha_text}»\n"
            f"Наедь на него реплаем: обложи за интернат, безделье или заподозри пьянку (1-2 коротких предложения):"
        )
        tanka_reply = await make_ai_request(prompt_tanka)
        if not tanka_reply:
            return

        tanka_sent = None
        if random.random() < 0.30:
            await bot_tanka.send_chat_action(chat_id, "record_voice")
            v_bytes = await generate_tanka_voice(tanka_reply)
            if v_bytes:
                tanka_sent = await bot_tanka.send_voice(
                    chat_id=chat_id,
                    voice=BufferedInputFile(v_bytes, filename="tanka.mp3"),
                    reply_to_message_id=pasha_msg.message_id,
                )

        if not tanka_sent:
            await bot_tanka.send_chat_action(chat_id, "typing")
            tanka_sent = await bot_tanka.send_message(
                chat_id=chat_id,
                text=tanka_reply,
                reply_to_message_id=pasha_msg.message_id,
            )

        if random.random() < 0.60:
            await asyncio.sleep(random.randint(5, 7))
            prompt_pasha = (
                f"{PASHA_PERSONA}\n\n"
                f"Танька наорала на тебя: «{tanka_reply}»\n"
                f"Испуганно оправдайся, скажи что трезвый (1 предложение). В конце обязательно: Связь."
            )
            pasha_counter = await make_ai_request(prompt_pasha)
            if pasha_counter:
                await bot_pasha.send_chat_action(chat_id, "typing")
                await bot_pasha.send_message(
                    chat_id=chat_id,
                    text=pasha_counter,
                    reply_to_message_id=tanka_sent.message_id,
                )

    finally:
        await asyncio.sleep(120)
        feud_in_progress[chat_id] = False


# --- ПУБЛИКАЦИИ В КАНАЛ @potlov_live ---
async def generate_channel_post() -> str | None:
    post_prompt = f"""{PASHA_PERSONA}

    ЗАДАЧА: Напиши авторскую житейскую мысль или историю в свой канал @potlov_live.
    Расскажи про интернат, снег на улице Мира, чекушку или смысл жизни простого мужика.
    Объем: 1–2 небольших абзаца. В конце обязательно: Связь.
    (Ссылку внизу не пиши, её добавит система)."""
    return await make_ai_request(post_prompt)


async def channel_poster_loop():
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

                text = await generate_channel_post()
                if text:
                    link = f'<a href="https://t.me/{CHANNEL_ID.lstrip("@")}">Потлов. Подписаться.</a>'
                    final_msg = f"{html.escape(text)}\n\n{link}"
                    await bot_pasha.send_message(
                        chat_id=CHANNEL_ID,
                        text=final_msg,
                        parse_mode="HTML",
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                    )
        except Exception as e:
            logging.error(f"Ошибка автопостинга: {e}")
        await asyncio.sleep(30)


# --- ТЕСТОВАЯ ПАНЕЛЬ В ЛИЧКЕ ПАШИ ---
def get_pasha_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎙 Байка Паши (войс)", callback_data="p_voice"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💨 Тест пердежа", callback_data="p_fart"
                ),
                InlineKeyboardButton(
                    text="👴 Тест кряхтения", callback_data="p_grunt"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📢 Текст в канал", callback_data="p_post"
                ),
                InlineKeyboardButton(
                    text="🗣 Войс в канал", callback_data="p_vpost"
                ),
            ],
        ]
    )


@dp.message(F.chat.type == ChatType.PRIVATE, F.text == "/start")
async def cmd_start_private(message: Message, bot: Bot):
    if bot.id == bot_tanka.id:
        await message.answer(
            "👵 Чего надо?! Я Танька Потлова, мужа-алкаша ищу! Не мешай кашу варить!"
        )
        return

    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        await message.reply(
            "Здорово, племяш. Работаю помаленьку. Танька ворчит. Связь."
        )
        return

    await message.answer(
        "👋 Здорово, племяш! Это Паша. Проверяй голос и посты кнопками:",
        reply_markup=get_pasha_keyboard(),
    )


@dp.callback_query(F.data.startswith("p_"))
async def handle_pasha_callbacks(callback: CallbackQuery):
    if ADMIN_ID and callback.from_user.id != ADMIN_ID:
        await callback.answer("Только для админа!", show_alert=True)
        return

    action = callback.data
    await callback.answer("Паша на связи...")

    if action == "p_fart":
        txt = "Ох, племяш, погоди секунду, чекушку припрячу... *пукнул* Фух, лишь бы Танька не почуяла чи шо. Связь."
    elif action == "p_grunt":
        txt = "Опять дедов ворочал в интернате... *кряхтит* поясницу заклинило наглухо, племяш. Связь."
    elif action == "p_voice":
        p = await generate_channel_post()
        txt = p or "Тю, племяш, сижу за сараем, тихо, хорошо. Связь."
    elif action == "p_post":
        p = await generate_channel_post()
        if p:
            link = f'<a href="https://t.me/{CHANNEL_ID.lstrip("@")}">Потлов. Подписаться.</a>'
            await bot_pasha.send_message(
                CHANNEL_ID,
                f"{html.escape(p)}\n\n{link}",
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            await callback.message.answer("Пост улетел в канал!")
        return
    elif action == "p_vpost":
        p = await generate_channel_post()
        if p:
            vb = await generate_pasha_voice(p)
            if vb:
                link = f'<a href="https://t.me/{CHANNEL_ID.lstrip("@")}">Потлов. Подписаться.</a>'
                await bot_pasha.send_voice(
                    CHANNEL_ID,
                    BufferedInputFile(vb, filename="potlov.mp3"),
                    caption=link,
                    parse_mode="HTML",
                )
                await callback.message.answer("Голосовой пост в канале!")
        return

    vb = await generate_pasha_voice(txt)
    if vb:
        await bot_pasha.send_voice(
            callback.from_user.id,
            BufferedInputFile(vb, filename="potlov.mp3"),
            caption=f"<i>{html.escape(txt)}</i>",
            parse_mode="HTML",
        )


# --- ОБРАБОТКА ОБЩИХ ЧАТОВ ---
@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.voice)
async def on_group_voice(message: Message, bot: Bot):
    if bot.id != bot_pasha.id:
        return

    chat_id = message.chat.id
    user_name = message.from_user.first_name or "Племяш"

    try:
        file_info = await bot.get_file(message.voice.file_id)
        buf = io.BytesIO()
        await bot.download_file(file_info.file_path, destination=buf)

        text = await transcribe_voice(buf.getvalue())
        if not text:
            return

        await message.reply(f"🗣 <b>Расшифровка:</b>\n{text}", parse_mode="HTML")
        chat_history[chat_id].append(f"{user_name} (голосовое): {text}")
        if len(chat_history[chat_id]) > 10:
            chat_history[chat_id].pop(0)

        if PASHA_KEYWORDS_REGEX.search(text):
            prompt = (
                f"{PASHA_PERSONA}\n\nУчастник сказал голосом: «{text}»\n"
                f"Ответь по-простому, по-деревенски (1-2 предложения). В конце: Связь."
            )
            reply = await make_ai_request(prompt)
            if reply:
                await bot_pasha.send_message(
                    chat_id, reply, reply_to_message_id=message.message_id
                )
    except Exception as e:
        logging.error(f"Ошибка расшифровки войса: {e}")


@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def on_group_message(message: Message, bot: Bot):
    if message.from_user.is_bot or not message.text:
        return

    chat_id = message.chat.id
    user_name = message.from_user.first_name or "Племяш"
    text = message.text

    # 1. ОБРАБОТКА ДЛЯ ТАНЬКИ
    if bot.id == bot_tanka.id:
        is_reply_to_tanka = bool(
            message.reply_to_message
            and message.reply_to_message.from_user.id == bot_tanka.id
        )
        if TANKA_KEYWORDS_REGEX.search(text) or is_reply_to_tanka:
            prompt = (
                f"{TANKA_PERSONA}\n\nИстория чата:\n"
                + "\n".join(chat_history[chat_id])
                + f"\n\nОтветь участнику {user_name} в своем крикливом бабском стиле (1-2 предложения):"
            )
            ans = await make_ai_request(prompt)
            if ans:
                if random.random() < 0.35:
                    await bot_tanka.send_chat_action(chat_id, "record_voice")
                    vb = await generate_tanka_voice(ans)
                    if vb:
                        await bot_tanka.send_voice(
                            chat_id,
                            BufferedInputFile(vb, filename="tanka.mp3"),
                            reply_to_message_id=message.message_id,
                        )
                        return
                await bot_tanka.send_message(
                    chat_id, ans, reply_to_message_id=message.message_id
                )
        return

    # 2. ОБРАБОТКА ДЛЯ ПАШИ
    if bot.id == bot_pasha.id:
        chat_history[chat_id].append(f"{user_name}: {text}")
        if len(chat_history[chat_id]) > 10:
            chat_history[chat_id].pop(0)

        is_reply_to_pasha = bool(
            message.reply_to_message
            and message.reply_to_message.from_user.id == bot_pasha.id
        )
        if PASHA_KEYWORDS_REGEX.search(text) or is_reply_to_pasha:
            prompt = (
                f"{PASHA_PERSONA}\n\nИстория чата:\n"
                + "\n".join(chat_history[chat_id])
                + f"\n\nОтветь по-простому, по-деревенски, с суржиком (1-2 предложения). В конце обязательно: Связь."
            )
            reply = await make_ai_request(prompt)
            if not reply:
                return

            sent_msg = None
            if random.random() < CHAT_VOICE_CHANCE:
                await bot_pasha.send_chat_action(chat_id, "record_voice")
                vb = await generate_pasha_voice(reply)
                if vb:
                    sent_msg = await bot_pasha.send_voice(
                        chat_id,
                        BufferedInputFile(vb, filename="potlov.mp3"),
                        reply_to_message_id=message.message_id,
                    )

            if not sent_msg:
                await bot_pasha.send_chat_action(chat_id, "typing")
                sent_msg = await bot_pasha.send_message(
                    chat_id, reply, reply_to_message_id=message.message_id
                )

            # Шанс перепалки с Танькой
            if random.random() < 0.25:
                asyncio.create_task(run_family_feud(chat_id, sent_msg, reply))


async def main():
    logging.basicConfig(level=logging.INFO)
    await bot_pasha.delete_webhook(drop_pending_updates=True)
    await bot_tanka.delete_webhook(drop_pending_updates=True)

    asyncio.create_task(channel_poster_loop())
    await dp.start_polling(bot_pasha, bot_tanka)


if __name__ == "__main__":
    asyncio.run(main())
