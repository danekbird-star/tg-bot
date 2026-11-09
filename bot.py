import asyncio
import itertools
import json
import logging
import os
import random
import re
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # не светим токены в логах
log = logging.getLogger("pasha")

TG_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
GEMINI_KEYS = [k.strip() for k in os.environ["GEMINI_KEYS"].split(",") if k.strip()]
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
HISTORY_LEN = int(os.getenv("HISTORY_LEN", "30"))
MUSIC_LIMIT = 3

PERSONA = (BASE / "persona.txt").read_text(encoding="utf-8")
RULES_FILE = BASE / "extra_rules.txt"
STATE_FILE = BASE / "state.json"

# ---- состояние (хранится в state.json) ----
state = {"reply_chance": float(os.getenv("REPLY_CHANCE", "0.25")), "enabled": True, "muted": []}
if STATE_FILE.exists():
    state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))


def save_state():
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def extra_rules() -> str:
    return RULES_FILE.read_text(encoding="utf-8").strip() if RULES_FILE.exists() else ""


history = defaultdict(lambda: deque(maxlen=HISTORY_LEN))
chats = {}  # chat_id -> название
music = {"day": date.today(), "n": 0}
MUSIC_RE = re.compile(r"beatles|битлз|битлы|rolling\s*stones|роллинг|ролинг", re.I)
key_cycle = itertools.cycle(range(len(GEMINI_KEYS)))
http = httpx.AsyncClient(timeout=60)


def system_prompt() -> str:
    s = PERSONA
    if extra_rules():
        s += "\n\n# Дополнительные указания от админа\n" + extra_rules()
    if music["day"] != date.today():
        music.update(day=date.today(), n=0)
    if music["n"] >= MUSIC_LIMIT:
        s += "\n\nСЕГОДНЯ ты уже достаточно говорил про Beatles и Rolling Stones — больше их не упоминай."
    return s


