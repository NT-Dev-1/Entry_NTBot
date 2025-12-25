#!/usr/bin/env python3
# debug_grabids.py
# pip install python-telegram-bot==20.5 python-dotenv

import os
import logging
from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
if not BOT_TOKEN or not ADMIN_ID:
    raise SystemExit("Missing BOT_TOKEN or ADMIN_ID in .env")
ADMIN_ID = int(ADMIN_ID)

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Startup command registration
async def set_commands(application):
    cmds = [
        BotCommand("start", "Start the bot"),
        BotCommand("grabids", "Record users"),
        BotCommand("admins", "List admins"),
        BotCommand("export", "Report count"),
    ]
    try:
        await application.bot.set_my_commands(cmds)
        log.info("Commands registered: %s", await application.bot.get_my_commands())
    except Exception as e:
        log.exception("Failed to set commands: %s", e)

# Reply to /start and other command-based handlers
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("OK — I got /start. Send /grabids or try in a group. I will also DM admin.")

async def generic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split()[0] if update.message and update.message.text else "<unknown>"
    await update.message.reply_text(f"Got command: {cmd}")
    # notify admin that a command was received
    try:
        await context.bot.send_message(ADMIN_ID, f"Received command {cmd} from {update.effective_user.id} @{update.effective_user.username or 'N/A'}")
    except Exception as e:
        log.warning("Failed to DM admin: %s", e)

# Debug: log every incoming update and DM admin a short summary
async def debug_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.debug("RAW UPDATE: %s", update)
    # send summarised DM to admin (avoid spamming; keep minimal)
    try:
        uid = update.effective_user.id if update.effective_user else "no-user"
        await context.bot.send_message(ADMIN_ID, f"Update type {type(update).__name__} from {uid}")
    except Exception as e:
        log.warning("Failed to notify admin: %s", e)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.post_init = set_commands

    # Register handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler(["grabids", "admins", "export"], generic_cmd))
    # Catch any /command that somehow bypasses CommandHandler; useful in groups
    app.add_handler(MessageHandler(filters.COMMAND, generic_cmd))
    # Log every update for debugging (priority 0 so it runs early)
    app.add_handler(MessageHandler(filters.ALL, debug_update), 0)

    log.info("Debug bot starting. Make sure the admin opened a chat with the bot.")
    app.run_polling()

if __name__ == "__main__":
    main()
