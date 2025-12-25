import os, asyncio
from dotenv import load_dotenv
from telegram import Bot

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
VERIFY_CHAT_ID = int(os.getenv("VERIFY_CHAT_ID") or 0)

async def run():
    bot = Bot(BOT_TOKEN)
    try:
        inv = await bot.create_chat_invite_link(chat_id=VERIFY_CHAT_ID, member_limit=1, expire_date=int(__import__('time').time())+300)
        print("invite ok:", inv.invite_link)
    except Exception as e:
        print("create_invite error:", repr(e))

asyncio.run(run())
