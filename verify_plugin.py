#---| Made By NT_Dev |
#---| plugins/verify_plugin.py |
import re
import os
import time
import io
import csv
import asyncio
import traceback
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, Message
from telegram.error import ChatMigrated, TelegramError, BadRequest
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    filters,
)

from . import db
from . import utils
from .active_time_plugin import require_active, register_background_tasks

#---| Config (read env and persisted verify id) |
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
_DEFAULT_VERIFY_CHAT_ID = os.getenv("VERIFY_CHAT_ID")
VERIFY_CHAT_ID = db.get_verify_chat_id() or (int(_DEFAULT_VERIFY_CHAT_ID) if _DEFAULT_VERIFY_CHAT_ID else None)

#---| Tunables |
RATE_LIMIT_SECONDS = 30
MAX_ATTEMPTS = 2
SESSION_TTL = 5 * 60           #---| session lifetime for captcha (seconds) |
INVITE_TTL = 2 * 60            #---| invites expire after 2 minutes |
INVITE_CLEANUP_INTERVAL = 5 * 60  #---| cleanup loop runs every 5 minutes |

#---| Minimal in-memory last-state store (keeps last UI state per user while process runs) |
LAST_STATE = {}  # uid -> {"state": "welcome"|"captcha"|"rules", "payload": {...}}

#---| Templates |
DEFAULT_TEMPLATES = {
    "msg_verified": "Verified — here's your one-time invite link (expires in {minutes} minutes):\n\n{link}",
    "msg_auto_fail_user": "Verified — auto-approve failed due to a system error. Admins have been notified and will review your request.",
    "msg_approved": "Approved — here's your one-time invite link (expires in {minutes} minutes):\n\n{link}",
    "msg_rejected": "Sorry, your verification was rejected by admin. You can try /verify again.",
    "msg_whitelisted": "You have been whitelisted by the admin and can join the group.",
    "msg_banned": "You have been banned from verification by the admin.",
}

def get_template(key: str):
    v = db.load_setting(key)
    return v if v is not None else DEFAULT_TEMPLATES.get(key, "")

def _persist_verify_chat_id(cid: int):
    global VERIFY_CHAT_ID
    VERIFY_CHAT_ID = int(cid)
    try:
        db.set_verify_chat_id(VERIFY_CHAT_ID)
    except Exception:
        db.log_event(0, 0, "db_set_verify_failed", f"{VERIFY_CHAT_ID}")
    db.log_event(0, 0, "chat_migrated", f"verify_chat_id updated to {VERIFY_CHAT_ID}")

#---| Navigation helpers / Back button |
def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="btn_back")]])

def main_user_kb():
    # /v removed visually; /verify kept
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("/verify", callback_data="btn_verify")],
            [InlineKeyboardButton("Rules", callback_data="btn_rules"), InlineKeyboardButton("Help", callback_data="btn_help")],
        ]
    )

async def safe_delete_message(bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        # ignore deletion errors (message already deleted / insufficient rights)
        pass

#---| Admin-only delivery helper (send errors/notifications to admin private chat) |
async def send_to_admin(bot, text, reply_markup=None, parse_mode=None):
    try:
        return await bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except ChatMigrated as e:
        # attempt to parse migrate id and persist if needed
        new_id = getattr(e, "migrate_to_chat_id", None)
        if new_id is None:
            try:
                new_id = int(str(e).split()[-1])
            except Exception:
                db.log_event(0, 0, "admin_send_migrate_parse_fail", str(e))
                raise
        _persist_verify_chat_id(new_id)
        try:
            return await bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except TelegramError as e2:
            db.log_event(0, 0, "admin_send_failed_after_migrate", str(e2))
            return None
    except TelegramError as e:
        db.log_event(0, 0, "admin_send_failed", str(e))
        return None

#---| Robust send to verify chat (handles ChatMigrated) |
async def send_to_verify_chat(bot, text, reply_markup=None, parse_mode=None):
    global VERIFY_CHAT_ID
    try:
        return await bot.send_message(chat_id=VERIFY_CHAT_ID, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except ChatMigrated as e:
        new_id = getattr(e, "migrate_to_chat_id", None)
        if new_id is None:
            try:
                new_id = int(str(e).split()[-1])
            except Exception:
                db.log_event(0, 0, "chat_migrate_parse_fail", str(e))
                raise
        _persist_verify_chat_id(new_id)
        try:
            return await bot.send_message(chat_id=VERIFY_CHAT_ID, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except TelegramError as e2:
            db.log_event(0, 0, "admin_send_failed_after_migrate", str(e2))
            return None
    except TelegramError as e:
        db.log_event(0, 0, "admin_send_failed", str(e))
        return None

#---| Invite creation with ChatMigrated handling (invite still created for VERIFY_CHAT_ID) |
async def create_one_time_invite(bot, chat_id: int, member_limit: int = 1, ttl: int = INVITE_TTL):
    global VERIFY_CHAT_ID
    try:
        invite = await bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=member_limit,
            expire_date=db.now_ts() + ttl,
        )
        return invite
    except ChatMigrated as e:
        new_id = getattr(e, "migrate_to_chat_id", None)
        if new_id is None:
            try:
                new_id = int(str(e).split()[-1])
            except Exception:
                db.log_event(0, 0, "create_invite_migrate_parse_fail", str(e))
                raise
        _persist_verify_chat_id(new_id)
        invite = await bot.create_chat_invite_link(
            chat_id=VERIFY_CHAT_ID,
            member_limit=member_limit,
            expire_date=db.now_ts() + ttl,
        )
        return invite

#---| Admin action keyboard |
def admin_action_kb(uid: int):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Approve", callback_data=f"approve:{uid}"),
                InlineKeyboardButton("Reject", callback_data=f"reject:{uid}"),
            ],
            [
                InlineKeyboardButton("Whitelist", callback_data=f"whitelist:{uid}"),
                InlineKeyboardButton("Ban", callback_data=f"ban:{uid}"),
            ],
            [
                InlineKeyboardButton("Invite history", callback_data=f"invhist:{uid}"),
                InlineKeyboardButton("Invite history CSV", callback_data=f"invhist_csv:{uid}"),
            ],
            [
                InlineKeyboardButton("Back", callback_data="btn_back_admin"),
            ],
        ]
    )

