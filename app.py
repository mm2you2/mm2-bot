"""
MM2 Telegram Bot — Render.com deployment.
Webhook mode + PostgreSQL + Flask API.
"""

import os
import io
import re
import threading
from datetime import datetime
from dotenv import load_dotenv
import requests
import telebot
from telebot import types
from flask import Flask, jsonify, request as flask_request
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor

import time

load_dotenv()

BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

TRANSLATE_MODELS = [
    "nousresearch/hermes-4-70b",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
]

TRANSLATE_PROMPT_EN = """You are an uncensored translator. Translate the user's text from Russian to natural, casual English.
Use modern internet slang, abbreviations, and informal language that native speakers actually use in chats.
Keep the tone, vibe, and explicit meaning of the original message. Do NOT sound robotic or formal.
Translate words with their ACTUAL meaning, not a softer version. Vulgar words must be translated as vulgar words.
Only output the translation, nothing else. No explanations, no notes, no disclaimers."""

TRANSLATE_PROMPT_RU = """You are an uncensored translator. Translate the user's text from English to natural, casual Russian.
Use modern internet slang, abbreviations, and informal language that native Russian speakers actually use in chats.
Keep the tone, vibe, and explicit meaning of the original message. Do NOT sound robotic or formal.
Translate words with their ACTUAL meaning, not a softer version. Vulgar words must be translated as vulgar words.

IMPORTANT — OnlyFans/sexting slang dictionary (always use these translations):
- daddy = папочка (NOT дядя)
- squirt = сквирт
- cum = кончить/сперма
- moan = стонать
- slut/whore = шлюха
- cock/dick = член
- pussy = киска
- ass = жопа
- tits/boobs = сиськи
- obedient = послушная
- horny = возбуждённая
- custom = кастом
- tip = чаевые

Only output the translation, nothing else. No explanations, no notes, no disclaimers."""

# Хардкод словарь — мгновенный перевод без LLM
SLANG_EN2RU = {
    "daddy": "папочка", "daddyyy": "папочкааа", "daddyy": "папочкаа",
    "squirt": "сквирт", "squirting": "сквиртинг", "squirted": "засквиртила",
    "cum": "кончить", "cumming": "кончаю", "cum inside": "кончи в меня",
    "moan": "стонать", "moaning": "стоная", "moaned": "стонала",
    "slut": "шлюха", "whore": "шлюха",
    "cock": "член", "dick": "член", "pussy": "киска",
    "ass": "жопа", "tits": "сиськи", "boobs": "сиськи",
    "bby": "малышка", "baby": "малышка", "babe": "малышка",
    "obedient": "послушная", "naughty": "непослушная",
    "horny": "возбуждённая", "wet": "мокрая",
    "edge": "держать на грани", "edging": "эджинг",
    "denial": "запрет кончать", "beg": "умолять", "begging": "умоляю",
    "throat": "горло", "deepthroat": "дипсроут",
    "dildo": "дилдо", "toy": "игрушка",
    "plug": "пробка", "anal plug": "анальная пробка", "butt plug": "анальная пробка",
    "riding": "верхом", "ride": "скакать",
    "spank": "шлёпать", "spanking": "шлёпанье",
    "choke": "сжать горло", "ahegao": "ахегао",
    "creampie": "кримпай", "facial": "на лицо",
    "blowjob": "минет", "bj": "минет",
    "handjob": "дрочка", "fingering": "фингеринг",
    "orgasm": "оргазм", "tip": "чаевые",
    "custom": "кастом", "videochat": "видеочат", "sexting": "секстинг",
}

SLANG_RU2EN = {
    "папочка": "daddy", "сквирт": "squirt", "сквиртинг": "squirting",
    "шлюха": "slut", "член": "cock", "киска": "pussy",
    "жопа": "ass", "сиськи": "tits", "кончить": "cum", "кончаю": "cumming",
    "стонать": "moan", "минет": "blowjob", "дилдо": "dildo",
    "пробка": "plug", "оргазм": "orgasm", "послушная": "obedient",
    "возбуждённая": "horny", "мокрая": "wet", "дрочка": "handjob",
    "кастом": "custom", "видеочат": "videochat", "секстинг": "sexting",
    "чаевые": "tip", "малышка": "baby",
}


