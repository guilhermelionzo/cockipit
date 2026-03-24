"""JSON-based storage for the Finance Routine Cockpit."""
import json
import os
from typing import List, Optional
from datetime import datetime
from .models import Variable, Routine, Schedule, LogEntry, gen_id

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "config")
LOGS_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")

VARIABLES_FILE = os.path.join(CONFIG_DIR, "variables.json")
ROUTINES_FILE = os.path.join(CONFIG_DIR, "routines.json")
SCHEDULER_FILE = os.path.join(CONFIG_DIR, "scheduler.json")
LOGS_FILE = os.path.join(LOGS_DIR, "execution_logs.json")


def _ensure_dirs():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)


def _read_json(path: str, default: dict) -> dict:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, data: dict):
    _ensure_dirs()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─── Variables ────────────────────────────────────────────────────────────────

def load_variables() -> List[Variable]:
    data = _read_json(VARIABLES_FILE, {"variables": []})
    return [Variable.from_dict(v) for v in data.get("variables", [])]


def save_variables(variables: List[Variable]):
    _write_json(VARIABLES_FILE, {"variables": [v.to_dict() for v in variables]})


def get_variables_dict() -> dict:
    """Returns a {name: value} dict for template substitution."""
    return {v.name: v.value for v in load_variables()}


def add_variable(var: Variable):
    variables = load_variables()
    variables.append(var)
    save_variables(variables)


def update_variable(var_id: str, updated: Variable):
    variables = load_variables()
    for i, v in enumerate(variables):
        if v.id == var_id:
            variables[i] = updated
            break
    save_variables(variables)


def delete_variable(var_id: str):
    variables = [v for v in load_variables() if v.id != var_id]
    save_variables(variables)


# ─── Routines ─────────────────────────────────────────────────────────────────

def load_routines() -> List[Routine]:
    data = _read_json(ROUTINES_FILE, {"routines": []})
    return [Routine.from_dict(r) for r in data.get("routines", [])]


def save_routines(routines: List[Routine]):
    _write_json(ROUTINES_FILE, {"routines": [r.to_dict() for r in routines]})


def add_routine(routine: Routine):
    routines = load_routines()
    routines.append(routine)
    save_routines(routines)


def update_routine(routine_id: str, updated: Routine):
    routines = load_routines()
    for i, r in enumerate(routines):
        if r.id == routine_id:
            routines[i] = updated
            break
    save_routines(routines)


def delete_routine(routine_id: str):
    routines = load_routines()
    # Also remove children and references
    routines = [r for r in routines if r.id != routine_id and r.parent_id != routine_id]
    for r in routines:
        if routine_id in r.depends_on:
            r.depends_on.remove(routine_id)
    save_routines(routines)


def update_routine_status(routine_id: str, status: str):
    routines = load_routines()
    for r in routines:
        if r.id == routine_id:
            r.last_status = status
            r.last_run = datetime.now().isoformat()
            break
    save_routines(routines)


# ─── Schedules ────────────────────────────────────────────────────────────────

def load_schedules() -> List[Schedule]:
    data = _read_json(SCHEDULER_FILE, {"schedules": []})
    return [Schedule.from_dict(s) for s in data.get("schedules", [])]


def save_schedules(schedules: List[Schedule]):
    _write_json(SCHEDULER_FILE, {"schedules": [s.to_dict() for s in schedules]})


def add_schedule(schedule: Schedule):
    schedules = load_schedules()
    schedules.append(schedule)
    save_schedules(schedules)


def update_schedule(schedule_id: str, updated: Schedule):
    schedules = load_schedules()
    for i, s in enumerate(schedules):
        if s.id == schedule_id:
            schedules[i] = updated
            break
    save_schedules(schedules)


def delete_schedule(schedule_id: str):
    schedules = [s for s in load_schedules() if s.id != schedule_id]
    save_schedules(schedules)


# ─── Logs ─────────────────────────────────────────────────────────────────────

def load_logs(limit: int = 200) -> List[LogEntry]:
    data = _read_json(LOGS_FILE, {"logs": []})
    logs = [LogEntry.from_dict(l) for l in data.get("logs", [])]
    return logs[-limit:]


def append_log(entry: LogEntry):
    data = _read_json(LOGS_FILE, {"logs": []})
    logs = data.get("logs", [])
    logs.append(entry.to_dict())
    # Keep only last 1000 entries
    if len(logs) > 1000:
        logs = logs[-1000:]
    _write_json(LOGS_FILE, {"logs": logs})


def clear_logs():
    _write_json(LOGS_FILE, {"logs": []})