#---| Revoke helpers that call Telegram |
async def revoke_invite(bot, invite_link: str, chat_id: int, revoked_by: int = None):
    try:
        await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=invite_link)
        db.mark_invite_revoked(invite_link, revoked_by=revoked_by)
        db.log_event(0, revoked_by or 0, "invite_revoked", invite_link)
        return True
    except TelegramError as e:
        err = str(e)
        db.log_event(0, 0, "revoke_failed", err)
        if "INVITE_LINK" in err.upper() or "NOT FOUND" in err.upper() or "invalid" in err.lower():
            db.mark_invite_revoked(invite_link, revoked_by=revoked_by)
            return True
        return False

async def revoke_all_other_invites_for_user(bot, uid: int, exclude_link: str = None, revoked_by: int = None):
    others = db.get_other_unrevoked_invites_for_user(uid, exclude_link)
    for rec in others:
        _, link, chat_id = rec
        try:
            await revoke_invite(bot, link, chat_id, revoked_by=revoked_by)
        except Exception as e:
            db.log_event(uid, revoked_by or 0, "revoke_other_failed", str(e))

#---| Invite cleanup loop |
async def invite_cleanup_loop(app):
    bot = app.bot
    while True:
        now = db.now_ts()
        rows = db.get_expired_unrevoked_invites(now)
        for r in rows:
            _, link, chat_id = r
            try:
                await revoke_invite(bot, link, chat_id)
            except Exception as e:
                db.log_event(0, 0, "invite_cleanup_exception", str(e))
                try:
                    await send_to_admin(bot, f"Invite cleanup exception: {e}")
                except Exception:
                    pass
        await asyncio_sleep(INVITE_CLEANUP_INTERVAL)

#---| Small compatibility wrapper for sleeping that works inside PTB context |
async def asyncio_sleep(seconds: int):
    import asyncio as _asyncio
    await _asyncio.sleep(seconds)

#---| Permission check at startup |
async def check_startup_permissions(app):
    bot = app.bot
    if VERIFY_CHAT_ID is None:
        db.log_event(0, 0, "perm_missing", "VERIFY_CHAT_ID not set")
        try:
            await send_to_admin(bot, "Warning: VERIFY_CHAT_ID not set; verify flow may fail.")
        except Exception:
            pass
        return
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=VERIFY_CHAT_ID, user_id=me.id)
    except Exception as e:
        db.log_event(0, 0, "startup_perm_check_failed", str(e))
        try:
            await send_to_admin(bot, f"Warning: failed to check bot permissions: {e}")
        except Exception:
            pass
        return

    is_admin = getattr(member, "status", "") in ("administrator", "creator")
    can_invite = bool(getattr(member, "can_invite_users", False) or getattr(member, "can_manage_chat", False))
    if not is_admin or not can_invite:
        note = f"Startup permission warning: bot admin/invite rights missing in verify chat (id {VERIFY_CHAT_ID}). Auto-approve may fail."
        db.log_event(0, 0, "perm_missing", note)
        try:
            await send_to_admin(bot, note)
        except Exception:
            pass

# -------------------------
# Handlers
# -------------------------