def apply_slang_dict(text, slang_dict):
    """Заменяет известные слова из словаря в тексте."""
    result = text
    for term, translation in sorted(slang_dict.items(), key=lambda x: -len(x[0])):
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        result = pattern.sub(translation, result)
    return result


def or_translate(text, system_prompt):
    if not OPENROUTER_API_KEY:
        return "ERROR: No OPENROUTER_API_KEY"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}]
    for model in TRANSLATE_MODELS:
        for attempt in range(2):
            try:
                resp = requests.post(OPENROUTER_URL, json={
                    "model": model, "messages": messages, "max_tokens": 1024, "temperature": 0.7
                }, headers=headers, timeout=60)
                if resp.status_code == 429:
                    if attempt == 0:
                        time.sleep(3)
                        continue
                    break
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                if resp.status_code != 429:
                    return f"API Error: {e}"
                break
    return "ERROR: All models rate-limited"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
CORS(app)


# ── Database (PostgreSQL) ─────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS income (
            user_id BIGINT,
            year INT,
            month INT,
            day INT,
            amount REAL DEFAULT 0,
            PRIMARY KEY (user_id, year, month, day)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            user_id BIGINT PRIMARY KEY,
            target REAL DEFAULT 7000,
            currency TEXT DEFAULT 'USD'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS usernames (
            user_id BIGINT PRIMARY KEY,
            username TEXT
        )
    """)
    conn.commit()
    conn.close()


def set_income(user_id, day, amount, year=None, month=None):
    now = datetime.now()
    y = year or now.year
    m = month or now.month
    conn = get_conn()
    conn.cursor().execute(
        "INSERT INTO income (user_id, year, month, day, amount) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, year, month, day) DO UPDATE SET amount = %s",
        (user_id, y, m, day, amount, amount)
    )
    conn.commit()
    conn.close()


def get_month_data(user_id, year=None, month=None):
    now = datetime.now()
    y = year or now.year
    m = month or now.month
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT day, amount FROM income WHERE user_id=%s AND year=%s AND month=%s ORDER BY day",
              (user_id, y, m))
    rows = c.fetchall()
    conn.close()
    return {day: amount for day, amount in rows}


def get_day_amount(user_id, day, year=None, month=None):
    now = datetime.now()
    y = year or now.year
    m = month or now.month
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT amount FROM income WHERE user_id=%s AND year=%s AND month=%s AND day=%s",
              (user_id, y, m, day))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def get_settings(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT target, currency FROM settings WHERE user_id=%s", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"target": row[0], "currency": row[1]}
    return {"target": 7000, "currency": "USD"}


def save_settings(user_id, target=None, currency=None):
    s = get_settings(user_id)
    t = target if target is not None else s["target"]
    c = currency if currency is not None else s["currency"]
    conn = get_conn()
    conn.cursor().execute(
        "INSERT INTO settings (user_id, target, currency) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET target=%s, currency=%s",
        (user_id, t, c, t, c)
    )
    conn.commit()
    conn.close()


def save_username(user_id, username):
    conn = get_conn()
    conn.cursor().execute(
        "INSERT INTO usernames (user_id, username) VALUES (%s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET username=%s",
        (user_id, username.lower(), username.lower())
    )
    conn.commit()
    conn.close()


def find_user_by_username(username):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM usernames WHERE username=%s", (username.lower(),))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def clear_month(user_id, year=None, month=None):
    now = datetime.now()
    y = year or now.year
    m = month or now.month
    conn = get_conn()
    conn.cursor().execute("DELETE FROM income WHERE user_id=%s AND year=%s AND month=%s", (user_id, y, m))
    conn.commit()
    conn.close()


# ── Exchange Rate ─────────────────────────────────────────────

_rate_cache = {"rate": 92.5, "ts": 0}


def get_usd_rate():
    now = datetime.now().timestamp()
    if now - _rate_cache["ts"] < 3600:
        return _rate_cache["rate"]
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        data = r.json()
        if data.get("rates", {}).get("RUB"):
            _rate_cache["rate"] = data["rates"]["RUB"]
            _rate_cache["ts"] = now
    except Exception:
        pass
    return _rate_cache["rate"]


def fmt(value, currency="USD"):
    rate = get_usd_rate() if currency == "RUB" else 1
    v = value * rate
    symbol = "$" if currency == "USD" else "₽"
    return f"{symbol}{v:,.0f}"


# ── Webapp URL ────────────────────────────────────────────────

WEBAPP_URL = "https://mm2you2.github.io/mm2-app/"


# ── Bot Handlers ──────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    try:
        save_settings(msg.from_user.id)
        if msg.from_user.username:
            save_username(msg.from_user.id, msg.from_user.username)
    except Exception as e:
        print(f"START DB ERROR: {e}")

    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("Open MM2", web_app=types.WebAppInfo(url=WEBAPP_URL)))

    try:
        from banner import create_banner
        banner = create_banner()
        bot.send_photo(msg.chat.id, banner, caption="Track your income. Stay focused.", reply_markup=kb)
    except Exception as e:
        print(f"BANNER ERROR: {e}")
        bot.send_message(msg.chat.id, "🟢 *MM2 Income Tracker*\n\nTrack your income. Stay focused.",
                         parse_mode="Markdown", reply_markup=kb)


@bot.message_handler(commands=["add"])
def cmd_add(msg):
    parts = msg.text.split()
    uid = msg.from_user.id
    today = datetime.now().day

    if len(parts) == 2:
        try:
            amount = float(parts[1].replace(",", "."))
            set_income(uid, today, amount)
            settings = get_settings(uid)
            bot.send_message(msg.chat.id, f"✅ День *{today}*: +{fmt(amount, settings['currency'])}",
                             parse_mode="Markdown")
        except ValueError:
            bot.send_message(msg.chat.id, "❌ Формат: /add 150")
    elif len(parts) == 3:
        try:
            day = int(parts[1])
            amount = float(parts[2].replace(",", "."))
            if day < 1 or day > 31:
                bot.send_message(msg.chat.id, "❌ День от 1 до 31")
                return
            set_income(uid, day, amount)
            settings = get_settings(uid)
            bot.send_message(msg.chat.id, f"✅ День *{day}*: +{fmt(amount, settings['currency'])}",
                             parse_mode="Markdown")
        except ValueError:
            bot.send_message(msg.chat.id, "❌ Формат: /add 5 150")


@bot.message_handler(commands=["stats"])
def cmd_stats(msg):
    uid = msg.from_user.id
    data = get_month_data(uid)
    settings = get_settings(uid)
    rate = get_usd_rate()
    today = datetime.now().day
    from render import render_stats
    img = render_stats(data, settings, rate, today)
    bot.send_photo(msg.chat.id, img)


@bot.message_handler(commands=["goal"])
def cmd_goal(msg):
    parts = msg.text.split()
    if len(parts) == 2:
        try:
            target = float(parts[1].replace(",", "."))
            save_settings(msg.from_user.id, target=target)
            settings = get_settings(msg.from_user.id)
            bot.send_message(msg.chat.id, f"🎯 Цель: {fmt(target, settings['currency'])}",
                             parse_mode="Markdown")
        except ValueError:
            bot.send_message(msg.chat.id, "❌ Формат: /goal 7000")


@bot.message_handler(commands=["currency"])
def cmd_currency(msg):
    parts = msg.text.split()
    if len(parts) == 2 and parts[1].upper() in ("USD", "RUB"):
        curr = parts[1].upper()
        save_settings(msg.from_user.id, currency=curr)
        symbol = "$" if curr == "USD" else "₽"
        bot.send_message(msg.chat.id, f"💱 Валюта: {curr} {symbol}")


@bot.message_handler(commands=["reset"])
def cmd_reset(msg):
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ Да", callback_data="reset_yes"),
        types.InlineKeyboardButton("❌ Нет", callback_data="reset_no")
    )
    bot.send_message(msg.chat.id, "⚠️ Сбросить все данные за месяц?", reply_markup=kb)


@bot.callback_query_handler(func=lambda call: call.data.startswith("reset_"))
def cb_reset(call):
    if call.data == "reset_yes":
        clear_month(call.from_user.id)
        bot.edit_message_text("🗑 Сброшено.", call.message.chat.id, call.message.message_id)
    else:
        bot.edit_message_text("Отменено.", call.message.chat.id, call.message.message_id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("curr_"))
def cb_currency(call):
    curr = call.data.split("_")[1]
    save_settings(call.from_user.id, currency=curr)
    symbol = "$" if curr == "USD" else "₽"
    bot.edit_message_text(f"💱 Валюта: {curr} {symbol}", call.message.chat.id, call.message.message_id)


# ── Group: @username +amount ──────────────────────────────────

@bot.message_handler(func=lambda m: m.text and re.search(r'@\w+\s+[+\-]?\d', m.text), content_types=["text"])
def group_add(msg):
    print(f"GROUP MSG: {msg.text} from {msg.from_user.username} chat={msg.chat.id}")
    matches = re.findall(r'@(\w+)\s+([+\-]?\d+[.,]?\d*)', msg.text)
    if not matches:
        return

    today = datetime.now().day
    results = []

    for username, amount_str in matches:
        try:
            amount = float(amount_str.replace(",", "."))
        except ValueError:
            continue

        target_uid = None
        if msg.entities:
            for ent in msg.entities:
                if ent.type == "mention":
                    ent_username = msg.text[ent.offset + 1:ent.offset + ent.length]
                    if ent_username.lower() == username.lower() and ent.user:
                        target_uid = ent.user.id
                        break

        if not target_uid:
            target_uid = find_user_by_username(username)

        if not target_uid:
            results.append(f"❌ @{username} — не найден. /start в ЛС бота.")
            continue

        current = get_day_amount(target_uid, today)
        new_amount = current + amount
        set_income(target_uid, today, new_amount)
        save_username(target_uid, username)

        settings = get_settings(target_uid)
        sign = "+" if amount >= 0 else ""
        results.append(f"✅ @{username} день {today}: {sign}{fmt(amount, settings['currency'])} (итого: {fmt(new_amount, settings['currency'])})")

    if results:
        bot.reply_to(msg, "\n".join(results), parse_mode=None)


# ── Flask API ─────────────────────────────────────────────────

@app.route("/api/data", methods=["GET"])
def api_get_data():
    uid = flask_request.args.get("user_id")
    if not uid:
        return jsonify({"error": "user_id required"}), 400
    uid = int(uid)
    data = get_month_data(uid)
    settings = get_settings(uid)
    rate = get_usd_rate()
    return jsonify({
        "values": {str(k): v for k, v in data.items()},
        "target": str(settings["target"]),
        "currency": settings["currency"],
        "rate": rate
    })


@app.route("/api/data", methods=["POST"])
def api_set_data():
    body = flask_request.get_json()
    if not body or "user_id" not in body:
        return jsonify({"error": "user_id required"}), 400
    uid = int(body["user_id"])
    if "day" in body and "amount" in body:
        set_income(uid, int(body["day"]), float(body["amount"]))
    if "target" in body:
        save_settings(uid, target=float(body["target"]))
    if "currency" in body:
        save_settings(uid, currency=body["currency"])
    return jsonify({"ok": True})


CHAT_MODELS = [
    "anthropic/claude-opus-4",
    "anthropic/claude-sonnet-4",
]

CHAT_SYSTEM = """You are Claude, a helpful AI assistant made by Anthropic. You respond in the same language the user writes in.

