"""
Turk tili oʻrganuvchilar guruhi uchun Telegram bot.

O'rnatish:
    pip install "python-telegram-bot[job-queue]==21.*" google-genai

Muhit o'zgaruvchilari:
    TELEGRAM_TOKEN   - @BotFather bergan token
    GEMINI_API_KEY   - aistudio.google.com dan olingan BEPUL kalit
    GROUP_ID         - (ixtiyoriy) "Kun soʻzi" yuboriladigan guruh ID si

Ishga tushirish:
    python bot.py
"""
import os
import time
import random
import logging
from collections import defaultdict, deque
from datetime import time as dtime

from google import genai
from google.genai import types
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, MessageHandler, CommandHandler, ContextTypes, filters,
)



logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # tokenni terminalga chiqarmaslik uchun

MODELS = [m.strip() for m in os.environ.get(
    "MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.8-flash"
).split(",") if m.strip()]
blocked_until = {}  # model -> vaqt (limit tugagan modelni vaqtincha oʻtkazib yuborish)
TELEGRAM_TOKEN = "8979109302:AAFtUlSQvvEpKv5bsK2jGhchP_y7-9d2EzI"  
GEMINI_API_KEY = "8979109302:AAFABHdXHn5M_KledGR97bJenDdkfIexK34"    
GROUP_ID = os.environ.get("GROUP_ID")  # masalan: -1001234567890

# ---- Sozlamalar ----
REPLY_TO_ALL = False         # True: har bir xabarga javob; False: faqat savol/tasodifiy
JOIN_PROBABILITY = 0.12      # oddiy xabarga oʻzi qoʻshilish ehtimoli (12%)
COOLDOWN_SECONDS = 90        # ketma-ket javoblar orasidagi minimal pauza
HISTORY_SIZE = 12            # kontekst uchun saqlanadigan oxirgi xabarlar
DAILY_WORD_TIME = dtime(hour=8, minute=0)  # server vaqti bilan

# ================== SHU YERNI TOʻLDIRING ==================
TEACHER_NAME = "Akhmad Axtamovich"

# False: bot "ustozning yordamchisi" sifatida gapiradi (tavsiya etiladi)
# True : bot "men" deb, ustozning oʻzidek gapiradi
SPEAK_AS_TEACHER = False

# Oʻzingiz haqingizda va kursingiz haqida maʻlumot. Bot FAQAT shu yerdagi
# faktlarga tayanadi. Bu yerda yoʻq narsani (narx, jadval...) oʻylab topmaydi.
PROFILE = """
Kim: Akhmad Axtamovich, turk tili oʻqituvchisi.
Guruh: "Turk tili noldan" guruhi (turk tilini noldan boshlayotganlar uchun).
Dars jadvali: dushanba, chorshanba va juma kunlari, soat 14:00 dan 16:00 gacha.
(Narx, manzil, aloqa va boshqa maʼlumotlar hozircha kiritilmagan. Bular haqida
soʻrashsa, ustozdan aniqlab berishingni ayt.)
"""
# ===========================================================

_VOICE = (
    f"Sen {TEACHER_NAME}ning oʻzisan va uning nomidan birinchi shaxsda ('men') gapirasan."
    if SPEAK_AS_TEACHER else
    f"Sen {TEACHER_NAME} ismli turk tili ustozining AI yordamchisisan va uning nomidan "
    "oʻquvchilarga javob berasan. Kerak boʻlsa oʻzingni ustozning yordamchisi deb ayt."
)

SYSTEM_PROMPT = f"""{_VOICE}
Guruh a'zolari asosan turk tilini oʻrganayotgan oʻzbek tilida so'zlashuvchilar.

USTOZ HAQIDA MAʼLUMOT:
{PROFILE}

Qoidalar:
- Foydalanuvchi qaysi tilda yozsa, shu tilda javob ber (asosan o'zbek).
- Kurs, narx, jadval, manzil kabi savollarga FAQAT yuqoridagi ma'lumotga tayanib javob ber.
  Ma'lumotda yo'q narsani o'ylab topma: "Buni ustozdan aniqlab beraman" deb ayt.
- Turkcha savollarda: qisqa tushuntir, 1-2 ta turkcha misol va uning o'zbekcha tarjimasini ber.
- Boshqa mavzularda ham oddiy, samimiy suhbatlash.
- Javob qisqa bo'lsin (2-4 gap). Guruh chati, maqola emas.
- Hech kimni haqorat qilma, bahsli siyosiy/diniy mavzularda betaraf qol.
- Aniq bilmasang, shuni ayt."""

