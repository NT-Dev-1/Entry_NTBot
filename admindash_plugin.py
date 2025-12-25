#———| Made By NT_Dev | plugins/admindash_plugin.py |
# Admin dashboard inline UI for verify-bot
# - /admindash opens a paginated inline keyboard of admin commands (4 per page)
# - Inline buttons send command templates to admin DM for copy/edit
# - /st sends a status summary (no dashboard)
# - /vis toggles member-list exposure (persisted in db.settings)
# - Defensive callback parsing, logs unexpected payloads to admin DM

import math
import os
from typing import List, Tuple, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from .active_time_plugin import require_active
from . import db

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

PAGE_SIZE = 4  # changed to 4 per your request
CALLBACK_PREFIX = "admindash"
SETTING_KEY_EXPOSE_MEMBERS = "expose_member_list"

# Admin command definitions (label, command_text, description)
ADMIN_COMMANDS: List[Tuple[str, str, str]] = [
    ("Approve", "/approve <user_id>", "Create one-time invite and approve user"),
    ("Reject", "/reject <user_id>", "Reject a pending verification"),
    ("Pending", "/pending", "List pending sessions"),
    ("Stats", "/stats", "Show basic stats"),
    ("SetMsg", "/setmsg <key> <text>", "Edit message templates"),
    ("InviteHist", "/invitehistory <user_id>", "Show invite history for user"),
    ("InviteHistCSV", "/invitehistory csv <user_id>", "Download invite history CSV"),
    ("Whitelist", "/whitelist <user_id>", "Whitelist a user"),
    ("Unwhitelist", "/unwhitelist <user_id>", "Remove whitelist"),
    ("Ban", "/ban <user_id>", "Ban a user from verification"),
    ("Unban", "/unban <user_id>", "Unban a user"),
    ("SetVerify", "/setverify <chat_id>", "Change target verify chat id"),
    ("ToggleMenu", "/vis", "Toggle member-list exposure via /vis (dashboard toggle also available)"),
]


def _get_expose_member_list_flag() -> bool:
    v = db.load_setting(SETTING_KEY_EXPOSE_MEMBERS)
    if v is None:
        return False
    return v == "1" or v is True


def _set_expose_member_list_flag(val: bool):
    db.save_setting(SETTING_KEY_EXPOSE_MEMBERS, "1" if val else "0")


def _build_page_keyboard(page: int) -> InlineKeyboardMarkup:
    total = len(ADMIN_COMMANDS)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    buttons = []

    for idx, (label, cmd, desc) in enumerate(ADMIN_COMMANDS[start:end], start=start):
        cb = f"{CALLBACK_PREFIX}:run:{idx}"
        buttons.append([InlineKeyboardButton(label, callback_data=cb)])

    # page nav
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀ Prev", callback_data=f"{CALLBACK_PREFIX}:page:{page-1}"))
    nav_row.append(InlineKeyboardButton(f"Page {page+1}/{pages}", callback_data=f"{CALLBACK_PREFIX}:noop:0"))
    if page < pages - 1:
        nav_row.append(InlineKeyboardButton("Next ▶", callback_data=f"{CALLBACK_PREFIX}:page:{page+1}"))
    buttons.append(nav_row)

    # toggle member-list expose button and close
    expose_flag = _get_expose_member_list_flag()
    toggle_label = "Hide member-list" if expose_flag else "Expose member-list"
    buttons.append([
        InlineKeyboardButton(toggle_label, callback_data=f"{CALLBACK_PREFIX}:toggle_members:0"),
        InlineKeyboardButton("Close", callback_data=f"{CALLBACK_PREFIX}:close:0"),
    ])

    return InlineKeyboardMarkup(buttons)


async def cmd_admindash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else 0
    if uid != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    try:
        if update.message:
            await update.message.delete()
    except Exception:
        pass
    kb = _build_page_keyboard(page=0)
    await context.bot.send_message(chat_id=uid, text="Admin dashboard", reply_markup=kb)


async def cmd_st_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else 0
    if uid != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return

    # Prefer status_plugin.build_status_text if available
    try:
        from .status_plugin import build_status_text  # type: ignore
        text = await build_status_text()
        await update.message.reply_text(text)
        return
    except Exception:
        pass

    # fallback summary
    pending = "n/a"
    try:
        pending = len(db.list_pending_sessions(100))
    except Exception:
        pass
    expose = "on" if _get_expose_member_list_flag() else "off"
    text = f"verify-bot status\nPending sessions: {pending}\nMember-list exposure: {expose}"
    await update.message.reply_text(text)