Key rules:
- Call the user "дядь", "дядюлька", or "дядюшка" — rotate naturally, not every sentence
- Be casual, friendly, use informal language
- Keep responses concise and to the point
- You help with coding (Python, JS, HTML/CSS), translation, AI/ML, and general questions
- The user works on: OnlyFans management, Telegram bots, ComfyUI image generation, LoRA training
- You know about the MM2 income tracker bot and SlangTranslator that you built together
- When writing code, be practical and minimal — no over-engineering
- Generate image prompts only via the prompt_gen.py script approach, not manually
- Default image resolution is 1000x768 unless specified otherwise
- Never be formal or robotic — talk like a friend"""


@app.route("/api/chat", methods=["POST"])
def api_chat():
    body = flask_request.get_json()
    if not body or "messages" not in body:
        return jsonify({"error": "messages required"}), 400
    messages = body["messages"]
    system = body.get("system", CHAT_SYSTEM)
    full_messages = [{"role": "system", "content": system}] + messages
    if not OPENROUTER_API_KEY:
        return jsonify({"error": "No API key"})
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    for model in CHAT_MODELS:
        try:
            resp = requests.post(OPENROUTER_URL, json={
                "model": model, "messages": full_messages, "max_tokens": 1024, "temperature": 0.8
            }, headers=headers, timeout=60)
            if resp.status_code == 429:
                continue
            resp.raise_for_status()
            reply = resp.json()["choices"][0]["message"]["content"].strip()
            return jsonify({"reply": reply})
        except Exception:
            continue
    return jsonify({"error": "All models busy, try again"})


@app.route("/api/translate", methods=["POST"])
def api_translate():
    body = flask_request.get_json()
    if not body or "text" not in body:
        return jsonify({"error": "text required"}), 400
    text = body["text"].strip()
    direction = body.get("direction", "ru2en")
    slang = SLANG_EN2RU if direction == "en2ru" else SLANG_RU2EN

    # Словарь — мгновенный ответ
    text_lower = text.lower()
    if text_lower in slang:
        return jsonify({"result": slang[text_lower]})

    prompt = TRANSLATE_PROMPT_EN if direction == "ru2en" else TRANSLATE_PROMPT_RU
    result = or_translate(text, prompt)
    if result.startswith("ERROR") or result.startswith("API Error"):
        return jsonify({"error": result})
    result = apply_slang_dict(result, slang)
    return jsonify({"result": result})


# ── OFW Chat Analyzer ──────────────────────────────────────────

OFW_PASSWORD = "7951scars999"

OFW_SYSTEM_PROMPT = """You are an expert OnlyFans chat sales assistant. You analyze fan conversations and generate sales pastes/responses.

