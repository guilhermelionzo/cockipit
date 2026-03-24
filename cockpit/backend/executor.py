"""Routine executor for the Finance Routine Cockpit."""
import subprocess
import threading
import os
import re
import time
import uuid
from datetime import datetime
from typing import Optional, Dict, Callable, List
from .models import Routine, LogEntry
from .storage import update_routine_status, append_log, load_routines, get_variables_dict


# Global execution state
_running_processes: Dict[str, subprocess.Popen] = {}
_execution_callbacks: List[Callable] = []

# ── In-memory live log buffer (últimas 500 linhas) ────────────────────────────
from collections import deque
_live_buffer: deque = deque(maxlen=500)
_live_lock = threading.Lock()


def get_live_logs(last_n: int = 100) -> List[dict]:
    """Return the most recent live log entries (thread-safe)."""
    with _live_lock:
        return list(_live_buffer)[-last_n:]


def clear_live_logs():
    with _live_lock:
        _live_buffer.clear()


def register_callback(fn: Callable):
    """Register a callback to be called when status changes."""
    _execution_callbacks.append(fn)


def _notify(routine_id: str, status: str, message: str, run_id: str):
    """Persist log, push to live buffer, and notify callbacks."""
    routine = next((r for r in load_routines() if r.id == routine_id), None)
    name = routine.name if routine else routine_id
    level = "ERROR" if status in ("failed", "error") else "INFO"
    entry = LogEntry(
        routine_id=routine_id,
        routine_name=name,
        message=message,
        level=level,
        run_id=run_id,
    )
    # Persist to JSON
    append_log(entry)
    update_routine_status(routine_id, status)

    # Push to live in-memory buffer
    with _live_lock:
        _live_buffer.append({
            "ts":       entry.timestamp,
            "run_id":   run_id,
            "id":       routine_id,
            "name":     name,
            "status":   status,
            "message":  message,
            "level":    level,
        })

    for cb in _execution_callbacks:
        try:
            cb(routine_id, status, message)
        except Exception:
            pass


def substitute_variables(text: str, variables: Optional[dict] = None) -> str:
    """Replace {VAR_NAME} placeholders with variable values."""
    if variables is None:
        variables = get_variables_dict()
    def replacer(match):
        key = match.group(1)
        return variables.get(key, match.group(0))
    return re.sub(r"\{(\w+)\}", replacer, text)


def _build_command(routine: Routine, variables: dict) -> list:
    """Build the shell command list from a routine definition."""
    rtype = routine.type.lower()
    cmd = substitute_variables(routine.command, variables)
    params = substitute_variables(routine.parameters, variables)

    if rtype == "python":
        base = ["python", cmd]
    elif rtype == "shell":
        base = ["bash", cmd] if not cmd.endswith(".bat") else [cmd]
    elif rtype in ("excel", "vba"):
        # Open with default handler or via script
        base = ["start", "", cmd] if os.name == "nt" else ["open", cmd]
    elif rtype == "api":
        # Treat command as a URL, call via curl
        base = ["curl", "-s", cmd]
    else:
        base = [cmd]

    if params:
        base.extend(params.split())

    return base


