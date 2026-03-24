# 📊 Finance Routine Cockpit

Dashboard para orquestração de rotinas financeiras — Python, Excel, Shell, APIs.

## ▶️ Como executar

### Windows
```
run.bat
```

### Mac / Linux
```bash
chmod +x run.sh
./run.sh
```

### Manual
```bash
cd cockpit/
pip install -r requirements.txt
streamlit run app.py
```

Acesse: **http://localhost:8501**

---

## 📁 Estrutura

```
cockpit/
├── app.py                   ← UI principal (Streamlit)
├── requirements.txt
├── run.bat / run.sh
├── backend/
│   ├── models.py            ← Dataclasses: Variable, Routine, Schedule, LogEntry
│   ├── storage.py           ← Leitura/escrita JSON
│   ├── executor.py          ← Execução de rotinas (threads)
│   └── scheduler.py         ← Agendamento cron
├── config/
│   ├── variables.json       ← Variáveis globais
│   ├── routines.json        ← Rotinas cadastradas
│   └── scheduler.json       ← Agendamentos cron
└── logs/
    └── execution_logs.json  ← Logs de execução
```

## 🔧 Funcionalidades

| Módulo      | O que faz |
|-------------|-----------|
| Dashboard   | Visão geral, status, próximas execuções, atalhos |
| Variables   | Criar/editar/deletar variáveis globais com preview de substituição |
| Routines    | CRUD de rotinas, árvore pai-filho, DAG de dependências, execução |
| Scheduler   | Agendamento cron, timezone, toggle enable/disable |
| Logs        | Logs de execução com filtros por rotina e nível |
| Settings    | Caminhos, status, export/import JSON completo |

## ⚡ Tipos de Rotina

- `python` → `python script.py {args}`
- `shell`  → `bash script.sh {args}`
- `excel`  → Abre arquivo .xlsx
- `vba`    → Executa macro VBA
- `api`    → `curl {url}`
- `group`  → Contêiner de sub-rotinas

## 🔗 Variáveis nas Rotinas

Use `{NOME_VAR}` em qualquer campo:

```
Comando:    export_pnl.py
Parâmetros: --date {Data_Ref} --fund {Fund_Name}
Dir:        {Path_Base}\scripts
```

## 🕐 Exemplos de Cron

| Expressão      | Descrição               |
|----------------|-------------------------|
| `0 8 * * 1-5`  | 08:00 dias úteis        |
| `0 */2 * * *`  | A cada 2 horas          |
| `*/15 * * * *` | A cada 15 minutos       |
| `0 23 * * *`   | 23:00 todo dia          |
| `0 8 1 * *`    | Dia 1 de cada mês 08:00 |
