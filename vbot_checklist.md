Verify-bot — Checklist.
Created & Maintained By Northern_Terps_Dev as a Private sole use Telegram Bot.
NOT FOR PUBLIC USE OR SALE. 

- Status legend:  
  - [ ] = Still To Do  
  - [*] = Pending Test / In Progress  
  - [×] = Complete

---

Important
- [ ] Change /start to /verify — Make /verify run the CAPTCHA; update command routing in verify-bot.py; ensure permission checks and rate limits; due: 2025-08-12  

- [ ] Update /start to send a welcome message — Include short instructions and the exact command required to /verify (runs CAPTCHA); add link/reference to help or FAQ; due: 2025-08-12  

- [ ] Add configuration validation at startup — Fail fast with clear log message when required env vars / tokens / DB are missing 

- [ ] Secure credentials and config — Move tokens and secrets to Termux-safe storage (env vars / .env in protected dir); add README note for deployers  

- [ ] Add permission and admin escalation checks — Ensure only allowed roles can change verification settings or bypass CAPTCHA

---

Pending

- [*] Remove logs and log command — Remove or rotate sensitive logs; ensure log command removed from bot commands; pending testing: 2025-08-12  

- [*] CAPTCHA integration tests — Automated unit tests and manual flows for success, fail, timeout, repeated attempts  

- [*] Survival / failing-state recovery tests — Confirm bot reconnects / re-registers handlers after crash or restart  

- [ ] Add health endpoint (HTTP) for monitoring and uptime checks (simple /health returning JSON status)  

- [ ] Add a toggleable verbose/debug mode for staging only; ensure disabled in production builds  

- [ ] Add database migrations and versioning (if using SQLite/DB) to handle future schema changes

---

Completed
- [×] Initial task scaffold created in todo_list.txt (source notes recorded)  

- [×] Basic verify-bot repository initialized (Termux-friendly project layout assumed)  

- [×] Captured early requirements and saved as project context in working memory (notes for ongoing work)

---

Implementation / Dev tasks (detailed)

- [ ] Code: rename handler for /start -> /verify and wire CAPTCHA call  

  - [ ] Update command registration in main dispatcher  
  
  - [ ] Move old /start behavior into a new welcome message function  
  
  - [ ] Ensure backward-compatible alias (optional) and document change
- [ ] Code: welcome message  

  - [ ] Compose short welcome text that includes: purpose, one-line verify command, link to rules/FAQ, contact for admins  
  
  - [ ] Localise strings or keep in a central messages file
  
- [ ] Code: CAPTCHA module  

  - [ ] Add modular captcha runner function (sync or async consistent with project)  
  
  - [ ] Expose clear result codes: VERIFIED / FAILED / TIMEOUT / ERROR.
  
  - [ ] Add retry/backoff policy and per-user attempt tracking.
  
- [ ] Storage: tasks & state.

  - [ ] Decide format (recommend SQLite for concurrency and Termux); if file-based, use JSON/YAML and lock appropriately.  
  
  - [ ] Schema: userid, status, attempts, lastattemptat, verifiedat, metadata
  
- [ ] Logging & privacy 

  - [ ] Remove or redact sensitive log outputs (tokens, full PII)  
  
  - [ ] Implement rotating logs or keep only short recent log in Termux storage
  
- [ ] Tests  

  - [ ] Unit tests for command routing and small helpers  
  
  - [ ] Integration test for full verify flow (mock captcha if needed)  
  
  - [ ] Smoke test for startup and config validation
  
- [ ] Deployment  

  - [ ] Termux: provide start script using tmux or Termux:Boot with auto-restart and log capture  
  - [ ] VPS/systemd option: create a systemd unit example and environment file template
  
- [ ] Health & monitoring  

  - [ ] Small HTTP /health endpoint returning ok + uptime + version  
  
  - [ ] Add a simple metrics counter for verify attempts (in-memory or DB)
  
- [ ] Documentation  

  - [ ] README: install, config, run, test, troubleshooting, migration steps 
  
  - [ ] DEPLOY.md: Termux and systemd instructions, backups, and restore steps
  
- [ ] Maintenance  

  - [ ] Backup script for DB or todo file (daily/weekly options)  
  
  - [ ] Add a migration checklist for major changes

---

Recommendations (nice to have / future).

- [ ] Add deduplication for concurrent verify requests (per-user mutex) to avoid race conditions  

- [ ] Add pinned/admin detection and an automated escalation notice for verification failures in group chats  

- [ ] Implement a janitor / cleanup task that removes expired pending verification entries after X days  

- [ ] Add a small web dashboard (static HTML + JSON from /health) for admins to view current pending/verifications and logs (read-only) 

- [ ] Add feature flags or remote config for AB testing survival of stricter CAPTCHA difficulty without redeploy  

- [ ] Add CI check to run lint, a test suite, and a simple verify flow simulation on push

---

Extras: checklist for testing and release.

- [ ] Run config validation on clean device (Termux fresh profile)  

- [ ] Run full verify flow with a test account; confirm verified state persists across restarts  
- [ ] Verify that removed log command no longer appears in /help or command list  

- [ ] Confirm /health returns expected fields and version string  

- [ ] Confirm backup and restore works: export DB, remove DB, import, and confirm state restored 

- [ ] Confirm that admin-only commands require the correct role and are rejected otherwise

---

Metadata / notes:

- Project: verify-bot (Termux-friendly layout, tmux/systemd deployment options)  

- Recommended storage: SQLite (concurrency-safe) or JSON/YAML for simple file-based workflow 

- Recommended testing cadence: add 1–2 items, test on fresh save/session, iterate

---
