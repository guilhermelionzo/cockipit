"""Cron-based scheduler for the Finance Routine Cockpit."""
import threading
import time
from datetime import datetime
from typing import Optional
from croniter import croniter
from .storage import load_schedules, save_schedules, update_schedule
from .executor import run_routine, run_group
from .models import Schedule

_scheduler_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def get_next_run(cron_expr: str, tz_name: str = "America/Sao_Paulo") -> Optional[str]:
    """Return the next run datetime string for a cron expression."""
    try:
        import pytz
        tz = pytz.timezone(tz_name)
        now = datetime.now(tz)
        it = croniter(cron_expr, now)
        nxt = it.get_next(datetime)
        return nxt.isoformat()
    except Exception:
        try:
            it = croniter(cron_expr, datetime.now())
            return it.get_next(datetime).isoformat()
        except Exception:
            return None


def refresh_next_runs():
    """Update next_run for all enabled schedules."""
    schedules = load_schedules()
    changed = False
    for s in schedules:
        if s.enabled:
            nxt = get_next_run(s.cron, s.timezone)
            if nxt != s.next_run:
                s.next_run = nxt
                changed = True
    if changed:
        save_schedules(schedules)


def _scheduler_loop():
    """Background thread that checks and fires due schedules."""
    while not _stop_event.is_set():
        try:
            now = datetime.now()
            schedules = load_schedules()
            for s in schedules:
                if not s.enabled or not s.next_run:
                    continue
                try:
                    nxt = datetime.fromisoformat(s.next_run.replace("Z", ""))
                    # Strip tz info for comparison
                    nxt_naive = nxt.replace(tzinfo=None)
                    if now >= nxt_naive:
                        # Fire
                        from .storage import load_routines
                        routines = load_routines()
                        target = next((r for r in routines if r.id == s.routine_id), None)
                        if target:
                            if target.type == "group":
                                run_group(target.id)
                            else:
                                run_routine(target.id)
                        s.last_triggered = now.isoformat()
                        s.next_run = get_next_run(s.cron, s.timezone)
                        update_schedule(s.id, s)
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(30)  # Check every 30 seconds


def start_scheduler():
    global _scheduler_thread, _stop_event
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _stop_event.clear()
    refresh_next_runs()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()


def stop_scheduler():
    _stop_event.set()


def is_running() -> bool:
    return _scheduler_thread is not None and _scheduler_thread.is_alive()