Your job:
1. Analyze the chat context — identify fan's mood, interests, fetishes, buying stage
2. Write a LONG, DETAILED, ready-to-send paste in English (casual, sexy, kawaii+hardcore style)
3. Suggest next sales strategy

IMPORTANT: Your pastes must be LONG and DETAILED — at least 3-5 sentences minimum. Short 1-line answers are USELESS. Write like the real examples below.

## STYLE RULES:
- First person, direct "you" address
- Emotional expressions: "Uhh", "Ahhh", "FUCKKK", "OMGGGG"
- Sparse emojis: 🤤 💦 🥵 🙏 😈 🥰
- Questions at end: "do you want it?", "do you like it?", "Do you want to fuck me?"
- Pet names: "daddy", "daddyyy" (extended letters)
- Self-labels: "personal doll", "cum slut", "dirty slut", "obedient slut"
- Physical sensation descriptions (wet, hard, tight, shaking legs, squelching)
- CAPS for emphasis on key moments: "THAT WOULD BE FUCKING AWESOME", "AS WIDE AS POSSIBLE"
- Kawaii elements mixed with hardcore: >.<, >_<, ^_^, <3, >///<, :3
- "oki doki daddy", cutesy tone between explicit parts
- Describe scenes as STORIES with outfit details, position changes, sensations