async def gemini(prompt: str) -> str:
    body = {
        "system_instruction": {"parts": [{"text": system_prompt()}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 500, "temperature": 1.0},
        "safetySettings": [
            {"category": c, "threshold": "BLOCK_NONE"}
            for c in (
                "HARM_CATEGORY_HARASSMENT",
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
            )
        ],
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    last = None
    for _ in range(min(len(GEMINI_KEYS), 6)):  # пробуем следующие ключи при ошибках/лимитах
        key = GEMINI_KEYS[next(key_cycle)]
        try:
            r = await http.post(url, json=body, headers={"x-goog-api-key": key})
            if r.status_code != 200:
                last = f"{r.status_code}: {r.text[:200]}"
                continue
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts).strip()
        except Exception as e:  # noqa: BLE001
            last = repr(e)
    log.error("Gemini failed: %s", last)
    return ""


async def generate_reply(chat_id: int, name: str, addressed: bool) -> str:
    transcript = "\n".join(f"{n}: {t}" for n, t in history[chat_id])
    prompt = (
        f"Вот последние сообщения в беседе:\n\n{transcript}\n\n"
        f"Напиши следующее сообщение Паши. Последнее сообщение от {name}"
        + (" — обратились к тебе напрямую." if addressed else " — ты просто вклиниваешься в разговор.")
    )
    reply = await gemini(prompt)
    if reply:
        if MUSIC_RE.search(reply):
            music["n"] += 1
        history[chat_id].append(("Паша", reply))
    return reply


# ---- обычные сообщения ----
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text or not msg.from_user or msg.from_user.is_bot:
        return
    chat_id = msg.chat_id
    private = msg.chat.type == "private"
    if private and msg.from_user.id != ADMIN_ID:
        return  # в личке бот только для админа
    if not private:
        chats[chat_id] = msg.chat.title or str(chat_id)

    name = msg.from_user.full_name or "кто-то"
    if msg.from_user.username:
        name += f" (@{msg.from_user.username})"
    history[chat_id].append((name, msg.text))

    if not state["enabled"] or chat_id in state["muted"]:
        return

    t = msg.text.lower()
    addressed = (
        private
        or f"@{context.bot.username.lower()}" in t
        or "паша" in t
        or "пашк" in t
        or bool(msg.reply_to_message and msg.reply_to_message.from_user
                and msg.reply_to_message.from_user.id == context.bot.id)
    )
    if not addressed and random.random() > state["reply_chance"]:
        return

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    reply = await generate_reply(chat_id, name, addressed)
    if not reply:
        return
    await asyncio.sleep(random.uniform(0.5, 2.0))
    if addressed:
        await msg.reply_text(reply)
    else:
        await context.bot.send_message(chat_id, reply)


# ---- админ-команды (только ЛС от ADMIN_ID) ----
def admin_only(fn):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u, c = update.effective_user, update.effective_chat
        if not u or u.id != ADMIN_ID or c.type != "private":
            return
        await fn(update, context)
    return wrapper


HELP = """Команды админа:
/status — состояние
/chats — известные беседы
/chance 0.3 — шанс ответа без обращения (0..1)
/on, /off — включить/выключить Пашу везде
/mute <chat_id>, /unmute <chat_id> — заглушить/вернуть беседу
/say <chat_id> текст — написать в беседу от имени бота
/talk <chat_id> — Паша сам скажет что-нибудь в беседу
/rule текст — добавить указание в характер
/rules — показать указания, /clearrules — стереть
/forget <chat_id> — стереть память беседы
Просто писать в личке — поболтать с Пашей."""


@admin_only
async def cmd_start(update, context):
    await update.message.reply_text(HELP)


@admin_only
async def cmd_status(update, context):
    await update.message.reply_text(
        f"Включён: {state['enabled']}\nШанс: {state['reply_chance']}\nМодель: {MODEL}\n"
        f"Ключей Gemini: {len(GEMINI_KEYS)}\nБесед: {len(chats)}\nЗаглушены: {state['muted'] or '-'}\n"
        f"Музыка сегодня: {music['n']}/{MUSIC_LIMIT}"
    )


@admin_only
async def cmd_chats(update, context):
    await update.message.reply_text("\n".join(f"{i} — {n}" for i, n in chats.items()) or "Пока ни одной беседы")


@admin_only
async def cmd_chance(update, context):
    try:
        v = float(context.args[0])
        assert 0 <= v <= 1
    except Exception:  # noqa: BLE001
        return await update.message.reply_text("Пример: /chance 0.3")
    state["reply_chance"] = v
    save_state()
    await update.message.reply_text(f"Шанс = {v}")


@admin_only
async def cmd_on(update, context):
    state["enabled"] = True
    save_state()
    await update.message.reply_text("Паша включён")


@admin_only
async def cmd_off(update, context):
    state["enabled"] = False
    save_state()
    await update.message.reply_text("Паша молчит")


def _chat_arg(context):
    try:
        return int(context.args[0])
    except Exception:  # noqa: BLE001
        return None


@admin_only
async def cmd_mute(update, context):
    cid = _chat_arg(context)
    if cid is None:
        return await update.message.reply_text("/mute <chat_id>")
    if cid not in state["muted"]:
        state["muted"].append(cid)
    save_state()
    await update.message.reply_text("Заглушено")


@admin_only
async def cmd_unmute(update, context):
    cid = _chat_arg(context)
    if cid in state["muted"]:
        state["muted"].remove(cid)
        save_state()
    await update.message.reply_text("Ок")


@admin_only
async def cmd_say(update, context):
    cid = _chat_arg(context)
    text = " ".join(context.args[1:])
    if cid is None or not text:
        return await update.message.reply_text("/say <chat_id> текст")
    await context.bot.send_message(cid, text)
    history[cid].append(("Паша", text))
    await update.message.reply_text("Отправлено")


@admin_only
async def cmd_talk(update, context):
    cid = _chat_arg(context)
    if cid is None:
        return await update.message.reply_text("/talk <chat_id>")
    reply = await generate_reply(cid, "группа", False)
    if not reply:
        return await update.message.reply_text("Gemini не ответил")
    await context.bot.send_message(cid, reply)
    await update.message.reply_text("Отправлено: " + reply)


@admin_only
async def cmd_rule(update, context):
    text = " ".join(context.args).strip()
    if not text:
        return await update.message.reply_text("/rule текст")
    with RULES_FILE.open("a", encoding="utf-8") as f:
        f.write("- " + text + "\n")
    await update.message.reply_text("Добавлено")


@admin_only
async def cmd_rules(update, context):
    await update.message.reply_text(extra_rules() or "Пусто")


@admin_only
async def cmd_clearrules(update, context):
    RULES_FILE.unlink(missing_ok=True)
    await update.message.reply_text("Стёр")


@admin_only
async def cmd_forget(update, context):
    cid = _chat_arg(context)
    history.pop(cid, None)
    await update.message.reply_text("Забыл")


def main():
    app = Application.builder().token(TG_TOKEN).build()
    for name, fn in [
        ("start", cmd_start), ("help", cmd_start), ("status", cmd_status), ("chats", cmd_chats),
        ("chance", cmd_chance), ("on", cmd_on), ("off", cmd_off), ("mute", cmd_mute),
        ("unmute", cmd_unmute), ("say", cmd_say), ("talk", cmd_talk), ("rule", cmd_rule),
        ("rules", cmd_rules), ("clearrules", cmd_clearrules), ("forget", cmd_forget),
    ]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info("Паша вышел во двор")
    app.run_polling()


if __name__ == "__main__":
    main()