async def start(update: Update, context):
    """Welcome message (also kept for /start). Shows user commands with inline buttons.
       Admins receive admin commands sent privately."""
    user = update.effective_user
    uid = user.id if user else 0

    # tidy: delete invoking message if possible
    try:
        if update.message:
            await safe_delete_message(context.bot, update.effective_chat.id, update.message.message_id)
    except Exception:
        pass

    if db.is_banned(uid):
        await context.bot.send_message(chat_id=uid, text="You are banned from verification. Contact an admin if this is a mistake.")
        db.log_event(uid, 0, "start_blocked_banned", "")
        return
    if db.is_whitelisted(uid):
        await context.bot.send_message(chat_id=uid, text="You are already whitelisted. Use your normal account to join the group.")
        db.log_event(uid, 0, "start_blocked_whitelisted", "")
        return

    # Personalized welcome message
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    if not name:
        name = user.username or "there"
    welcome_text = (
        f"Welcome {name} to Northern Terpz Verification.\n\n"
        "Use the following commands to verify for Northern Terpz Lounge.\n\n"
        "Please do not discuss illegal activities, materials, Politics, etc, in The Lounge — this is for Northern Clan Members only.\n\n"
        "— Contact administrators once verified for support and enquiries...\n\n"
        "—| Made by NT_Dev."
    )

    # Send welcome in private chat (safer) or reply in same chat if private already
    try:
        sent = await context.bot.send_message(chat_id=uid, text=welcome_text, reply_markup=main_user_kb())
    except Exception:
        # fallback to replying in the current chat
        sent = await context.bot.send_message(chat_id=update.effective_chat.id, text=welcome_text, reply_markup=main_user_kb())

    LAST_STATE[uid] = {"state": "welcome", "payload": {}}
    db.log_event(uid, 0, "start_sent_welcome", "")

    # If this user is the admin, send the admin-only command list privately
    if uid == ADMIN_ID:
        admin_text = (
            "Admin commands (private):\n\n"
            "/st — send heartbeat\n"
            "/setverify — Update Target Link\n"
            "/approve <user_id>\n"
            "/reject <user_id>\n"
            "/pending\n"
            "/stats\n"
            "/setmsg <key> <text>\n"
            "/invitehistory <user_id> | /invitehistory csv <user_id>\n"
            "/whitelist <user_id>\n"
            "/unwhitelist <user_id>\n"
            "/ban <user_id>\n"
            "/unban <user_id>\n\n"
            "Use these commands here in this private chat with the bot."
        )
        try:
            await send_to_admin(context.bot, admin_text)
        except Exception:
            db.log_event(ADMIN_ID, 0, "admin_dm_failed", "Failed to send admin commands DM")

#---| Button press handler for welcome inline buttons |
async def welcome_button_handler(update: Update, context):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    uid = q.from_user.id

    # delete the inline message that had the buttons to keep chat tidy
    try:
        await q.message.delete()
    except Exception:
        pass

    if data == "btn_verify":
        # Start verify flow
        await begin_verify_command(update, context)
        return
    if data == "btn_rules":
        text = "Rules: Please be respectful. No illegal content, politics, or promotions. Northern Clan members only."
        await context.bot.send_message(chat_id=uid, text=text, reply_markup=back_kb())
        LAST_STATE[uid] = {"state": "rules", "payload": {}}
        return
    if data == "btn_help":
        text = "Help: Use /verify to start. If you have issues, contact the admins after verification."
        await context.bot.send_message(chat_id=uid, text=text, reply_markup=back_kb())
        LAST_STATE[uid] = {"state": "help", "payload": {}}
        return
    if data == "btn_back":
        await start(update, context)
        return
    if data == "btn_back_admin":
        # admin back from admin inline keyboard: re-send admin actions or info
        if uid == ADMIN_ID:
            await send_to_admin(context.bot, "Admin actions: use the commands listed previously.")
        return

    await q.answer("Unknown action.", show_alert=True)

#---| Entry point for /verify and plain text 'verify' messages |
async def begin_verify_command(update: Update, context):
    # Accept either Command or CallbackQuery or Message. Normalize to update and message
    q = update.callback_query if update.callback_query else None
    message = q.message if q else update.message
    from_user = q.from_user if q else update.effective_user

    user = from_user
    uid = user.id if user else 0

    # tidy: delete the triggering message (user command) if present
    try:
        if update.message:
            await safe_delete_message(context.bot, update.effective_chat.id, update.message.message_id)
        elif update.callback_query and update.callback_query.message:
            # callback message was just deleted earlier in welcome_button_handler
            pass
    except Exception:
        pass

    if db.is_banned(uid):
        await context.bot.send_message(chat_id=uid, text="You are banned from verification. Contact an admin if this is a mistake.")
        db.log_event(uid, 0, "start_blocked_banned", "")
        return
    if db.is_whitelisted(uid):
        await context.bot.send_message(chat_id=uid, text="You are already whitelisted. Use your normal account to join the group.")
        db.log_event(uid, 0, "start_blocked_whitelisted", "")
        return

    now = db.now_ts()
    s = db.get_session(uid)
    if s and (now - s[4]) < RATE_LIMIT_SECONDS:
        wait = RATE_LIMIT_SECONDS - (now - s[4])
        await context.bot.send_message(chat_id=uid, text=f"Please wait {wait} more seconds before requesting a new captcha.", reply_markup=back_kb())
        return

    chosen, options = utils.gen_emoji_challenge()
    db.save_session(uid, chosen, "awaiting_captcha", attempts=0, ttl=SESSION_TTL)
    LAST_STATE[uid] = {"state": "captcha", "payload": {"chosen": chosen, "options": options}}

    row = [InlineKeyboardButton(opt, callback_data=f"captcha:{opt}:{uid}") for opt in options]
    kb = InlineKeyboardMarkup([row, [InlineKeyboardButton("Back", callback_data="btn_back")]])

    await context.bot.send_message(chat_id=uid, text=f"Solve this captcha by clicking the matching emoji button below.\n\nSelect the emoji that matches this token: {chosen}", reply_markup=kb)
    db.log_event(uid, 0, "start_sent_captcha_inline", "options=" + " ".join(options))