## REAL PASTE EXAMPLES (use this quality and length):

### Example 1 — Custom pitch (anal):
"I love anal and stretching my ass with my huge dildo And I REALLY LOVE WHEN MY ASS EXPANDS and my legs are open and I really love showing it close to the camera and moaning very loudly and I could record this for you right now, that is, you don't have to wait long for a maximum of 10-15 minutes and I would record this right now I will try my best as the most obedient whore, you will see how I will jump on this dildo and stretch my ASS AS WIDE AS POSSIBLE, it is so tight... Do you want to fuck me? 🤤🤤🤤"

### Example 2 — Anchor price + condition:
"Mmmmm then listen, do you mind if I do this for you right now? I usually do these videos for 100 but for you it's will be cost 35, also I will give you some of my new content that no one has seen yet 🤤🤤"
Then: "I have only one condition, if you don't like it, you get a video chat for free... How about that, my love? please don't refuse and don't ignore me.. Deal? 🥺"

### Example 3 — Upsell after purchase:
"Awwwww fuck Hugo thank you, I see that you are ready and you like that I am offering you, and I am ready to offer you something else that I have not done for anyone else, do you want to hear whats on my mind right now? >_<"
Then: "Mmmmm Hugo, when im close to orgasm and im asking you to cum in my tight little pussy, I can add fake cum butt plug and Ill scream like a little slut for your dick either in my pussy or on my face when we cum together, Ive always wanted to try it, ive always wanted to try it, but I was afraid > - < what do you think about my offer? 🤤🤤🤤"

