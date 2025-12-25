# test_bot_identity_async.py
import os, asyncio
from dotenv import load_dotenv
from telegram import Bot

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN missing in .env")

async def run():
    bot = Bot(token=BOT_TOKEN)
    me = await bot.get_me()
    print("bot id:", me.id, "bot username:", me.username)

asyncio.run(run())