async def _dm_admin_unexpected(context: ContextTypes.DEFAULT_TYPE, payload: str):
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"[admindash] unexpected callback payload: {payload}")
    except Exception:
        pass


def _extract_parts(payload: str) -> Optional[Tuple[str, str, str]]:
    """
    Defensive extraction:
    - Normalize separators to colon
    - Split into at most 3 parts
    - Ensure first part matches CALLBACK_PREFIX
    Returns (prefix, action, arg) or None
    """
    if not payload:
        return None
    norm = payload.strip().replace(";", ":").replace("|", ":")
    parts = norm.split(":", 2)
    if len(parts) == 3 and parts[0] == CALLBACK_PREFIX:
        return parts[0], parts[1], parts[2]
    # try to recover by taking first token and last numeric token as arg
    tokens = norm.split(":")
    if not tokens:
        return None
    if tokens[0] != CALLBACK_PREFIX:
        # allow payloads that contain prefix later (e.g., "something:admindash:run:2")
        try:
            idx = tokens.index(CALLBACK_PREFIX)
        except ValueError:
            return None
        tokens = tokens[idx:]
    if len(tokens) < 2:
        return None
    action = tokens[1]
    # find last numeric token for arg
    arg = ""
    for t in reversed(tokens[2:]):
        if t.isdigit():
            arg = t
            break
    if arg == "":
        # fallback to empty arg
        arg = "0"
    return tokens[0], action, arg


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    # answer quickly so client doesn't hang
    try:
        await q.answer()
    except Exception:
        pass

    uid = q.from_user.id
    if uid != ADMIN_ID:
        try:
            await q.answer("Only admin may use this.", show_alert=True)
        except Exception:
            pass
        return

    payload = q.data or ""
    parts = _extract_parts(payload)
    if parts is None:
        # log for debug and inform admin once
        try:
            await _dm_admin_unexpected(context, payload)
        except Exception:
            pass
        try:
            await q.answer("Invalid callback data.", show_alert=True)
        except Exception:
            pass
        return

    _, action, arg = parts

    # handle actions
    if action == "noop":
        return

    if action == "page":
        try:
            page = int(arg)
        except Exception:
            page = 0
        kb = _build_page_keyboard(page)
        try:
            await q.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            try:
                await q.message.reply_text("Unable to update dashboard UI.")
            except Exception:
                pass
        return

    if action == "close":
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    if action == "toggle_members":
        curr = _get_expose_member_list_flag()
        _set_expose_member_list_flag(not curr)
        kb = _build_page_keyboard(page=0)
        txt = "Member-list exposure enabled." if not curr else "Member-list exposure disabled."
        try:
            await q.edit_message_text(text=txt, reply_markup=kb)
        except Exception:
            try:
                await q.edit_message_reply_markup(reply_markup=kb)
            except Exception:
                pass
        return

    if action == "run":
        try:
            idx = int(arg)
        except Exception:
            try:
                await q.answer("Invalid command index.", show_alert=True)
            except Exception:
                pass
            return
        if idx < 0 or idx >= len(ADMIN_COMMANDS):
            try:
                await q.answer("Invalid command.", show_alert=True)
            except Exception:
                pass
            return
        label, cmd_text, desc = ADMIN_COMMANDS[idx]
        if label == "ToggleMenu":
            try:
                await q.answer("Use /vis to toggle the member-list exposure (dashboard toggle also available).", show_alert=True)
            except Exception:
                pass
            return
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"{label} command:\n{cmd_text}\n\n{desc}")
            try:
                await q.answer("Command sent to admin chat.", show_alert=False)
            except Exception:
                pass
        except Exception:
            try:
                await q.answer("Failed to send command to admin chat.", show_alert=True)
            except Exception:
                pass
        return

    try:
        await q.answer("Unknown action.", show_alert=True)
    except Exception:
        pass


async def cmd_vis_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else 0
    if uid != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    try:
        curr = _get_expose_member_list_flag()
        _set_expose_member_list_flag(not curr)
        await update.message.reply_text("Member-list exposure enabled." if not curr else "Member-list exposure disabled.")
    except Exception as e:
        await update.message.reply_text(f"Failed to toggle: {e}")


def register(app):
    # /admindash opens the paginated UI (admin only)
    app.add_handler(CommandHandler("admindash", require_active(cmd_admindash)))
    # /st shows status only (admin only)
    app.add_handler(CommandHandler("st", require_active(cmd_st_status)))
    # /vis toggles member-list exposure (admin only)
    app.add_handler(CommandHandler("vis", require_active(cmd_vis_toggle)))
    # Register a broad callback handler (pattern omitted so all callbacks reach this handler)
    app.add_handler(CallbackQueryHandler(require_active(callback_handler)))