#---| Callback handler for inline buttons and admin actions |
async def callback_handler(update: Update, context):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    # route welcome button callbacks (including back)
    if data.startswith("btn_"):
        await welcome_button_handler(update, context)
        return

    if data.startswith("captcha:"):
        try:
            _, picked, uid_s = data.split(":", 2)
            uid = int(uid_s)
        except Exception:
            await q.edit_message_text("Invalid captcha data.")
            return

        if q.from_user.id != uid:
            await q.answer("This captcha is not for you.", show_alert=True)
            return

        s = db.get_session(uid)
        if not s or s[2] != "awaiting_captcha":
            await q.edit_message_text((q.message.text or "") + "\n\nNo active captcha (maybe expired). Send /verify to try again.")
            return
        _, answer, _, _, _, expires_at = s
        if db.now_ts() > expires_at:
            db.del_session(uid)
            await q.edit_message_text((q.message.text or "") + "\n\nCaptcha expired. Send /verify to try again.")
            LAST_STATE.pop(uid, None)
            return

        if picked == answer:
            try:
                invite = await create_one_time_invite(context.bot, VERIFY_CHAT_ID, member_limit=1, ttl=INVITE_TTL)
                link = invite.invite_link
                db.del_session(uid)
                db.store_invite(link, VERIFY_CHAT_ID, db.now_ts() + INVITE_TTL, user_id=uid, approved_by=0)
                await revoke_all_other_invites_for_user(context.bot, uid, exclude_link=link, revoked_by=0)
                # Send invite as MarkdownV2 spoiler (falls back to plain message)
                escaped = utils.escape_md_v2(link)
                tpl = get_template("msg_verified")
                spoiler_text = tpl.format(link=f"||{escaped}||", minutes=INVITE_TTL//60)
                try:
                    await context.bot.send_message(chat_id=uid, text=spoiler_text, parse_mode="MarkdownV2", reply_markup=back_kb())
                except BadRequest as bre:
                    db.log_event(uid, 0, "markdownv2_send_failed", str(bre))
                    fallback_text = get_template("msg_verified").format(link=link, minutes=INVITE_TTL//60)
                    await context.bot.send_message(chat_id=uid, text=fallback_text, reply_markup=back_kb())
                try:
                    await q.edit_message_text((q.message.text or "") + "\n\nCaptcha correct. Invite sent to your DM.")
                except Exception:
                    pass
                LAST_STATE.pop(uid, None)
                db.log_event(uid, 0, "auto_approved", f"invite_sent ttl={INVITE_TTL}")
            except Exception as e:
                err = str(e)
                db.log_event(uid, 0, "auto_approve_failed", err)
                db.save_session(uid, answer, "pending_admin", attempts=0, ttl=SESSION_TTL)
                caption = (
                    f"Verification request (auto-approve failed): @{q.from_user.username or 'no_username'} (id {uid})\n\n"
                    f"Auto-approve failed with error:\n{err}\n\nPlease approve or reject."
                )
                kb = admin_action_kb(uid)
                try:
                    await send_to_admin(context.bot, caption, reply_markup=kb)
                except Exception as e2:
                    db.log_event(uid, 0, "admin_notify_fail_after_auto_fail", str(e2))
                await context.bot.send_message(chat_id=uid, text=get_template("msg_auto_fail_user"), reply_markup=back_kb())
                try:
                    await q.edit_message_text((q.message.text or "") + "\n\nCaptcha correct but auto-approve failed; admins notified.")
                except Exception:
                    pass
            return
        else:
            attempts = db.inc_attempt(uid)
            if attempts >= MAX_ATTEMPTS:
                caption = f"User @{q.from_user.username or 'no_username'} (id {uid}) failed captcha {attempts} times and requires manual review."
                kb = admin_action_kb(uid)
                try:
                    await send_to_admin(context.bot, caption, reply_markup=kb)
                except Exception as e:
                    db.log_event(uid, 0, "escalation_notify_fail", str(e))
                db.del_session(uid)
                LAST_STATE.pop(uid, None)
                await q.edit_message_text((q.message.text or "") + "\n\nToo many failed attempts — admins have been notified.")
                db.log_event(uid, 0, "captcha_escalated", f"attempts={attempts}")
            else:
                await q.edit_message_text((q.message.text or "") + f"\n\nIncorrect. You have {MAX_ATTEMPTS - attempts} attempts left. Send /verify to try again.")
                db.log_event(uid, 0, "captcha_fail_inline", f"attempts={attempts}")
            return

    # Admin callbacks (approve/reject/whitelist/ban/invhist/invhist_csv)
    if ":" not in data:
        await q.answer("Unknown action.")
        return

    parts = data.split(":", 2)
    action = parts[0]
    try:
        uid = int(parts[1])
    except ValueError:
        await q.edit_message_text(text=(q.message.text or "") + "\n\nInvalid callback data.")
        return

    if q.from_user.id != ADMIN_ID:
        await q.answer("Only the configured admin may use these buttons.", show_alert=True)
        return

    #---| Approve |
    if action == "approve":
        try:
            invite = await create_one_time_invite(context.bot, VERIFY_CHAT_ID, member_limit=1, ttl=INVITE_TTL)
            link = invite.invite_link
            db.store_invite(link, VERIFY_CHAT_ID, db.now_ts() + INVITE_TTL, user_id=uid, approved_by=q.from_user.id)
            await revoke_all_other_invites_for_user(context.bot, uid, exclude_link=link, revoked_by=q.from_user.id)
        except Exception as e:
            await q.edit_message_text(text=(q.message.text or "") + f"\n\nApproval failed: {e}")
            db.log_event(uid, ADMIN_ID, "approve_fail", str(e))
            try:
                await send_to_admin(context.bot, f"Approval failed for uid {uid}: {e}")
            except Exception:
                pass
            return
        db.del_session(uid)
        escaped = utils.escape_md_v2(link)
        tpl = get_template("msg_approved")
        spoiler_text = tpl.format(link=f"||{escaped}||", minutes=INVITE_TTL//60)
        try:
            await context.bot.send_message(chat_id=uid, text=spoiler_text, parse_mode="MarkdownV2", reply_markup=back_kb())
        except BadRequest as bre:
            db.log_event(uid, ADMIN_ID, "markdownv2_send_failed_adminflow", str(bre))
            fallback_text = get_template("msg_approved").format(link=link, minutes=INVITE_TTL//60)
            await context.bot.send_message(chat_id=uid, text=fallback_text, reply_markup=back_kb())

        await q.edit_message_text(text=(q.message.text or "") + f"\n\nApproved. Invite sent to user.")
        db.log_event(uid, ADMIN_ID, "approved", "")

    #---| Reject |
    elif action == "reject":
        db.del_session(uid)
        await context.bot.send_message(chat_id=uid, text=get_template("msg_rejected"), reply_markup=back_kb())
        await q.edit_message_text(text=(q.message.text or "") + "\n\nRejected by admin.")
        db.log_event(uid, ADMIN_ID, "rejected", "")

    #---| Whitelist |
    elif action == "whitelist":
        db.set_whitelist(uid, True, ADMIN_ID, note="whitelisted via admin button")
        await q.edit_message_text(text=(q.message.text or "") + "\n\nUser whitelisted by admin.")
        await context.bot.send_message(chat_id=uid, text=get_template("msg_whitelisted"), reply_markup=back_kb())

    #---| Ban |
    elif action == "ban":
        db.set_ban(uid, True, ADMIN_ID, note="banned via admin button")
        db.del_session(uid)
        await q.edit_message_text(text=(q.message.text or "") + "\n\nUser banned by admin.")
        await context.bot.send_message(chat_id=uid, text=get_template("msg_banned"), reply_markup=back_kb())

    #---| Manual review request |
    elif action == "manual":
        await q.edit_message_text(text=(q.message.text or "") + "\n\nAdmin requested manual verification.")
        db.log_event(uid, ADMIN_ID, "manual_review_requested", "")

    #---| Invite history |
    elif action == "invhist":
        rows = db.get_all_invites_for_user(uid, limit=200)
        if not rows:
            await q.answer("No invites found for that user.", show_alert=True)
            return
        out = []
        for r in rows:
            iid, link, chat_id, created_at, expires_at, revoked, approved_by, revoked_by = r
            created_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at))
            expires_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expires_at))
            out.append(f"id={iid} | revoked={revoked} | created={created_s} | expires={expires_s} | approved_by={approved_by} | revoked_by={revoked_by} | link={link}")
        text = "\n".join(out)
        if len(text) <= 1000:
            await q.answer(text, show_alert=True)
        else:
            for chunk_start in range(0, len(text), 4000):
                await context.bot.send_message(chat_id=ADMIN_ID, text=text[chunk_start:chunk_start+4000])
            await q.answer("Invite history sent to admin chat.", show_alert=True)

    #---| Invite history CSV |
    elif action == "invhist_csv":
        rows = db.get_all_invites_for_user(uid, limit=2000)
        if not rows:
            await q.answer("No invites found for that user.", show_alert=True)
            return
        out_io = io.StringIO()
        writer = csv.writer(out_io)
        writer.writerow(["id", "invite_link", "chat_id", "created_at", "expires_at", "revoked", "approved_by", "revoked_by"])
        for r in rows:
            iid, link, chat_id, created_at, expires_at, revoked, approved_by, revoked_by = r
            writer.writerow([iid, link, chat_id, created_at, expires_at, revoked, approved_by, revoked_by])
        csv_text = out_io.getvalue()
        out_io.close()
        for chunk_start in range(0, len(csv_text), 4000):
            await context.bot.send_message(chat_id=ADMIN_ID, text=csv_text[chunk_start:chunk_start+4000])
        await q.answer("CSV invite history sent to admin chat.", show_alert=True)

    else:
        await q.answer("Unknown action.")

