"""Finance Routine Cockpit — Streamlit UI"""
import streamlit as st
import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from backend.storage import (
    load_variables, save_variables, add_variable, update_variable, delete_variable,
    load_routines, save_routines, add_routine, update_routine, delete_routine,
    load_schedules, save_schedules, add_schedule, update_schedule, delete_schedule,
    load_logs, clear_logs, append_log, get_variables_dict,
)
from backend.models import Variable, Routine, Schedule, LogEntry, ROUTINE_TYPES, RUN_CONDITIONS
from backend.executor import run_routine, run_group, stop_routine, is_running, get_running_ids, get_live_logs, clear_live_logs
from backend.scheduler import (
    start_scheduler, stop_scheduler,
    is_running as sched_running, refresh_next_runs, get_next_run,
)

# ─── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Finance Cockpit",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #0f1117; }
[data-testid="stSidebar"] { background: #1a1d27; border-right: 1px solid #2d3149; }
.main .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1400px; }

div[data-testid="stSidebar"] .stButton button {
    width:100%; text-align:left; background:transparent;
    border:none; color:#b0b8d4; padding:0.5rem 1rem;
    border-radius:6px; font-size:0.9rem; transition:all 0.2s;
}
div[data-testid="stSidebar"] .stButton button:hover { background:#252a3f; color:#fff; }
div[data-testid="stSidebar"] .stButton button[kind="primary"] { background:#2563eb; color:white; }

.badge { display:inline-block; padding:2px 10px; border-radius:12px; font-size:0.76rem; font-weight:600; }
.badge-success { background:#064e3b; color:#34d399; }
.badge-failed  { background:#7f1d1d; color:#fca5a5; }
.badge-running { background:#1e3a5f; color:#60a5fa; }
.badge-pending { background:#292524; color:#a8a29e; }
.badge-skipped { background:#2d2a1e; color:#fbbf24; }
.badge-stopped { background:#312e2e; color:#d1d5db; }

.metric-card {
    background:#1a1d27; border:1px solid #2d3149;
    border-radius:10px; padding:1.2rem; text-align:center;
}
.metric-card .val { font-size:2rem; font-weight:700; color:#60a5fa; }
.metric-card .lbl { font-size:0.8rem; color:#6b7280; margin-top:4px; }

.routine-card {
    background:#1a1d27; border:1px solid #2d3149; border-radius:10px;
    padding:0.9rem 1.1rem; margin-bottom:8px; transition: border-color 0.2s;
    cursor: pointer;
}
.routine-card:hover { border-color: #3b82f6; }
.routine-card.selected { border-color:#2563eb; background:#1e2a45; }

.child-card {
    background:#151823; border-left:3px solid #2563eb;
    border-radius:0 8px 8px 0; padding:0.6rem 1rem;
    margin:4px 0 4px 24px;
}
.dep-tag {
    display:inline-block; background:#1e2a45; color:#60a5fa;
    padding:1px 8px; border-radius:8px; font-size:0.72rem; margin-right:4px;
}
.edit-panel {
    background:#141822; border:1px solid #2563eb;
    border-radius:10px; padding:1.2rem; margin-top:8px;
}
.section-header {
    font-size:1.4rem; font-weight:700; color:#e2e8f0;
    margin-bottom:1rem; padding-bottom:0.5rem;
    border-bottom:2px solid #2563eb;
}
.log-container {
    background:#0d1117; border:1px solid #21262d; border-radius:8px;
    padding:1rem; font-family:'Courier New',monospace; font-size:0.82rem;
    max-height:500px; overflow-y:auto;
}
/* Streamlit expander styling */
details { border:1px solid #2d3149 !important; border-radius:10px !important; background:#1a1d27 !important; margin-bottom:8px !important; }
details summary { padding:0.7rem 1rem !important; color:#e2e8f0 !important; font-weight:600 !important; }
details[open] { border-color:#2563eb !important; background:#1a1d27 !important; }
</style>
""", unsafe_allow_html=True)

# ─── Session State Init ───────────────────────────────────────────────────────
for key, default in [
    ("page", "Dashboard"),
    ("editing_routine_id", None),
    ("dash_expanded", set()),
    ("sched_started", False),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ─── Helpers ──────────────────────────────────────────────────────────────────
STATUS_BADGE = {
    "success": '<span class="badge badge-success">✓ success</span>',
    "failed":  '<span class="badge badge-failed">✗ failed</span>',
    "running": '<span class="badge badge-running">● running</span>',
    "pending": '<span class="badge badge-pending">◌ pending</span>',
    "skipped": '<span class="badge badge-skipped">⏭ skipped</span>',
    "stopped": '<span class="badge badge-stopped">■ stopped</span>',
}
TYPE_ICON = {
    "python":"🐍","excel":"📗","vba":"📘",
    "shell":"💻","api":"🌐","group":"📁",
}
COND_ICON = {"always":"▶","on_success":"✓","on_failure":"✗"}


def status_badge(status):
    return STATUS_BADGE.get(status, f'<span class="badge badge-pending">{status}</span>')


def fmt_dt(iso):
    if not iso: return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z",""))
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return iso[:16]


def _children_of(routines, parent_id):
    return sorted([r for r in routines if r.parent_id == parent_id], key=lambda r: r.order)


# ─── Inline Edit Form ─────────────────────────────────────────────────────────
def inline_edit_form(routine: Routine, routines, key_prefix=""):
    """Renders an inline edit form for a routine. Returns ('saved', updated) or ('deleted', None) or (None, None)."""
    all_parents = [r for r in routines if r.type == "group" and r.id != routine.id]
    parent_names = ["(nenhum)"] + [r.name for r in all_parents]
    current_parent = next((r.name for r in all_parents if r.id == routine.parent_id), "(nenhum)")

    dep_candidates = [r for r in routines if r.id != routine.id and r.parent_id == routine.parent_id]
    dep_names_all = [r.name for r in dep_candidates]
    current_deps = [r.name for r in dep_candidates if r.id in routine.depends_on]

    with st.form(key=f"edit_form_{key_prefix}_{routine.id}", clear_on_submit=False):
        st.markdown(f"<div style='font-size:1rem;font-weight:700;color:#60a5fa;margin-bottom:0.8rem;'>✏️ Editando: {routine.name}</div>", unsafe_allow_html=True)

        c1, c2, c3 = st.columns([2, 2, 1])
        with c1:
            e_name    = st.text_input("Nome",       value=routine.name,        key=f"en_{key_prefix}_{routine.id}")
            e_cmd     = st.text_input("Comando",    value=routine.command,     key=f"ec_{key_prefix}_{routine.id}")
            e_params  = st.text_input("Parâmetros", value=routine.parameters,  key=f"ep_{key_prefix}_{routine.id}")
            e_workdir = st.text_input("Working Dir",value=routine.working_dir, key=f"ew_{key_prefix}_{routine.id}")
        with c2:
            e_desc    = st.text_area("Descrição", value=routine.description, height=82, key=f"ed_{key_prefix}_{routine.id}")
            e_type    = st.selectbox("Tipo", ROUTINE_TYPES, index=ROUTINE_TYPES.index(routine.type), key=f"et_{key_prefix}_{routine.id}")
            e_parent  = st.selectbox("Rotina pai", parent_names,
                                     index=parent_names.index(current_parent) if current_parent in parent_names else 0,
                                     key=f"epar_{key_prefix}_{routine.id}")
            e_cond    = st.selectbox("Condição exec.", RUN_CONDITIONS,
                                     index=RUN_CONDITIONS.index(routine.run_condition),
                                     key=f"econd_{key_prefix}_{routine.id}")
        with c3:
            e_timeout = st.number_input("Timeout (s)", min_value=0, value=routine.timeout, key=f"eto_{key_prefix}_{routine.id}")
            e_retry   = st.number_input("Retries",     min_value=0, value=routine.retry,   key=f"er_{key_prefix}_{routine.id}")
            e_order   = st.number_input("Ordem",       min_value=0, value=routine.order,   key=f"eord_{key_prefix}_{routine.id}")
            e_enabled = st.checkbox("Habilitada", value=routine.enabled, key=f"ee_{key_prefix}_{routine.id}")

        if dep_names_all:
            e_deps = st.multiselect("Depende de", dep_names_all, default=current_deps, key=f"edep_{key_prefix}_{routine.id}")
        else:
            e_deps = []
            st.caption("Sem outras rotinas no mesmo grupo para criar dependência.")

        col_s, col_c, col_d = st.columns([2, 1, 1])
        saved = deleted = cancelled = False
        with col_s:
            saved = st.form_submit_button("💾 Salvar alterações", use_container_width=True, type="primary")
        with col_c:
            cancelled = st.form_submit_button("✕ Cancelar", use_container_width=True)
        with col_d:
            deleted = st.form_submit_button("🗑 Deletar rotina", use_container_width=True)

    if saved:
        parent_id = next((r.id for r in all_parents if r.name == e_parent), None) if e_parent != "(nenhum)" else None
        dep_ids = [r.id for r in dep_candidates if r.name in e_deps]
        routine.name        = e_name
        routine.type        = e_type
        routine.command     = e_cmd
        routine.parameters  = e_params
        routine.working_dir = e_workdir
        routine.description = e_desc
        routine.enabled     = e_enabled
        routine.timeout     = int(e_timeout)
        routine.retry       = int(e_retry)
        routine.order       = int(e_order)
        routine.parent_id   = parent_id
        routine.depends_on  = dep_ids
        routine.run_condition = e_cond
        update_routine(routine.id, routine)
        return "saved", routine

    if deleted:
        delete_routine(routine.id)
        return "deleted", None

    if cancelled:
        return "cancelled", None

    return None, None


# ─── Sidebar ──────────────────────────────────────────────────────────────────
def sidebar():
    with st.sidebar:
        st.markdown("""
        <div style='text-align:center;padding:1rem 0 1.5rem;'>
            <div style='font-size:2rem;'>📊</div>
            <div style='font-size:1.1rem;font-weight:700;color:#e2e8f0;'>Finance Cockpit</div>
            <div style='font-size:0.75rem;color:#6b7280;'>Routine Orchestrator</div>
        </div>""", unsafe_allow_html=True)

        for icon, name in [("📋","Dashboard"),("🔀","Workflow"),("🟢","Live"),("⚡","Variables"),("🔧","Routines"),("🕐","Scheduler"),("📜","Logs"),("⚙️","Settings")]:
            is_active = st.session_state.page == name
            if st.button(f"{icon}  {name}", key=f"nav_{name}", type="primary" if is_active else "secondary", use_container_width=True):
                st.session_state.page = name
                st.session_state.editing_routine_id = None
                st.rerun()

        st.markdown("---")
        running = sched_running()
        dot = "🟢" if running else "🔴"
        st.markdown(f"<div style='text-align:center;font-size:0.8rem;color:#9ca3af;'>{dot} Scheduler {'ON' if running else 'OFF'}</div>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("▶ Start", use_container_width=True, disabled=running):
                start_scheduler(); st.rerun()
        with c2:
            if st.button("■ Stop", use_container_width=True, disabled=not running):
                stop_scheduler(); st.rerun()

        st.markdown(f"<div style='text-align:center;margin-top:2rem;font-size:0.7rem;color:#374151;'>v1.0.0 · {datetime.now().strftime('%d/%m/%Y')}</div>", unsafe_allow_html=True)


# ─── Dashboard ────────────────────────────────────────────────────────────────
def page_dashboard():
    st.markdown('<div class="section-header">📋 Dashboard</div>', unsafe_allow_html=True)

    routines  = load_routines()
    schedules = load_schedules()
    logs      = load_logs(50)
    running_ids = get_running_ids()

    # ── Metrics ──
    total   = len(routines)
    success = sum(1 for r in routines if r.last_status == "success")
    failed  = sum(1 for r in routines if r.last_status == "failed")
    running = len(running_ids)
    enabled = sum(1 for r in routines if r.enabled)

    for col, val, lbl, color in zip(
        st.columns(5),
        [total, success, failed, running, enabled],
        ["Total Routines","Success","Failed","Running","Enabled"],
        ["#60a5fa","#34d399","#f87171","#60a5fa","#a78bfa"],
    ):
        with col:
            st.markdown(f'<div class="metric-card"><div class="val" style="color:{color}">{val}</div><div class="lbl">{lbl}</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    left, right = st.columns([3, 1])

    with left:
        st.markdown("#### 🔧 Routines")

        parents = [r for r in routines if r.parent_id is None]
        children_map = {}
        for r in routines:
            if r.parent_id:
                children_map.setdefault(r.parent_id, []).append(r)

        editing_id = st.session_state.get("editing_routine_id")

        for parent in sorted(parents, key=lambda r: r.name):
            p_running = parent.id in running_ids
            p_status  = "running" if p_running else parent.last_status
            p_icon    = TYPE_ICON.get(parent.type, "⚙️")
            children  = sorted(children_map.get(parent.id, []), key=lambda r: r.order)
            has_children = bool(children)

            # Parent row inside expander so children are visible
            with st.expander(
                f"{p_icon} **{parent.name}** {'🟢' if parent.enabled else '🔴'}  "
                f"{'  ↳ ' + str(len(children)) + ' sub-rotinas' if has_children else ''}",
                expanded=(parent.id in st.session_state.dash_expanded or editing_id == parent.id),
            ):
                # ── Parent detail row ──
                d1, d2, d3, d4, d5 = st.columns([3, 2, 2, 1, 1])
                with d1:
                    st.markdown(
                        f"<span style='color:#9ca3af;font-size:0.82rem'>{parent.description or '—'}</span><br>"
                        f"<span style='color:#4b5563;font-size:0.78rem'>type: `{parent.type}` · timeout: {parent.timeout}s · retry: {parent.retry}x</span>",
                        unsafe_allow_html=True,
                    )
                with d2:
                    st.markdown(status_badge(p_status), unsafe_allow_html=True)
                    st.markdown(f"<span style='color:#6b7280;font-size:0.78rem'>Last: {fmt_dt(parent.last_run)}</span>", unsafe_allow_html=True)
                with d3:
                    cmd_str = parent.command or "—"
                    params_str = parent.parameters or ""
                    st.markdown(f"<code style='font-size:0.78rem;color:#a78bfa'>{cmd_str}</code><br><span style='color:#4b5563;font-size:0.75rem'>{params_str[:40]}</span>", unsafe_allow_html=True)
                with d4:
                    if st.button("▶ Run", key=f"d_run_{parent.id}", use_container_width=True, type="primary"):
                        if parent.type == "group":
                            run_group(parent.id)
                        else:
                            run_routine(parent.id)
                        st.toast(f"▶ {parent.name} iniciado!")
                        time.sleep(0.3); st.rerun()
                    if p_running:
                        if st.button("■ Stop", key=f"d_stop_{parent.id}", use_container_width=True):
                            stop_routine(parent.id); st.rerun()
                with d5:
                    if st.button("✏️ Edit", key=f"d_edit_{parent.id}", use_container_width=True):
                        st.session_state.editing_routine_id = parent.id if editing_id != parent.id else None
                        st.rerun()
                    tog = st.toggle("On", value=parent.enabled, key=f"d_tog_{parent.id}")
                    if tog != parent.enabled:
                        parent.enabled = tog
                        update_routine(parent.id, parent)
                        st.rerun()

                # ── Inline Edit Form for parent ──
                if editing_id == parent.id:
                    st.markdown('<div class="edit-panel">', unsafe_allow_html=True)
                    action, updated = inline_edit_form(parent, routines, key_prefix="dash")
                    st.markdown('</div>', unsafe_allow_html=True)
                    if action == "saved":
                        st.toast("✓ Salvo!")
                        st.session_state.editing_routine_id = None
                        st.rerun()
                    elif action == "deleted":
                        st.toast("✓ Deletado!")
                        st.session_state.editing_routine_id = None
                        st.rerun()
                    elif action == "cancelled":
                        st.session_state.editing_routine_id = None
                        st.rerun()

                # ── Children ──
                if children:
                    st.markdown("<div style='margin-top:8px;border-top:1px solid #1f2937;padding-top:8px;'>", unsafe_allow_html=True)
                    for child in children:
                        c_running = child.id in running_ids
                        c_status  = "running" if c_running else child.last_status
                        c_icon    = TYPE_ICON.get(child.type, "⚙️")

                        cc1, cc2, cc3, cc4, cc5 = st.columns([3, 2, 2, 1, 1])
                        with cc1:
                            dep_tags = ""
                            for dep_id in child.depends_on:
                                dep_r = next((r for r in routines if r.id == dep_id), None)
                                if dep_r:
                                    dep_tags += f'<span class="dep-tag">→ {dep_r.name}</span>'
                            cond_icon = COND_ICON.get(child.run_condition, "")
                            st.markdown(
                                f"&nbsp;&nbsp;↳ {c_icon} **{child.name}** "
                                f"<span style='color:#6b7280;font-size:0.78rem'>{cond_icon} {child.run_condition}</span><br>"
                                f"{dep_tags}"
                                f"<span style='color:#4b5563;font-size:0.75rem'>ordem: {child.order}</span>",
                                unsafe_allow_html=True,
                            )
                        with cc2:
                            st.markdown(status_badge(c_status), unsafe_allow_html=True)
                            st.markdown(f"<span style='color:#6b7280;font-size:0.78rem'>Last: {fmt_dt(child.last_run)}</span>", unsafe_allow_html=True)
                        with cc3:
                            st.markdown(
                                f"<code style='font-size:0.78rem;color:#a78bfa'>{child.command or '—'}</code><br>"
                                f"<span style='color:#4b5563;font-size:0.75rem'>{child.parameters[:40] if child.parameters else ''}</span>",
                                unsafe_allow_html=True,
                            )
                        with cc4:
                            if st.button("▶", key=f"d_crun_{child.id}", help="Executar"):
                                run_routine(child.id)
                                st.toast(f"▶ {child.name} iniciado!")
                                time.sleep(0.3); st.rerun()
                            if c_running:
                                if st.button("■", key=f"d_cstop_{child.id}"):
                                    stop_routine(child.id); st.rerun()
                        with cc5:
                            if st.button("✏️", key=f"d_cedit_{child.id}", help="Editar"):
                                st.session_state.editing_routine_id = child.id if editing_id != child.id else None
                                st.rerun()
                            tog_c = st.toggle("On", value=child.enabled, key=f"d_ctog_{child.id}")
                            if tog_c != child.enabled:
                                child.enabled = tog_c
                                update_routine(child.id, child)
                                st.rerun()

                        # ── Inline edit for child ──
                        if editing_id == child.id:
                            st.markdown('<div class="edit-panel">', unsafe_allow_html=True)
                            action, updated = inline_edit_form(child, routines, key_prefix="dashc")
                            st.markdown('</div>', unsafe_allow_html=True)
                            if action == "saved":
                                st.toast("✓ Salvo!")
                                st.session_state.editing_routine_id = None
                                st.rerun()
                            elif action == "deleted":
                                st.toast("✓ Deletado!")
                                st.session_state.editing_routine_id = None
                                st.rerun()
                            elif action == "cancelled":
                                st.session_state.editing_routine_id = None
                                st.rerun()

                        st.markdown("<hr style='border-color:#1a2030;margin:4px 0'>", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown("#### 🕐 Próximas Execuções")
        active_scheds = [s for s in schedules if s.enabled]
        if not active_scheds:
            st.info("Nenhum agendamento ativo.")
        for s in active_scheds:
            r = next((x for x in routines if x.id == s.routine_id), None)
            rname = r.name if r else s.routine_id
            st.markdown(f"""
            <div style='background:#1a1d27;border:1px solid #2d3149;border-radius:8px;padding:0.7rem;margin-bottom:8px;'>
                <div style='font-weight:600;color:#e2e8f0;font-size:0.9rem;'>⏰ {s.name}</div>
                <div style='color:#60a5fa;font-size:0.8rem;'>{rname}</div>
                <div style='color:#6b7280;font-size:0.75rem;'>Próxima: {fmt_dt(s.next_run)}</div>
                <div style='color:#4b5563;font-size:0.72rem;'><code>{s.cron}</code></div>
            </div>""", unsafe_allow_html=True)

        st.markdown("#### 📜 Últimos Logs")
        for log in reversed(load_logs(10)):
            lvl_color = "#f87171" if log.level == "ERROR" else "#9ca3af"
            st.markdown(
                f"<div style='font-family:monospace;font-size:0.75rem;color:{lvl_color};'>"
                f"[{fmt_dt(log.timestamp)}] {log.message[:55]}</div>",
                unsafe_allow_html=True,
            )

        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()


# ─── Variables ────────────────────────────────────────────────────────────────
def page_variables():
    st.markdown('<div class="section-header">⚡ Variables</div>', unsafe_allow_html=True)
    variables = load_variables()

    tab_list, tab_new = st.tabs(["📋 Variables", "➕ New Variable"])

    with tab_list:
        if not variables:
            st.info("Nenhuma variável cadastrada. Crie na aba 'New Variable'.")
        else:
            for var in variables:
                with st.expander(f"**{var.name}** = `{var.value}`", expanded=False):
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        new_val  = st.text_input("Valor",      value=var.value,       key=f"val_{var.id}")
                        new_desc = st.text_input("Descrição",  value=var.description, key=f"desc_{var.id}")
                        st.caption(f"ID: `{var.id}` · Criado: {fmt_dt(var.created_at)}")
                    with c2:
                        st.markdown("<br><br>", unsafe_allow_html=True)
                        if st.button("💾 Salvar", key=f"save_{var.id}", use_container_width=True, type="primary"):
                            var.value = new_val; var.description = new_desc
                            update_variable(var.id, var)
                            st.toast("✓ Salvo!"); st.rerun()
                        if st.button("🗑 Deletar", key=f"del_{var.id}", use_container_width=True):
                            delete_variable(var.id)
                            st.toast("✓ Deletado!"); st.rerun()

    with tab_new:
        with st.form("new_var_form", clear_on_submit=True):
            st.markdown("#### Nova Variável")
            c1, c2 = st.columns(2)
            with c1:
                name  = st.text_input("Nome *",  placeholder="ex: Data_Ref")
                value = st.text_input("Valor *", placeholder="ex: 2026-03-23")
            with c2:
                desc = st.text_area("Descrição", placeholder="Para que serve esta variável?", height=95)
            if st.form_submit_button("✓ Criar Variável", use_container_width=True, type="primary"):
                if not name or not value:
                    st.error("Nome e Valor são obrigatórios.")
                elif any(v.name == name for v in variables):
                    st.error(f"Variável '{name}' já existe.")
                else:
                    add_variable(Variable(name=name, value=value, description=desc))
                    st.toast(f"✓ '{name}' criada!"); st.rerun()

    with st.expander("🔍 Preview de substituição"):
        vars_dict = get_variables_dict()
        test = st.text_input("Teste de template", placeholder="ex: python script.py --date {Data_Ref} --fund {Fund_Name}")
        if test:
            from backend.executor import substitute_variables
            st.code(substitute_variables(test, vars_dict))
        st.markdown("**Variáveis disponíveis:**")
        for k, v in vars_dict.items():
            st.markdown(f"- `{{{k}}}` → `{v}`")


# ─── Routines ─────────────────────────────────────────────────────────────────
def page_routines():
    st.markdown('<div class="section-header">🔧 Routines</div>', unsafe_allow_html=True)
    routines    = load_routines()
    running_ids = get_running_ids()
    editing_id  = st.session_state.get("editing_routine_id")

    tab_tree, tab_group, tab_sub = st.tabs(["🌳 Tree View", "📁 Nova Rotina Pai (Group)", "⚙️ Nova Sub-rotina"])

    with tab_tree:
        if not routines:
            st.info("Nenhuma rotina cadastrada.")
        else:
            parents = [r for r in routines if r.parent_id is None]
            children_map = {}
            for r in routines:
                if r.parent_id:
                    children_map.setdefault(r.parent_id, []).append(r)

            for parent in sorted(parents, key=lambda r: r.order):
                p_running = parent.id in running_ids
                p_status  = "running" if p_running else parent.last_status
                p_icon    = TYPE_ICON.get(parent.type, "⚙️")
                children  = sorted(children_map.get(parent.id, []), key=lambda r: r.order)

                with st.expander(
                    f"{p_icon} **{parent.name}** {'🟢' if parent.enabled else '🔴'}  "
                    f"{'· ' + str(len(children)) + ' sub-rotinas' if children else ''}",
                    expanded=(editing_id == parent.id or any(editing_id == c.id for c in children)),
                ):
                    # Header row
                    h1, h2, h3, h4, h5, h6 = st.columns([2, 2, 2, 1, 1, 1])
                    with h1:
                        st.markdown(
                            f"<span style='color:#9ca3af;font-size:0.82rem'>{parent.description or '—'}</span><br>"
                            f"<code style='font-size:0.76rem;color:#a78bfa'>{parent.command or '(group)'}</code>",
                            unsafe_allow_html=True,
                        )
                    with h2:
                        st.markdown(status_badge(p_status), unsafe_allow_html=True)
                        st.markdown(f"<span style='color:#6b7280;font-size:0.78rem'>Last: {fmt_dt(parent.last_run)}</span>", unsafe_allow_html=True)
                    with h3:
                        params_preview = parent.parameters[:50] if parent.parameters else "—"
                        st.markdown(f"<span style='color:#4b5563;font-size:0.78rem'>{params_preview}</span>", unsafe_allow_html=True)
                    with h4:
                        if st.button("▶ Run", key=f"rt_run_{parent.id}", use_container_width=True, type="primary"):
                            if parent.type == "group": run_group(parent.id)
                            else: run_routine(parent.id)
                            st.toast(f"▶ {parent.name}"); time.sleep(0.3); st.rerun()
                        if p_running:
                            if st.button("■ Stop", key=f"rt_stop_{parent.id}", use_container_width=True):
                                stop_routine(parent.id); st.rerun()
                    with h5:
                        if st.button("✏️ Editar", key=f"rt_edit_{parent.id}", use_container_width=True):
                            st.session_state.editing_routine_id = parent.id if editing_id != parent.id else None
                            st.rerun()
                    with h6:
                        tog = st.toggle("On", value=parent.enabled, key=f"rt_tog_{parent.id}")
                        if tog != parent.enabled:
                            parent.enabled = tog; update_routine(parent.id, parent); st.rerun()

                    # Edit form
                    if editing_id == parent.id:
                        st.markdown("---")
                        action, _ = inline_edit_form(parent, routines, key_prefix="rt")
                        if action in ("saved", "deleted", "cancelled"):
                            st.session_state.editing_routine_id = None
                            st.toast("✓ Salvo!" if action == "saved" else "✓ Deletado!" if action == "deleted" else "Cancelado")
                            st.rerun()

                    # Children
                    for child in children:
                        st.markdown("<hr style='border-color:#1a2030;margin:6px 0'>", unsafe_allow_html=True)
                        c_running = child.id in running_ids
                        c_status  = "running" if c_running else child.last_status
                        c_icon    = TYPE_ICON.get(child.type, "⚙️")

                        cc1, cc2, cc3, cc4, cc5, cc6 = st.columns([2, 2, 2, 1, 1, 1])
                        with cc1:
                            dep_tags = "".join(
                                f'<span class="dep-tag">→ {next((r.name for r in routines if r.id==d), d)}</span>'
                                for d in child.depends_on
                            )
                            cond_icon = COND_ICON.get(child.run_condition, "")
                            st.markdown(
                                f"&nbsp;&nbsp;↳ {c_icon} **{child.name}**<br>"
                                f"{dep_tags}<span style='color:#6b7280;font-size:0.75rem'>{cond_icon} {child.run_condition} · ordem {child.order}</span>",
                                unsafe_allow_html=True,
                            )
                        with cc2:
                            st.markdown(status_badge(c_status), unsafe_allow_html=True)
                            st.markdown(f"<span style='color:#6b7280;font-size:0.78rem'>Last: {fmt_dt(child.last_run)}</span>", unsafe_allow_html=True)
                        with cc3:
                            st.markdown(
                                f"<code style='font-size:0.78rem;color:#a78bfa'>{child.command or '—'}</code><br>"
                                f"<span style='color:#4b5563;font-size:0.75rem'>{child.parameters[:45] if child.parameters else ''}</span>",
                                unsafe_allow_html=True,
                            )
                        with cc4:
                            if st.button("▶", key=f"rt_crun_{child.id}", help="Run"):
                                run_routine(child.id); st.toast(f"▶ {child.name}"); time.sleep(0.3); st.rerun()
                            if c_running:
                                if st.button("■", key=f"rt_cstop_{child.id}"):
                                    stop_routine(child.id); st.rerun()
                        with cc5:
                            if st.button("✏️", key=f"rt_cedit_{child.id}", help="Editar"):
                                st.session_state.editing_routine_id = child.id if editing_id != child.id else None
                                st.rerun()
                        with cc6:
                            tog_c = st.toggle("On", value=child.enabled, key=f"rt_ctog_{child.id}")
                            if tog_c != child.enabled:
                                child.enabled = tog_c; update_routine(child.id, child); st.rerun()

                        if editing_id == child.id:
                            st.markdown("&nbsp;&nbsp;", unsafe_allow_html=True)
                            action, _ = inline_edit_form(child, routines, key_prefix="rtc")
                            if action in ("saved", "deleted", "cancelled"):
                                st.session_state.editing_routine_id = None
                                st.toast("✓ Salvo!" if action == "saved" else "✓ Deletado!")
                                st.rerun()

    # ── Tab: Nova Rotina Pai (Group) ──────────────────────────────────────────
    with tab_group:
        st.markdown("""
        <div style='background:#1a2a1a;border:1px solid #2d4a2d;border-radius:8px;padding:0.8rem 1rem;margin-bottom:1rem;'>
            <div style='color:#34d399;font-weight:600;'>📁 Rotina Pai = Pipeline / Grupo</div>
            <div style='color:#9ca3af;font-size:0.85rem;margin-top:4px;'>
                Uma rotina pai é um <b>container</b> que agrupa sub-rotinas em sequência.<br>
                Não executa nenhum comando diretamente — ela organiza e dispara as filhas.
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.form("new_group_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                g_name    = st.text_input("Nome do Pipeline *", placeholder="ex: Daily Finance Pipeline")
                g_timeout = st.number_input("Timeout total (s)", min_value=0, value=3600,
                                            help="Tempo máximo para todas as sub-rotinas terminarem")
                g_retry   = st.number_input("Retries",           min_value=0, max_value=5, value=0)
            with c2:
                g_desc    = st.text_area("Descrição",   placeholder="O que este pipeline faz?", height=100)
                g_enabled = st.checkbox("Habilitado", value=True)
                g_order   = st.number_input("Ordem de exibição", min_value=0, value=0)

            if st.form_submit_button("📁 Criar Rotina Pai", use_container_width=True, type="primary"):
                if not g_name:
                    st.error("Nome é obrigatório.")
                else:
                    add_routine(Routine(
                        name=g_name, type="group", description=g_desc,
                        command="", working_dir="", parameters="",
                        enabled=g_enabled, timeout=int(g_timeout), retry=int(g_retry),
                        parent_id=None, order=int(g_order), depends_on=[], run_condition="always",
                    ))
                    st.toast(f"✓ Pipeline '{g_name}' criado! Agora adicione sub-rotinas a ele.")
                    st.rerun()

    # ── Tab: Nova Sub-rotina ───────────────────────────────────────────────────
    with tab_sub:
        st.markdown("""
        <div style='background:#1a1a2a;border:1px solid #2d2d4a;border-radius:8px;padding:0.8rem 1rem;margin-bottom:1rem;'>
            <div style='color:#60a5fa;font-weight:600;'>⚙️ Sub-rotina = Script / Comando real</div>
            <div style='color:#9ca3af;font-size:0.85rem;margin-top:4px;'>
                Uma sub-rotina executa um comando real (Python, Shell, Excel, API).<br>
                Associe-a a uma <b>Rotina Pai</b> para ela fazer parte de um pipeline.
            </div>
        </div>
        """, unsafe_allow_html=True)

        groups = [r for r in routines if r.type == "group"]

        if not groups:
            st.warning("⚠️ Crie pelo menos uma **Rotina Pai (Group)** antes de adicionar sub-rotinas.")

        with st.form("new_sub_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                s_name = st.text_input("Nome *", placeholder="ex: Export PnL")
                s_type = st.selectbox("Tipo *",  [t for t in ROUTINE_TYPES if t != "group"])

                # Dynamic hints depending on selected type
                _cmd_hints = {
                    "python": ("Comando *", "ex: export_pnl.py ou C:\\scripts\\pnl.py",
                               "Arquivo .py a executar. Pode usar variáveis: {Path_Base}\\script.py"),
                    "vba":    ("Arquivo Excel *", "ex: C:\\Finance\\relatorio.xlsm",
                               "Caminho completo para o arquivo .xlsm/.xlsb. Em Parâmetros coloque o nome da macro."),
                    "excel":  ("Arquivo Excel *", "ex: C:\\Finance\\report.xlsx",
                               "Caminho completo para o arquivo Excel. Será aberto, atualizado e salvo automaticamente."),
                    "shell":  ("Comando *", "ex: update_data.bat",
                               "Arquivo .bat, .cmd ou script shell a executar."),
                    "api":    ("URL *", "ex: https://api.exemplo.com/refresh",
                               "URL da API. Será chamada via curl -s."),
                }
                _ph = _cmd_hints.get(s_type, ("Comando *", "ex: comando", ""))
                s_command = st.text_input(_ph[0], placeholder=_ph[1], help=_ph[2])

                _param_hints = {
                    "python": ("Parâmetros",   "ex: --date {Data_Ref} --fund {Fund_Name}",
                               "Argumentos passados ao script Python."),
                    "vba":    ("Nome da Macro","ex: Module1.ExportPnL  ou  ExportPnL",
                               "Nome exato da macro VBA a executar. Use 'Modulo.NomeMacro' se necessário. Deixe vazio para apenas abrir/salvar/fechar."),
                    "excel":  ("Parâmetros",   "",
                               "Não utilizado para tipo Excel puro — deixe vazio."),
                    "shell":  ("Parâmetros",   "ex: --env prod",
                               "Argumentos passados ao script shell."),
                    "api":    ("Parâmetros",   "ex: -H 'Authorization: Bearer {API_TOKEN}'",
                               "Flags extras passadas ao curl."),
                }
                _pp = _param_hints.get(s_type, ("Parâmetros", "", ""))
                s_params  = st.text_input(_pp[0], placeholder=_pp[1], help=_pp[2])
                s_workdir = st.text_input("Working Dir", placeholder="ex: {Path_Base}\\scripts",
                                          help="Diretório de trabalho. Deixe vazio para usar o diretório do Cockpit.")
            with c2:
                s_desc    = st.text_area("Descrição", height=70)
                p_names   = ["(sem pai — rotina independente)"] + [r.name for r in groups]
                s_parent  = st.selectbox("📁 Rotina Pai *", p_names,
                                         help="Selecione o pipeline ao qual esta sub-rotina pertence")
                s_timeout = st.number_input("Timeout (s)",  min_value=0, value=300)
                s_retry   = st.number_input("Retries",      min_value=0, max_value=10, value=0)
                s_order   = st.number_input("Ordem de execução", min_value=0, value=0,
                                            help="0 = primeira, 1 = segunda, etc.")
                s_enabled = st.checkbox("Habilitada", value=True)

            # Dependencies — only siblings (same parent)
            parent_obj = next((r for r in groups if r.name == s_parent), None)
            sibling_routines = [r for r in routines if r.parent_id == (parent_obj.id if parent_obj else None)]
            sibling_names = [r.name for r in sibling_routines]

            if sibling_names:
                s_deps = st.multiselect(
                    "Depende de (executa só após estas terminarem)",
                    sibling_names,
                    help="Deixe vazio para executar sem dependência"
                )
                s_cond = st.selectbox(
                    "Condição",
                    RUN_CONDITIONS,
                    format_func=lambda x: {"always":"▶ Sempre","on_success":"✓ Só se a dependência tiver sucesso","on_failure":"✗ Só se a dependência falhar"}.get(x, x),
                )
            else:
                s_deps = []
                s_cond = "always"
                if s_parent != "(sem pai — rotina independente)":
                    st.info("Este grupo ainda não tem outras sub-rotinas. Dependências poderão ser adicionadas depois.")

            # VBA-specific guidance block (always visible when type == vba)
            if s_type == "vba":
                st.markdown("""
                <div style='background:#1a2a1a;border:1px solid #34d399;border-radius:8px;
                            padding:0.75rem 1rem;margin-top:0.5rem;font-size:0.85rem;'>
                    <div style='color:#34d399;font-weight:700;margin-bottom:6px;'>📘 Como configurar uma Macro VBA</div>
                    <table style='color:#d1fae5;border-collapse:collapse;width:100%;'>
                        <tr><td style='padding:2px 8px 2px 0;color:#6ee7b7;font-weight:600;'>Arquivo Excel</td>
                            <td>Caminho completo do <code>.xlsm</code> ou <code>.xlsb</code><br>
                                Ex: <code>C:\\Finance\\relatorio.xlsm</code></td></tr>
                        <tr><td style='padding:2px 8px 2px 0;color:#6ee7b7;font-weight:600;'>Nome da Macro</td>
                            <td>Nome exato da Sub VBA a executar<br>
                                Ex: <code>ExportPnL</code> &nbsp;ou&nbsp; <code>Module1.ExportPnL</code><br>
                                Deixe vazio para apenas abrir → salvar → fechar (atualiza conexões).</td></tr>
                        <tr><td style='padding:2px 8px 2px 0;color:#6ee7b7;font-weight:600;'>Timeout</td>
                            <td>Defina pelo menos 120s para arquivos grandes</td></tr>
                    </table>
                    <div style='color:#9ca3af;margin-top:6px;'>
                        ⚙️ A execução usa <b>PowerShell + COM (Excel.Application)</b> — requer Excel instalado na máquina.
                    </div>
                </div>
                """, unsafe_allow_html=True)

            if st.form_submit_button("⚙️ Criar Sub-rotina", use_container_width=True, type="primary"):
                if not s_name or not s_command:
                    st.error("Nome e Comando são obrigatórios.")
                else:
                    pid = parent_obj.id if parent_obj else None
                    dep_ids = [r.id for r in sibling_routines if r.name in s_deps]
                    add_routine(Routine(
                        name=s_name, type=s_type, description=s_desc,
                        command=s_command, working_dir=s_workdir, parameters=s_params,
                        enabled=s_enabled, timeout=int(s_timeout), retry=int(s_retry),
                        parent_id=pid, order=int(s_order), depends_on=dep_ids,
                        run_condition=s_cond,
                    ))
                    st.toast(f"✓ '{s_name}' criada!"); st.rerun()


# ─── Scheduler ────────────────────────────────────────────────────────────────
def page_scheduler():
    st.markdown('<div class="section-header">🕐 Scheduler</div>', unsafe_allow_html=True)
    schedules = load_schedules()
    routines  = load_routines()

    running = sched_running()
    color   = "#34d399" if running else "#f87171"
    st.markdown(
        f"<div style='background:#1a1d27;border:1px solid #2d3149;border-radius:8px;"
        f"padding:0.8rem 1rem;margin-bottom:1rem;display:flex;align-items:center;gap:1rem;'>"
        f"<div style='width:10px;height:10px;border-radius:50%;background:{color};'></div>"
        f"<div style='color:#e2e8f0;font-weight:600;'>Scheduler: {'RUNNING' if running else 'STOPPED'}</div></div>",
        unsafe_allow_html=True,
    )

    tab_list, tab_new = st.tabs(["📋 Schedules", "➕ New Schedule"])

    with tab_list:
        if not schedules:
            st.info("Nenhum agendamento configurado.")
        for s in schedules:
            r     = next((x for x in routines if x.id == s.routine_id), None)
            rname = r.name if r else "—"
            with st.expander(f"{'🟢' if s.enabled else '🔴'} **{s.name}** — `{s.cron}`", expanded=False):
                c1, c2, c3 = st.columns([2, 2, 1])
                with c1:
                    st.markdown(f"**Rotina:** {rname}")
                    st.markdown(f"**Cron:** `{s.cron}`")
                    st.markdown(f"**Timezone:** `{s.timezone}`")
                    st.markdown(f"**Descrição:** {s.description or '—'}")
                with c2:
                    st.markdown(f"**Próxima:** {fmt_dt(s.next_run)}")
                    st.markdown(f"**Último trigger:** {fmt_dt(s.last_triggered)}")
                with c3:
                    if st.button("🔄 Recalcular", key=f"ref_{s.id}", use_container_width=True):
                        s.next_run = get_next_run(s.cron, s.timezone)
                        update_schedule(s.id, s); st.rerun()
                    tog = st.toggle("Enabled", value=s.enabled, key=f"tog_{s.id}")
                    if tog != s.enabled:
                        s.enabled = tog; update_schedule(s.id, s); st.rerun()
                    if st.button("🗑 Deletar", key=f"del_s_{s.id}", use_container_width=True):
                        delete_schedule(s.id); st.rerun()

    with tab_new:
        routine_opts = {r.name: r.id for r in routines if r.enabled}
        with st.form("new_schedule_form", clear_on_submit=True):
            st.markdown("#### Novo Agendamento")
            c1, c2 = st.columns(2)
            with c1:
                sname  = st.text_input("Nome *",        placeholder="ex: Daily Morning Run")
                scron  = st.text_input("Cron *",        placeholder="0 8 * * 1-5",
                                       help="min hora dia mês dia_semana")
                sdesc  = st.text_input("Descrição",     placeholder="08:00 dias úteis")
            with c2:
                sroute = st.selectbox("Rotina *", list(routine_opts.keys()) or ["—"])
                stz    = st.text_input("Timezone", value="America/Sao_Paulo")
                senab  = st.checkbox("Habilitado", value=True)

            st.markdown("""
            **Exemplos:** `0 8 * * 1-5` (08h dias úteis) · `0 */2 * * *` (a cada 2h) · `*/15 * * * *` (a cada 15min) · `0 23 * * *` (23h diário)
            """)
            if st.form_submit_button("✓ Criar Schedule", use_container_width=True, type="primary"):
                if not sname or not scron or not routine_opts:
                    st.error("Nome, Cron e Rotina são obrigatórios.")
                else:
                    rid = routine_opts.get(sroute, "")
                    nxt = get_next_run(scron, stz)
                    add_schedule(Schedule(name=sname, routine_id=rid, cron=scron,
                                         description=sdesc, enabled=senab, timezone=stz, next_run=nxt))
                    st.toast(f"✓ '{sname}' criado! Próxima: {fmt_dt(nxt)}"); st.rerun()


# ─── Logs ─────────────────────────────────────────────────────────────────────
def _render_log_lines(entries):
    """Render a list of log dicts/LogEntry objects as a terminal HTML block."""
    if not entries:
        st.info("Nenhum log encontrado.")
        return
    html = '<div class="log-container">'
    for e in entries:
        # Support both LogEntry objects and plain dicts (from live buffer)
        if hasattr(e, "timestamp"):
            ts   = fmt_dt(e.timestamp)
            lvl  = e.level
            nm   = e.routine_name
            msg  = e.message
            rid  = e.run_id[:6] if e.run_id else ""
        else:
            ts   = fmt_dt(e.get("ts",""))
            lvl  = e.get("level","INFO")
            nm   = e.get("name","")
            msg  = e.get("message","")
            rid  = e.get("run_id","")[:6]

        lc = {"ERROR":"#f85149","WARN":"#e3b341","INFO":"#3fb950"}.get(lvl, "#9ca3af")
        lvl_span = f'<span style="color:{lc};font-weight:bold;">[{lvl:5s}]</span>'
        run_span = f'<span style="color:#4b5563;">#{rid}</span> ' if rid else ""
        nm_span  = f'<span style="color:#60a5fa;">{nm[:24]}</span>'
        html += (
            f'<div style="padding:1px 0;white-space:pre-wrap;">'
            f'<span style="color:#4b5563;">{ts}</span> {lvl_span} {run_span}{nm_span} '
            f'<span style="color:#c9d1d9;">{msg}</span></div>'
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def page_logs():
    st.markdown('<div class="section-header">📜 Execution Logs</div>', unsafe_allow_html=True)
    all_logs = load_logs(500)

    tab_runs, tab_filter = st.tabs(["🕐 Por Execução (Run)", "🔎 Filtro Livre"])

    # ── Tab 1: grouped by run_id, most recent first ──────────────────────────
    with tab_runs:
        if not all_logs:
            st.info("Nenhum log ainda. Execute uma rotina para ver o histórico aqui.")
        else:
            # Group by run_id — iterate in CHRONOLOGICAL order so entries stay oldest→newest
            runs: dict = {}
            for e in all_logs:          # oldest first
                rid = e.run_id or "__no_run__"
                if rid not in runs:
                    runs[rid] = []
                runs[rid].append(e)     # each list is now oldest → newest

            # Sort runs so most recent run appears first
            runs_sorted = sorted(
                runs.items(),
                key=lambda kv: kv[1][-1].timestamp,  # last (= newest) entry timestamp
                reverse=True,
            )

            # Optionally filter to one routine
            routine_names_in_logs = sorted(set(e.routine_name for e in all_logs))
            f_r = st.selectbox(
                "Filtrar por rotina",
                ["Todas as rotinas"] + routine_names_in_logs,
                key="log_run_filter",
            )

            for run_id, entries in runs_sorted:
                if f_r != "Todas as rotinas":
                    entries = [e for e in entries if e.routine_name == f_r]
                if not entries:
                    continue

                # Summary for the run
                first_ts        = fmt_dt(entries[0].timestamp)   # oldest = start
                last_ts         = fmt_dt(entries[-1].timestamp)  # newest = end
                has_error       = any(e.level == "ERROR" for e in entries)
                routines_in_run = sorted(set(e.routine_name for e in entries))
                status_icon     = "✗" if has_error else "✓"
                routines_label  = ", ".join(routines_in_run[:3])
                run_label       = f"{status_icon}  #{run_id[:8]}  {routines_label}  {first_ts} → {last_ts}  ({len(entries)} linhas)"

                with st.expander(run_label, expanded=False):
                    sc = "#f87171" if has_error else "#34d399"
                    st.markdown(
                        f"<div style='display:flex;gap:16px;align-items:center;"
                        f"background:#141822;border-radius:8px;padding:8px 12px;margin-bottom:8px;'>"
                        f"<span style='font-size:1.3rem;color:{sc};'>{status_icon}</span>"
                        f"<div>"
                        f"<div style='color:#e2e8f0;font-weight:600;'>{routines_label}</div>"
                        f"<div style='color:#6b7280;font-size:0.78rem;'>"
                        f"Run <code style='color:#9ca3af;'>#{run_id[:8]}</code> &nbsp;·&nbsp; "
                        f"{first_ts} → {last_ts} &nbsp;·&nbsp; {len(entries)} linhas"
                        f"</div></div></div>",
                        unsafe_allow_html=True,
                    )
                    # entries already in chronological order (oldest → newest = top → bottom)
                    _render_log_lines(entries)

    # ── Tab 2: free filter ────────────────────────────────────────────────────
    with tab_filter:
        fc1, fc2, fc3, fc4 = st.columns([2, 2, 1, 1])
        with fc1:
            routine_names = ["All"] + sorted(set(l.routine_name for l in all_logs))
            f_routine = st.selectbox("Rotina", routine_names, key="log_f_rot")
        with fc2:
            f_level = st.selectbox("Nível", ["All", "INFO", "ERROR", "WARN"], key="log_f_lvl")
        with fc3:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🔄 Refresh", use_container_width=True, key="log_refresh"):
                st.rerun()
        with fc4:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🗑 Limpar tudo", use_container_width=True, key="log_clear"):
                clear_logs(); st.rerun()

        filtered = all_logs
        if f_routine != "All": filtered = [l for l in filtered if l.routine_name == f_routine]
        if f_level   != "All": filtered = [l for l in filtered if l.level == f_level]
        filtered = list(reversed(filtered))

        st.markdown(f"<span style='color:#6b7280;font-size:0.85rem;'>{len(filtered)} entradas</span>", unsafe_allow_html=True)
        _render_log_lines(filtered)


# ─── Settings ─────────────────────────────────────────────────────────────────
def page_settings():
    st.markdown('<div class="section-header">⚙️ Settings</div>', unsafe_allow_html=True)

    tab_cfg, tab_io = st.tabs(["🔧 Configuração", "📤 Import / Export"])

    with tab_cfg:
        from backend.storage import VARIABLES_FILE, ROUTINES_FILE, SCHEDULER_FILE, LOGS_FILE
        st.markdown("#### Arquivos de Configuração")
        st.code(
            f"Variables:  {VARIABLES_FILE}\n"
            f"Routines:   {ROUTINES_FILE}\n"
            f"Scheduler:  {SCHEDULER_FILE}\n"
            f"Logs:       {LOGS_FILE}"
        )
        st.markdown("#### Status do Sistema")
        cols = st.columns(3)
        with cols[0]:
            st.metric("Rotinas",   len(load_routines()))
            st.metric("Variáveis", len(load_variables()))
        with cols[1]:
            st.metric("Schedules", len(load_schedules()))
            st.metric("Logs",      len(load_logs(1000)))
        with cols[2]:
            st.metric("Running",   len(get_running_ids()))
            st.metric("Scheduler", "ON" if sched_running() else "OFF")

        if st.button("🔄 Recalcular Próximas Execuções"):
            refresh_next_runs(); st.success("✓ Recalculado!")

    with tab_io:
        import json
        st.markdown("#### Export")
        export = {
            "exported_at": datetime.now().isoformat(),
            "variables":   [v.to_dict() for v in load_variables()],
            "routines":    [r.to_dict() for r in load_routines()],
            "schedules":   [s.to_dict() for s in load_schedules()],
        }
        st.download_button("📥 Download cockpit_config.json",
                           data=json.dumps(export, indent=2, ensure_ascii=False),
                           file_name="cockpit_config.json", mime="application/json",
                           use_container_width=True)

        st.markdown("#### Import")
        up = st.file_uploader("Upload cockpit_config.json", type=["json"])
        if up:
            try:
                data = json.load(up)
                if st.button("⬆️ Importar (sobrescreve tudo)", use_container_width=True):
                    if "variables" in data: save_variables([Variable.from_dict(v) for v in data["variables"]])
                    if "routines"  in data: save_routines([Routine.from_dict(r)  for r in data["routines"]])
                    if "schedules" in data: save_schedules([Schedule.from_dict(s) for s in data["schedules"]])
                    st.success("✓ Importado!"); st.rerun()
            except Exception as e:
                st.error(f"Erro: {e}")


# ─── Workflow ─────────────────────────────────────────────────────────────────
def build_workflow_html(routines, running_ids):
    import json as _json

    STATUS_COLORS = {
        "success": {"bg": "#064e3b", "border": "#34d399", "font": "#34d399"},
        "failed":  {"bg": "#7f1d1d", "border": "#f87171", "font": "#fca5a5"},
        "running": {"bg": "#1e3a5f", "border": "#3b82f6", "font": "#93c5fd"},
        "pending": {"bg": "#1f2937", "border": "#4b5563", "font": "#9ca3af"},
        "skipped": {"bg": "#292524", "border": "#d97706", "font": "#fbbf24"},
        "stopped": {"bg": "#1c1917", "border": "#6b7280", "font": "#d1d5db"},
    }
    TYPE_ICONS_JS = {
        "python": "🐍", "excel": "📗", "vba": "📘",
        "shell": "💻", "api": "🌐", "group": "📁",
    }
    COND_LABELS = {
        "always": "", "on_success": "✓ if ok", "on_failure": "✗ if fail",
    }

    nodes = []
    edges = []

    for r in routines:
        is_run = r.id in running_ids
        status = "running" if is_run else r.last_status
        c = STATUS_COLORS.get(status, STATUS_COLORS["pending"])
        icon = TYPE_ICONS_JS.get(r.type, "⚙️")
        is_group = r.type == "group"

        label = f"{icon} {r.name}"
        title = (
            f"<b>{r.name}</b><br>"
            f"Type: {r.type}<br>"
            f"Status: {status}<br>"
            f"Cmd: {r.command or '—'}<br>"
            f"Params: {r.parameters or '—'}<br>"
            f"Timeout: {r.timeout}s | Retry: {r.retry}x"
        )

        node = {
            "id": r.id,
            "label": label,
            "title": title,
            "shape": "box",
            "color": {
                "background": c["bg"],
                "border": c["border"],
                "highlight": {"background": "#1e3a5f", "border": "#60a5fa"},
                "hover":     {"background": "#252d45", "border": "#60a5fa"},
            },
            "font": {"color": c["font"], "size": 13, "face": "monospace"},
            "borderWidth": 2,
            "borderWidthSelected": 3,
            "margin": {"top": 10, "bottom": 10, "left": 14, "right": 14},
            "shadow": {"enabled": True, "color": "rgba(0,0,0,0.5)", "size": 8},
            "level": 0 if r.parent_id is None else 1,
            "_status": status,
            "_enabled": r.enabled,
            "_type": r.type,
            "_parent_id": r.parent_id or "",
            "_routine_id": r.id,
            "_name": r.name,
            "_cmd": r.command or "",
            "_params": r.parameters or "",
            "_desc": r.description or "",
        }
        if is_group:
            node["borderDashes"] = [5, 3]
            node["font"]["size"] = 14
            node["font"]["bold"] = True
        if not r.enabled:
            node["opacity"] = 0.5
        nodes.append(node)

        # Parent → child edge (group membership)
        if r.parent_id:
            edges.append({
                "from": r.parent_id,
                "to": r.id,
                "color": {"color": "#2d3149", "highlight": "#4b5563"},
                "dashes": [4, 4],
                "arrows": {"to": {"enabled": True, "scaleFactor": 0.6}},
                "width": 1,
                "label": "",
                "smooth": {"type": "cubicBezier", "forceDirection": "vertical"},
            })

        # Dependency edges
        for dep_id in r.depends_on:
            cond_lbl = COND_LABELS.get(r.run_condition, "")
            dep_status = next((x.last_status for x in routines if x.id == dep_id), "pending")
            edge_color = "#34d399" if dep_status == "success" else "#f87171" if dep_status == "failed" else "#3b82f6" if dep_id in running_ids else "#4b5563"
            edges.append({
                "from": dep_id,
                "to": r.id,
                "color": {"color": edge_color, "highlight": "#60a5fa"},
                "arrows": {"to": {"enabled": True, "scaleFactor": 0.8}},
                "width": 2,
                "label": cond_lbl,
                "font": {"color": "#9ca3af", "size": 10, "background": "#0f1117"},
                "smooth": {"type": "cubicBezier", "forceDirection": "vertical"},
            })

    nodes_json = _json.dumps(nodes)
    edges_json = _json.dumps(edges)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.js"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.css" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#0f1117; font-family: 'Segoe UI', sans-serif; overflow:hidden; }}
  #graph {{ width:100%; height:580px; border:1px solid #2d3149; border-radius:10px; background:#0f1117; }}

  /* Tooltip */
  .vis-tooltip {{
    background:#1a1d27 !important; border:1px solid #3b82f6 !important;
    color:#e2e8f0 !important; border-radius:8px !important;
    padding:8px 12px !important; font-size:12px !important;
    font-family: 'Segoe UI', sans-serif !important; max-width:260px;
  }}

  /* Info panel (tooltip on click — info only, no action buttons) */
  #panel {{
    position:absolute; bottom:12px; left:12px; right:300px;
    background:#1a1d27cc; border:1px solid #2563eb; border-radius:10px;
    padding:10px 14px; display:none; z-index:100;
    backdrop-filter: blur(4px);
  }}
  #panel-name  {{ font-size:0.95rem; font-weight:700; color:#e2e8f0; margin-bottom:4px; }}
  #panel-meta  {{ font-size:0.78rem; color:#9ca3af; line-height:1.5; }}
  .btn-close {{
    position:absolute; top:8px; right:10px;
    background:transparent; border:none; color:#6b7280;
    cursor:pointer; font-size:1rem;
  }}
  .btn-close:hover {{ color:#e2e8f0; }}

  /* Legend */
  #legend {{
    position:absolute; top:12px; right:12px;
    background:#1a1d27cc; border:1px solid #2d3149; border-radius:8px;
    padding:8px 12px; display:flex; gap:12px; font-size:0.75rem; z-index:10;
  }}
  .leg {{ display:flex; align-items:center; gap:4px; color:#9ca3af; }}
  .dot {{ width:10px; height:10px; border-radius:3px; }}

  /* Controls */
  #controls {{
    position:absolute; top:12px; left:12px;
    display:flex; gap:6px; z-index:10;
  }}
  .ctrl-btn {{
    background:#1a1d27cc; border:1px solid #2d3149; color:#9ca3af;
    border-radius:6px; padding:5px 10px; font-size:0.78rem; cursor:pointer;
  }}
  .ctrl-btn:hover {{ background:#252a3f; color:#e2e8f0; }}

  #selected-id {{ display:none; }}
  #selected-action {{ display:none; }}
</style>
</head>
<body>
<div style="position:relative;">
  <div id="graph"></div>

  <div id="legend">
    <div class="leg"><div class="dot" style="background:#34d399;"></div>success</div>
    <div class="leg"><div class="dot" style="background:#f87171;"></div>failed</div>
    <div class="leg"><div class="dot" style="background:#3b82f6;"></div>running</div>
    <div class="leg"><div class="dot" style="background:#4b5563;"></div>pending</div>
    <div class="leg"><div class="dot" style="background:#d97706;"></div>skipped</div>
    <div class="leg"><div class="dot" style="background:#2d3149; border:1px dashed #4b5563;"></div>group</div>
  </div>

  <div id="controls">
    <button class="ctrl-btn" onclick="network.fit()">⊡ Fit</button>
    <button class="ctrl-btn" onclick="toggleLayout()">⇅ Layout</button>
    <button class="ctrl-btn" onclick="network.setOptions({{physics:{{enabled:!physicsOn}}}});physicsOn=!physicsOn; this.textContent=physicsOn?'⏸ Physics':'▶ Physics';">⏸ Physics</button>
  </div>

  <div id="panel">
    <button class="btn-close" onclick="closePanel()">✕</button>
    <div id="panel-name">—</div>
    <div id="panel-meta">—</div>
  </div>
</div>

<script>
var nodes_data = {nodes_json};
var edges_data = {edges_json};
var selectedNode = null;
var physicsOn = true;
var layoutVertical = true;

var nodes = new vis.DataSet(nodes_data);
var edges = new vis.DataSet(edges_data);

var options = {{
  layout: {{
    hierarchical: {{
      enabled: true,
      direction: 'UD',
      sortMethod: 'directed',
      nodeSpacing: 160,
      levelSeparation: 120,
      treeSpacing: 200,
    }}
  }},
  physics: {{ enabled: false }},
  interaction: {{
    hover: true,
    tooltipDelay: 200,
    navigationButtons: false,
    keyboard: true,
    zoomView: true,
  }},
  edges: {{
    smooth: {{ type: 'cubicBezier', forceDirection: 'vertical' }},
  }},
  nodes: {{
    borderWidth: 2,
    chosen: true,
  }},
}};

var network = new vis.Network(
  document.getElementById('graph'),
  {{ nodes: nodes, edges: edges }},
  options
);

function toggleLayout() {{
  layoutVertical = !layoutVertical;
  network.setOptions({{
    layout: {{
      hierarchical: {{
        direction: layoutVertical ? 'UD' : 'LR',
      }}
    }}
  }});
}}

network.on('click', function(params) {{
  if (params.nodes.length === 0) {{
    closePanel();
    return;
  }}
  var id = params.nodes[0];
  var nd = nodes_data.find(function(n) {{ return n.id === id; }});
  if (!nd) return;
  selectedNode = nd;
  showPanel(nd);
}});

function showPanel(nd) {{
  var panel = document.getElementById('panel');
  panel.style.display = 'flex';

  var statusEmoji = {{'success':'✓','failed':'✗','running':'●','pending':'◌','skipped':'⏭','stopped':'■'}};
  var em = statusEmoji[nd._status] || '?';

  document.getElementById('panel-name').innerHTML =
    '<span style="color:' + ({{success:'#34d399',failed:'#f87171',running:'#60a5fa',skipped:'#fbbf24'}}[nd._status]||'#9ca3af') + '">' + em + '</span> ' + nd.label;
  document.getElementById('panel-meta').innerHTML =
    '<b>Type:</b> ' + nd._type +
    (nd._cmd ? ' &nbsp;|&nbsp; <b>Cmd:</b> <code>' + nd._cmd + '</code>' : '') +
    (nd._params ? ' <code>' + nd._params + '</code>' : '') +
    (nd._desc ? '<br><span style="color:#6b7280">' + nd._desc + '</span>' : '') +
    '<br><span style="color:#4b5563;font-size:0.75rem;">⬇ Use os botões abaixo para executar</span>';
}}

function closePanel() {{
  document.getElementById('panel').style.display = 'none';
  selectedNode = null;
  network.unselectAll();
}}

// Pulse animation for running nodes
function pulseRunning() {{
  nodes_data.forEach(function(nd) {{
    if (nd._status === 'running') {{
      var t = Date.now() / 400;
      var alpha = 0.6 + 0.4 * Math.sin(t);
      nodes.update({{
        id: nd.id,
        color: {{
          background: 'rgba(30,58,95,' + alpha + ')',
          border: '#3b82f6',
        }}
      }});
    }}
  }});
  requestAnimationFrame(pulseRunning);
}}
pulseRunning();

// Initial fit
setTimeout(function() {{ network.fit({{ animation: {{ duration: 600, easingFunction: 'easeInOutQuad' }} }}); }}, 300);
</script>
</body>
</html>"""


def page_workflow():
    import streamlit.components.v1 as components
    import json

    st.markdown('<div class="section-header">🔀 Workflow</div>', unsafe_allow_html=True)

    routines    = load_routines()
    running_ids = get_running_ids()

    if not routines:
        st.info("Nenhuma rotina cadastrada. Vá em **Routines** para criar rotinas.")
        return

    # ── Toolbar ──
    col_filter, col_group, col_refresh, col_auto = st.columns([2, 2, 1, 1])
    with col_filter:
        all_groups = [r for r in routines if r.type == "group"]
        group_names = ["Todas as rotinas"] + [r.name for r in all_groups]
        selected_group = st.selectbox("Filtrar por pipeline", group_names, label_visibility="collapsed")
    with col_group:
        show_mode = st.selectbox("Visualização", ["Hierárquico (cima→baixo)", "Hierárquico (esquerda→direita)", "Livre"], label_visibility="collapsed")
    with col_refresh:
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()
    with col_auto:
        auto = st.checkbox("Auto (3s)")

    # Filter routines
    if selected_group != "Todas as rotinas":
        grp = next((r for r in routines if r.name == selected_group), None)
        if grp:
            visible_ids = {grp.id} | {r.id for r in routines if r.parent_id == grp.id}
            show_routines = [r for r in routines if r.id in visible_ids]
        else:
            show_routines = routines
    else:
        show_routines = routines

    # ── DAG graph (visualization — click nodes to see info tooltip) ──
    @st.fragment(run_every=3)
    def dag_fragment():
        r_now   = load_routines()
        run_now = get_running_ids()
        if selected_group != "Todas as rotinas":
            grp = next((r for r in r_now if r.name == selected_group), None)
            if grp:
                vis_ids = {grp.id} | {r.id for r in r_now if r.parent_id == grp.id}
                r_now = [r for r in r_now if r.id in vis_ids]
        html = build_workflow_html(r_now, run_now)
        components.html(html, height=540, scrolling=False)

    dag_fragment()

    # ── Task cards: one row per pipeline, one card per task ──────────────────
    st.markdown("---")
    st.markdown(
        "<div style='color:#9ca3af;font-size:0.8rem;margin-bottom:8px;'>"
        "👇 Clique em <b>▶ Run</b> ou <b>■ Stop</b> para controlar as rotinas</div>",
        unsafe_allow_html=True,
    )

    @st.fragment(run_every=2)
    def task_cards_fragment():
        from backend.storage import update_routine_status as _urs
        r_now   = load_routines()
        run_now = get_running_ids()

        STATUS_C = {
            "success":"#34d399","failed":"#f87171","running":"#60a5fa",
            "pending":"#9ca3af","skipped":"#fbbf24","stopped":"#d1d5db",
        }
        from backend.executor import substitute_variables as _sv
        vd = get_variables_dict()

        # Group: parents first, then standalone
        parents = [r for r in r_now if r.parent_id is None]
        ch_map  = {}
        for r in r_now:
            if r.parent_id:
                ch_map.setdefault(r.parent_id, []).append(r)

        for parent in sorted(parents, key=lambda r: r.order):
            children = sorted(ch_map.get(parent.id, []), key=lambda r: r.order)
            rows = [parent] + children if children else [parent]

            with st.container():
                cols = st.columns(len(rows))
                for col, r in zip(cols, rows):
                    with col:
                        is_run  = r.id in run_now
                        status  = "running" if is_run else r.last_status
                        sc      = STATUS_C.get(status, "#9ca3af")
                        icon    = TYPE_ICON.get(r.type, "⚙️")
                        indent  = "↳ " if r.parent_id else ""
                        cmd_str = _sv(f"{r.command} {r.parameters}", vd).strip()
                        border  = "#2563eb" if is_run else ("#34d399" if status=="success" else "#f87171" if status=="failed" else "#2d3149")

                        st.markdown(
                            f"<div style='background:#1a1d27;border:2px solid {border};"
                            f"border-radius:10px;padding:0.7rem 0.8rem;margin-bottom:4px;'>"
                            f"<div style='font-weight:700;color:#e2e8f0;font-size:0.88rem;"
                            f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
                            f"{indent}{icon} {r.name}</div>"
                            f"<div style='color:{sc};font-size:0.75rem;font-weight:600;margin:3px 0;'>"
                            f"{'⠿ ' if is_run else ''}● {status}</div>"
                            f"<div style='color:#4b5563;font-size:0.72rem;white-space:nowrap;"
                            f"overflow:hidden;text-overflow:ellipsis;' title='{cmd_str}'>"
                            f"{cmd_str[:30] or '(group)'}</div>"
                            f"<div style='color:#374151;font-size:0.7rem;'>⏱ {fmt_dt(r.last_run)}</div>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                        b1, b2 = st.columns(2)
                        with b1:
                            if st.button("▶ Run", key=f"wf_run_{r.id}", use_container_width=True,
                                         type="primary", disabled=is_run):
                                if r.type == "group":
                                    run_group(r.id)
                                else:
                                    run_routine(r.id)
                                st.toast(f"▶ {r.name}")
                        with b2:
                            if is_run:
                                if st.button("■ Stop", key=f"wf_stop_{r.id}", use_container_width=True):
                                    stop_routine(r.id)
                            else:
                                if st.button("↺ Reset", key=f"wf_rst_{r.id}", use_container_width=True,
                                             help="Limpar status"):
                                    _urs(r.id, "pending")

                st.markdown("<hr style='border-color:#1a2030;margin:6px 0'>", unsafe_allow_html=True)

    task_cards_fragment()

    if auto:
        time.sleep(3)
        st.rerun()


# ─── Live Execution ───────────────────────────────────────────────────────────
def page_live():
    st.markdown('<div class="section-header">🟢 Live Execution</div>', unsafe_allow_html=True)

    routines = load_routines()

    # ── Quick-launch bar ──
    r_opts = {f"{TYPE_ICON.get(r.type,'⚙')} {r.name}": r for r in routines if r.enabled}
    lc1, lc2, lc3, lc4 = st.columns([3, 1, 1, 1])
    with lc1:
        sel_name = st.selectbox("Rotina", list(r_opts.keys()), label_visibility="collapsed", key="live_sel")
    sel_r = r_opts.get(sel_name)
    with lc2:
        if st.button("▶ Run", use_container_width=True, type="primary", key="live_run"):
            if sel_r:
                if sel_r.type == "group": run_group(sel_r.id)
                else: run_routine(sel_r.id)
                st.toast(f"▶ {sel_r.name} iniciado!")
    with lc3:
        running_ids_now = get_running_ids()
        if sel_r and sel_r.id in running_ids_now:
            if st.button("■ Stop", use_container_width=True, key="live_stop"):
                stop_routine(sel_r.id)
                st.rerun()
    with lc4:
        if st.button("🗑 Limpar", use_container_width=True, key="live_clear"):
            clear_live_logs()
            st.rerun()

    st.markdown("---")

    # ── Live status pills (fragment atualiza a cada 1s) ──
    @st.fragment(run_every=1)
    def live_status_fragment():
        routines_now = load_routines()
        running_now  = get_running_ids()

        # Status row
        running_list = [r for r in routines_now if r.id in running_now]
        if running_list:
            pills = ""
            for r in running_list:
                pills += (
                    f"<span style='display:inline-flex;align-items:center;gap:6px;"
                    f"background:#1e3a5f;border:1px solid #3b82f6;border-radius:20px;"
                    f"padding:4px 14px;margin:3px;font-size:0.85rem;color:#93c5fd;font-weight:600;'>"
                    f"<span style='width:8px;height:8px;border-radius:50%;background:#3b82f6;"
                    f"animation:pulse 1s infinite;display:inline-block'></span>"
                    f"{TYPE_ICON.get(r.type,'⚙')} {r.name}</span>"
                )
            st.markdown(
                f"<style>@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.4}}}}</style>"
                f"<div style='margin-bottom:8px;'><b style='color:#9ca3af;font-size:0.8rem;'>EM EXECUÇÃO:</b><br>{pills}</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div style='color:#4b5563;font-size:0.85rem;margin-bottom:8px;'>Nenhuma rotina em execução no momento.</div>",
                unsafe_allow_html=True,
            )

    live_status_fragment()

    # ── Terminal live (fragment atualiza a cada 1s) ──
    @st.fragment(run_every=1)
    def live_terminal_fragment():
        live_entries = get_live_logs(last_n=150)

        if not live_entries:
            # Fallback: show latest from persistent logs
            persisted = load_logs(30)
            if persisted:
                st.markdown(
                    "<div style='color:#4b5563;font-size:0.8rem;margin-bottom:4px;'>"
                    "Aguardando execuções... (mostrando últimos logs persistidos)</div>",
                    unsafe_allow_html=True,
                )
                live_entries = [e.to_dict() for e in reversed(persisted)]
            else:
                st.markdown(
                    "<div style='background:#0d1117;border:1px solid #21262d;border-radius:8px;"
                    "padding:2rem;text-align:center;color:#4b5563;font-size:0.9rem;'>"
                    "Terminal vazio. Execute uma rotina para ver os logs em tempo real.</div>",
                    unsafe_allow_html=True,
                )
                return

        # Build terminal HTML — oldest first (top), newest last (bottom), auto-scroll to bottom
        STATUS_COLORS = {
            "success": "#34d399", "failed": "#f87171", "running": "#60a5fa",
            "skipped": "#fbbf24", "stopped": "#9ca3af", "pending": "#6b7280",
        }
        html = """
        <div id="terminal" style="
            background:#0d1117; border:1px solid #21262d; border-radius:10px;
            padding:1rem 1.2rem; font-family:'Cascadia Code','Courier New',monospace;
            font-size:0.82rem; height:460px; overflow-y:auto;
        ">
        """
        # live_entries is already oldest→newest (deque order)
        for e in live_entries:
            ts  = fmt_dt(e.get("ts",""))
            lvl = e.get("level","INFO")
            nm  = e.get("name","")
            msg = e.get("message","")
            st_ = e.get("status","")
            rid = e.get("run_id","")[:6]

            sc   = STATUS_COLORS.get(st_, "#9ca3af")
            lc   = "#f85149" if lvl == "ERROR" else "#3fb950" if lvl == "INFO" else "#e3b341"
            icon = {"success":"✓","failed":"✗","running":"●","skipped":"⏭","stopped":"■","pending":"◌"}.get(st_,"·")

            html += (
                f"<div style='padding:2px 0;white-space:pre-wrap;'>"
                f"<span style='color:#4b5563;'>{ts}</span> "
                f"<span style='color:{lc};font-weight:600;'>[{lvl:5s}]</span> "
                f"<span style='color:#4b5563;'>#{rid}</span> "
                f"<span style='color:{sc};font-weight:600;'>{icon}</span> "
                f"<span style='color:#60a5fa;'>{nm[:20]:<20}</span> "
                f"<span style='color:#e6edf3;'>{msg}</span>"
                f"</div>"
            )

        # Auto-scroll to bottom so newest lines are always visible
        html += """
        </div>
        <script>
          (function() {
            var t = document.getElementById('terminal');
            if (t) t.scrollTop = t.scrollHeight;
          })();
        </script>
        """
        st.markdown(html, unsafe_allow_html=True)

    live_terminal_fragment()

    # ── Persistent log summary ──
    with st.expander("📜 Histórico persistido (últimas 50 entradas)"):
        page_logs()


# ─── Main ─────────────────────────────────────────────────────────────────────
sidebar()

{
    "Dashboard": page_dashboard,
    "Workflow":  page_workflow,
    "Live":      page_live,
    "Variables": page_variables,
    "Routines":  page_routines,
    "Scheduler": page_scheduler,
    "Logs":      page_logs,
    "Settings":  page_settings,
}.get(st.session_state.get("page", "Dashboard"), page_dashboard)()