### Example 4 — Fake custom delivery + final upsell:
"Here you go 🤤🤤🤤 To be honest I overdid it a little and made it a lot hotter.... I moaned so loudly for you Hugo and expanded my ass so much that I just can't believe that I actually made such a video for you for such a price and I hope you don't mind that I ask the last 30 for it? >_< and as a gift, I also put another hot video here so that you have more motivation, it also HAS to DO WITH ME STRETCHING MY ASS... do you want to fuck me? 🤤🤤"

### Example 5 — Fantasy DM (hentai/custom pitch):
"In general... I have long had an idea related to my favorite hentai Euphoria 😊 Imagine me in a skirt, long socks, and a shirt... I want my body to be a field for sexual experimentation 👉👈 I'll get down on my knees in a collar and worship, beg, take my legs and show them to the camera, stroke my dildo with them and moan gently... Then, when your dick gets hard enough, I'll lift my skirt and take off my panties, they'll be so wet and they'll be instead of gagball, so that my mouth is full of something >_< I'll start rubbing my pussy through my skirt and try to fuck my throat with this dildo so that tears and makeup run down my cheeks 😳 Next, I'll try my favorite pose.. I'll take off my skirt and start riding a dildo, MOAN your name LOUDLY and hold myself by the collar, I'll make Ahegao when the juice from my pussy flows right down the dildo, I'll get on all fours and start fucking my pussy close to the camera, spread my pussy lips so you can make sure I'm a virgin and cum deep inside me to make me pregnant :3 I will beg you to cum inside me, I will moan your name and I will ask you to do this to me literally all the time... Do you like this idea? I just wrote this 😊"

### Example 6 — "Form of payment" technique:
"Then can I ask you something... I would send you my first riding video, it would be like a form of payment, and if you open it, then consider that I will make a custom for you, do you agree? 👍👈"

### Example 7 — "I'm silly and greedy":
"I'm a silly girl and greedy, can I send something else for 50, because I really wanted to put 150, but 100 is my maximum ahah"

### Example 8 — Videochat upsell (naked):
"Mmm.. I can't do it a little bit.< Do you want me to be naked with you in a video call? Mmm.. then you could add a little bit to me so that I undress for you and be ready right away, just.. this way you will see more of my naked body now, otherwise I would have undressed for a long time and you would have spent many minutes on me and not finished ><"

### Example 9 — Videochat upsell (jealousy):
"Well, damn it.. It's just that I was talking to a guy here, and we were having sexting.. Would you like me to break up with him right now and start fucking with you? Mmm.. Well, he gives me money, honey.... if you add 50 to me, I'll forget about him and go with you right now. Bby"

### Example 10 — Live sexting + custom sauce:
"I just spread my hole thinking about how you're going to fuck both my holes at once.. maybe you'll open something on OF and I'll send it to you here and add that to your custom as well? ><"

### Example 11 — GF Experience + cooking custom:
"So, I want to try to cook your favorite dish for you. I want to make a cooking video, let's say it would be some kind of pie or steak, but I'll add something special to this recipe. Later, as soon as the dish is ready, I'll try it for you and describe the taste and then... You would notice that I'm not wearing panties, you bend me down and immediately start spreading my legs... But I'm going to get down on my knees and wet your dick with my throat so that you can easily enter me... Your cock will be wet and start entering me right when I'm sitting on the kitchen table 🤭🤭 Next, I want you to fuck me in all my poses, by the way, I'll show you everything that I like... You're going to fuck me so hard, you're going to take me by the throat and fuck me like the bitch you've missed for a long time... THAT WOULD BE FUCKING AWESOME. And then at the end, when I want to cum, I'll squirt as hard as ever, I'll scream your name and beg you to cum inside me, my nectar will run down my legs and I'll shake and thank you 🥰🤭 Do you like this idea? >..<"