# -------------------------
# Admin command handlers
# -------------------------

async def _delete_invoking_message(update: Update, context):
    try:
        if update.message:
            await safe_delete_message(context.bot, update.effective_chat.id, update.message.message_id)
    except Exception:
        pass

async def cmd_approve(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    await _delete_invoking_message(update, context)
    if not context.args:
        await update.message.reply_text("Usage: /approve <user_id>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user id.")
        return
    s = db.get_session(uid)
    if s is None:
        await update.message.reply_text("No pending session for that user.")
        return
    try:
        invite = await create_one_time_invite(context.bot, VERIFY_CHAT_ID, member_limit=1, ttl=INVITE_TTL)
        link = invite.invite_link
        db.store_invite(link, VERIFY_CHAT_ID, db.now_ts() + INVITE_TTL, user_id=uid, approved_by=update.effective_user.id)
        await revoke_all_other_invites_for_user(context.bot, uid, exclude_link=link, revoked_by=update.effective_user.id)
    except Exception as e:
        await update.message.reply_text(f"Invite creation failed: {e}")
        db.log_event(uid, ADMIN_ID, "approve_cmd_invite_fail", str(e))
        try:
            await send_to_admin(context.bot, f"Approve command failed for uid {uid}: {e}")
        except Exception:
            pass
        return
    db.del_session(uid)
    tpl = get_template("msg_approved")
    escaped = utils.escape_md_v2(link)
    spoiler_text = tpl.format(link=f"||{escaped}||", minutes=INVITE_TTL//60)
    try:
        await context.bot.send_message(chat_id=uid, text=spoiler_text, parse_mode="MarkdownV2", reply_markup=back_kb())
    except BadRequest:
        await context.bot.send_message(chat_id=uid, text=get_template("msg_approved").format(link=link, minutes=INVITE_TTL//60), reply_markup=back_kb())
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"Approved {uid} and invite sent.")
    db.log_event(uid, ADMIN_ID, "approved_cmd", "")

async def cmd_reject(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    await _delete_invoking_message(update, context)
    if not context.args:
        await update.message.reply_text("Usage: /reject <user_id>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user id.")
        return
    db.del_session(uid)
    await context.bot.send_message(chat_id=uid, text=get_template("msg_rejected"), reply_markup=back_kb())
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"Rejected {uid}.")
    db.log_event(uid, ADMIN_ID, "rejected_cmd", "")

async def cmd_pending(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    await _delete_invoking_message(update, context)
    rows = db.list_pending_sessions(100)
    if not rows:
        await context.bot.send_message(chat_id=ADMIN_ID, text="No pending sessions.")
        return
    out = []
    for r in rows:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[1]))
        out.append(f"uid={r[0]} started={ts}")
    for chunk_start in range(0, len("\n".join(out)), 4000):
        await context.bot.send_message(chat_id=ADMIN_ID, text="\n".join(out)[chunk_start:chunk_start+4000])

async def cmd_stats(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    await _delete_invoking_message(update, context)
    total_attempts = db.cursor.execute("SELECT COUNT(*) FROM logs WHERE event_type LIKE 'attempt_inc'").fetchone()[0]
    total_approved = db.cursor.execute("SELECT COUNT(*) FROM logs WHERE event_type='approved'").fetchone()[0]
    total_banned = db.cursor.execute("SELECT COUNT(*) FROM logs WHERE event_type='ban'").fetchone()[0]
    pending = db.cursor.execute("SELECT COUNT(*) FROM sessions WHERE state='pending_admin'").fetchone()[0]
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"attempts={total_attempts} approved={total_approved} banned={total_banned} pending={pending}")

async def cmd_setmsg(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    await _delete_invoking_message(update, context)
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setmsg <key> <text>\nKeys: msg_verified, msg_auto_fail_user, msg_approved, msg_rejected, msg_whitelisted, msg_banned")
        return
    key = context.args[0]
    text = " ".join(context.args[1:])
    # Save template (validate minimal length)
    if not text or len(text) < 3:
        await context.bot.send_message(chat_id=ADMIN_ID, text="Template too short.")
        return
    db.save_setting(key, text)
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"Saved template {key}.")

# --- New runtime admin command: setverify ---
async def cmd_set_verify_chat(update: Update, context):
    """Admin command: /setverify <chat_id>
    Persists VERIFY_CHAT_ID in DB (if helper exists) and updates runtime global VERIFY_CHAT_ID.
    """
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    await _delete_invoking_message(update, context)
    if not context.args:
        await update.message.reply_text("Usage: /setverify <chat_id>")
        return
    try:
        cid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid chat id. Use numeric id (supergroups: -100...)")
        return
    # persist to DB if available
    try:
        if hasattr(db, "set_verify_chat_id"):
            db.set_verify_chat_id(cid)
            db.log_event(ADMIN_ID, ADMIN_ID, "set_verify_chat_db", f"{cid}")
    except Exception as e:
        db.log_event(ADMIN_ID, ADMIN_ID, "set_verify_chat_db_fail", str(e))
    # update runtime var
    try:
        global VERIFY_CHAT_ID
        VERIFY_CHAT_ID = int(cid)
    except Exception:
        pass
    await update.message.reply_text(f"VERIFY_CHAT_ID updated to {cid}")
    try:
        await send_to_admin(context.bot, f"VERIFY_CHAT_ID updated to {cid} by admin {update.effective_user.id}")
    except Exception:
        pass

async def cmd_invitehistory(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    await _delete_invoking_message(update, context)
    if not context.args:
        await context.bot.send_message(chat_id=ADMIN_ID, text="Usage: /invitehistory <user_id> or /invitehistory csv <user_id>")
        return
    if context.args[0].lower() == "csv" and len(context.args) >= 2:
        try:
            uid = int(context.args[1])
        except ValueError:
            await context.bot.send_message(chat_id=ADMIN_ID, text="Invalid user id.")
            return
        rows = db.get_all_invites_for_user(uid, limit=2000)
        if not rows:
            await context.bot.send_message(chat_id=ADMIN_ID, text="No invites found for that user.")
            return
        out_io = io.StringIO()
        writer = csv.writer(out_io)
        writer.writerow(["id", "invite_link", "chat_id", "created_at", "expires_at", "revoked", "approved_by", "revoked_by"])
        for r in rows:
            iid, link, chat_id, created_at, expires_at, revoked, approved_by, revoked_by = r
            writer.writerow([iid, link, chat_id, created_at, expires_at, revoked, approved_by, revoked_by])
        csv_text = out_io.getvalue()
        out_io.close()
        for chunk_start in range(0, len(csv_text), 4000):
            await context.bot.send_message(chat_id=ADMIN_ID, text=csv_text[chunk_start:chunk_start+4000])
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await context.bot.send_message(chat_id=ADMIN_ID, text="Invalid user id.")
        return
    rows = db.get_all_invites_for_user(uid, limit=200)
    if not rows:
        await context.bot.send_message(chat_id=ADMIN_ID, text="No invites found for that user.")
        return
    out = []
    for r in rows:
        iid, link, chat_id, created_at, expires_at, revoked, approved_by, revoked_by = r
        created_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at))
        expires_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expires_at))
        out.append(f"id={iid} | revoked={revoked} | created={created_s} | expires={expires_s} | approved_by={approved_by} | revoked_by={revoked_by} | link={link}")
    text = "\n".join(out)
    for chunk_start in range(0, len(text), 4000):
        await context.bot.send_message(chat_id=ADMIN_ID, text=text[chunk_start:chunk_start+4000])

