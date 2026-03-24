"""Routine executor for the Finance Routine Cockpit."""
import subprocess
import threading
import os
import re
import time
import uuid
import tempfile
import shutil
import shlex
from datetime import datetime
from typing import Optional, Dict, Callable, List
from .models import Routine, LogEntry
from .storage import update_routine_status, append_log, load_routines, get_variables_dict


# Full path to PowerShell on Windows (fallback chain)
_PS_CANDIDATES = [
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe",
    "powershell.exe",
    "powershell",
    "pwsh.exe",
    "pwsh",
]


def _find_powershell() -> str:
    """Return the first PowerShell executable that exists on this machine."""
    for candidate in _PS_CANDIDATES:
        if os.path.isabs(candidate):
            if os.path.isfile(candidate):
                return candidate
        else:
            if shutil.which(candidate):
                return candidate
    return "powershell.exe"  # last-resort — will fail with a clear OS error


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


def substitute_variables(text: str, variables: Optional[dict] = None,
                         quote_spaces: bool = False) -> str:
    """
    Replace {VAR_NAME} placeholders with variable values.

    quote_spaces=True: if the substituted value contains spaces AND is not already
    surrounded by quotes in the template, wrap it in double-quotes automatically.
    Use this when the result will be passed through shlex.split() as shell args.
    """
    if variables is None:
        variables = get_variables_dict()

    def replacer(match):
        key   = match.group(1)
        value = variables.get(key, match.group(0))
        if quote_spaces and " " in value:
            # Only add quotes if not already quoted in the template context
            start = match.start()
            end   = match.end()
            before = text[start - 1] if start > 0 else ""
            after  = text[end]       if end < len(text) else ""
            if before not in ('"', "'") and after not in ('"', "'"):
                value = f'"{value}"'
        return value

    return re.sub(r"\{(\w+)\}", replacer, text)


def _parse_cell_assignments(cell_values_str: str, variables: dict) -> list:
    """
    Parse a multiline cell-assignment block and return a list of dicts:
        [{"sheet": "Sheet1", "cell": "A1", "value": "2026-03-24"}, ...]

    Accepted formats (one per line, lines starting with # are comments):
        A1=valor                      → active sheet, cell A1
        Sheet1!A1=valor               → explicit sheet
        Parametros!C5={Data_Ref}      → with variable substitution
        B2=texto com espaços          → values may contain spaces
    """
    assignments = []
    for raw_line in cell_values_str.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        lhs, _, rhs = line.partition("=")
        lhs = lhs.strip()
        value = substitute_variables(rhs.strip(), variables)

        if "!" in lhs:
            sheet, cell = lhs.split("!", 1)
        else:
            sheet = None
            cell = lhs

        assignments.append({"sheet": sheet, "cell": cell.strip(), "value": value})
    return assignments


def _render_cell_assignments_ps(assignments: list) -> str:
    """
    Render a list of cell assignments as PowerShell lines.
    Each line sets $wb.Sheets["name"].Range("A1").Value = "..."
    or $wb.ActiveSheet.Range("A1").Value = "..."
    """
    lines = []
    for a in assignments:
        cell  = a["cell"].replace('"', '`"')
        value = a["value"].replace('"', '`"')
        if a["sheet"]:
            sheet = a["sheet"].replace('"', '`"')
            lines.append(
                f'    Write-Output "  Celula: [{sheet}!{cell}] = {value}"\n'
                f'    $wb.Sheets["{sheet}"].Range("{cell}").Value = "{value}"'
            )
        else:
            lines.append(
                f'    Write-Output "  Celula: [{cell}] = {value}"\n'
                f'    $wb.ActiveSheet.Range("{cell}").Value = "{value}"'
            )
    return "\n".join(lines)