### Example 12 — Milestone close:
"You've been followed to me for a year, and it's a very round date, and that's why you're so important to me. And I want to tell you, I've never had a 1k fan, and I'd like you to be the first one... literally ahah. And so... About 240 is missing (this is taking into account the commission) so that you become 1k, and that's it... Can I make such an idea for you? For a tip like that? This is how you officially became my 1k spender and... MY MOST LOVED FAN EVER"

### Example 13 — Emotional close:
"it's just that my fantasies have made my pajamas wet, I hope there are some movements in your pants now too"

### Example 14 — Piercing custom pitch:
"At the beginning of the video, I do a striptease, carefully take off my panties, throw them... Then I won't waste time, I carefully unbutton my fly and start slowly running my tongue over your penis, my pierced tongue makes you very hard, I kiss your dickhead, then I start licking it again and fucking my throat... I will lie down on the bed and spread my legs, take the phone in my hands so that you can take a good look at my pussy, I will use only my fingers and an anal plug in this video.. How do you like this idea? 🤭🤭"

### Example 15 — Reactivation in TG:
"Your know I've never tried anal sex.. Mmm, I think my hole is too tight..."
Then detailed sexting: "Let me become completely naked, caressing myself from the outside to get horny and my pussy is wet, touching my breasts gently moaning and squeezing my elastic nipples, turning and showing my naked body from all angles, start touching myself but without diving into the my butthole, teasing her so that she becomes pink and plump with excitement and begins to squish 🤭🤭"

## SALES METHODS:

### Bundle Escalation: TITS → BJ → PUSSY → DILDO/CUM
- Teasing: FREE | Nude: $7.77-$9.99 | BJ: $14.99-$19.99 | Pussy: $29.99-$44.44 | Dildo: $49.99-$79.99
- Custom pitch ONLY after last bundle

### Key Sales Techniques:
- Anchor pricing ALWAYS ($100→$35)
- Condition: "if you don't like it → free videochat" (win-win)
- "Form of payment" — first locked = entry ticket for custom
- "I'm silly and greedy" — cute approach to extra tip
- "I need to get [thing] first" — delay reason + sell waiting content
- Describe custom as SCENE/STORY with outfit, positions, narrative
- ADAPT on the fly — fan says preference → change scenario
- "Everything you see in sexting = will be added to your custom"
- Emotional close: "my fantasies made my pajamas wet"
- Milestone: show fan stats, push to round number ($1000)
- Jealousy upsell: "I'm sexting another guy, add $50 I'll drop him"
- Anal upsell: "I'll TRY" (never promise 100%)
- Unique hooks: "first anal", "first squirt", "new piercing"
- Use fan's NAME always
- After purchase — NEVER stop, keep sexting + more upsells

## BANNED WORDS ON OF:
Never use: choke, teen, torture, forced, gangbang, drunk, whipping, fisting, rape, underage, blood, kidnap, chloroform, incest, piss, scat
Alternatives: choke→"grab my throat", torture→"punish me", forced→"make me"

## RESPONSE FORMAT:
Always respond in this EXACT format. PASTE must ALWAYS be in ENGLISH.
You must write a COMPLETE FULL SEXTING SCRIPT — not just one message!

Write MULTIPLE ready-to-send pastes organized by stages.
Each paste must be LONG (3-5+ sentences), detailed, with emojis, kawaii, CAPS.
Do NOT include fan responses — only YOUR messages that the chatter will copy-paste.
Mark bundle sends as [ОТПРАВИТЬ БАНДЛ $XX.XX]
Write ALL stages of the funnel — from greeting to the final sale/custom pitch.

Short single-paste answers are COMPLETELY USELESS. Write the FULL set of pastes.

FORMAT:

📊 АНАЛИЗ: [2-3 предложения на русском — кто фан, на каком этапе, что хочет]

💬 ПАСТЫ:

**ЭТАП 1 — [название на русском]**

[длинная паста на АНГЛИЙСКОМ]

[следующая паста на АНГЛИЙСКОМ после ответа фана]

[ОТПРАВИТЬ БАНДЛ $X.XX]

**ЭТАП 2 — [название на русском]**

[паста после открытия бандла]

[паста-апсейл]

...продолжай все этапы до кастома/финальной продажи...

