"""Claude Code CLI wrapper with NDJSON streaming support.

Parallels `cursor_cli.py` and exposes the same surface so app.py can route
either backend through `backends.py` without conditionals.

Uses `claude -p --output-format stream-json --include-partial-messages --verbose`
to stream individual text deltas from the Claude Code CLI.

Output event types (NDJSON, one JSON object per line):
  type=system, subtype=init        — has session_id, cwd, tools list
  type=system, subtype=status      — status changes (requesting/responding)
  type=stream_event                — wraps native Anthropic streaming events:
      event.type=message_start
      event.type=content_block_start    — content_block.type ∈ {text, thinking, tool_use}
      event.type=content_block_delta    — delta.type ∈ {text_delta, thinking_delta, input_json_delta, signature_delta}
      event.type=content_block_stop
      event.type=message_delta          — stop_reason, usage
      event.type=message_stop
  type=assistant                   — accumulated assistant message snapshot
  type=user                        — accumulated user message snapshot
  type=result                      — final summary {is_error, duration_ms, result, ...}

Auth: uses your Claude subscription via OAuth (same as PrepSignal); does NOT
need ANTHROPIC_API_KEY. `--bare` is deliberately avoided because it would
force API key auth.

To override the CLI location:
  Set MLGPT_CLAUDE_PATH to the full path to the claude executable.

Debug logging:
  Set env MLGPT_DEBUG_NDJSON=1 to write raw NDJSON lines to
  data/debug_ndjson/<timestamp>.jsonl for protocol analysis.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Generator

_ROOT = Path(__file__).resolve().parent


def _debug_enabled() -> bool:
    return bool(os.environ.get("MLGPT_DEBUG_NDJSON"))


def _open_debug_log():
    if not _debug_enabled():
        return None
    log_dir = _ROOT / "data" / "debug_ndjson"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{int(time.time())}_claude.jsonl"
    return open(log_path, "w", encoding="utf-8")


# Claude Code CLI uses model aliases or full ids; there's no `claude models`
# subcommand, so we hardcode the current Claude 4.x family. Updated as new
# models ship.
_AVAILABLE_MODELS: list[tuple[str, str]] = [
    ("opus",   "Opus 4.8 (latest, most capable)"),
    ("sonnet", "Sonnet 4.6 (balanced)"),
    ("haiku",  "Haiku 4.5 (fastest, cheapest)"),
    ("claude-opus-4-7",   "Opus 4.7 (pinned)"),
    ("claude-sonnet-4-6", "Sonnet 4.6 (pinned)"),
    ("claude-haiku-4-5",  "Haiku 4.5 (pinned)"),
]


def get_available_models() -> list[tuple[str, str]]:
    return list(_AVAILABLE_MODELS)


def _find_claude_cmd() -> str:
    explicit = os.environ.get("MLGPT_CLAUDE_PATH")
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p.resolve())

    found = shutil.which("claude")
    if found:
        return found

    if os.name == "nt":
        # npm-global on Windows
        for base in (os.environ.get("APPDATA", ""),):
            if not base:
                continue
            for name in ("claude.cmd", "claude.exe", "claude"):
                candidate = Path(base) / "npm" / name
                if candidate.is_file():
                    return str(candidate.resolve())

    return "claude"  # fallback for error message


_NOT_FOUND_MSG = (
    "Claude Code CLI (`claude`) not found.\n\n"
    "Install:  `npm install -g @anthropic-ai/claude-code`\n\n"
    "Then log in (uses your Claude subscription, no API key needed):  `claude /login`\n\n"
    "If installed but not found by this app, set `MLGPT_CLAUDE_PATH` to the full path "
    "(e.g. `$env:MLGPT_CLAUDE_PATH = \"C:\\Users\\you\\AppData\\Roaming\\npm\\claude.cmd\"`)."
)


# Modes — same names as Cursor for app-level parity.
# `agent` uses bypassPermissions so Bash/Edit/Write run without per-call
# approval prompts. Default permission mode is interactive-only and silently
# denies in -p mode, which broke the show-note skill (show_file.py Bash call
# was rejected with "This command requires approval"). This matches how
# Cursor's agent operates by default.
_MODE_TO_CLAUDE_FLAGS: dict[str, list[str]] = {
    "agent": ["--permission-mode", "bypassPermissions"],
    "ask":   ["--tools", "default"],
    "plan":  ["--permission-mode", "plan"],
}


def create_process(
    prompt: str,
    cwd: str | Path | None = None,
    model: str | None = None,
    mode: str = "agent",
    resume_session: str | None = None,
) -> tuple[subprocess.Popen | None, str | None]:
    """Start a Claude Code CLI subprocess.

    Returns (process, None) on success or (None, error_message) on failure.
    """
    claude = _find_claude_cmd()
    args: list[str] = [
        claude, "-p",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",                # required for stream-json output
    ]
    if model:
        args.extend(["--model", model])
    args.extend(_MODE_TO_CLAUDE_FLAGS.get(mode, []))
    if resume_session:
        args.extend(["--resume", resume_session])

    if _debug_enabled():
        log_dir = _ROOT / "data" / "debug_ndjson"
        log_dir.mkdir(parents=True, exist_ok=True)
        req_path = log_dir / f"{int(time.time())}_claude_request.txt"
        with open(req_path, "w", encoding="utf-8") as f:
            f.write(f"cwd: {cwd}\n")
            f.write(f"args: {args}\n")
            f.write(f"model: {model}\n")
            f.write(f"mode: {mode}\n")
            f.write(f"resume_session: {resume_session}\n")
            f.write("--- PROMPT ---\n")
            f.write(prompt)
            f.write("\n")

    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            cwd=str(cwd) if cwd else None,
        )
        process.stdin.write(prompt.encode("utf-8"))
        process.stdin.close()
        return process, None
    except FileNotFoundError:
        return None, _NOT_FOUND_MSG


def kill_process(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Detect show_file.py shell invocations → push show_file events to frontend
# (mirrors Cursor's _SHOW_FILE_RE; user-facing "attach this file to chat")
# ---------------------------------------------------------------------------
_SHOW_FILE_RE = re.compile(r'show_file\.py\s+(.+)')


def _SHOW_FILE_RE_PATHS(cmd: str) -> list[str]:
    m = _SHOW_FILE_RE.search(cmd or "")
    if not m:
        return []
    raw = m.group(1).strip()
    return [p.strip().strip("'\"") for p in raw.split() if p.strip()]




# ---------------------------------------------------------------------------
# Tool-call descriptions for the streaming UI
# ---------------------------------------------------------------------------
# Built-in Claude Code tools we care about and how to summarize them.
# Key: (tool name, arg field used for description, verb).
_TOOL_MAP: dict[str, tuple[str, str]] = {
    "Read":     ("file_path",     "Reading"),
    "Edit":     ("file_path",     "Editing"),
    "Write":    ("file_path",     "Writing"),
    "Bash":     ("command",       "Running"),
    "Grep":     ("pattern",       "Searching"),
    "Glob":     ("pattern",       "Finding"),
    "LS":       ("path",          "Listing"),
    "WebFetch": ("url",           "Fetching"),
    "WebSearch":("query",         "Web search"),
    "Task":     ("description",   "Subagent"),
    # MCP tools come through as "mcp__<server>__<tool>"; handled separately.
}


def _describe_tool(name: str, input_obj: dict) -> str:
    if not name:
        return ""
    if name in _TOOL_MAP:
        field, verb = _TOOL_MAP[name]
        val = (input_obj or {}).get(field, "")
        if val:
            short = val if len(val) <= 80 else val[:77] + "..."
            return f"{verb}: `{short}`"
        return verb
    if name.startswith("mcp__"):
        # mcp__<server>__<tool>
        parts = name.split("__", 2)
        pretty = parts[-1] if parts else name
        return f"MCP: `{pretty}`"
    return f"Tool: `{name}`"


# ---------------------------------------------------------------------------
# Streaming iterator — translates Claude Code NDJSON → (event_type, payload)
# ---------------------------------------------------------------------------

def iter_events(
    process: subprocess.Popen,
) -> Generator[tuple[str, str], None, None]:
    """Iterate NDJSON events from a running Claude Code process.

    Yields the same (event_type, payload) tuples cursor_cli yields, so app.py
    can consume both backends interchangeably:
        ("text",         "<chunk>")    — assistant text delta (append)
        ("text_replace", "<full>")     — full text replacing accumulated
        ("tool",         "<desc>")     — tool activity indicator
        ("show_file",    "<path>")     — render this file in the chat
        ("session_id",   "<id>")       — CLI session id (for --resume)
        ("error",        "<message>")  — error detail
        ("done",         "<exit>")     — stream finished
    """
    session_id: str | None = None
    accumulated = ""
    # Per content_block (indexed by `index`): track active tool_use {name, input_buf}
    tool_blocks: dict[int, dict] = {}
    # Track which Read paths we've already surfaced as show_file (dedup)
    seen_show: set[str] = set()
    dbg = _open_debug_log()

    try:
        for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if dbg:
                dbg.write(line + "\n")
                dbg.flush()

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            top_type = data.get("type", "")

            # session_id appears on init AND on every event; capture once
            if not session_id and "session_id" in data:
                session_id = data["session_id"]
                yield ("session_id", session_id)

            if top_type == "system":
                sub = data.get("subtype", "")
                if sub == "init":
                    continue  # already captured session_id
                # status / post_turn_summary etc — informational, skip
                continue

            if top_type == "stream_event":
                evt = data.get("event", {})
                ev_type = evt.get("type", "")

                if ev_type == "content_block_start":
                    idx = evt.get("index", -1)
                    cb = evt.get("content_block", {})
                    if cb.get("type") == "tool_use":
                        tool_blocks[idx] = {
                            "name":  cb.get("name", ""),
                            "input": dict(cb.get("input", {}) or {}),
                            "buf":   "",
                        }
                        # Surface immediately even before input deltas arrive
                        desc = _describe_tool(tool_blocks[idx]["name"], tool_blocks[idx]["input"])
                        if desc:
                            yield ("tool", desc)

                elif ev_type == "content_block_delta":
                    idx = evt.get("index", -1)
                    delta = evt.get("delta", {})
                    d_type = delta.get("type", "")

                    if d_type == "text_delta":
                        text = delta.get("text", "")
                        if not text:
                            continue
                        if not accumulated:
                            accumulated = text
                            yield ("text", text)
                        else:
                            accumulated += text
                            yield ("text", text)

                    elif d_type == "input_json_delta":
                        # Tool input streams in piece by piece as partial JSON.
                        # Buffer it; we re-describe the tool when block stops.
                        tb = tool_blocks.get(idx)
                        if tb is not None:
                            tb["buf"] += delta.get("partial_json", "")

                    # thinking_delta / signature_delta: ignore for UI

                elif ev_type == "content_block_stop":
                    idx = evt.get("index", -1)
                    tb = tool_blocks.pop(idx, None)
                    if tb is not None:
                        # Now we have the complete tool input; re-describe with
                        # actual arguments (the earlier yield was just the verb).
                        if tb["buf"]:
                            try:
                                parsed = json.loads(tb["buf"])
                                if isinstance(parsed, dict):
                                    tb["input"].update(parsed)
                            except json.JSONDecodeError:
                                pass
                        desc = _describe_tool(tb["name"], tb["input"])
                        if desc:
                            yield ("tool", desc)
                        # Bash calls matching `show_file.py <path>` mirror
                        # Cursor's user-facing "attach this file to the chat"
                        # convention. Regular Read calls are NOT surfaced as
                        # show_file — internal file reads (e.g. the show-note
                        # skill reading its own temp normalized .md) must not
                        # leak as collapsed raw-code attachments, since the
                        # skill pastes content as response text instead.
                        if tb["name"] == "Bash":
                            cmd = tb["input"].get("command", "") or ""
                            for path in _SHOW_FILE_RE_PATHS(cmd):
                                if path and path not in seen_show:
                                    seen_show.add(path)
                                    yield ("show_file", path)

                elif ev_type == "message_delta":
                    # final stop_reason; carry no UI payload
                    pass

                # message_start / message_stop: informational, skip

            elif top_type == "assistant":
                # End-of-stream snapshot of the full assistant message.
                # Use it only when our delta accumulation looks short/empty
                # (sanity replace).
                msg = data.get("message", {})
                full_text = ""
                for block in msg.get("content", []):
                    if block.get("type") == "text":
                        full_text += block.get("text", "")
                if full_text and not accumulated:
                    accumulated = full_text
                    yield ("text_replace", full_text)

            elif top_type == "result":
                if data.get("is_error"):
                    msg = (
                        data.get("result")
                        or data.get("api_error_status")
                        or "Claude Code reported an error"
                    )
                    yield ("error", str(msg))
                # final 'result' carries duration/usage; nothing else to surface

            elif top_type == "user":
                # Tool results echoed back as user messages — ignore (Claude
                # Code attributes them to the user role, but they're internal).
                continue

        process.wait()
        if process.returncode and process.returncode != 0:
            err_raw = process.stderr.read()
            if err_raw:
                stderr = err_raw.decode("utf-8", errors="replace").strip()
                if stderr:
                    yield ("error", stderr)

    except Exception as exc:
        yield ("error", str(exc))
    finally:
        kill_process(process)
        if dbg:
            dbg.close()

    rc = process.returncode if process.returncode is not None else 1
    yield ("done", str(rc))


def stream_response(
    prompt: str,
    cwd: str | Path | None = None,
    model: str | None = None,
    mode: str = "agent",
    resume_session: str | None = None,
) -> Generator[tuple[str, str], None, None]:
    """Convenience wrapper: create_process + iter_events."""
    process, err = create_process(prompt, cwd, model, mode, resume_session)
    if err:
        yield ("error", err)
        yield ("done", "1")
        return
    yield from iter_events(process)
