# MLGPT

**MLGPT** is a ChatGPT-like web UI for the **Cursor Agent CLI**. Put papers and notes under **`doc/`**; the agent’s **working directory** should be that **`doc/`** folder so answers are based on those files only, not on the rest of the repository.

The app is only a front end — the agent runs with `doc/` (or whatever you set) as its cwd.

## Prerequisites

1. **Cursor CLI** — install and authenticate:

```bash
# macOS / Linux / WSL
curl https://cursor.com/install -fsS | bash
```

```powershell
# Windows PowerShell
irm 'https://cursor.com/install?win32=true' | iex
```

Then log in (once):

```bash
agent login
```

2. **Python 3.11+**

## Install

```bash
pip install -r requirements.txt
```

## Usage

Default working directory is **`./doc`** next to `app.py` if that folder exists; otherwise the app directory. Set **`MLGPT_CWD`** to your `doc/` path explicitly if needed.

```bash
# Linux / macOS / WSL — point at the knowledge folder
MLGPT_CWD=/path/to/MLGPT/doc streamlit run app.py
```

```powershell
# Windows PowerShell
$env:MLGPT_CWD = "C:\Users\You\Desktop\MLGPT\doc"
streamlit run app.py
```

Open the URL shown (default `http://localhost:8501`). **Settings** (sidebar): Working Directory, model, and mode. Settings persist per client IP.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `MLGPT_CWD` | Default working directory for the agent (use your **`doc/`** path). If unset or invalid, defaults to `./doc` if present, else this app’s directory. |
| `MLGPT_AGENT_PATH` | Full path to the `agent` executable if Streamlit’s PATH does not find it. |
| `MLGPT_DEBUG_NDJSON` | Set to `1` to write raw NDJSON to `data/debug_ndjson/` for debugging the CLI protocol. |

### "Agent not found" in the app

1. Find the path: `which agent` (Linux/macOS) or `(Get-Command agent).Source` (PowerShell)
2. `export MLGPT_AGENT_PATH=/path/to/agent` (or the PowerShell equivalent)
3. Start Streamlit in the same terminal.

## Features

- **Streaming** — `agent` with `stream-json` partial output
- **Conversations** — SQLite per client IP; sidebar list and delete
- **Session resume** — `--resume` for follow-ups
- **Memory** — rolling summary of older turns + recent raw turns (shorter prompts on long threads)
- **Knowledge base** — save an answer; a worker writes a Markdown summary under `liked_answers/`
- **Share links** — `?conv=…&msg=…` read-only view
- **Plotly / images / files** — paths in the reply or `show_file`-style flows can be rendered in the UI

## Project layout

| Path | Role |
|------|------|
| `app.py` | Streamlit UI |
| `cursor_cli.py` | Agent subprocess and NDJSON parsing |
| `db.py` | SQLite |
| `memory.py` | Summary + recent turns for `build_prompt` |
| `prompt_utils.py` | `doc/` + cwd prefix for the agent |
| `knowledge.py` | Liked-answer summarization worker |
| `media_utils.py` | Plotly, images, files, markdown |
| `ui_styles.py` | CSS |
| `MEMORY_ARCHITECTURE.md` | How memory works |
| `doc/` | Your knowledge files (PDF, Markdown, etc.) |
| `data/` | DB, Plotly cache, optional NDJSON debug logs |
| `liked_answers/` | Saved summaries from the knowledge base |