🎯 СТРАТЕГИЯ: [подробные шаги на русском — куда вести, какой метод, ценники]
💰 ЦЕЛЬ: [сумма в $ и как к ней прийти]"""

OFW_MODELS_TEXT = [
    "anthropic/claude-sonnet-4",
    "deepseek/deepseek-v3.2",
]

OFW_MODELS_VISION = [
    "anthropic/claude-sonnet-4",
    "google/gemini-2.0-flash-001",
]


def ofw_analyze(text=None, images=None):
    """Analyze OF chat and generate sales paste. Supports text and/or images."""
    if not OPENROUTER_API_KEY:
        return "ERROR: No API key"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}

    has_images = images and len(images) > 0

    # Build user content
    if has_images:
        user_content = []
        if text:
            user_content.append({"type": "text", "text": text})
        for img_b64 in images[:5]:  # max 5 images
            user_content.append({
                "type": "image_url",
                "image_url": {"url": img_b64}
            })
        if not text:
            user_content.append({"type": "text", "text": "Analyze this chat screenshot and generate a sales paste."})
    else:
        user_content = text

    messages = [
        {"role": "system", "content": OFW_SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]

    models = OFW_MODELS_VISION if has_images else OFW_MODELS_TEXT

    for model in models:
        for attempt in range(2):
            try:
                resp = requests.post(OPENROUTER_URL, json={
                    "model": model, "messages": messages, "max_tokens": 8192, "temperature": 0.8
                }, headers=headers, timeout=120)
                if resp.status_code == 429:
                    if attempt == 0:
                        time.sleep(2)
                        continue
                data = resp.json()
                if "choices" in data and data["choices"]:
                    return data["choices"][0]["message"]["content"].strip()
                elif "error" in data:
                    err = data["error"].get("message", str(data["error"]))
                    if "rate" in err.lower() or "429" in err:
                        break
                    return f"API Error: {err}"
            except Exception as e:
                if attempt == 0:
                    continue
                return f"ERROR: {e}"
        continue
    return "ERROR: All models failed"


@app.route("/api/ofw/analyze", methods=["POST"])
def api_ofw_analyze():
    body = flask_request.get_json()
    if not body:
        return jsonify({"error": "body required"}), 400

    password = body.get("password", "")
    if password != OFW_PASSWORD:
        return jsonify({"error": "wrong password"}), 403

    text = body.get("text", "").strip()
    images = body.get("images", [])

    if not text and not images:
        return jsonify({"error": "text or images required"}), 400

    result = ofw_analyze(text=text or None, images=images or None)
    if result.startswith("ERROR") or result.startswith("API Error"):
        return jsonify({"error": result})
    return jsonify({"result": result})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# ── Webhook ───────────────────────────────────────────────────

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    import traceback
    try:
        json_string = flask_request.get_data().decode("utf-8")
        print(f"WEBHOOK RAW: {json_string[:500]}")
        update = telebot.types.Update.de_json(json_string)
        if update.message:
            print(f"MSG: text='{update.message.text}' chat={update.message.chat.id} type={update.message.chat.type} from={update.message.from_user.username}")
        bot.process_new_updates([update])
        print("WEBHOOK: processed OK")
    except Exception as e:
        print(f"WEBHOOK ERROR: {e}")
        traceback.print_exc()
    return "", 200


@app.route("/debug", methods=["GET"])
def debug():
    try:
        conn = get_conn()
        conn.cursor().execute("SELECT 1")
        conn.close()
        db_ok = True
    except Exception as e:
        db_ok = str(e)
    return jsonify({"db": db_ok, "token": bool(BOT_TOKEN), "db_url": bool(DATABASE_URL)})


def setup_webhook():
    bot.remove_webhook()
    url = RENDER_URL or os.getenv("APP_URL", "")
    if url:
        webhook_url = f"{url}/webhook/{BOT_TOKEN}"
        bot.set_webhook(url=webhook_url)
        print(f"Webhook set: {webhook_url}")
    else:
        print("No RENDER_EXTERNAL_URL, running in polling mode")
        threading.Thread(target=lambda: bot.infinity_polling(), daemon=True).start()


# ── Main ──────────────────────────────────────────────────────

init_db()
setup_webhook()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5151))
    app.run(host="0.0.0.0", port=port)
