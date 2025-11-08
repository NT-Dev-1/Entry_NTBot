Verification‑Bot
———| Made By NT_Dev | For Private Use Only |
A Telegram verification bot designed for private communities. Built to run on Termux, VPS or any Python 3.10+ runtime. Core features: emoji CAPTCHA, one‑time invite links, admin escalation UI, invite audit and revoke, persistent SQLite storage, and background tasks for cleanup and health checks.

---

Key features
- Emoji CAPTCHA verification with rate limits and retry tracking  
- Auto‑approve flow that issues one‑time, time‑limited invite links  
- Admin escalation when auto‑approve fails or users exceed attempts  
- Persistent SQLite DB for sessions, users, invites, settings and logs  
- Admin dashboard (inline UI) with paginated command templates and toggleable member‑list exposure  
- Background tasks: invite cleanup loop, configurable background jobs via activetimeplugin  
- Safe handling of ChatMigrated exceptions and robust invite revoke logic  
- Defensive error handling and admin DM delivery for tracebacks and unexpected payloads

---

Commands

User (public/private DM)
- /verify — start CAPTCHA verification (primary flow)  
- /v — alias for /verify  
- /start — (optional) welcome message (configurable)  

Admin (only usable by configured ADMIN_ID in .env)
- /admindash — open inline admin dashboard (paginated)  
- /st — get status summary (pending sessions, member-list exposure)  
- /vis — toggle member-list exposure flag (persisted setting)  
- /approve <user_id> — approve pending verification and send invite  
- /reject <user_id> — reject pending verification  
- /pending — list pending sessions  
- /stats — show basic stats (attempts, approved, banned, pending)  
- /setmsg <key> <text> — edit message templates (keys: msgverified, msgautofailuser, msgapproved, msgrejected, msgwhitelisted, msgbanned)  
- /setverify <chatid> — change and persist VERIFYCHAT_ID used for invites  
- /invitehistory <userid> | /invitehistory csv <userid> — show invite history or CSV export  
- /whitelist <userid> | /unwhitelist <userid> — manage whitelist  
- /ban <userid> | /unban <userid> — manage ban list

Notes
- Admin buttons in the dashboard send command templates to admin DM for copy/edit instead of running destructive actions directly.
- Admin must start a private DM with the bot so the bot can deliver admin messages.

---

Quick install (Termux / Linux / VPS)

1. Clone or place code into your project directory.
2. Create and populate .env:
   - BOTTOKEN=yourbot_token
   - ADMINID=yournumericadminid
   - VERIFYCHATID=targetsupergroupid (optional; can be set later with /setverify)
3. Create virtual environment and install deps:
   - python -m venv .venv
   - source .venv/bin/activate
   - pip install -U pip
   - pip install python-telegram-bot==20.* python-dotenv
4. Initialize DB and run:
   - python verify-bot.py
5. Confirm startup message and test admin commands in private DM.

---

Configuration

Environment variables (.env)
- BOT_TOKEN — Telegram Bot token (required)  
- ADMIN_ID — numeric Telegram user id for admin (required)  
- VERIFYCHATID — target chat id for creating invites (can be persisted via /setverify)  
- DB_PATH — optional path to SQLite DB (default: sessions.db)  

Runtime settings and message templates are persisted in the settings table via the /setmsg command.

---

Files and plugins (overview)
- verify-bot.py / bot.py — bootstrap and plugin registration  
- plugins/
  - verify_plugin.py — main verify flow, handlers, invite logic  
  - activetimeplugin.py — require_active decorator and background task helpers  
  - admindash_plugin.py — admin dashboard, /admindash, /vis, /st handlers  
  - db.py — SQLite helpers and schema init  
  - utils.py — helpers (emoji generator, escaping)  
  - status_plugin.py — optional status builder (if present)

---

Security & best practices
- Keep repository private for source code; publish only README publicly.  
- Never commit .env, tokens, private keys or DB files. Add .env and DB files to .gitignore.  
- Enable 2FA on your GitHub account and use least‑privilege for collaborators.  
- Use environment variables or a secrets manager for production tokens. Rotate tokens if they’re ever exposed.  
- Use deploy keys or machine users for automated deploys; prefer read‑only keys where possible.

---

Troubleshooting & tips
- If admin DMs fail, make sure the ADMIN_ID has started a DM with the bot (bots cannot DM users who never messaged them).  
- If inline buttons show “Invalid callback data”, replace admindash_plugin.py with the defensive parsing version and restart the bot. Unexpected payloads will be DM’d to admin for inspection.  
- To debug handler registration, add temporary prints in each plugin.register(app) to confirm they run before app.run_polling().

---

Development checklist
See V-Bot Checklist.md for in‑progress items: tests, health endpoint, DB migrations, automated README sync, and CI. Priorities: move /start -> /verify, config validation, and audit logging for admin actions.

---

License
———| Made By NT_Dev | For Private Use Only |