def _write_vba_ps1(excel_path: str, macro_name: str, cell_values_str: str = "",
                   variables: Optional[dict] = None) -> str:
    """
    Write a PowerShell script to a temp .ps1 file and return its path.

    Using a .ps1 file (instead of inline -Command) avoids:
      - Quoting/escaping issues with backslashes in UNC paths (\\\\server\\share\\file.xlsb)
      - Command-line length limits
      - Single-quote conflicts inside the inline -Command string

    excel_path      – full UNC or local path to the .xlsm / .xlsb file
    macro_name      – exact VBA Sub name ('Module1.ExportPnL'). Leave blank to
                      only open → set cells → RefreshAll → save → close.
    cell_values_str – multiline string of cell assignments (Sheet1!A1={Var}).
    variables       – cockpit variables dict for substitution.
    """
    safe_path = excel_path.replace('"', '`"')

    # Build cell-injection block
    assignments = _parse_cell_assignments(cell_values_str or "", variables or {})
    cell_block = ""
    if assignments:
        rendered = _render_cell_assignments_ps(assignments)
        cell_block = (
            f'    Write-Output "Injetando {len(assignments)} valor(es) nas celulas..."\n'
            f'{rendered}\n'
        )

    # Bloco PowerShell compartilhado: obtém instância Excel já aberta (com add-ins)
    # ou cria uma nova e carrega os add-ins manualmente.
    excel_init = """\
# Tenta reutilizar instância Excel já aberta (add-ins já carregados)
try {
    $excel = [System.Runtime.InteropServices.Marshal]::GetActiveObject("Excel.Application")
    Write-Output "Usando instancia Excel existente (add-ins ja carregados)."
} catch {
    Write-Output "Criando nova instancia Excel..."
    $excel = New-Object -ComObject Excel.Application
    # Permitir macros e add-ins sem prompt de segurança
    $excel.AutomationSecurity = 1  # msoAutomationSecurityLow
    # Carregar add-ins instalados
    $loaded = 0
    foreach ($addin in $excel.AddIns) {
        if ($addin.Installed) {
            try { $addin.Installed = $true; $loaded++ } catch {}
        }
    }
    Write-Output "Add-ins carregados: $loaded"
}
$excel.Visible = $true
$excel.DisplayAlerts = $false
"""

    if macro_name.strip():
        script_body = f"""\
$ErrorActionPreference = 'Stop'
{excel_init}
try {{
    Write-Output "Abrindo: {safe_path}"
    $wb = $excel.Workbooks.Open("{safe_path}")
    Write-Output "Executando macro: {macro_name.strip()}"
    $excel.Run("{macro_name.strip()}")
    $wb.Save()
    Write-Output "Macro concluida com sucesso."
}} catch {{
    Write-Error "ERRO: $_"
    exit 1
}} finally {{
    if ($wb)    {{ $wb.Close($false) }}
    if ($excel) {{ $excel.Quit() }}
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
}}
"""
    else:
        script_body = f"""\
$ErrorActionPreference = 'Stop'
{excel_init}
try {{
    Write-Output "Abrindo: {safe_path}"
    $wb = $excel.Workbooks.Open("{safe_path}")
    Write-Output "Atualizando conexoes de dados..."
    $wb.RefreshAll()
    Start-Sleep -Seconds 5
    $wb.Save()
    Write-Output "Workbook atualizado com sucesso."
}} catch {{
    Write-Error "ERRO: $_"
    exit 1
}} finally {{
    if ($wb)    {{ $wb.Close($false) }}
    if ($excel) {{ $excel.Quit() }}
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
}}
"""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding="utf-8"
    )
    tmp.write(script_body)
    tmp.flush()
    tmp.close()
    return tmp.name


def _write_open_ps1(excel_path: str) -> str:
    """
    Write a tiny PS1 that opens the Excel file with the default app (visibly).
    The script exits immediately after handing the file to Windows Shell —
    the routine is marked success and the user can edit the file freely.
    """
    safe_path = excel_path.replace('"', '`"')
    script_body = f"""\
$ErrorActionPreference = 'Stop'
Write-Output "Abrindo arquivo: {safe_path}"
Start-Process -FilePath "{safe_path}"
Write-Output "Arquivo aberto com sucesso."
"""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding="utf-8"
    )
    tmp.write(script_body)
    tmp.flush()
    tmp.close()
    return tmp.name


