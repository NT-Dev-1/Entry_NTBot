#———| Made By NT_Dev | For Private Use Only |
#———| plugins/active_time_plugin.py |

import asyncio
from functools import wraps
from typing import Callable
from telegram import Update
from telegram.ext import ContextTypes

#———| No-op gating decorator — replace with real active-hours logic if you have one. |
def require_active(handler: Callable):
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await handler(update, context)
    return wrapper

#———| Background loop example
async def _active_hours_loop(app, interval: int = 60):
    try:
        while True:
            #———| Insert periodic checks here if needed |
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        return
    except Exception:
        return

#———| Helper to normalize/merge app.post_init so plugins can schedule startup coroutines safely
def _ensure_post_init_norm(app, startup_coro_fn):
    existing = getattr(app, "post_init", None)

    async def _our_post_startup(application):
        maybe = startup_coro_fn(application)
        if hasattr(maybe, "__await__"):
            await maybe

    if existing is None:
        app.post_init = _our_post_startup
        return

    if isinstance(existing, list):
        async def _merged_list(application):
            for item in existing:
                try:
                    maybe = item(application) if callable(item) else item
                    if hasattr(maybe, "__await__"):
                        await maybe
                except Exception:
                    pass
            await _our_post_startup(application)
        app.post_init = _merged_list
        return

    async def _merged_existing_then_ours(application):
        try:
            if callable(existing):
                maybe = existing(application)
                if hasattr(maybe, "__await__"):
                    await maybe
            elif hasattr(existing, "__await__"):
                await existing
        except Exception:
            pass
        await _our_post_startup(application)

    app.post_init = _merged_existing_then_ours

#———| Public: register background tasks (idempotent)
def register_background_tasks(app, interval: int = 60):
    async def _startup(application):
        application.create_task(_active_hours_loop(application, interval=interval))

    _ensure_post_init_norm(app, _startup)

    try:
        async def _job_callback(context):
            return
        if getattr(app, "job_queue", None) is not None:
            app.job_queue.run_repeating(_job_callback, interval=3600, first=1)
    except Exception:
        pass

#———| Backwards-compatible register(app) used by bootstrap loader
def register(app):
    register_background_tasks(app)
#———| Made By NT_Dev | For Private Use Only |