def _run_routine_thread(routine: Routine, run_id: str, variables: dict,
                        on_done: Optional[Callable] = None):
    """Execute a single routine in a thread."""
    routine_id = routine.id

    _notify(routine_id, "running", f"▶ Iniciando: {routine.name}", run_id)

    if routine.type == "group":
        _notify(routine_id, "success", f"✓ Grupo '{routine.name}' iniciado", run_id)
        if on_done:
            on_done(routine_id, "success")
        return

    work_dir = substitute_variables(routine.working_dir or ".", variables)
    if not os.path.isdir(work_dir):
        work_dir = os.getcwd()

    attempts = 0
    max_attempts = max(1, routine.retry + 1)
    final_status = "failed"

    while attempts < max_attempts:
        attempts += 1
        if attempts > 1:
            _notify(routine_id, "running", f"↺ Tentativa {attempts}/{max_attempts}", run_id)

        try:
            cmd = _build_command(routine, variables)
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=work_dir,
                text=True,
                bufsize=1,
            )
            _running_processes[routine_id] = proc

            # Stream output
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    _notify(routine_id, "running", f"  {line}", run_id)

            proc.wait(timeout=routine.timeout)
            del _running_processes[routine_id]

            if proc.returncode == 0:
                final_status = "success"
                _notify(routine_id, "success", f"✓ Concluído: {routine.name}", run_id)
                break
            else:
                _notify(routine_id, "failed",
                        f"✗ Erro (código {proc.returncode}): {routine.name}", run_id)

        except subprocess.TimeoutExpired:
            if routine_id in _running_processes:
                _running_processes[routine_id].kill()
                del _running_processes[routine_id]
            _notify(routine_id, "failed",
                    f"⏱ Timeout após {routine.timeout}s: {routine.name}", run_id)

        except FileNotFoundError:
            cmd_str = substitute_variables(routine.command, variables)
            _notify(routine_id, "failed",
                    f"✗ Comando não encontrado: '{cmd_str}' — verifique o caminho e o tipo.", run_id)
            break  # No point retrying a missing file

        except Exception as e:
            _notify(routine_id, "failed", f"✗ Exceção: {str(e)}", run_id)

    if final_status == "failed":
        _notify(routine_id, "failed",
                f"✗ Falhou após {attempts} tentativa(s): {routine.name}", run_id)

    if on_done:
        on_done(routine_id, final_status)


def run_routine(routine_id: str) -> str:
    """Start a single routine asynchronously. Returns run_id."""
    routines = load_routines()
    routine = next((r for r in routines if r.id == routine_id), None)
    if not routine:
        return ""
    if not routine.enabled:
        return ""

    variables = get_variables_dict()
    run_id = uuid.uuid4().hex[:8]

    t = threading.Thread(
        target=_run_routine_thread,
        args=(routine, run_id, variables),
        daemon=True,
    )
    t.start()
    return run_id


def run_group(parent_id: str) -> str:
    """Run all children of a parent routine, respecting order and dependencies."""
    routines = load_routines()
    parent = next((r for r in routines if r.id == parent_id), None)
    if not parent:
        return ""

    children = sorted(
        [r for r in routines if r.parent_id == parent_id and r.enabled],
        key=lambda r: r.order,
    )

    variables = get_variables_dict()
    run_id = uuid.uuid4().hex[:8]
    results: Dict[str, str] = {}

    def _run_with_deps(routine: Routine):
        # Check dependencies
        for dep_id in routine.depends_on:
            dep_status = results.get(dep_id, "pending")
            cond = routine.run_condition
            if cond == "on_success" and dep_status != "success":
                _notify(routine.id, "skipped",
                        f"⏭ Pulado: dependência '{dep_id}' não teve sucesso", run_id)
                results[routine.id] = "skipped"
                return
            elif cond == "on_failure" and dep_status != "failed":
                _notify(routine.id, "skipped",
                        f"⏭ Pulado: dependência '{dep_id}' não falhou", run_id)
                results[routine.id] = "skipped"
                return

        done_event = threading.Event()

        def on_done(rid, status):
            results[rid] = status
            done_event.set()

        _run_routine_thread(routine, run_id, variables, on_done=on_done)
        done_event.wait()

    def _group_thread():
        _notify(parent_id, "running", f"▶ Iniciando grupo: {parent.name}", run_id)
        for child in children:
            _run_with_deps(child)
        all_ok = all(v == "success" for v in results.values() if v != "skipped")
        final = "success" if all_ok else "failed"
        _notify(parent_id, final, f"{'✓' if all_ok else '✗'} Grupo concluído: {parent.name}", run_id)

    t = threading.Thread(target=_group_thread, daemon=True)
    t.start()
    return run_id


def stop_routine(routine_id: str):
    """Stop a running routine."""
    proc = _running_processes.get(routine_id)
    if proc:
        proc.terminate()
        del _running_processes[routine_id]
        update_routine_status(routine_id, "stopped")


def is_running(routine_id: str) -> bool:
    return routine_id in _running_processes


def get_running_ids() -> List[str]:
    return list(_running_processes.keys())