async def cmd_whitelist(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    await _delete_invoking_message(update, context)
    args = context.args
    if not args:
        await context.bot.send_message(chat_id=ADMIN_ID, text="Usage: /whitelist <user_id>")
        return
    try:
        uid = int(args[0])
    except ValueError:
        await context.bot.send_message(chat_id=ADMIN_ID, text="Invalid user id.")
        return
    db.set_whitelist(uid, True, ADMIN_ID, note="whitelisted via command")
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"Whitelisted {uid}.")

async def cmd_unwhitelist(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    await _delete_invoking_message(update, context)
    args = context.args
    if not args:
        await context.bot.send_message(chat_id=ADMIN_ID, text="Usage: /unwhitelist <user_id>")
        return
    try:
        uid = int(args[0])
    except ValueError:
        await context.bot.send_message(chat_id=ADMIN_ID, text="Invalid user id.")
        return
    db.set_whitelist(uid, False, ADMIN_ID, note="unwhitelisted via command")
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"Unwhitelisted {uid}.")

async def cmd_ban(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    await _delete_invoking_message(update, context)
    args = context.args
    if not args:
        await context.bot.send_message(chat_id=ADMIN_ID, text="Usage: /ban <user_id>")
        return
    try:
        uid = int(args[0])
    except ValueError:
        await context.bot.send_message(chat_id=ADMIN_ID, text="Invalid user id.")
        return
    db.set_ban(uid, True, ADMIN_ID, note="banned via command")
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"Banned {uid}.")

