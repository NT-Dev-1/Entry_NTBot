#———| Made By NT_Dev | plugins/db.py |
import os
import sqlite3
import time
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "sessions.db")
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_cur = _conn.cursor()

def init_db():
    _cur.executescript(
        """
CREATE TABLE IF NOT EXISTS sessions (
  user_id INTEGER PRIMARY KEY,
  answer TEXT,
  state TEXT,
  attempts INTEGER,
  last_started INTEGER,
  expires_at INTEGER
);

CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  whitelisted INTEGER DEFAULT 0,
  banned INTEGER DEFAULT 0,
  note TEXT
);

CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp INTEGER,
  user_id INTEGER,
  actor_id INTEGER,
  event_type TEXT,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  val TEXT
);

CREATE TABLE IF NOT EXISTS invites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invite_link TEXT,
  chat_id INTEGER,
  created_at INTEGER,
  expires_at INTEGER,
  revoked INTEGER DEFAULT 0,
  user_id INTEGER,
  approved_by INTEGER,
  revoked_by INTEGER
);
"""
    )
    _conn.commit()

# Settings helpers
def save_setting(key: str, val: str):
    _cur.execute("REPLACE INTO settings (key, val) VALUES (?, ?)", (key, str(val)))
    _conn.commit()

def load_setting(key: str, default=None) -> Optional[str]:
    _cur.execute("SELECT val FROM settings WHERE key=?", (key,))
    r = _cur.fetchone()
    return r[0] if r else default

# Verify chat persistence helpers
def get_verify_chat_id() -> Optional[int]:
    v = load_setting("verify_chat_id")
    return int(v) if v is not None else None

def set_verify_chat_id(cid: int):
    save_setting("verify_chat_id", str(cid))

# Invite helpers
def store_invite(invite_link: str, chat_id: int, expires_at: int, user_id: int = None, approved_by: int = None) -> int:
    _cur.execute(
        "INSERT INTO invites (invite_link, chat_id, created_at, expires_at, revoked, user_id, approved_by, revoked_by) VALUES (?,?,?,?,0,?,?,NULL)",
        (invite_link, chat_id, int(time.time()), expires_at, user_id, approved_by),
    )
    _conn.commit()
    return _cur.lastrowid

def mark_invite_revoked(invite_link: str, revoked_by: int = None):
    _cur.execute("UPDATE invites SET revoked=1, revoked_by=? WHERE invite_link=?", (revoked_by, invite_link))
    _conn.commit()

def get_expired_unrevoked_invites(now_ts: int):
    return _cur.execute("SELECT id, invite_link, chat_id FROM invites WHERE revoked=0 AND expires_at<=?", (now_ts,)).fetchall()

def get_unrevoked_invite_for_user(uid: int):
    return _cur.execute("SELECT id, invite_link, chat_id FROM invites WHERE revoked=0 AND user_id=? ORDER BY created_at DESC LIMIT 1", (uid,)).fetchone()

def get_all_invites_for_user(uid: int, limit: int = 100):
    return _cur.execute("SELECT id, invite_link, chat_id, created_at, expires_at, revoked, approved_by, revoked_by FROM invites WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (uid, limit)).fetchall()

def get_other_unrevoked_invites_for_user(uid: int, exclude_link: str = None):
    if exclude_link:
        return _cur.execute("SELECT id, invite_link, chat_id FROM invites WHERE revoked=0 AND user_id=? AND invite_link!=? ORDER BY created_at DESC", (uid, exclude_link)).fetchall()
    return _cur.execute("SELECT id, invite_link, chat_id FROM invites WHERE revoked=0 AND user_id=? ORDER BY created_at DESC", (uid,)).fetchall()

# Session helpers
def save_session(uid: int, answer: str, state: str, attempts: int = 0, ttl: int = 300):
    exp = int(time.time()) + ttl
    last = int(time.time())
    _cur.execute(
        "REPLACE INTO sessions (user_id, answer, state, attempts, last_started, expires_at) VALUES (?,?,?,?,?,?)",
        (uid, answer, state, attempts, last, exp),
    )
    _conn.commit()
    log_event(uid, 0, "session_start", f"state={state} ttl={ttl}")

def get_session(uid: int):
    _cur.execute("SELECT user_id, answer, state, attempts, last_started, expires_at FROM sessions WHERE user_id=?", (uid,))
    return _cur.fetchone()

def del_session(uid: int):
    _cur.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    _conn.commit()
    log_event(uid, 0, "session_deleted", "")

def inc_attempt(uid: int) -> int:
    s = get_session(uid)
    if not s:
        return 0
    attempts = s[3] + 1
    _cur.execute("UPDATE sessions SET attempts=? WHERE user_id=?", (attempts, uid))
    _conn.commit()
    log_event(uid, 0, "attempt_inc", f"attempts={attempts}")
    return attempts

def list_pending_sessions(limit: int = 50):
    rows = _cur.execute("SELECT user_id, last_started FROM sessions WHERE state=? ORDER BY last_started DESC LIMIT ?", ("pending_admin", limit)).fetchall()
    return rows

# Users table helpers
def is_whitelisted(uid: int) -> bool:
    _cur.execute("SELECT whitelisted FROM users WHERE user_id=?", (uid,))
    r = _cur.fetchone()
    return bool(r and r[0] == 1)

def is_banned(uid: int) -> bool:
    _cur.execute("SELECT banned FROM users WHERE user_id=?", (uid,))
    r = _cur.fetchone()
    return bool(r and r[0] == 1)

def set_whitelist(uid: int, val: bool, actor: int, note: str = ""):
    _cur.execute(
        "REPLACE INTO users (user_id, whitelisted, banned, note) VALUES (?,?, COALESCE((SELECT banned FROM users WHERE user_id=?),0), ?)",
        (uid, 1 if val else 0, uid, note),
    )
    _conn.commit()
    log_event(uid, actor, "whitelist" if val else "unwhitelist", note)

def set_ban(uid: int, val: bool, actor: int, note: str = ""):
    _cur.execute(
        "REPLACE INTO users (user_id, whitelisted, banned, note) VALUES (?, COALESCE((SELECT whitelisted FROM users WHERE user_id=?),0), ?, ?)",
        (uid, uid, 1 if val else 0, note),
    )
    _conn.commit()
    log_event(uid, actor, "ban" if val else "unban", note)

# Logging
def now_ts() -> int:
    return int(time.time())

def log_event(user_id: int, actor_id: int, event_type: str, detail: str = ""):
    ts = now_ts()
    try:
        _cur.execute(
            "INSERT INTO logs (timestamp, user_id, actor_id, event_type, detail) VALUES (?,?,?,?,?)",
            (ts, user_id, actor_id, event_type, detail),
        )
        _conn.commit()
    except Exception:
        pass

# Expose cursor/connection if necessary (use with care)
conn = _conn
cursor = _cur