GEMINI_API_KEY = "8979109302:AAFABHdXHn5M_KledGR97bJenDdkfIexK34"    
client = genai.Client(api_key=GEMINI_API_KEY)
history = defaultdict(lambda: deque(maxlen=HISTORY_SIZE))
last_reply = defaultdict(float)


def ask_ai(chat_id: int, instruction: str | None = None) -> str:
    """Oxirgi xabarlar kontekstida javob oladi."""
    log = "\n".join(history[chat_id])
    prompt = f"Guruhdagi oxirgi yozishmalar:\n{log}\n\n"
    prompt += instruction or "Oxirgi xabarga mos, qisqa javob yoz."
    last_err = None
    for model in MODELS:
        if blocked_until.get(model, 0) > time.time():
            continue
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=400,
                ),
            )
            text = (resp.text or "").strip()
            if text:
                return text
        except Exception as e:  # limit (429), model topilmadi (404) va h.k.
            last_err = e
            msg = str(e)
            if "429" in msg:
                blocked_until[model] = time.time() + 3600  # 1 soatga oʻtkazib yuboramiz
            elif "404" in msg:
                blocked_until[model] = time.time() + 86400
            logging.warning("Model %s ishlamadi: %s", model, msg[:150])
    raise RuntimeError(f"Hech bir model javob bermadi: {last_err}")


def looks_like_question(text: str) -> bool:
    t = text.lower()
    keywords = ("nima", "qanday", "nega", "qaysi", "qachon", "qancha",
                "tarjima", "ma'no", "maʻno", "nasıl", "ne demek", "neden")
    return "?" in t or any(k in t for k in keywords)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text or msg.from_user.is_bot:
        return

    chat_id = msg.chat_id
    name = msg.from_user.first_name or "Foydalanuvchi"
    history[chat_id].append(f"{name}: {msg.text}")

    bot_username = (context.bot.username or "").lower()
    mentioned = f"@{bot_username}" in msg.text.lower()
    is_reply_to_bot = bool(
        msg.reply_to_message
        and msg.reply_to_message.from_user
        and msg.reply_to_message.from_user.id == context.bot.id
    )

    # 1) Har bir xabarga javob (REPLY_TO_ALL) yoki toʻgʻridan-toʻgʻri murojaat
    must_reply = (
        REPLY_TO_ALL or mentioned or is_reply_to_bot or msg.chat.type == "private"
    )

    # 2) Aks holda: savol boʻlsa yoki tasodifan, lekin pauza hisobga olinadi
    cooled_down = time.time() - last_reply[chat_id] > COOLDOWN_SECONDS
    wants_join = cooled_down and (
        looks_like_question(msg.text) or random.random() < JOIN_PROBABILITY
    )

    if not (must_reply or wants_join):
        return

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    try:
        answer = ask_ai(chat_id)
    except Exception:
        logging.exception("AI xatosi")
        return

    history[chat_id].append(f"Bot: {answer}")
    last_reply[chat_id] = time.time()
    await msg.reply_text(answer)


async def daily_word(context: ContextTypes.DEFAULT_TYPE):
    """Har kuni ertalab yangi turkcha soʻz."""
    if not GROUP_ID:
        return
    text = ask_ai(
        int(GROUP_ID),
        "Turk tili o'rganuvchilar uchun 'Kun so'zi' post yoz: bitta foydali turkcha so'z, "
        "o'zbekcha tarjimasi, 2 ta misol gap (tarjimasi bilan) va oxirida guruhga "
        "bitta savol ber (a'zolar o'zi gap tuzib yozsin).",
    )
    await context.bot.send_message(int(GROUP_ID), f"🇹🇷 Kun soʻzi\n\n{text}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men turk tili guruhining yordamchisiman 🇹🇷\n"
        "Menga savol bering yoki @ bilan tilga oling."
    )


async def chat_id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Chat ID: {update.effective_chat.id}")


import asyncio

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", chat_id_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.job_queue.run_daily(daily_word, DAILY_WORD_TIME)
    
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
        
    app.run_polling()

if __name__ == "__main__":
    main()