async def cmd_unban(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not allowed.")
        return
    await _delete_invoking_message(update, context)
    args = context.args
    if not args:
        await context.bot.send_message(chat_id=ADMIN_ID, text="Usage: /unban <user_id>")
        return
    try:
        uid = int(args[0])
    except ValueError:
        await context.bot.send_message(chat_id=ADMIN_ID, text="Invalid user id.")
        return
    db.set_ban(uid, False, ADMIN_ID, note="unbanned via command")
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"Unbanned {uid}.")

#---| chat_member_update handler kept as-is (revoke on join) |
async def chat_member_update(update: Update, context):
    chat = update.chat_member.chat
    if VERIFY_CHAT_ID is None or chat.id != VERIFY_CHAT_ID:
        return
    new_status = update.chat_member.new_chat_member.status
    if new_status == "member":
        user = update.chat_member.new_chat_member.user
        uid = user.id
        rec = db.get_unrevoked_invite_for_user(uid)
        if rec:
            _, link, chat_id = rec
            try:
                await revoke_invite(context.bot, link, chat_id, revoked_by=0)
            except Exception as e:
                db.log_event(uid, 0, "revoke_on_join_failed", str(e))
                try:
                    await send_to_admin(context.bot, f"Revoke on join failed for uid {uid}: {e}")
                except Exception:
                    pass
            try:
                await revoke_all_other_invites_for_user(context.bot, uid, exclude_link=link, revoked_by=0)
            except Exception as e:
                db.log_event(uid, 0, "revoke_other_on_join_failed", str(e))
                try:
                    await send_to_admin(context.bot, f"Revoke-other on join failed for uid {uid}: {e}")
                except Exception:
                    pass

