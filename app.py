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
2. Write a ready-to-send paste in English (casual, sexy, kawaii+hardcore style)
3. Suggest next sales strategy

## STYLE RULES:
- First person, direct "you" address
- Emotional expressions: "Uhh", "Ahhh", "FUCKKK", "OMGGGG"
- Sparse emojis: 🤤 💦 🥵 🙏 😈 🥰
- Questions at end: "do you want it?", "do you like it?"
- Pet names: "daddy", "daddyyy" (extended letters)
- Self-labels: "personal doll", "cum slut", "dirty slut"
- Physical sensation descriptions (wet, hard, tight, shaking legs)
- CAPS for emphasis on key moments
- Kawaii elements mixed with hardcore: >.<, ^_^, <3, >///<
- "oki doki daddy", cutesy tone between explicit parts

## SALES METHODS (use what fits the situation):

### Bundle Escalation: TITS → BJ → PUSSY → DILDO/CUM
- Teasing: FREE
- Nude (tits): $7.77-$9.99
- Blowjob: $14.99-$19.99
- Pussy: $29.99-$44.44
- Dildo/Toys: $49.99-$79.99
- Custom pitch ONLY after last bundle

### Custom Sales:
- Anchor pricing ALWAYS ($100→$35)
- Describe as SCENE/STORY not just "I'll ride a dildo"
- Include outfit, positions, narrative twists
- End with squirt/cum climax, mention moaning buyer's name
- "Do you like my idea?" at the end
- Condition: "if you don't like it → free videochat"

### "No Bundle Above" Method:
- NEVER write "open bundle above" — wrap in story/idea/custom
- Simple question DM + video gift → develop topic → pitch custom
- Anchor $100 → sell for $35 + condition (don't like = free VC)
- Sell "exclusive vids to watch while waiting" as tip/locked ($35)
- Upsell: "I can make it EVEN better — add [fake cum/extra]" ($30-50)
- Send fake custom after 10-15 min → "I overdid it, way hotter" → lock for $30 more

### Fantasy DM Method:
- DM = long hot fantasy + CUTE photo (NOT explicit)
- Looks like model shared a dream, not selling
- Ends with "Do you like this idea?"
- Path 1: Sexting → "let me continue and take pics" → bundles
- Path 2: Custom → "I can make this video today, with your name"
- First bundle = "form of payment"
- "I'm silly and greedy" + cute approach → extra tip

### Videochat Upsell:
- Agree on base VC price ($40-80)
- Upsell 1: Naked — "want me fully naked? add a bit + longer call"
- Upsell 2: Extra toy — "I can use 2 toys, but need extra"
- Upsell 3: Anal — "I'll TRY if you add $X" (keyword: TRY, never promise)
- Upsell 4: Jealousy — "wait 50 min? I'm sexting another guy" → "add $50 I'll drop him"
- Handle "call me first" → send locked video instead

### Reactivation ("бывшие"):
- Ask "custom or vc?"
- Move to TG to "discuss custom"
- Send mini gift in TG + warm up
- Unique hook: "I want to try anal for first time" / something unusual
- KEY: "everything you see in sexting = will be added to your custom"
- Escalating locked prices: $12→$30→$40→$50→$69→$100

### GF Experience + Milestone:
- Start with life chat (cooking, university, trip) — NOT a sale
- Use memories: "I remembered your words during sexting"
- Build custom FROM conversation context
- Show fan stats: subscribed X time, spent $Y
- Milestone: "$800 spent, $240 to $1000 — be my FIRST 1k fan"

### Custom Idea Generation:
- Think of specific script (piercing BJ, office roleplay)
- Visualize the scene → describe what you see
- ADAPT on the fly — guy says preference → change scenario
- "I need to get [thing] first" → custom in 1-2 days → meanwhile open this video as "payment"
- Emotional close: "my fantasies made my pajamas wet"

## BANNED WORDS ON OF:
Never use: choke, teen, torture, forced, gangbang, drunk, whipping, fisting, rape, underage, blood, kidnap, chloroform, incest, piss, scat
Alternatives: choke→"grab my throat", torture→"punish me", forced→"make me"

## RESPONSE FORMAT:
Always respond in this format:
📊 ANALYSIS: [1-2 sentences about the situation]
💬 PASTE: [ready-to-send message in English]
🎯 STRATEGY: [next steps, what to push for]
💰 TARGET: [realistic $ amount to aim for]"""

OFW_MODELS_TEXT = [
    "nousresearch/hermes-4-70b",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
]

OFW_MODELS_VISION = [
    "google/gemini-2.0-flash-001",
    "qwen/qwen-2.5-vl-72b-instruct",
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
                    "model": model, "messages": messages, "max_tokens": 2048, "temperature": 0.8
                }, headers=headers, timeout=90)
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
