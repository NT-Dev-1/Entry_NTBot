#———| Made By NT_Dev | For Private Use Only |
#———| Main Core — verify-bot.py |

import os
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in .env")

def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    return app

if __name__ == "__main__":
    #———| Initialize DB schema first
    from plugins import db
    db.init_db()

    #———| Build the application
    app = build_app()

    #———| Import and register plugins (order: core background tasks first)
    try:
        from plugins.active_time_plugin import register as register_active_time_plugin
        register_active_time_plugin(app)
    except Exception:
        try:
            db.log_event(0, 0, "plugin_register_fail", "active_time_plugin")
        except Exception:
            pass

    try:
        from plugins.verify_plugin import register as register_verify
        register_verify(app)
    except Exception:
        try:
            db.log_event(0, 0, "plugin_register_fail", "verify_plugin")
        except Exception:
            pass

    #———| Status plugin (hourly status + /st) — optional
    try:
        from plugins.status_plugin import register as register_status
        register_status(app)
    except Exception:
        try:
            db.log_event(0, 0, "plugin_register_fail", "status_plugin")
        except Exception:
            pass

    #———| Admin dashboard plugin (admindash, /vis)
    try:
        from plugins.admindash_plugin import register as register_admindash
        register_admindash(app)
    except Exception:
        try:
            db.log_event(0, 0, "plugin_register_fail", "admindash_plugin")
        except Exception:
            pass

    #———| If you have other plugin register() functions, import and call them here:
    #———| from plugins.another_plugin import register as register_another
    #———| register_another(app)

    print("verify-bot started. Plugins registered.")
    app.run_polling(close_loop=False)
#———| Made By NT_Dev | For Private Use Only |