def _build_vba_command(ps1_path: str) -> list:
    """Return the command list to execute a .ps1 file via PowerShell."""
    ps_exe = _find_powershell()
    return [
        ps_exe,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", ps1_path,
    ]


def _build_command(routine: Routine, variables: dict) -> tuple:
    """
    Build the shell command list from a routine definition.

    Returns (cmd_list, tmp_ps1_path_or_None).
    The caller must delete tmp_ps1_path after the process finishes.
    """
    rtype = routine.type.lower()
    cmd = substitute_variables(routine.command, variables)
    # quote_spaces=True: variáveis com espaços ficam entre aspas antes do shlex.split()
    params = substitute_variables(routine.parameters or "", variables, quote_spaces=True)
    tmp_ps1 = None

    if rtype == "python":
        base = ["python", cmd]
        if params:
            base.extend(shlex.split(params, posix=False))

    elif rtype == "shell":
        if cmd.endswith(".bat") or cmd.endswith(".cmd"):
            base = [cmd]
        else:
            base = ["bash", cmd]
        if params:
            base.extend(shlex.split(params, posix=False))

    elif rtype == "excel":
        # Just open the file visibly with the default application (no automation).
        # The routine completes as soon as the file is handed off to Excel.
        # Use this step so the user can review/edit before a downstream VBA routine runs.
        if os.name == "nt":
            tmp_ps1 = _write_open_ps1(cmd)
            base = _build_vba_command(tmp_ps1)
        else:
            base = ["open", cmd]

    elif rtype == "vba":
        # Run a VBA macro inside the workbook (invisible Excel via COM).
        # cmd    = path to the .xlsm / .xlsb file (local or UNC)
        # params = macro name, e.g. 'Module1.ExportPnL'
        if os.name == "nt":
            tmp_ps1 = _write_vba_ps1(cmd, params)
            base = _build_vba_command(tmp_ps1)
        else:
            macro_name = params.strip() or "Main"
            base = [
                "libreoffice", "--headless", "--norestore",
                f"macro:///Standard.Module1.{macro_name}",
                cmd,
            ]

    elif rtype == "api":
        base = ["curl", "-s", cmd]
        if params:
            base.extend(shlex.split(params, posix=False))

    else:
        base = [cmd]
        if params:
            base.extend(shlex.split(params, posix=False))

    return base, tmp_ps1


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

        tmp_ps1 = None
        try:
            cmd, tmp_ps1 = _build_command(routine, variables)

            # Log the effective command so the user can diagnose issues
            _notify(routine_id, "running",
                    f"  cmd: {' '.join(str(c) for c in cmd[:4])} …", run_id)

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=work_dir,
                text=True,
                bufsize=1,
                # On Windows, shell=False + full exe path is more reliable for COM/VBA
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

        except FileNotFoundError as e:
            # Show the actual executable that was not found, not just routine.command
            exe = str(e).split("'")[1] if "'" in str(e) else str(e)
            _notify(routine_id, "failed",
                    f"✗ Executável não encontrado: '{exe}'. "
                    f"Verifique se o programa está instalado e no PATH do sistema.", run_id)
            if routine.type.lower() in ("vba", "excel"):
                _notify(routine_id, "failed",
                        "  → Para VBA/Excel: PowerShell deve estar disponível. "
                        "Verifique C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\", run_id)
            break  # No point retrying a missing executable

        except Exception as e:
            _notify(routine_id, "failed", f"✗ Exceção: {str(e)}", run_id)

        finally:
            # Always clean up temp .ps1 file
            if tmp_ps1 and os.path.exists(tmp_ps1):
                try:
                    os.unlink(tmp_ps1)
                except OSError:
                    pass

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
