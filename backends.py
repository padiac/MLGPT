"""Backend dispatcher — routes between Cursor Agent CLI and Claude Code CLI.

Both backend modules expose the same surface (`get_available_models`,
`create_process`, `kill_process`, `iter_events`, `stream_response`).
This module:
  - lists the available backend names,
  - dispatches calls by name,
  - tags every process object with the backend that created it so
    cleanup-on-refresh (which has no settings context) can still kill it.
"""
from __future__ import annotations

import subprocess
from typing import Generator

import cursor_cli
import claude_cli

BACKENDS: dict[str, object] = {
    "cursor": cursor_cli,
    "claude": claude_cli,
}

DEFAULT_BACKEND = "claude"

# Display labels for the settings UI (kept in this module to avoid a circular).
BACKEND_LABELS: dict[str, str] = {
    "cursor": "Cursor Agent CLI",
    "claude": "Claude Code CLI",
}

# Default model per backend — used when switching backends and the old
# model id isn't recognized by the new one.
DEFAULT_MODELS: dict[str, str] = {
    "cursor": "composer-2",
    "claude": "sonnet",
}

# Attribute we attach to the Popen object so kill_process() can dispatch
# without consulting settings (the cleanup-on-refresh path has none).
_BACKEND_ATTR = "_mlgpt_backend"


def list_backends() -> list[tuple[str, str]]:
    return [(name, BACKEND_LABELS.get(name, name)) for name in BACKENDS]


def _resolve(name: str | None):
    return BACKENDS.get(name or DEFAULT_BACKEND, BACKENDS[DEFAULT_BACKEND])


def get_available_models(backend: str) -> list[tuple[str, str]]:
    return _resolve(backend).get_available_models()


def create_process(
    backend: str,
    prompt: str,
    cwd=None,
    model: str | None = None,
    mode: str = "agent",
    resume_session: str | None = None,
) -> tuple[subprocess.Popen | None, str | None]:
    mod = _resolve(backend)
    proc, err = mod.create_process(
        prompt=prompt, cwd=cwd, model=model, mode=mode, resume_session=resume_session
    )
    if proc is not None:
        try:
            setattr(proc, _BACKEND_ATTR, backend)
        except Exception:
            pass
    return proc, err


def kill_process(process: subprocess.Popen | None) -> None:
    """Terminate a process started by either backend."""
    if process is None:
        return
    backend = getattr(process, _BACKEND_ATTR, DEFAULT_BACKEND)
    _resolve(backend).kill_process(process)


def iter_events(process: subprocess.Popen) -> Generator[tuple[str, str], None, None]:
    backend = getattr(process, _BACKEND_ATTR, DEFAULT_BACKEND)
    yield from _resolve(backend).iter_events(process)
