# plugins/status_plugin.py
# /st status command for verify-bot + hourly automatic status report to admin
# Also provides heartbeat messages to admin at HEARTBEAT_INTERVAL.
# Uses Application.job_queue to schedule repeating jobs so tasks are started only when the app runs.

import time
from datetime import timedelta

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

import config
from . import db
from .verify_plugin import VERIFY_CHAT_ID
from .active_time_plugin import require_active

START_TS = time.time()
HOURLY_INTERVAL_SECONDS = 60 * 60  # 1 hour
HEARTBEAT_INTERVAL_SECONDS = getattr(config, "HEARTBEAT_INTERVAL", 5 * 60)

def _format_seconds(s: float) -> str:
    td = timedelta(seconds=int(s))
    days = td.days
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

async def build_status_text() -> str:
    uptime = _format_seconds(time.time() - START_TS)
    verify_cid = VERIFY_CHAT_ID or getattr(config, "VERIFY_CHAT_ID", None) or "unset"

    # DB stats (best-effort)
    try:
        pending = db.cursor.execute("SELECT COUNT(*) FROM sessions WHERE state='pending_admin'").fetchone()[0]
    except Exception:
        try:
            pending = len(db.list_pending_sessions(100))
        except Exception:
            pending = "n/a"

    try:
        active_invites = db.cursor.execute("SELECT COUNT(*) FROM invites WHERE revoked=0 AND expires_at > ?",
                                           (int(time.time()),)).fetchone()[0]
    except Exception:
        try:
            active_invites = len(db.get_unrevoked_invites(int(time.time())))
        except Exception:
            active_invites = "n/a"

    try:
        total_sessions = db.cursor.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    except Exception:
        total_sessions = "n/a"

    try:
        total_invites = db.cursor.execute("SELECT COUNT(*) FROM invites").fetchone()[0]
    except Exception:
        total_invites = "n/a"

    text = (
        f"verify-bot status\n\n"
        f"Uptime: {uptime}\n"
        f"Verify chat id: {verify_cid}\n\n"
        f"Pending sessions (admin review): {pending}\n"
        f"Active invites (unrevoked, not expired): {active_invites}\n"
        f"Total sessions (all): {total_sessions}\n"
        f"Total invites (all): {total_invites}\n\n"
        f"Config: RATE_LIMIT_SECONDS={getattr(config, 'RATE_LIMIT_SECONDS', 'n/a')} "
        f"INVITE_TTL={getattr(config, 'INVITE_TTL', 'n/a')} "
        f"HEARTBEAT_INTERVAL={getattr(config, 'HEARTBEAT_INTERVAL', 'n/a')}"
    )
    return text

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id if user else 0
    if uid != config.ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return

    text = await build_status_text()
    try:
        await update.message.reply_text(text)
    except Exception:
        try:
            await context.bot.send_message(chat_id=config.ADMIN_ID, text=text)
        except Exception:
            pass

async def _hourly_status_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        bot = context.bot
        text = await build_status_text()
        await bot.send_message(chat_id=config.ADMIN_ID, text=text)
    except Exception:
        try:
            db.log_event(0, 0, "status_job_failed", "failed to send hourly status")
        except Exception:
            pass

async def _heartbeat_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        bot = context.bot
        await bot.send_message(chat_id=config.ADMIN_ID, text="[💚] —  Plusing...")
    except Exception:
        try:
            db.log_event(0, 0, "heartbeat_job_failed", "failed to send heartbeat")
        except Exception:
            pass

def register(app):
    # Register manual command handlers (admin-only via require_active)
    app.add_handler(CommandHandler("st", require_active(cmd_status)))
    app.add_handler(CommandHandler("status", require_active(cmd_status)))

    # Schedule repeating jobs on post_init so they start only when the app runs
    existing = getattr(app, "post_init", None)

    async def _our_post_init(application):
        try:
            # hourly status: first run immediately, then every HOURLY_INTERVAL_SECONDS
            application.job_queue.run_repeating(_hourly_status_job, interval=HOURLY_INTERVAL_SECONDS, first=0)
            # heartbeat: repeat every configured heartbeat interval; first run after HEARTBEAT_INTERVAL_SECONDS
            application.job_queue.run_repeating(_heartbeat_job, interval=HEARTBEAT_INTERVAL_SECONDS, first=HEARTBEAT_INTERVAL_SECONDS)
        except Exception:
            pass

        # call previously configured post_init if present
        if existing is not None:
            maybe = existing(application)
            if hasattr(maybe, "__await__"):
                await maybe

    app.post_init = _our_post_init
