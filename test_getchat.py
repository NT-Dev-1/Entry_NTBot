# test_get_chat.py
import os
import asyncio
from dotenv import load_dotenv
from telegram import Bot

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
VERIFY_CHAT_ID = -5032938724  # replace if needed

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN not set in environment (.env)")

async def run():
    bot = Bot(token=BOT_TOKEN)
    try:
        chat = await bot.get_chat(chat_id=VERIFY_CHAT_ID)
        print("get_chat OK:", chat.id, chat.type, getattr(chat, "title", None))
    except Exception as e:
        print("get_chat error:", repr(e))

asyncio.run(run())