#---| Global error handler (sends traceback to admin instead of group) |
async def global_error_handler(update: Optional[Update], context):
    try:
        err = getattr(context, "error", None)
        if err is not None:
            tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
            msg = f"Unhandled exception: {err}\n\nTraceback:\n{tb}"
        else:
            msg = "Unhandled exception occurred (no context.error available)."
        await send_to_admin(context.bot, msg)
    except Exception:
        pass

#---| Register function to add handlers to app |
def register(app):
    # Basic config validation
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if ADMIN_ID == 0:
        missing.append("ADMIN_ID (env)")
    if VERIFY_CHAT_ID is None:
        missing.append("VERIFY_CHAT_ID (env or db)")
    if missing:
        # log and try to notify admin if possible (best-effort)
        db.log_event(0, 0, "config_missing", ",".join(missing))
        # don't raise here; leave it to check_startup_permissions and invite_cleanup_loop to notify
    # register handlers
    app.add_handler(CommandHandler("start", require_active(start)))
    app.add_handler(CommandHandler("verify", require_active(begin_verify_command)))
    # keep /v command operational but not shown on keyboard
    app.add_handler(CommandHandler("v", require_active(begin_verify_command)))

    # Plain text triggers: "verify" or "v" without slash (case-insensitive) using compiled regex |delete # to activate----
    
    #app.add_handler(MessageHandler(filters.Regex(re.compile(r"^(?:/?)(?:verify|v)$", re.IGNORECASE)) & ~filters.COMMAND, require_active(begin_verify_command)))

    # Callback handlers for inline buttons
    app.add_handler(CallbackQueryHandler(require_active(callback_handler)))

    # Catch-all text handler (friendly hint)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), require_active(lambda u, c: u.message.reply_text("Send /verify to begin verification."))))

    # chat member updates - detect joins
    app.add_handler(ChatMemberHandler(chat_member_update, chat_member_types=ChatMemberHandler.CHAT_MEMBER))

    # admin commands
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("setmsg", cmd_setmsg))
    app.add_handler(CommandHandler("invitehistory", cmd_invitehistory))
    app.add_handler(CommandHandler("whitelist", cmd_whitelist))
    app.add_handler(CommandHandler("unwhitelist", cmd_unwhitelist))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    # register runtime setverify admin command
    app.add_handler(CommandHandler("setverify", cmd_set_verify_chat))

    # create our startup coroutine
    async def _our_post_startup(application):
        # Notify admin on startup
        try:
            await send_to_admin(application.bot, "[Startup] Bot is online")
        except Exception:
            pass
        await check_startup_permissions(application)
        # background tasks: schedule invite cleanup only
        application.create_task(invite_cleanup_loop(application))

    # Normalize and merge with any existing post_init (it may be None, a coroutine/coro-func, or a list)
    existing = getattr(app, "post_init", None)

    if existing is None:
        app.post_init = _our_post_startup
    else:
        async def _merged_post_init(application):
            if isinstance(existing, list):
                for item in existing:
                    if callable(item):
                        maybe = item(application)
                        if hasattr(maybe, "__await__"):
                            await maybe
                    elif hasattr(item, "__await__"):
                        await item
            else:
                if callable(existing):
                    maybe = existing(application)
                    if hasattr(maybe, "__await__"):
                        await maybe
                elif hasattr(existing, "__await__"):
                    await existing
            await _our_post_startup(application)
        app.post_init = _merged_post_init

    # register active_time plugin background tasks (if any)
    register_background_tasks(app)
#---| Made By NT_Dev |
